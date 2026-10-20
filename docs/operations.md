# Operations

A plan is a list of steps. Each step is an object with an `operation`, a
`column` (or `null` for table-level operations), `params` and a
`rationale`:

```json
{"operation": "mask", "column": "email", "params": {"method": "hash"}, "rationale": "PII:EMAIL at match_rate=1.0"}
```

The `operation` must be one of the operations below, and only the
parameters listed for it are accepted. `validate_plan` and `execute_plan`
check the whole plan against this vocabulary and the table's schema before
any step runs. `execute_plan` also requires `confirm=True`.

## Vocabulary (v1)

| Operation | Does |
|---|---|
| `mask` | Replace values (`hash` / `partial` / `constant`) |
| `drop_column` | Remove a column entirely |
| `impute` | Fill nulls (`mean` / `median` / `mode` / `constant`) |
| `standardize_case` | Normalize text casing (`upper` / `lower` / `title`) |
| `trim_whitespace` | Strip leading/trailing whitespace |
| `dedupe_rows` | Remove duplicate rows on a key set |
| `bucket_numeric` | Bin a numeric column (`bin_width` or explicit `bins`) |
| `parse_date` | Normalize a date/time format (format given explicitly) |

Every operation has a fixed parameter schema (`dataveil.core.plan.OPERATIONS`)
checked by `validate_plan`/`execute_plan` before anything runs.

`operation_vocabulary()` in `dataveil.core.plan` returns the same
vocabulary, with each parameter's allowed values, as the dictionary an
agent receives from the MCP server.

## No new operations without a use case

Operations are added when a real plan needs one, not speculatively. An
operation never runs free-form SQL or code. To propose one, open an issue
with the plan that needs it.
