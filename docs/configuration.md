# Configuration

Settings live in a `dataveil.toml` file. Every section and key is optional.
Without a file, the defaults below apply.

## Where the file is found

1. The path passed to `load_config(path)`.
2. The `DATAVEIL_CONFIG` environment variable.
3. `./dataveil.toml` in the working directory.

A file named by the first two must exist. A missing `./dataveil.toml` just
means the defaults.

The MCP server loads the file at startup. In library code, load it and pass
its sections explicitly:

```python
from dataveil.config import load_config
from dataveil.core.classify import classify_table
from dataveil.core.profile import profile_table

config = load_config()
profile_table(adapter, "customers", config.profile)
classify_table(adapter, "customers", config.classify)
```

Without a config argument, `profile_table` and `classify_table` use the
defaults.

## Rules

- **Privacy thresholds can only be made stricter.** A minimum count can be
  raised but not lowered, and `max_format_signatures` can be lowered but not
  raised. A value that would make the profile reveal more is rejected when
  the file is loaded.
- **Unknown sections, keys and tags are errors.** A typo such as
  `min_signature_cont` fails loudly instead of silently keeping a default.
- **Environment variables win.** The MCP server's environment variables
  take precedence over `[adapter]` and `[audit]`.
- **Keep credentials out of the file.** Pass a Postgres URL with a password
  in `DATAVEIL_POSTGRES_URL`.

## `[profile]`

| Key | Default | Rule | Meaning |
|---|---|---|---|
| `min_signature_count` | 5 | raise only | A format signature is returned only when at least this many non-null values share it. |
| `min_extreme_count` | 5 | raise only | `min` and `max` are returned only when at least this many rows have that exact value. |
| `min_rows_for_numeric_stats` | 10 | raise only | Numeric stats are withheld for tables with fewer rows. |
| `min_distinct_for_numeric_stats` | 3 | raise only | Numeric stats are withheld for columns with fewer distinct values. |
| `max_format_signatures` | 20 | lower only | At most this many signatures per string column; the rest are counted as `<other signatures>`. |

## `[classify]`

| Key | Default | Meaning |
|---|---|---|
| `match_threshold` | 0.8 | Share of non-null values that must match for a column to be tagged. Above 0, at most 1. |
| `name_hint_threshold` | 0.5 | The lower bar used when the column name hints at the tag. Not above `match_threshold`. |
| `ner_max_distinct_values` | 10000 | The name model labels at most this many of a column's most frequent distinct values. |
| `disabled` | `[]` | Tags of built-in or plugin classifiers to skip, for example `["PII:PHONE"]`. |

### Custom classifiers

Add one `[[classify.custom]]` table per identifier the built-in classifiers
do not know:

```toml
[[classify.custom]]
tag = "PII:CUSTOMER_ID"
pattern = "^CUS-[0-9]{8}$"
name_hints = ["customer_id", "cust_no"]   # optional
```

| Key | Required | Meaning |
|---|---|---|
| `tag` | yes | `PREFIX:NAME`, letters, digits and `_`. Must not reuse a built-in or plugin tag, or `none`. |
| `pattern` | yes | The regex every value is matched against. |
| `name_hints` | no | Column-name fragments that lower the bar to `name_hint_threshold`. Compared case-insensitively. |

A custom classifier runs like a built-in regex: one aggregate `COUNT` per
column, with the same thresholds, on every adapter.

Patterns are checked when the file is loaded and must mean the same thing
on DuckDB and Postgres. So a pattern:

- must compile,
- must start with `^` and end with `$`, because DuckDB's `~` matches the
  whole value and Postgres's `~` any substring,
- may use `(?:...)` groups but not lookarounds, named groups or inline flags,
- may not use backreferences or `\b`, `\B`, `\A`, `\Z`, `\z`. For example, `\b`
  is a word boundary in DuckDB but a backspace in Postgres.

## `[adapter]`

Used by the [MCP server](mcp-server.md).

| Key | Environment variable | Meaning |
|---|---|---|
| `kind` | `DATAVEIL_ADAPTER` | `duckdb`, `postgres`, `sqlmesh`, or the name of an [adapter plugin](adapters/plugins.md). |
| `duckdb_path` | `DATAVEIL_DUCKDB_PATH` | A DuckDB file, or `:memory:` (the default). |
| `postgres_url` | `DATAVEIL_POSTGRES_URL` | A SQLAlchemy URL. Required for `postgres`. |
| `sqlmesh_project_path` | `SQLMESH_PROJECT_PATH` | A SQLMesh project directory. Required for `sqlmesh`. |

`[adapter.options]` is a table of strings passed to an adapter plugin's
factory:

```toml
[adapter]
kind = "acme"

[adapter.options]
account = "analytics"
region = "eu"
```

## `[audit]`

| Key | Environment variable | Default | Meaning |
|---|---|---|---|
| `log_path` | `DATAVEIL_AUDIT_LOG_PATH` | `.dataveil/audit.jsonl` | Where the MCP server appends its [audit log](audit-and-approval.md). |

## A complete example

```toml
[profile]
min_signature_count = 10
min_extreme_count = 10
max_format_signatures = 10

[classify]
match_threshold = 0.9
disabled = ["PII:PHONE"]

[[classify.custom]]
tag = "PII:CUSTOMER_ID"
pattern = "^CUS-[0-9]{8}$"
name_hints = ["customer"]

[adapter]
kind = "duckdb"
duckdb_path = "warehouse.duckdb"

[audit]
log_path = "logs/dataveil-audit.jsonl"
```
