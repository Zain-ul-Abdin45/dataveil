"""Benchmark profile_table and classify_table on large synthetic tables.

    python benchmarks/run.py                          # DuckDB, 1M and 10M rows
    python benchmarks/run.py --rows 1000000 --ner     # with the person-name model
    python benchmarks/run.py --engine postgres --postgres-url postgresql+psycopg2://localhost/dataveil_test
    python benchmarks/run.py --json results.json      # also write the raw numbers

The table is generated inside the database (no Python rows), with one column
per kind of data dataveil looks at: an id, email, phone, SSN, card number,
person name, a free-text note, an amount and a date. All values are
synthetic. For each run the script reports wall time and the number of
queries dataveil sent, in total and per column.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from dataveil.core.adapter import Adapter
from dataveil.core.classify import classify_table
from dataveil.core.profile import profile_table

FIRST = (
    "['Alice','Bob','Priya','Mohammed','Chen','John','Jane','Maria',"
    "'Liam','Sofia','Omar','Yuki','Elena','Kwame','Lucas']"
)
LAST = (
    "['Smith','Patel','Ali','Wei','Doe','Rossi','Murphy','Novak','Garcia','Kim','Haddad','Tanaka','Ivanova','Mensah']"
)

DUCKDB_TABLE_SQL = f"""
CREATE OR REPLACE TABLE bench AS
SELECT
    i AS id,
    'user' || i || '@example.com' AS email,
    printf('%03d-%03d-%04d', 200 + i % 800, i % 1000, i % 10000) AS phone,
    printf('%03d-%02d-%04d', i % 1000, i % 100, i % 10000) AS ssn,
    lpad(CAST(i AS VARCHAR), 16, '4') AS card,
    list_element({FIRST}, 1 + CAST(i % 15 AS INTEGER)) || ' '
        || list_element({LAST}, 1 + CAST((i // 15) % 14 AS INTEGER)) AS full_name,
    CASE i % 4 WHEN 0 THEN 'order shipped' WHEN 1 THEN 'refund requested'
        WHEN 2 THEN 'call back ' || (i % 1000) ELSE NULL END AS note,
    (i % 100000) * 1.5 AS amount,
    DATE '2020-01-01' + CAST(i % 1500 AS INTEGER) AS signup
FROM range({{rows}}) AS t(i)
"""

POSTGRES_TABLE_SQL = f"""
DROP TABLE IF EXISTS bench;
CREATE TABLE bench AS
SELECT
    i AS id,
    'user' || i || '@example.com' AS email,
    lpad((200 + i % 800)::text, 3, '0') || '-' || lpad((i % 1000)::text, 3, '0') || '-'
        || lpad((i % 10000)::text, 4, '0') AS phone,
    lpad((i % 1000)::text, 3, '0') || '-' || lpad((i % 100)::text, 2, '0') || '-'
        || lpad((i % 10000)::text, 4, '0') AS ssn,
    lpad(i::text, 16, '4') AS card,
    (ARRAY{FIRST})[1 + i % 15] || ' ' || (ARRAY{LAST})[1 + (i / 15) % 14] AS full_name,
    CASE i % 4 WHEN 0 THEN 'order shipped' WHEN 1 THEN 'refund requested'
        WHEN 2 THEN 'call back ' || (i % 1000) ELSE NULL END AS note,
    (i % 100000) * 1.5 AS amount,
    DATE '2020-01-01' + (i % 1500)::integer AS signup
FROM generate_series(0, {{rows}} - 1) AS t(i);
ANALYZE bench;
"""


class CountingAdapter(Adapter):
    """Delegates to another adapter and records every aggregate query."""

    def __init__(self, inner: Adapter):
        self._inner = inner
        self.queries: list[tuple[str, float]] = []

    def run_aggregate_query(self, sql: str) -> list[dict[str, Any]]:
        start = time.perf_counter()
        rows = self._inner.run_aggregate_query(sql)
        self.queries.append((sql, time.perf_counter() - start))
        return rows

    def list_tables(self) -> list[str]:
        return self._inner.list_tables()

    def get_schema(self, table: str) -> dict[str, str]:
        return self._inner.get_schema(table)

    def has_checksum_functions(self) -> bool:
        return self._inner.has_checksum_functions()

    def has_ner_function(self) -> bool:
        return self._inner.has_ner_function()

    def execute_operation(
        self, table: str, operation: str, column: str | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        raise RuntimeError("benchmarks never change data")


def _per_column(adapter: CountingAdapter, columns: list[str]) -> dict[str, dict[str, float]]:
    """Queries and seconds per column, attributed by the quoted column name in the SQL."""
    result = {c: {"queries": 0, "seconds": 0.0} for c in columns}
    result["(table)"] = {"queries": 0, "seconds": 0.0}
    for sql, seconds in adapter.queries:
        owner = next((c for c in columns if f'"{c}"' in sql), "(table)")
        result[owner]["queries"] += 1
        result[owner]["seconds"] += seconds
    return result


def _measure(adapter: CountingAdapter, fn: Callable[[], object], columns: list[str]) -> dict[str, Any]:
    adapter.queries.clear()
    start = time.perf_counter()
    fn()
    return {
        "seconds": time.perf_counter() - start,
        "queries": len(adapter.queries),
        "per_column": _per_column(adapter, columns),
    }


def _duckdb_adapter(rows: int, ner: bool) -> Adapter:
    import duckdb

    from dataveil.adapters.duckdb import DuckDBAdapter

    con = duckdb.connect(":memory:")
    con.execute(DUCKDB_TABLE_SQL.format(rows=rows))
    adapter = DuckDBAdapter(con, register_ner_function=ner)
    if ner and not adapter.has_ner_function():
        sys.exit("--ner needs the ner extra and en_core_web_sm (python -m spacy download en_core_web_sm)")
    return adapter


def _postgres_adapter(rows: int, url: str) -> Adapter:
    import sqlalchemy as sa

    from dataveil.adapters.postgres import PostgresAdapter

    engine = sa.create_engine(url)
    with engine.begin() as conn:
        for statement in POSTGRES_TABLE_SQL.format(rows=rows).split(";"):
            if statement.strip():
                conn.execute(sa.text(statement))
    return PostgresAdapter(engine)


def _version(command: list[str]) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _hardware() -> dict[str, str]:
    info = {"platform": platform.platform(), "python": platform.python_version(), "machine": platform.machine()}
    if sys.platform == "darwin":
        info["cpu"] = _version(["sysctl", "-n", "machdep.cpu.brand_string"])
        memory = _version(["sysctl", "-n", "hw.memsize"])
        info["memory_gb"] = str(round(int(memory) / 2**30)) if memory else ""
    elif sys.platform.startswith("linux"):
        with open("/proc/cpuinfo") as f:
            info["cpu"] = next((line.split(":", 1)[1].strip() for line in f if line.startswith("model name")), "")
        with open("/proc/meminfo") as f:
            kb = int(next(line.split()[1] for line in f if line.startswith("MemTotal")))
            info["memory_gb"] = str(round(kb / 2**20))
    info["cpus"] = str(__import__("os").cpu_count())
    return info


def run(engine: str, rows: int, ner: bool, postgres_url: str | None) -> dict[str, Any]:
    start = time.perf_counter()
    inner = _duckdb_adapter(rows, ner) if engine == "duckdb" else _postgres_adapter(rows, postgres_url or "")
    setup = time.perf_counter() - start
    adapter = CountingAdapter(inner)
    columns = list(adapter.get_schema("bench"))
    result = {
        "engine": engine,
        "rows": rows,
        "ner": ner,
        "setup_seconds": setup,
        "profile": _measure(adapter, lambda: profile_table(adapter, "bench"), columns),
        "classify": _measure(adapter, lambda: classify_table(adapter, "bench"), columns),
    }
    tags = {r.column: r.tag for r in classify_table(inner, "bench")}
    result["tags"] = tags
    return result


def _markdown(results: list[dict[str, Any]]) -> str:
    lines = [
        "| Engine | Rows | Name model | Profile (s) | Profile queries | Classify (s) | Classify queries |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| {r['engine']} | {r['rows']:,} | {'on' if r['ner'] else 'off'} | {r['profile']['seconds']:.2f} | "
            f"{r['profile']['queries']} | {r['classify']['seconds']:.2f} | {r['classify']['queries']} |"
        )
    last = results[-1]
    lines += [
        "",
        f"Per column, {last['engine']} at {last['rows']:,} rows (name model {'on' if last['ner'] else 'off'}):",
        "",
    ]
    lines += [
        "| Column | Profile queries | Profile (s) | Classify queries | Classify (s) |",
        "|---|---:|---:|---:|---:|",
    ]
    for column, p in last["profile"]["per_column"].items():
        c = last["classify"]["per_column"][column]
        lines.append(f"| {column} | {p['queries']} | {p['seconds']:.2f} | {c['queries']} | {c['seconds']:.2f} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", choices=["duckdb", "postgres"], default="duckdb")
    parser.add_argument("--rows", type=int, nargs="+", default=[1_000_000, 10_000_000])
    parser.add_argument("--ner", action="store_true", help="also run the person-name classifier (DuckDB only)")
    parser.add_argument("--postgres-url", help="SQLAlchemy URL; the script creates and replaces a 'bench' table")
    parser.add_argument("--json", help="write the raw results to this file")
    args = parser.parse_args()
    if args.engine == "postgres" and not args.postgres_url:
        parser.error("--engine postgres needs --postgres-url")
    if args.engine == "postgres" and args.ner:
        parser.error("the person-name classifier does not run on Postgres")

    results = []
    for rows in args.rows:
        print(f"{args.engine}: {rows:,} rows ...", file=sys.stderr, flush=True)
        results.append(run(args.engine, rows, args.ner, args.postgres_url))
    print(_markdown(results))
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"hardware": _hardware(), "results": results}, f, indent=2)


if __name__ == "__main__":
    main()
