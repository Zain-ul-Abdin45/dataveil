# Postgres

```bash
pip install "dataveil[postgres]"
```

```python
import sqlalchemy as sa
from dataveil.adapters.postgres import PostgresAdapter

adapter = PostgresAdapter(sa.create_engine("postgresql+psycopg2://localhost/analytics"))
```

A plain SQLAlchemy `Engine` against real tables. The Luhn and IBAN checksum
functions are written in PL/pgSQL rather than Python, because most managed
Postgres instances do not allow Python functions. So every regex and
checksum classifier works. The person-name classifier needs Python inside
the engine, so it is skipped on Postgres.

`parse_date` takes Python strftime-style formats (`%Y-%m-%d`), like the other
adapters, and the adapter translates the common tokens to Postgres's
`to_char`/`to_timestamp` patterns.

Building the Postgres adapter is what caught two real dialect-coupling bugs
in `core/`: `classify.py` was built against DuckDB's `regexp_matches()`
returning a boolean, which doesn't hold on Postgres (there it's a
set-returning function and can't go inside `FILTER`) — fixed by switching
to the POSIX `~` match operator, which both engines support as a boolean
predicate. `profile.py` used DuckDB's `QUANTILE_CONT(col, frac)` shorthand —
fixed by switching to the ANSI-standard `PERCENTILE_CONT(frac) WITHIN GROUP
(ORDER BY col)`, which both support. Exactly the kind of bug a second
adapter is supposed to surface.

With the MCP server: `DATAVEIL_ADAPTER=postgres` and `DATAVEIL_POSTGRES_URL`.
