# dataveil

Local data profiling, PII classification, and plan-based cleansing.

dataveil lets an agent clean a table it never sees. It works in three
stages, strictly separated:

1. **Profile and classify** (no LLM). dataveil computes aggregate statistics
   and finds sensitive columns with local regex, checksum and optional NER
   matching. Only counts, rates and generalized format signatures such as
   `ddd-dd-dddd` leave the database.
2. **Plan** (an agent, outside dataveil). An LLM or a person reads that
   output and writes a plan: a list of operations picked from a closed,
   human-reviewed vocabulary.
3. **Execute** (no LLM). dataveil validates the whole plan against the
   vocabulary and the table's schema, then runs it. A plan with one bad step
   applies nothing.

## Two rules

**Aggregate-only, with documented limits.** No raw rows or samples are
returned, not one row and not a "few examples". Some aggregates can still
reveal a value, for example a median that equals one row's value. The
[privacy page](privacy.md) lists every such case.

**No LLM-authored code, ever.** The LLM's only output is a plan of
`{operation, column, params, rationale}` steps. A step outside the
[operation vocabulary](operations.md) is rejected before anything runs.
dataveil itself never calls an LLM.

## Where to go next

- [Getting started](getting-started.md) installs dataveil and runs all three stages on a DuckDB table.
- [Configuration](configuration.md) lists every key of `dataveil.toml`.
- [Adapters](adapters/index.md) covers DuckDB, Postgres, SQLMesh, dbt and writing your own.
- [MCP server](mcp-server.md) exposes the same tools to an agent.
- [Benchmarks](benchmarks.md) shows timings on 1M and 10M row tables.
