# MCP server

With more than one adapter, dataveil can also expose itself directly as an
MCP server, usable outside SQLMesh entirely:

```bash
pip install dataveil[duckdb,mcp]   # or [postgres,mcp] / [sqlmesh,mcp]
```

```json
{
  "mcpServers": {
    "dataveil": {
      "command": "dataveil-mcp",
      "env": { "DATAVEIL_ADAPTER": "duckdb", "DATAVEIL_DUCKDB_PATH": "/path/to/your.duckdb" }
    }
  }
}
```

`DATAVEIL_ADAPTER` selects `duckdb` / `postgres` / `sqlmesh`, each with its
own setting: `DATAVEIL_DUCKDB_PATH` (a file path, or `:memory:`),
`DATAVEIL_POSTGRES_URL` (a SQLAlchemy URL), or `SQLMESH_PROJECT_PATH` (same
env var sqlmesh-mcp uses). Four tools: `list_tables`, `profile`,
`propose_cleansing_plan`, `apply_cleansing_plan` (requires `confirm=true`,
`destructiveHint`) -- the same three-tool shape as sqlmesh-mcp's
dataveil-backed tools, just against `table` instead of `model_name`, since
there's no SQLMesh Context or physical-snapshot concept here.

The adapter can also come from the `[adapter]` section of a
[configuration file](configuration.md#adapter), and the server applies the
file's `[profile]` and `[classify]` thresholds to every call. Environment
variables take precedence over the file. Set `DATAVEIL_ADAPTER` to the name
of an [adapter plugin](adapters/plugins.md) to use one.

| Tool | Changes data | Does |
|---|---|---|
| `list_tables` | no | Lists the tables or models the adapter can see. |
| `profile` | no | Returns the aggregate-only profile of a table. |
| `propose_cleansing_plan` | no | Returns the profile, the classification of each column and the operation vocabulary: everything an agent needs to write a plan. |
| `apply_cleansing_plan` | yes | Validates and runs a plan. Requires `confirm=true`. |

The server never proposes a plan itself and never calls an LLM. Every call
is recorded in the [audit log](audit-and-approval.md).
