# dbt

The dbt adapter needs no extra and does not import dbt. It reads the
`target/manifest.json` that `dbt compile`, `dbt run` and `dbt build` write,
and runs all SQL through a DuckDB or Postgres adapter connected to the
database dbt built into.

For a dbt project, point the adapter at the manifest that `dbt run` (or
`dbt compile`) writes, and at the warehouse dbt built into:

```python
import duckdb
from dataveil.adapters.dbt import DbtAdapter
from dataveil.adapters.duckdb import DuckDBAdapter
from dataveil.core.profile import profile_table

adapter = DbtAdapter("target/manifest.json", DuckDBAdapter(duckdb.connect("shop.duckdb")))
adapter.list_tables()  # ['main_analytics.customers', ...]
profile_table(adapter, "main_analytics.customers")
```

Models are named `schema.alias`, the relation dbt builds. Ephemeral models
are skipped, because they have no relation. Every classifier works, because
the warehouse adapter registers the checksum functions.

!!! warning "Cleansing is not durable yet"
    An operation changes the relation dbt built, outside dbt. The next
    `dbt run` rebuilds a `table` model and undoes it. Views cannot be
    cleansed at all. Durable cleansing, written into the model, is planned
    for 0.5.
