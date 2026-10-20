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

# optional: recognize person names (PII:PERSON_NAME) with a local spaCy model
pip install "dataveil[duckdb,ner]"
python -m spacy download en_core_web_sm
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

## Documentation

The full documentation is at **[zain-ul-abdin45.github.io/dataveil](https://zain-ul-abdin45.github.io/dataveil/)**:

- [Getting started](https://zain-ul-abdin45.github.io/dataveil/getting-started/)
- [Configuration](https://zain-ul-abdin45.github.io/dataveil/configuration/): every key of `dataveil.toml`, including custom classifiers
- [Classifiers](https://zain-ul-abdin45.github.io/dataveil/classifiers/) and [operations](https://zain-ul-abdin45.github.io/dataveil/operations/)
- [Adapters](https://zain-ul-abdin45.github.io/dataveil/adapters/): DuckDB, Postgres, SQLMesh, dbt, and [plugins](https://zain-ul-abdin45.github.io/dataveil/adapters/plugins/)
- [MCP server](https://zain-ul-abdin45.github.io/dataveil/mcp-server/) and [audit log and approval](https://zain-ul-abdin45.github.io/dataveil/audit-and-approval/)
- [Benchmarks](https://zain-ul-abdin45.github.io/dataveil/benchmarks/) on 1M and 10M row tables

## Known limits

The profile is aggregate-only, but aggregate does not always mean it cannot
reveal a value. In short:

- Numeric stats are withheld for small or constant columns, and `min` and
  `max` only appear when at least 5 rows share that value.
- Percentiles and the mean can still equal a cell value.
- Format signatures keep spaces and punctuation, and a signature only
  appears when at least 5 values share it.
- Classification is pattern-based, and the name model is English only and
  statistical.

So the guarantee is "no raw row is returned and no sample is taken", not "no
value can ever be inferred". The [privacy page](https://zain-ul-abdin45.github.io/dataveil/privacy/) lists
every limit in full.

## Development

```bash
make setup    # .venv + ".[dev]" + pre-commit hook
make check    # lint, format check, mypy, tests (same as CI)
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the design rules and the pull
request process, and [SECURITY.md](SECURITY.md) to report a privacy leak.

To build the documentation locally:

```bash
pip install -e ".[docs]"
mkdocs serve
```

The `sqlmesh` extra pulls in real SQLMesh; `tests/test_sqlmesh_adapter.py`
runs the `SQLMeshAdapter` end to end against a small, self-contained SQLMesh
project (`tests/fixtures/sqlmesh_project`) and is skipped automatically if
SQLMesh isn't installed.

`tests/test_postgres_adapter.py` runs the `PostgresAdapter` end to end
against a real Postgres instance — point it at one with
`DATAVEIL_TEST_POSTGRES_URL` (default: a local `dataveil_test` database);
the whole module skips automatically if nothing is reachable there. CI
spins up a throwaway `postgres:16` service container for this.
