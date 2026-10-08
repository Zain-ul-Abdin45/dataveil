"""Reference Adapter implementation backed by an in-process DuckDB
connection.

This is what dataveil's own test suite runs the core engine against end to
end (see PLAN.md Phase 1: "operating against a plain DuckDB/SQLite table").
It also registers the two checksum UDFs (``dataveil_luhn_valid``,
``dataveil_iban_valid``) that core/classify.py's checksum-backed classifiers
require — those run entirely inside DuckDB's own execution engine per row;
only the resulting aggregate COUNT ever reaches Python in core/.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.adapter import Adapter
from ..core.sql import quote_ident as _quote_ident

if TYPE_CHECKING:
    import duckdb as duckdb_module


def _luhn_valid(value: str | None) -> bool:
    if value is None:
        return False
    digits = [c for c in value if c.isdigit()]
    if len(digits) < 2:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _iban_valid(value: str | None) -> bool:
    if value is None:
        return False
    s = value.replace(" ", "").upper()
    if not (5 <= len(s) <= 34):
        return False
    if not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    rearranged = s[4:] + s[:4]
    numeric_chars = []
    for ch in rearranged:
        if ch.isdigit():
            numeric_chars.append(ch)
        elif ch.isalpha():
            numeric_chars.append(str(ord(ch) - ord("A") + 10))
        else:
            return False
    try:
        return int("".join(numeric_chars)) % 97 == 1
    except ValueError:
        return False


def _title_case(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(word[:1].upper() + word[1:].lower() if word else word for word in value.split(" "))


class DuckDBAdapter(Adapter):
    def __init__(self, connection: "duckdb_module.DuckDBPyConnection"):
        self._con = connection
        self._con.create_function("dataveil_luhn_valid", _luhn_valid, [str], bool)
        self._con.create_function("dataveil_iban_valid", _iban_valid, [str], bool)
        self._con.create_function("dataveil_title_case", _title_case, [str], str)

    def run_aggregate_query(self, sql: str) -> list[dict[str, Any]]:
        cursor = self._con.execute(sql)
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def list_tables(self) -> list[str]:
        rows = self.run_aggregate_query("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'")
        return [r["table_name"] for r in rows]

    def get_schema(self, table: str) -> dict[str, str]:
        rows = self.run_aggregate_query(
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_schema = 'main' AND table_name = '{table}' ORDER BY ordinal_position"
        )
        if not rows:
            raise ValueError(f"unknown table: {table}")
        return {r["column_name"]: r["data_type"] for r in rows}

    def has_checksum_functions(self) -> bool:
        return True

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
        self._con.execute(f"ALTER TABLE {_quote_ident(table)} DROP COLUMN {_quote_ident(column)}")
        return {"dropped_column": column}

    def _op_trim_whitespace(self, table: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        t, c = _quote_ident(table), _quote_ident(column)
        self._con.execute(f"UPDATE {t} SET {c} = TRIM({c}) WHERE {c} IS NOT NULL")
        return {}

    def _op_standardize_case(self, table: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        t, c = _quote_ident(table), _quote_ident(column)
        case = params["case"]
        if case == "upper":
            self._con.execute(f"UPDATE {t} SET {c} = UPPER({c}) WHERE {c} IS NOT NULL")
        elif case == "lower":
            self._con.execute(f"UPDATE {t} SET {c} = LOWER({c}) WHERE {c} IS NOT NULL")
        elif case == "title":
            self._con.execute(f"UPDATE {t} SET {c} = dataveil_title_case({c}) WHERE {c} IS NOT NULL")
        else:
            raise ValueError(f"unknown case {case!r}")
        return {}

    def _op_mask(self, table: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        t, c = _quote_ident(table), _quote_ident(column)
        method = params["method"]
        if method == "hash":
            self._con.execute(f"UPDATE {t} SET {c} = md5(CAST({c} AS VARCHAR)) WHERE {c} IS NOT NULL")
        elif method == "constant":
            self._con.execute(f"UPDATE {t} SET {c} = ? WHERE {c} IS NOT NULL", [params["value"]])
        elif method == "partial":
            keep_last = int(params["keep_last"])
            self._con.execute(
                f"UPDATE {t} SET {c} = repeat('*', GREATEST(LENGTH(CAST({c} AS VARCHAR)) - {keep_last}, 0)) "
                f"|| RIGHT(CAST({c} AS VARCHAR), {keep_last}) WHERE {c} IS NOT NULL"
            )
        else:
            raise ValueError(f"unknown mask method {method!r}")
        return {}

    def _op_impute(self, table: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        t, c = _quote_ident(table), _quote_ident(column)
        strategy = params["strategy"]
        if strategy == "constant":
            self._con.execute(f"UPDATE {t} SET {c} = ? WHERE {c} IS NULL", [params["value"]])
            return {}
        expr = {
            "mean": f"(SELECT AVG({c}) FROM {t})",
            "median": f"(SELECT MEDIAN({c}) FROM {t})",
            "mode": f"(SELECT MODE({c}) FROM {t})",
        }.get(strategy)
        if expr is None:
            raise ValueError(f"unknown impute strategy {strategy!r}")
        self._con.execute(f"UPDATE {t} SET {c} = {expr} WHERE {c} IS NULL")
        return {}

    def _op_dedupe_rows(self, table: str, column: str | None, params: dict[str, Any]) -> dict[str, Any]:
        t = _quote_ident(table)
        keys = ", ".join(_quote_ident(k) for k in params["key_columns"])
        before = self.run_aggregate_query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
        self._con.execute(f"DELETE FROM {t} WHERE rowid NOT IN (SELECT MIN(rowid) FROM {t} GROUP BY {keys})")
        after = self.run_aggregate_query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
        return {"rows_removed": before - after}

    def _op_bucket_numeric(self, table: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        t, c = _quote_ident(table), _quote_ident(column)
        target = params.get("target_column") or f"{column}_bucket"
        qtarget = _quote_ident(target)
        self._con.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {qtarget} VARCHAR")

        if "bin_width" in params:
            width = params["bin_width"]
            lower = f"(CAST(FLOOR({c} / {width}) AS BIGINT) * {width})"
            upper = f"({lower} + {width})"
            expr = f"CAST({lower} AS VARCHAR) || '-' || CAST({upper} AS VARCHAR)"
            self._con.execute(f"UPDATE {t} SET {qtarget} = {expr} WHERE {c} IS NOT NULL")
        else:
            bins = params["bins"]
            when_clauses = " ".join(
                f"WHEN {c} >= {bins[i]} AND {c} < {bins[i + 1]} THEN '{bins[i]}-{bins[i + 1]}'"
                for i in range(len(bins) - 1)
            )
            expr = f"CASE WHEN {c} < {bins[0]} THEN '<{bins[0]}' {when_clauses} ELSE '>={bins[-1]}' END"
            self._con.execute(f"UPDATE {t} SET {qtarget} = {expr} WHERE {c} IS NOT NULL")
        return {"bucket_column": target}

    def _op_parse_date(self, table: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        t, c = _quote_ident(table), _quote_ident(column)
        source_format = params["source_format"]
        target_format = params.get("target_format", "%Y-%m-%d")
        self._con.execute(
            f"UPDATE {t} SET {c} = strftime(strptime(CAST({c} AS VARCHAR), ?), ?) WHERE {c} IS NOT NULL",
            [source_format, target_format],
        )
        return {}
