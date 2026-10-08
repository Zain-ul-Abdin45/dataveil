"""End-to-end tests for the Postgres adapter against a real local Postgres
instance -- the second adapter (PLAN.md Phase 5), built to prove the
Adapter interface holds for something that isn't SQLMesh: no Context, no
virtual/physical table split, just a plain SQLAlchemy Engine.

Connects to DATAVEIL_TEST_POSTGRES_URL (default: a local 'dataveil_test'
database) and skips this whole module if nothing is reachable there -- the
core engine (and the DuckDB adapter) must stay usable without Postgres (see
PLAN.md Phase 4's "core stays usable without pulling in" a given adapter).
"""

import json
import os
import uuid

import pytest

sa = pytest.importorskip("sqlalchemy")

from dataveil.adapters.postgres import PostgresAdapter  # noqa: E402
from dataveil.core.classify import classify_table  # noqa: E402
from dataveil.core.execute import execute_plan  # noqa: E402
from dataveil.core.plan import PlanValidationError  # noqa: E402
from dataveil.core.profile import MIN_EXTREME_COUNT, MIN_SIGNATURE_COUNT, profile_table  # noqa: E402

TEST_DB_URL = os.environ.get("DATAVEIL_TEST_POSTGRES_URL", "postgresql+psycopg2://localhost/dataveil_test")

VALID_VISA = "4111111111111111"
VALID_IBAN = "GB29NWBK60161331926819"


def _postgres_available() -> bool:
    try:
        engine = sa.create_engine(TEST_DB_URL)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="no Postgres instance reachable")


@pytest.fixture
def engine():
    eng = sa.create_engine(TEST_DB_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def table(engine):
    name = f"dataveil_test_{uuid.uuid4().hex[:8]}"
    yield name
    with engine.begin() as conn:
        conn.execute(sa.text(f'DROP TABLE IF EXISTS "{name}"'))


@pytest.fixture
def adapter(engine):
    return PostgresAdapter(engine)


def test_list_tables_and_get_schema(adapter, engine, table):
    with engine.begin() as conn:
        conn.execute(sa.text(f'CREATE TABLE "{table}" (id INTEGER, email TEXT)'))
    assert table in adapter.list_tables()
    assert adapter.get_schema(table) == {"id": "INTEGER", "email": "TEXT"}


def test_profile_never_contains_literal_values(adapter, engine, table):
    with engine.begin() as conn:
        conn.execute(sa.text(f'CREATE TABLE "{table}" (email TEXT, age INTEGER)'))
        conn.execute(
            sa.text(f'INSERT INTO "{table}" VALUES (:e, :a)'),
            [{"e": "alice.wonderland@example.com", "a": 29}, {"e": "bob@example.com", "a": 41}],
        )
    profile = profile_table(adapter, table)
    assert profile.row_count == 2
    serialized = json.dumps(profile.to_dict())
    assert "alice.wonderland@example.com" not in serialized
    assert "bob@example.com" not in serialized


def test_classify_email_and_checksum_backed_classifiers(adapter, engine, table):
    assert adapter.has_checksum_functions() is True
    with engine.begin() as conn:
        conn.execute(sa.text(f'CREATE TABLE "{table}" (contact_email TEXT, card_number TEXT, iban TEXT)'))
        conn.execute(
            sa.text(f'INSERT INTO "{table}" VALUES (:e, :c, :i)'),
            [{"e": "a@example.com", "c": VALID_VISA, "i": VALID_IBAN}],
        )
    results = {r.column: r.tag for r in classify_table(adapter, table)}
    assert results["contact_email"] == "PII:EMAIL"
    assert results["card_number"] == "PII:CREDIT_CARD"  # Luhn-checked via plpgsql
    assert results["iban"] == "PII:IBAN"  # mod-97-checked via plpgsql


def test_date_column_not_misclassified(adapter, engine, table):
    with engine.begin() as conn:
        conn.execute(sa.text(f'CREATE TABLE "{table}" (event_date DATE)'))
        conn.execute(sa.text(f'INSERT INTO "{table}" VALUES (:d)'), [{"d": "2020-01-01"}])
    results = {r.column: r.tag for r in classify_table(adapter, table)}
    assert results["event_date"] == "none"


def test_execute_plan_runs_core_operations(adapter, engine, table):
    with engine.begin() as conn:
        conn.execute(sa.text(f'CREATE TABLE "{table}" (name TEXT, email TEXT, age INTEGER, joined TEXT, ssn TEXT)'))
        conn.execute(
            sa.text(f'INSERT INTO "{table}" VALUES (:n, :e, :a, :j, :s)'),
            [
                {"n": "  bob  ", "e": "bob@example.com", "a": 10, "j": "03/15/2024", "s": "123-45-6789"},
                {"n": "alice", "e": "alice@example.com", "a": None, "j": "07/02/2024", "s": "987-65-4321"},
            ],
        )

    execute_plan(
        adapter,
        table,
        [
            {"operation": "trim_whitespace", "column": "name", "params": {}, "rationale": "x"},
            {"operation": "standardize_case", "column": "name", "params": {"case": "upper"}, "rationale": "x"},
            {"operation": "mask", "column": "email", "params": {"method": "hash"}, "rationale": "x"},
            {"operation": "impute", "column": "age", "params": {"strategy": "mean"}, "rationale": "x"},
            {
                "operation": "parse_date",
                "column": "joined",
                "params": {"source_format": "%m/%d/%Y", "target_format": "%Y-%m-%d"},
                "rationale": "x",
            },
            {"operation": "drop_column", "column": "ssn", "params": {}, "rationale": "PII"},
        ],
        confirm=True,
    )

    with engine.connect() as conn:
        rows = conn.execute(sa.text(f'SELECT name, email, age, joined FROM "{table}" ORDER BY joined')).fetchall()

    assert rows[0][0] == "BOB"
    assert rows[0][1] != "bob@example.com"
    assert len(rows[0][1]) == 32
    assert rows[0][2] == 10  # untouched, not null
    assert rows[1][2] == 10  # imputed to the mean of the one non-null value
    assert rows[0][3] == "2024-03-15"
    assert "ssn" not in adapter.get_schema(table)


def test_dedupe_rows(adapter, engine, table):
    with engine.begin() as conn:
        conn.execute(sa.text(f'CREATE TABLE "{table}" (email TEXT, name TEXT)'))
        conn.execute(
            sa.text(f'INSERT INTO "{table}" VALUES (:e, :n)'),
            [{"e": "a@x.com", "n": "A"}, {"e": "a@x.com", "n": "A dup"}, {"e": "b@x.com", "n": "B"}],
        )
    execute_plan(
        adapter,
        table,
        [{"operation": "dedupe_rows", "params": {"key_columns": ["email"]}, "rationale": "dup key"}],
        confirm=True,
    )
    n = adapter.run_aggregate_query(f'SELECT COUNT(*) AS n FROM "{table}"')[0]["n"]
    assert n == 2


def test_bucket_numeric(adapter, engine, table):
    with engine.begin() as conn:
        conn.execute(sa.text(f'CREATE TABLE "{table}" (age INTEGER)'))
        conn.execute(sa.text(f'INSERT INTO "{table}" VALUES (:a)'), [{"a": 5}, {"a": 25}, {"a": 45}])
    execute_plan(
        adapter,
        table,
        [{"operation": "bucket_numeric", "column": "age", "params": {"bin_width": 20}, "rationale": "x"}],
        confirm=True,
    )
    rows = sorted(r["age_bucket"] for r in adapter.run_aggregate_query(f'SELECT age_bucket FROM "{table}"'))
    assert rows == ["0-20", "20-40", "40-60"]


def test_unknown_operation_rejected_before_any_write(adapter, engine, table):
    with engine.begin() as conn:
        conn.execute(sa.text(f'CREATE TABLE "{table}" (name TEXT)'))
        conn.execute(sa.text(f'INSERT INTO "{table}" VALUES (:n)'), [{"n": "bob"}])
    with pytest.raises(PlanValidationError, match="unknown operation"):
        execute_plan(
            adapter,
            table,
            [
                {"operation": "trim_whitespace", "column": "name", "params": {}, "rationale": "ok"},
                {"operation": "drop_table", "column": "name", "params": {}, "rationale": "bad"},
            ],
            confirm=True,
        )
    # the valid first step must not have run either
    value = adapter.run_aggregate_query(f'SELECT name FROM "{table}"')[0]["name"]
    assert value == "bob"


def test_format_signatures_match_duckdb_behaviour(adapter, engine, table):
    """DuckDB's ~ is a full-string match and Postgres's is a substring match,
    so signature generalization is only portable if its patterns are anchored.
    These cases pin down the behaviour the DuckDB tests check."""
    with engine.begin() as conn:
        conn.execute(sa.text(f'CREATE TABLE "{table}" (v TEXT)'))
        conn.execute(
            sa.text(f'INSERT INTO "{table}" VALUES (:v)'),
            [{"v": v} for v in ["123-45-6789", "ALICE josé", "---", "Abc1"] for _ in range(MIN_SIGNATURE_COUNT)],
        )
    signatures = {s["signature"] for s in profile_table(adapter, table).to_dict()["columns"][0]["format_signatures"]}
    assert signatures == {
        "ddd-dd-dddd",
        "aaaaa aaaa",
        "<punctuation or whitespace only>",
        "aaad",
    }
    assert "---" not in signatures


def test_numeric_stats_withheld_for_single_row_table(adapter, engine, table):
    with engine.begin() as conn:
        conn.execute(sa.text(f'CREATE TABLE "{table}" (amount DOUBLE PRECISION)'))
        conn.execute(sa.text(f'INSERT INTO "{table}" VALUES (1234.5)'))
    column = profile_table(adapter, table).to_dict()["columns"][0]
    assert "min" not in column and "max" not in column
    assert "1234.5" not in json.dumps(column)


def test_unique_numeric_extreme_is_withheld(adapter, engine, table):
    """Same rule as the DuckDB tests: min/max only when MIN_EXTREME_COUNT rows share it."""
    values = [18] * MIN_EXTREME_COUNT + list(range(19, 40)) + [250_000]
    with engine.begin() as conn:
        conn.execute(sa.text(f'CREATE TABLE "{table}" (n INTEGER)'))
        conn.execute(sa.text(f'INSERT INTO "{table}" VALUES (:n)'), [{"n": v} for v in values])
    column = profile_table(adapter, table).to_dict()["columns"][0]
    assert column["min"] == 18
    assert "max" not in column
    assert "p50" in column
