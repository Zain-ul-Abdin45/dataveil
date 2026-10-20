# Benchmarks

How long profiling and classification take on large tables, and how many
queries they send. Measured with `benchmarks/run.py` on 2026-10-20.

## Setup

| | |
|---|---|
| Machine | Apple M3 Pro, 11 cores, 18 GB, macOS 26.5 |
| Python | 3.11.9 |
| DuckDB | 1.5.6, in memory |
| Postgres | 14.17 (Homebrew), local, default settings |
| dataveil | 0.4 development branch |

The script generates the table inside the database. It has nine columns,
all synthetic: an integer id, an email, a phone number, an SSN, a 16-digit
card-like number, a person name, a short free-text note, an amount and a
date. Seven are strings, so profiling and classification have work to do on
most of them. Times are wall-clock seconds for one call of `profile_table`
or `classify_table`, after the table is built.

```bash
python benchmarks/run.py                                   # DuckDB, 1M and 10M rows
python benchmarks/run.py --rows 1000000 --ner              # with the person-name model
python benchmarks/run.py --engine postgres --postgres-url postgresql+psycopg2://localhost/dataveil_test
```

A non-blocking CI job runs the same script weekly and on pull requests that
change the core or the adapters, and shows the tables in the job summary.

## Results

| Engine | Rows | Name model | Profile (s) | Profile queries | Classify (s) | Classify queries |
|---|---:|---|---:|---:|---:|---:|
| DuckDB | 1,000,000 | off | 0.97 | 24 | 7.51 | 36 |
| DuckDB | 10,000,000 | off | 7.98 | 24 | 74.88 | 36 |
| DuckDB | 1,000,000 | on | 0.90 | 24 | 74.22 | 42 |
| DuckDB | 10,000,000 | on | 8.20 | 24 | 134.86 | 42 |
| Postgres | 1,000,000 | off | 31.51 | 24 | 11.94 | 36 |
| Postgres | 10,000,000 | off | 199.46 | 24 | 115.33 | 36 |

The query counts do not depend on the row count. Profiling sends 2 or 3
queries per column. Classification sends 6 per string column, or 7 with the
name model, and none for other columns.

## What the numbers show

**Profiling scales linearly.** About 0.8 seconds per million rows on DuckDB
for the whole nine-column table. Postgres is about 25 times slower here
(200 seconds at 10M rows). The format signature query, three nested
`regexp_replace` calls and a `GROUP BY`, takes 23 to 47 seconds per string
column at 10M rows, and `COUNT(DISTINCT ...)` adds to every column. The
Postgres server ran with default settings, so these numbers are an upper
bound rather than a tuned result.

**On DuckDB, classification is about ten times slower than profiling,
because of the checksum functions.** At 10M rows, each string column takes 14 to 20
seconds to classify, and almost all of it is the Luhn and IBAN checks. They
are Python functions, and DuckDB calls them for every value, even when the
regex in the same condition has already ruled the value out. On 1M rows, the
Luhn query of the email column takes 1.18 seconds and finds nothing.
Guarding the function with the regex brings that query down to 0.005 seconds
with the same result.

Postgres appears to skip the PL/pgSQL checksum when the regex fails: its
non-card columns classify in 7 to 17 seconds at 10M rows. A column of real
card numbers is the expensive case on both engines: 20 seconds on DuckDB and
57 seconds on Postgres at 10M rows, because every value needs the checksum.

**The name model costs about 15 to 30 seconds per high-cardinality column,
whatever the row count.** It labels up to 10,000 distinct values per string
column (`ner_max_distinct_values`), including columns a regex has already
tagged as email, phone or SSN. Columns with few distinct values, like the
name and note columns here, take under a second.

**Each string column is scanned once per classifier, and three times by
profiling.** One combined query
with a `FILTER` per classifier scans an email column of 1M rows in 0.024
seconds, against six separate queries today.

## Follow-up issues

Both findings have an issue with the measurements above:

- Run the checksum functions only on values that match the pattern.
- Classify each column in one scan, and skip the name model on columns a
  regex has already tagged.
