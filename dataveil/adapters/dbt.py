"""dbt adapter: exposes the models of a dbt project, read from dbt's
``target/manifest.json``, and runs all SQL through a warehouse adapter
(``DuckDBAdapter`` or ``PostgresAdapter``) connected to the same database.

dbt itself is not imported: the manifest is a JSON file that ``dbt compile``,
``dbt run`` and ``dbt build`` write. So this adapter needs no extra
dependency, and works with whichever dbt version produced the manifest.

Models are named ``schema.alias`` (the relation dbt builds), the same dotted
form the SQLMesh adapter uses. Ephemeral models are skipped: they have no
relation in the database.

Known limitation, stated rather than hidden: a cleansing operation changes
the relation dbt built, outside dbt. The next ``dbt run`` rebuilds a ``table``
model and silently undoes it. Views cannot be cleansed at all, so
``execute_operation`` rejects them with a clear error.

Checksum functions come from the warehouse adapter: both ``DuckDBAdapter``
and ``PostgresAdapter`` register them, so every classifier works.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.adapter import Adapter


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class DbtAdapter(Adapter):
    def __init__(self, manifest_path: str | Path, warehouse: Adapter):
        manifest = json.loads(Path(manifest_path).read_text())
        self._warehouse = warehouse
        # "schema.alias" -> materialization ("table", "view", "incremental", ...)
        self._models: dict[str, str] = {}
        for node in manifest.get("nodes", {}).values():
            if node.get("resource_type") != "model":
                continue
            materialized = node.get("config", {}).get("materialized", "view")
            if materialized == "ephemeral":
                continue
            relation = f"{node['schema']}.{node.get('alias') or node['name']}"
            self._models[relation] = materialized

    def run_aggregate_query(self, sql: str) -> list[dict[str, Any]]:
        return self._warehouse.run_aggregate_query(sql)

    def list_tables(self) -> list[str]:
        return sorted(self._models)

    def get_schema(self, table: str) -> dict[str, str]:
        self._require_model(table)
        schema, name = table.split(".", 1)
        rows = self._warehouse.run_aggregate_query(
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_schema = {_sql_literal(schema)} AND table_name = {_sql_literal(name)} "
            "ORDER BY ordinal_position"
        )
        if not rows:
            raise ValueError(f"dbt model {table!r} is in the manifest but not in the database -- run `dbt run` first")
        return {r["column_name"]: r["data_type"] for r in rows}

    def has_checksum_functions(self) -> bool:
        return self._warehouse.has_checksum_functions()

    def execute_operation(
        self, table: str, operation: str, column: str | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        if self._require_model(table) == "view":
            raise ValueError(
                f"dbt model {table!r} is a view, which cannot be cleansed in place -- "
                "change its materialization to 'table', or cleanse the model it selects from"
            )
        return self._warehouse.execute_operation(table, operation, column, params)

    def _require_model(self, table: str) -> str:
        materialized = self._models.get(table)
        if materialized is None:
            raise ValueError(f"unknown dbt model: {table!r} (expected one of {self.list_tables()})")
        return materialized
