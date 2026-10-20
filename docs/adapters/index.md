# Adapters

An adapter is the only part of dataveil that touches real rows. Everything
in `dataveil.core` builds SQL and reads back aggregates; the adapter runs it.

| Adapter | Status | Notes |
|---|---|---|
| `dataveil.adapters.duckdb.DuckDBAdapter` | Reference implementation | All 8 operations; registers Luhn/IBAN checksum UDFs, so every classifier works |
| `dataveil.adapters.sqlmesh.SQLMeshAdapter` | First real-world integration | Reads through a model's virtual layer, writes to its physical snapshot table; registers the checksum functions on DuckDB and Postgres projects, so every classifier works there; on other engines the credit-card/IBAN classifiers are skipped |
| `dataveil.adapters.postgres.PostgresAdapter` | Second adapter, proves the interface holds outside SQLMesh | Plain SQLAlchemy `Engine`, no Context/virtual-layer split; checksum functions written in PL/pgSQL (no Python UDFs), so every classifier works here too |
| `dataveil.adapters.dbt.DbtAdapter` | dbt projects | Lists the models from dbt's `target/manifest.json` as `schema.alias` and runs SQL through a DuckDB or Postgres adapter on the same database, so every classifier works; dbt itself is not imported. Views cannot be cleansed, and the next `dbt run` rebuilds a cleansed table model |

## Writing an adapter

Implement the five abstract methods of `dataveil.core.adapter.Adapter`:
`run_aggregate_query`, `list_tables`, `get_schema`,
`has_checksum_functions` and `execute_operation`. `has_ner_function` is
optional. The module's docstring describes the contract an adapter must
uphold, not just implement: for example, that it registers the checksum
functions inside its own engine so values never cross into Python.

An adapter can live in dataveil, behind an optional extra, or in its own
package as a [plugin](plugins.md).
