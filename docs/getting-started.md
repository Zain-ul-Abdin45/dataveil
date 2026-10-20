# Getting started

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

## A messier, more realistic example

The example above uses three clean rows. [`examples/messy_customers.py`](https://github.com/Zain-ul-Abdin45/dataveil/blob/main/examples/messy_customers.py)
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

Next, set thresholds and adapter settings in a [configuration file](configuration.md).
