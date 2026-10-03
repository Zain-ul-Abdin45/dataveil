"""Generates a realistic, deliberately messy customer table -- duplicate
rows, nulls, inconsistent casing, stray whitespace, a non-ISO date format,
a few malformed emails -- and runs it through dataveil's full pipeline:
profile (no LLM) -> classify (no LLM) -> an agent proposes a plan -> execute
(no LLM). Demonstrates the actual value proposition against data shaped
like what a real end user hands over, not the clean rows the test suite
uses.

Run: python examples/messy_customers.py
"""

from __future__ import annotations

import json
import random

import duckdb
from faker import Faker

from dataveil.adapters.duckdb import DuckDBAdapter
from dataveil.core.classify import classify_table
from dataveil.core.execute import execute_plan
from dataveil.core.profile import profile_table

SEED = 20261003
N_ROWS = 300


def generate_messy_customers(n: int) -> list[tuple]:
    fake = Faker()
    Faker.seed(SEED)
    random.seed(SEED)

    rows = []
    for i in range(1, n + 1):
        name = fake.name()
        casing = random.choice(["lower", "upper", "title", "title"])
        if casing == "lower":
            name = name.lower()
        elif casing == "upper":
            name = name.upper()
        if random.random() < 0.3:
            name = f"  {name}  "

        email = fake.email()
        if random.random() < 0.05:
            email = email.replace("@", "")  # malformed, no @
        if random.random() < 0.1:
            email = f" {email} "

        phone = fake.phone_number()
        ssn = fake.ssn() if random.random() > 0.08 else None  # some missing
        age = random.randint(18, 85) if random.random() > 0.12 else None  # some missing

        signup = fake.date_between(start_date="-3y", end_date="today")
        signup_str = signup.strftime("%m/%d/%Y")  # one consistent non-ISO format to normalize

        country = fake.country()
        if random.random() < 0.15:
            country = country.lower()  # inconsistent casing

        rows.append((i, name, email, phone, ssn, age, signup_str, country))

    # a few exact duplicate rows on a new id -- mimics a repeat/accidental signup
    dup_sources = random.sample(rows, k=max(1, n // 25))
    next_id = len(rows) + 1
    for src in dup_sources:
        rows.append((next_id, *src[1:]))
        next_id += 1

    random.shuffle(rows)
    return rows


def main() -> None:
    rows = generate_messy_customers(N_ROWS)

    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE customers (
            id INTEGER,
            full_name VARCHAR,
            email VARCHAR,
            phone VARCHAR,
            ssn VARCHAR,
            age INTEGER,
            signup_date VARCHAR,
            country VARCHAR
        )
        """
    )
    con.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    adapter = DuckDBAdapter(con)

    print(f"=== Loaded {len(rows)} messy customer rows ===\n")

    print("--- Stage 1: profile (aggregate-only, no LLM) ---")
    profile = profile_table(adapter, "customers")
    for col in profile.columns:
        d = col.to_dict()
        bits = [f"null_rate={d['null_rate']:.2f}"]
        if "format_signatures" in d:
            sigs = ", ".join(f"{s['signature']!r}x{s['count']}" for s in d["format_signatures"][:3])
            bits.append(f"signatures=[{sigs}]")
        print(f"  {col.name:15s} {col.type:10s} {' '.join(bits)}")
    print()

    print("--- Stage 2: classify (local regex/checksum, no LLM) ---")
    classifications = classify_table(adapter, "customers")
    for c in classifications:
        print(f"  {c.column:15s} -> {c.tag:16s} match_rate={c.match_rate:.2f}")
    print()

    print("--- Stage 3: an agent reasons over stages 1-2 and proposes a plan ---")
    plan = [
        {
            "operation": "trim_whitespace",
            "column": "full_name",
            "params": {},
            "rationale": "stray leading/trailing whitespace observed in format signatures",
        },
        {
            "operation": "standardize_case",
            "column": "full_name",
            "params": {"case": "title"},
            "rationale": "mixed upper/lower/title casing observed",
        },
        {
            "operation": "trim_whitespace",
            "column": "email",
            "params": {},
            "rationale": "stray whitespace observed",
        },
        {
            "operation": "mask",
            "column": "email",
            "params": {"method": "hash"},
            "rationale": "PII:EMAIL detected by classify",
        },
        {
            "operation": "mask",
            "column": "ssn",
            "params": {"method": "hash"},
            "rationale": "PII:SSN detected by classify",
        },
        {
            "operation": "mask",
            "column": "phone",
            "params": {"method": "partial", "keep_last": 4},
            "rationale": "PII:PHONE detected by classify",
        },
        {
            "operation": "impute",
            "column": "age",
            "params": {"strategy": "mean"},
            "rationale": "non-zero null rate observed on a numeric column",
        },
        {
            "operation": "parse_date",
            "column": "signup_date",
            "params": {"source_format": "%m/%d/%Y", "target_format": "%Y-%m-%d"},
            "rationale": "non-ISO date format observed in format signatures",
        },
        {
            "operation": "standardize_case",
            "column": "country",
            "params": {"case": "title"},
            "rationale": "inconsistent casing observed",
        },
        {
            "operation": "dedupe_rows",
            "params": {"key_columns": ["email"]},
            "rationale": "exact duplicate rows on email observed (repeat signups)",
        },
    ]
    print(json.dumps(plan, indent=2))
    print()

    print("--- Stage 4: execute (validates the whole plan before any step runs) ---")
    before_count = con.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    results = execute_plan(adapter, "customers", plan, confirm=True)
    after_count = con.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    for r in results:
        print(f"  {r['operation']:18s} {r['column'] or '':12s} -> {r['result']}")
    print(f"\n  rows: {before_count} -> {after_count}")
    print()

    print("--- Sample cleaned rows ---")
    sample = con.execute(
        "SELECT id, full_name, email, phone, age, signup_date, country FROM customers LIMIT 5"
    ).fetchdf()
    print(sample.to_string(index=False))


if __name__ == "__main__":
    main()
