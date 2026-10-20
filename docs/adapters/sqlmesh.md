# SQLMesh

```bash
pip install "dataveil[sqlmesh]"
```

```python
from sqlmesh.core.context import Context
from dataveil.adapters.sqlmesh import SQLMeshAdapter

adapter = SQLMeshAdapter(Context(paths="path/to/project"))
```

Reads go through the project's virtual layer, so a model is addressed by its
plain `schema.model` name. Writes go to the model's current physical
snapshot table, because a virtual-layer view is generally not updatable.

On DuckDB and Postgres projects the adapter registers the checksum
functions, so every classifier works. On DuckDB projects it also registers
the person-name function. On other engines, the credit-card, IBAN and name
classifiers are skipped, and the regex classifiers work.

!!! warning "Cleansing is not durable yet"
    An operation changes the physical snapshot table outside SQLMesh's own
    state. The next `sqlmesh run` or plan of a FULL model rebuilds the table
    and undoes it. Durable cleansing, written into the model's query, is
    planned for 0.5.

Used by [`sqlmesh-mcp`](https://github.com/Zain-ul-Abdin45/sqlmesh-mcp)'s
`profile_model`, `propose_cleansing_plan` and `apply_cleansing_plan` tools.
With dataveil's own MCP server: `DATAVEIL_ADAPTER=sqlmesh` and
`SQLMESH_PROJECT_PATH`.
