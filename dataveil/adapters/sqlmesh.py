"""SQLMesh adapter: implements the dataveil Adapter interface against a
SQLMesh ``Context``.

Reads (``run_aggregate_query``, which profiling/classification build on) go
through the project's virtual layer: ``ctx.fetchdf(sql)`` against a model's
plain ``schema.model`` name, the same form sqlmesh-mcp's
``list_models()``/``get_model()`` already use.

Writes (``execute_operation``) resolve the model's *physical* snapshot table
(``ctx.snapshots[model.fqn].table_name()``) and issue SQL directly through
``ctx.engine_adapter.execute()`` -- a model's virtual-layer view generally
isn't updatable. This is the same "changes real data, needs confirm=true"
posture sqlmesh-mcp's own ``apply_plan``/``run`` tools already have, not a
new write path.

Known limitation, stated rather than hidden: a cleansing operation applied
this way lands on the model's *current* physical snapshot table, outside
SQLMesh's own state tracking. A FULL-kind model is fully rematerialized on
its next ``sqlmesh run``/plan apply, which will silently undo the
cleansing -- it was never written into the model's SQL definition. Making
cleansing durable across reruns (e.g. by folding the operation into the
model's SQL itself) is deliberately out of scope for v1; see PLAN.md Phase 5.

No checksum functions are registered: a SQLMesh project can point at any
backend engine (DuckDB, Snowflake, BigQuery, ...), and registering a Python
UDF portably across all of them is out of scope for v1. The credit-card and
IBAN classifiers are simply not considered for this adapter (see
adapter.py's contract) -- the regex-only classifiers (email, SSN, phone)
still work normally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlmesh.utils.errors import SQLMeshError

from ..core.adapter import Adapter
from ..core.sql import quote_ident

if TYPE_CHECKING:
    from sqlmesh.core.context import Context


class SQLMeshAdapter(Adapter):
    def __init__(self, context: "Context"):
        self._ctx = context

    def run_aggregate_query(self, sql: str) -> list[dict[str, Any]]:
        try:
            df = self._ctx.fetchdf(sql)
        except Exception as e:
            # Most commonly: the model hasn't been planned/applied yet, so its
            # virtual-layer view doesn't exist -- the underlying engine's raw
            # error (e.g. DuckDB's CatalogException) doesn't say that clearly.
            raise ValueError(
                f"query against the SQLMesh project failed -- if the model "
                f"hasn't been applied yet, apply a plan first: {e}"
            ) from e
        records: list[dict[str, Any]] = df.to_dict(orient="records")
        return records

    def list_tables(self) -> list[str]:
        return [model.name for model in self._ctx.models.values()]

    def get_schema(self, table: str) -> dict[str, str]:
        model = self._ctx.get_model(table, raise_if_missing=True)
        return {col: str(dtype) for col, dtype in (model.columns_to_types or {}).items()}

    def has_checksum_functions(self) -> bool:
        return False

    def _physical_table(self, table: str) -> str:
        model = self._ctx.get_model(table, raise_if_missing=True)
        snapshot = self._ctx.snapshots.get(model.fqn)
        if snapshot is None:
            raise ValueError(
                f"model {table!r} has no snapshot yet -- apply a plan first "
                f"(it must be materialized before dataveil can cleanse it)"
            )
        try:
            return snapshot.table_name()
        except SQLMeshError as e:
            raise ValueError(
                f"model {table!r} has not been applied yet -- apply a plan first "
                f"(it must be materialized before dataveil can cleanse it): {e}"
            ) from e

    def execute_operation(
        self, table: str, operation: str, column: str | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        method = getattr(self, f"_op_{operation}", None)
        if method is None:
            raise ValueError(f"adapter does not implement operation {operation!r}")
        physical = self._physical_table(table)
        result: dict[str, Any] = method(physical, column, params)
        return result

    # --- operations: same SQL shapes as adapters/duckdb.py, issued through
    # ctx.engine_adapter.execute() against the resolved physical table ---

    def _op_drop_column(self, physical: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        self._ctx.engine_adapter.execute(f"ALTER TABLE {physical} DROP COLUMN {quote_ident(column)}")
        return {"dropped_column": column}

    def _op_trim_whitespace(self, physical: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        c = quote_ident(column)
        self._ctx.engine_adapter.execute(f"UPDATE {physical} SET {c} = TRIM({c}) WHERE {c} IS NOT NULL")
        return {}

    def _op_standardize_case(self, physical: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        c = quote_ident(column)
        case = params["case"]
        if case == "upper":
            self._ctx.engine_adapter.execute(f"UPDATE {physical} SET {c} = UPPER({c}) WHERE {c} IS NOT NULL")
        elif case == "lower":
            self._ctx.engine_adapter.execute(f"UPDATE {physical} SET {c} = LOWER({c}) WHERE {c} IS NOT NULL")
        else:
            raise ValueError(
                f"standardize_case: {case!r} isn't supported by the SQLMesh adapter "
                f"(no portable multi-word title-case SQL across backend engines); "
                f"'upper' and 'lower' are supported"
            )
        return {}

    def _op_mask(self, physical: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        c = quote_ident(column)
        method = params["method"]
        if method == "hash":
            self._ctx.engine_adapter.execute(
                f"UPDATE {physical} SET {c} = MD5(CAST({c} AS VARCHAR)) WHERE {c} IS NOT NULL"
            )
        elif method == "constant":
            value = params["value"].replace("'", "''")
            self._ctx.engine_adapter.execute(f"UPDATE {physical} SET {c} = '{value}' WHERE {c} IS NOT NULL")
        elif method == "partial":
            keep_last = int(params["keep_last"])
            self._ctx.engine_adapter.execute(
                f"UPDATE {physical} SET {c} = REPEAT('*', GREATEST(LENGTH(CAST({c} AS VARCHAR)) - {keep_last}, 0)) "
                f"|| RIGHT(CAST({c} AS VARCHAR), {keep_last}) WHERE {c} IS NOT NULL"
            )
        else:
            raise ValueError(f"unknown mask method {method!r}")
        return {}

    def _op_impute(self, physical: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        c = quote_ident(column)
        strategy = params["strategy"]
        if strategy == "constant":
            value = params["value"]
            literal = "'" + value.replace("'", "''") + "'" if isinstance(value, str) else str(value)
            self._ctx.engine_adapter.execute(f"UPDATE {physical} SET {c} = {literal} WHERE {c} IS NULL")
            return {}
        expr = {
            "mean": f"(SELECT AVG({c}) FROM {physical})",
            "median": f"(SELECT MEDIAN({c}) FROM {physical})",
            "mode": f"(SELECT MODE({c}) FROM {physical})",
        }.get(strategy)
        if expr is None:
            raise ValueError(f"unknown impute strategy {strategy!r}")
        self._ctx.engine_adapter.execute(f"UPDATE {physical} SET {c} = {expr} WHERE {c} IS NULL")
        return {}

    def _op_dedupe_rows(self, physical: str, column: str | None, params: dict[str, Any]) -> dict[str, Any]:
        keys = ", ".join(quote_ident(k) for k in params["key_columns"])
        before = self.run_aggregate_query(f"SELECT COUNT(*) AS n FROM {physical}")[0]["n"]
        self._ctx.engine_adapter.execute(
            f"DELETE FROM {physical} WHERE rowid NOT IN (SELECT MIN(rowid) FROM {physical} GROUP BY {keys})"
        )
        after = self.run_aggregate_query(f"SELECT COUNT(*) AS n FROM {physical}")[0]["n"]
        return {"rows_removed": before - after}

    def _op_bucket_numeric(self, physical: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        c = quote_ident(column)
        target = params.get("target_column") or f"{column}_bucket"
        qtarget = quote_ident(target)
        self._ctx.engine_adapter.execute(f"ALTER TABLE {physical} ADD COLUMN IF NOT EXISTS {qtarget} VARCHAR")

        if "bin_width" in params:
            width = params["bin_width"]
            lower = f"(CAST(FLOOR({c} / {width}) AS BIGINT) * {width})"
            upper = f"({lower} + {width})"
            expr = f"CAST({lower} AS VARCHAR) || '-' || CAST({upper} AS VARCHAR)"
        else:
            bins = params["bins"]
            when_clauses = " ".join(
                f"WHEN {c} >= {bins[i]} AND {c} < {bins[i + 1]} THEN '{bins[i]}-{bins[i + 1]}'"
                for i in range(len(bins) - 1)
            )
            expr = f"CASE WHEN {c} < {bins[0]} THEN '<{bins[0]}' {when_clauses} ELSE '>={bins[-1]}' END"

        self._ctx.engine_adapter.execute(f"UPDATE {physical} SET {qtarget} = {expr} WHERE {c} IS NOT NULL")
        return {"bucket_column": target}

    def _op_parse_date(self, physical: str, column: str, params: dict[str, Any]) -> dict[str, Any]:
        c = quote_ident(column)
        source_format = params["source_format"].replace("'", "''")
        target_format = params.get("target_format", "%Y-%m-%d").replace("'", "''")
        self._ctx.engine_adapter.execute(
            f"UPDATE {physical} SET {c} = strftime(strptime(CAST({c} AS VARCHAR), "
            f"'{source_format}'), '{target_format}') WHERE {c} IS NOT NULL"
        )
        return {}
