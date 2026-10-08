# dataveil

[![CI](https://github.com/Zain-ul-Abdin45/dataveil/actions/workflows/ci.yml/badge.svg)](https://github.com/Zain-ul-Abdin45/dataveil/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Local data profiling, PII classification, and plan-based cleansing.

**Aggregate-only, with documented limits.** The LLM reasons over counts,
rates, and generalized format signatures (`ddd-dd-dddd`, not an actual SSN).
No raw rows or samples are returned — not one row, not a "few examples."
Some aggregates can still reveal a value (for example a median that equals
one row's value); see [Known limits](#known-limits).

**No LLM-authored code, ever.** The LLM's only output is a plan: a list of
`{operation, column, params, rationale}` picked from a closed, versioned
operation vocabulary written and reviewed by a human. A plan referencing
anything outside that vocabulary is rejected before it ever executes.

## Install

```bash
pip install dataveil[duckdb]    # core + the DuckDB reference adapter
pip install dataveil[sqlmesh]   # core + the SQLMesh adapter
pip install dataveil[postgres]  # core + the Postgres adapter
```

The core engine (`dataveil.core`) has no dependencies of its own — an
adapter extra only pulls in what that one adapter needs.

## Example

Three stages, strictly separated: profile (no LLM) → an agent proposes a
plan by reasoning over that profile → execute (no LLM). This runs all three
directly against a DuckDB table:

```python
import duckdb
from dataveil.adapters.duckdb import DuckDBAdapter
from dataveil.core.profile import profile_table
from dataveil.core.classify import classify_table
from dataveil.core.execute import execute_plan

con = duckdb.connect(":memory:")
con.execute("CREATE TABLE customers (id INTEGER, email VARCHAR, signup_date VARCHAR)")
con.executemany(
    "INSERT INTO customers VALUES (?, ?, ?)",
    [
        (1, "alice@example.com", "03/15/2024"),
        (2, "bob@example.com", "07/02/2024"),
        (3, None, "11/30/2024"),
    ],
)
adapter = DuckDBAdapter(con)

# 1. Profile -- aggregate stats only, no raw rows or samples (see Known limits).
profile = profile_table(adapter, "customers")
print(profile.to_dict())
# {'table': 'customers', 'row_count': 3, 'columns': [
#   ...,
#   {'name': 'email', ..., 'format_signatures': [{'signature': '<other signatures>', 'count': 2}]},
#   {'name': 'signup_date', ..., 'format_signatures': [{'signature': '<other signatures>', 'count': 3}]},
# ]}
# A signature is only shown when at least 5 values share it (MIN_SIGNATURE_COUNT);
# on a 3-row table every shape is rare. On a larger table you get shapes such
# as {'signature': 'dd/dd/dddd', 'count': 312} -- see the messier example below.

# 2. Classify -- local regex/checksum matching, same aggregate-only posture.
for result in classify_table(adapter, "customers"):
    print(result.column, result.tag, result.match_rate)
# id none 0.0
# email PII:EMAIL 1.0
# signup_date none 0.0

# 3. An agent reasons over that profile/classification (not shown here --
#    this project doesn't call an LLM itself) and proposes a plan:
plan = [
    {
        "operation": "mask",
        "column": "email",
        "params": {"method": "hash"},
        "rationale": "PII:EMAIL at match_rate=1.0",
    },
    {
        "operation": "parse_date",
        "column": "signup_date",
        "params": {"source_format": "%m/%d/%Y", "target_format": "%Y-%m-%d"},
        "rationale": "normalize inconsistent date format",
    },
]

# 4. Execute -- validates the whole plan against the operation vocabulary
#    and the table's schema before running a single step. Requires confirm=True.
execute_plan(adapter, "customers", plan, confirm=True)

print(con.execute("SELECT email, signup_date FROM customers").fetchall())
# [('<md5 hash>', '2024-03-15'), ('<md5 hash>', '2024-07-02'), (None, '2024-11-30')]
```

A plan referencing anything outside the vocabulary is rejected before
`execute_plan` touches the adapter at all:

```python
from dataveil.core.plan import PlanValidationError

try:
    execute_plan(
        adapter,
        "customers",
        [{"operation": "drop_table", "column": "email", "params": {}, "rationale": "x"}],
        confirm=True,
    )
except PlanValidationError as e:
    print(e)  # "step 0: unknown operation 'drop_table'; must be one of [...]"
```

### A messier, more realistic example

The example above uses three clean rows. [`examples/messy_customers.py`](examples/messy_customers.py)
generates ~300 rows of synthetic (Faker-based) customer data with the kind
of mess real end-user data actually has -- duplicate rows, nulls,
inconsistent casing, stray whitespace, a non-ISO date format, a few
malformed emails -- and runs the full pipeline against it:

```bash
python examples/messy_customers.py
```

`profile` surfaces the mess as aggregate stats (null rates, scattered
format signatures such as `'  aaaaa aaaaaa  '` next to `'aaaaa aaaaaa'`, which
show the stray whitespace; letter case is folded, so `'ALICE'` and `'alice'`
both become `'aaaaa'`), `classify`
flags the PII columns, and a 9-step plan (`trim_whitespace`,
`standardize_case`, `mask`, `impute`, `parse_date`, `dedupe_rows`) cleans it
up -- in the seeded run, 12 duplicate rows removed, every email/SSN masked,
ages imputed, dates normalized to ISO 8601, casing standardized.

## Operation vocabulary (v1)

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

## Sensitivity classifiers (v1)

Local, deterministic pattern + checksum matching (Presidio-style), run as
aggregate `COUNT`s so matched values never leave the adapter:
`PII:EMAIL`, `PII:SSN`, `PII:PHONE`, `PII:CREDIT_CARD` (Luhn-checked),
`PII:IBAN` (mod-97 checked). Free-text PII (names, addresses) needs NER, not
regex, and is deliberately out of scope for v1.

## Known limits

The profile is aggregate-only, but aggregate does not always mean it cannot
reveal a value. What the code does, and where it stops short:

- **Numeric stats are withheld for small or constant columns.** `min`, `max`,
  `mean`, `stddev` and the percentiles are only returned when the table has at
  least 10 rows and the column at least 3 distinct values. Below that, a single
  row could be recovered from them, so they are omitted entirely. The
  thresholds are conservative heuristics, not a privacy guarantee.
- **`min` and `max` are only returned when they are shared.** They are literal
  cell values, so each one is returned only when at least `MIN_EXTREME_COUNT`
  (5) rows have that exact value. A unique outlier (the largest salary, for
  example) is withheld; a shared bound (age 18 for many rows) is kept.
- **Percentiles and the mean can still equal a cell value.** `p25`, `p50` and
  `p75` are exact cell values when the row count lines up, and `mean` and
  `stddev` describe the whole column. They are not tied to an outlier, but
  they are not anonymized either.
- **Format signatures are generalized, but some structure is kept.** Letters
  become `a`, digits become `d`, and case is folded. Spaces and punctuation
  stay, so `ddd-dd-dddd` is an SSN shape and `aaa@aaaaaaa.aaa` is an email
  shape. Accented and other non-ASCII characters fold to `a`. A value made
  only of punctuation or whitespace is replaced with
  `<punctuation or whitespace only>`.
- **Rare format signatures are grouped.** A signature is only returned when at
  least `MIN_SIGNATURE_COUNT` (5) non-null values share it. Rarer ones, and
  any beyond the first 20, are counted together as `<other signatures>`. The
  bucket's count can still be small, so it can tell you that a few values have
  an unusual shape, but not what that shape is.
- **Small tables give unreliable statistics**: match rates on a handful of
  rows are noise (a single-row column is either 0.0 or 1.0).
- **Classification is pattern-based**: regex and checksum matching will miss
  personal identifiers written in an unusual format.

So the guarantee is "no raw row is returned and no sample is taken, small or
constant numeric columns are withheld, and `min`, `max` and format signatures
are only returned when several rows share them", not "no value can ever be
inferred".

## Adapters

| Adapter | Status | Notes |
|---|---|---|
| `dataveil.adapters.duckdb.DuckDBAdapter` | Reference implementation | All 8 operations; registers Luhn/IBAN checksum UDFs, so every classifier works |
| `dataveil.adapters.sqlmesh.SQLMeshAdapter` | First real-world integration | Reads through a model's virtual layer, writes to its physical snapshot table; registers the checksum functions on DuckDB and Postgres projects, so every classifier works there; on other engines the credit-card/IBAN classifiers are skipped |
| `dataveil.adapters.postgres.PostgresAdapter` | Second adapter, proves the interface holds outside SQLMesh | Plain SQLAlchemy `Engine`, no Context/virtual-layer split; checksum functions written in PL/pgSQL (no Python UDFs), so every classifier works here too |

Building the Postgres adapter is what caught two real dialect-coupling bugs
in `core/`: `classify.py` was built against DuckDB's `regexp_matches()`
returning a boolean, which doesn't hold on Postgres (there it's a
set-returning function and can't go inside `FILTER`) — fixed by switching
to the POSIX `~` match operator, which both engines support as a boolean
predicate. `profile.py` used DuckDB's `QUANTILE_CONT(col, frac)` shorthand —
fixed by switching to the ANSI-standard `PERCENTILE_CONT(frac) WITHIN GROUP
(ORDER BY col)`, which both support. Exactly the kind of bug a second
adapter is supposed to surface.

Used by [`sqlmesh-mcp`](https://github.com/Zain-ul-Abdin45/sqlmesh-mcp)'s
`profile_model`/`propose_cleansing_plan`/`apply_cleansing_plan` tools — the
first integration, not the whole project. Writing a new adapter means
implementing `dataveil.core.adapter.Adapter`'s five methods; see that
module's docstring for the contract.

## Audit logging and plan approval

`dataveil.audit.AuditLog` is an append-only JSON-lines log — one entry per
profile/classify/execute call, with a timestamp and enough detail to
reconstruct what happened without re-running anything. `record()` stores
whatever detail the caller passes: dataveil's MCP server logs row counts,
tags and plans, not cell values, but a caller that logs a full profile also
logs its numeric stats (see Known limits). It's a reusable component, not wired automatically into every core
call: whichever integration drives this (sqlmesh-mcp's tools, a future
standalone server) calls `log.record(...)` around the calls it wants logged.

`dataveil.core.approval` adds an optional second gate in front of
`execute_plan`: register a plan (`PlanRegistry.register`), get it approved
by id (`PlanRegistry.approve`), then run it with `execute_approved_plan` —
`confirm=True` is still required on top of that approval, not instead of
it. Useful once this touches anything with real compliance stakes; not
required for every caller.

## Standalone MCP server

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

## Development

```bash
make setup    # .venv + ".[dev]" + pre-commit hook
make check    # lint, format check, mypy, tests (same as CI)
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the design rules and the pull
request process, and [SECURITY.md](SECURITY.md) to report a privacy leak.

The `sqlmesh` extra pulls in real SQLMesh; `tests/test_sqlmesh_adapter.py`
runs the `SQLMeshAdapter` end to end against a small, self-contained SQLMesh
project (`tests/fixtures/sqlmesh_project`) and is skipped automatically if
SQLMesh isn't installed.

`tests/test_postgres_adapter.py` runs the `PostgresAdapter` end to end
against a real Postgres instance — point it at one with
`DATAVEIL_TEST_POSTGRES_URL` (default: a local `dataveil_test` database);
the whole module skips automatically if nothing is reachable there. CI
spins up a throwaway `postgres:16` service container for this.
