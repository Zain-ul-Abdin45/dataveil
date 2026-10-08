"""Postgres adapter via SQLAlchemy -- the second adapter (PLAN.md Phase 5),
built specifically to prove the Adapter interface holds for something that
isn't SQLMesh: no Context, no virtual/physical table split, just a plain
SQLAlchemy ``Engine`` against a real table.

Registers Luhn/IBAN checksum functions as Postgres PL/pgSQL functions, not
Python UDFs -- Postgres doesn't run arbitrary Python without the untrusted
``plpython3u`` extension, which most managed Postgres instances don't have
enabled. Written in SQL instead, they run entirely inside Postgres's own
engine per row; only the resulting aggregate COUNT ever reaches Python in
core/classify.py, same guarantee as the DuckDB adapter.

Dialect note: ``parse_date``'s ``source_format``/``target_format`` use
Python strftime-style tokens (``%Y-%m-%d``), the same convention the
DuckDB/SQLMesh adapters use, so a plan built against one adapter's profile
reads the same way against this one. This adapter translates the small set
of tokens the operation vocabulary actually needs into Postgres's
``to_char``/``to_timestamp`` pattern language (``YYYY-MM-DD``) -- not a
full strftime-compatibility layer, just the common date/time tokens.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from ..core.adapter import Adapter
from ..core.sql import quote_ident
from ._checksums import IBAN_FUNCTION_SQL, LUHN_FUNCTION_SQL

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

_STRFTIME_TO_POSTGRES = {
    "%Y": "YYYY",
    "%y": "YY",
    "%m": "MM",
    "%d": "DD",
    "%H": "HH24",
    "%I": "HH12",
    "%M": "MI",
    "%S": "SS",
    "%p": "AM",
}


def _to_jsonable(value: Any) -> Any:
    # Postgres/psycopg2 returns NUMERIC aggregates (AVG, STDDEV,
    # PERCENTILE_CONT, ...) as Decimal; DuckDB returns plain floats for the
    # same queries. Converting here keeps run_aggregate_query's output
    # contract (plain JSON-safe scalars) consistent across adapters, so
    # core/profile.py and anything downstream (e.g. audit logging, an MCP
    # tool's JSON result) never needs to know which adapter produced a value.
    if isinstance(value, Decimal):
        return float(value)
    return value


def _translate_date_format(fmt: str) -> str:
    result = fmt
    for token, replacement in _STRFTIME_TO_POSTGRES.items():
        result = result.replace(token, replacement)
    if "%" in result:
        raise ValueError(
            f"date format {fmt!r} contains a strftime token the Postgres adapter "
            f"doesn't translate (only {sorted(_STRFTIME_TO_POSTGRES)} are supported)"
        )
    return result


class PostgresAdapter(Adapter):
    def __init__(self, engine: "Engine", *, register_checksum_functions: bool = True):
        self._engine = engine
        self._has_checksum = False
        if register_checksum_functions:
            with self._engine.begin() as conn:
                conn.execute(sa.text(LUHN_FUNCTION_SQL))
                conn.execute(sa.text(IBAN_FUNCTION_SQL))
            self._has_checksum = True

    def run_aggregate_query(self, sql: str) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            result = conn.execute(sa.text(sql))
            columns = list(result.keys())
            return [{col: _to_jsonable(value) for col, value in zip(columns, row)} for row in result.fetchall()]

    def list_tables(self) -> list[str]:
        return sa.inspect(self._engine).get_table_names()

    def get_schema(self, table: str) -> dict[str, str]:
        columns = sa.inspect(self._engine).get_columns(table)
        if not columns:
            raise ValueError(f"unknown table: {table}")
        return {c["name"]: str(c["type"]) for c in columns}

    def has_checksum_functions(self) -> bool:
        return self._has_checksum

    def execute_operation(
        self, table: str, operation: str, column: str | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        method = getattr(self, f"_op_{operation}", None)
        if method is None:
            raise ValueError(f"adapter does not implement operation {operation!r}")
        result: dict[str, Any] = method(table, column, params)
        return result

    # --- operations ---

    def _op_drop_column(self, table: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        t, c = quote_ident(table), quote_ident(column)
        with self._engine.begin() as conn:
            conn.execute(sa.text(f"ALTER TABLE {t} DROP COLUMN {c}"))
        return {"dropped_column": column}

    def _op_trim_whitespace(self, table: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        t, c = quote_ident(table), quote_ident(column)
        with self._engine.begin() as conn:
            conn.execute(sa.text(f"UPDATE {t} SET {c} = TRIM({c}) WHERE {c} IS NOT NULL"))
        return {}

    def _op_standardize_case(self, table: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        t, c = quote_ident(table), quote_ident(column)
        case = params["case"]
        fn = {"upper": "UPPER", "lower": "LOWER", "title": "INITCAP"}.get(case)
        if fn is None:
            raise ValueError(f"unknown case {case!r}")
        with self._engine.begin() as conn:
            conn.execute(sa.text(f"UPDATE {t} SET {c} = {fn}({c}) WHERE {c} IS NOT NULL"))
        return {}

    def _op_mask(self, table: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        t, c = quote_ident(table), quote_ident(column)
        method = params["method"]
        with self._engine.begin() as conn:
            if method == "hash":
                conn.execute(sa.text(f"UPDATE {t} SET {c} = MD5(CAST({c} AS TEXT)) WHERE {c} IS NOT NULL"))
            elif method == "constant":
                conn.execute(
                    sa.text(f"UPDATE {t} SET {c} = :value WHERE {c} IS NOT NULL"),
                    {"value": params["value"]},
                )
            elif method == "partial":
                keep_last = int(params["keep_last"])
                conn.execute(
                    sa.text(
                        f"UPDATE {t} SET {c} = REPEAT('*', GREATEST(LENGTH(CAST({c} AS TEXT)) - {keep_last}, 0)) "
                        f"|| RIGHT(CAST({c} AS TEXT), {keep_last}) WHERE {c} IS NOT NULL"
                    )
                )
            else:
                raise ValueError(f"unknown mask method {method!r}")
        return {}

    def _op_impute(self, table: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        t, c = quote_ident(table), quote_ident(column)
        strategy = params["strategy"]
        with self._engine.begin() as conn:
            if strategy == "constant":
                conn.execute(
                    sa.text(f"UPDATE {t} SET {c} = :value WHERE {c} IS NULL"),
                    {"value": params["value"]},
                )
                return {}
            expr = {
                "mean": f"(SELECT AVG({c}) FROM {t})",
                "median": f"(SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {c}) FROM {t})",
                "mode": f"(SELECT MODE() WITHIN GROUP (ORDER BY {c}) FROM {t})",
            }.get(strategy)
            if expr is None:
                raise ValueError(f"unknown impute strategy {strategy!r}")
            conn.execute(sa.text(f"UPDATE {t} SET {c} = {expr} WHERE {c} IS NULL"))
        return {}

    def _op_dedupe_rows(self, table: str, column: str | None, params: dict[str, Any]) -> dict[str, Any]:
        t = quote_ident(table)
        keys = ", ".join(quote_ident(k) for k in params["key_columns"])
        before = self.run_aggregate_query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
        with self._engine.begin() as conn:
            conn.execute(sa.text(f"DELETE FROM {t} WHERE ctid NOT IN (SELECT MIN(ctid) FROM {t} GROUP BY {keys})"))
        after = self.run_aggregate_query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
        return {"rows_removed": before - after}

    def _op_bucket_numeric(self, table: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        t, c = quote_ident(table), quote_ident(column)
        target = params.get("target_column") or f"{column}_bucket"
        qtarget = quote_ident(target)

        if "bin_width" in params:
            width = params["bin_width"]
            lower = f"(FLOOR({c} / {width}::NUMERIC) * {width})"
            upper = f"({lower} + {width})"
            expr = f"CAST({lower} AS TEXT) || '-' || CAST({upper} AS TEXT)"
        else:
            bins = params["bins"]
            when_clauses = " ".join(
                f"WHEN {c} >= {bins[i]} AND {c} < {bins[i + 1]} THEN '{bins[i]}-{bins[i + 1]}'"
                for i in range(len(bins) - 1)
            )
            expr = f"CASE WHEN {c} < {bins[0]} THEN '<{bins[0]}' {when_clauses} ELSE '>={bins[-1]}' END"

        with self._engine.begin() as conn:
            conn.execute(sa.text(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {qtarget} TEXT"))
            conn.execute(sa.text(f"UPDATE {t} SET {qtarget} = {expr} WHERE {c} IS NOT NULL"))
        return {"bucket_column": target}

    def _op_parse_date(self, table: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        t, c = quote_ident(table), quote_ident(column)
        source_format = _translate_date_format(params["source_format"])
        target_format = _translate_date_format(params.get("target_format", "%Y-%m-%d"))
        with self._engine.begin() as conn:
            conn.execute(
                sa.text(
                    f"UPDATE {t} SET {c} = to_char(to_timestamp(CAST({c} AS TEXT), :src), :tgt) WHERE {c} IS NOT NULL"
                ),
                {"src": source_format, "tgt": target_format},
            )
        return {}
