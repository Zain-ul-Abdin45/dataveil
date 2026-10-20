# Benchmarks

How long profiling and classification take on large tables, and how many
queries they send. Measured with `benchmarks/run.py` on 2026-10-20, for
dataveil 0.4.0 and, for comparison, the code before the 0.4.0
classification changes.

## Setup

| | |
|---|---|
| Machine | Apple M3 Pro, 11 cores, 18 GB, macOS 26.5 |
| Python | 3.11.9 |
| DuckDB | 1.5.6, in memory |
| Postgres | 14.17 (Homebrew), local, default settings (2 parallel workers per query) |

The script generates the table inside the database. It has nine columns,
all synthetic: an integer id, an email, a phone number, an SSN, a 16-digit
card-like number, a person name, a short free-text note, an amount and a
date. Seven are strings, so profiling and classification have work to do on
most of them. Times are wall-clock seconds for one call of `profile_table`
or `classify_table`, after the table is built. Each run with the name model
starts in a fresh process, so its cache is cold.

```bash
python benchmarks/run.py                                   # DuckDB, 1M and 10M rows
python benchmarks/run.py --rows 1000000 --ner              # with the person-name model
python benchmarks/run.py --engine postgres --postgres-url postgresql+psycopg2://localhost/dataveil_test
```

A non-blocking CI job runs the same script weekly and on pull requests that
change the core or the adapters, and shows the tables in the job summary.

## Results (0.4.0)

| Engine | Rows | Name model | Profile (s) | Profile queries | Classify (s) | Classify queries |
|---|---:|---|---:|---:|---:|---:|
| DuckDB | 1,000,000 | off | 0.91 | 24 | 2.10 | 6 |
| DuckDB | 10,000,000 | off | 8.58 | 24 | 18.88 | 6 |
| DuckDB | 1,000,000 | on | 0.90 | 24 | 16.88 | 9 |
| DuckDB | 10,000,000 | on | 8.56 | 24 | 35.40 | 9 |
| Postgres | 1,000,000 | off | 32.82 | 24 | 5.51 | 6 |
| Postgres | 10,000,000 | off | 197.92 | 24 | 54.11 | 6 |

The query counts do not depend on the row count. Profiling sends 2 or 3
queries per column. Classification sends one query per string column, plus
one for the name model on columns no regex has tagged, and none for other
columns.

## Before and after the 0.4.0 classification changes

Classification time at 10M rows, in seconds:

| Engine | Name model | Before | 0.4.0 |
|---|---|---:|---:|
| DuckDB | off | 74.88 | 18.88 |
| DuckDB | on | 134.86 | 35.40 |
| Postgres | off | 115.33 | 54.11 |

Before 0.4.0, classification sent 36 queries for this table, or 42 with
the name model. Three changes made the difference:

- **Checksums only run on matching values.** DuckDB evaluates both sides of
  an `AND` for a whole vector, so the Python Luhn and IBAN functions ran on
  every value of every string column. On 1M rows, the Luhn query of the
  email column took 1.18 seconds and found nothing. A `CASE` guard brings
  it to 0.005 seconds with the same result.
- **One scan per column.** The non-null count and every regex classifier's
  match count now come from one query with a `FILTER` per classifier,
  instead of six queries.
- **The name model skips tagged columns.** It used to label up to 10,000
  distinct values of every string column, including columns already tagged
  as email, phone or SSN, at 15 to 30 seconds each.

On Postgres, combining the classifiers into one query was slower at first
(156 seconds). The PL/pgSQL checksum functions were not declared
`PARALLEL SAFE`, so any query that used them ran on a single worker. With
the declaration, the combined query uses parallel workers again.

## What is still slow

- **A column of real card numbers.** Every value matches the pattern, so
  every value needs the Luhn check: 18 seconds on DuckDB and 24 on Postgres
  at 10M rows. On DuckDB the check is a Python function.
- **Profiling on Postgres.** About 25 times slower than DuckDB here, 198
  seconds at 10M rows. The format signature query, three nested
  `regexp_replace` calls and a `GROUP BY`, takes 23 to 45 seconds per
  string column, and `COUNT(DISTINCT ...)` adds to every column. Profiling
  also reads each string column three times. The Postgres server ran with
  default settings, so these numbers are an upper bound rather than a tuned
  result.
- **The name model on untagged, high-cardinality columns.** The card-like
  column here is not tagged, so the model still labels 10,000 of its
  values. That costs about 15 seconds the first time and is cached after.
