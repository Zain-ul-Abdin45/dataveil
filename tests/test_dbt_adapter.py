"""DbtAdapter against a dbt manifest (tests/fixtures/dbt/manifest.json) and
real DuckDB tables in the schema the manifest names (dbt prefixes a custom
schema with the target schema: `main_analytics`). dbt itself is not
needed: the adapter only reads the manifest.
"""

import json
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from dataveil.adapters.dbt import DbtAdapter  # noqa: E402
from dataveil.adapters.duckdb import DuckDBAdapter  # noqa: E402
from dataveil.core.classify import classify_table  # noqa: E402
from dataveil.core.execute import execute_plan  # noqa: E402
from dataveil.core.profile import profile_table  # noqa: E402

MANIFEST = Path(__file__).parent / "fixtures" / "dbt" / "manifest.json"
VALID_VISA = "4111111111111111"
VALID_IBAN = "GB29NWBK60161331926819"


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA main_analytics")
    connection.execute("CREATE TABLE main_analytics.customers (id INTEGER, email VARCHAR, full_name VARCHAR)")
    connection.executemany(
        "INSERT INTO main_analytics.customers VALUES (?, ?, ?)",
        [[i, f"user{i}@example.com", f"  Person {i}  "] for i in range(1, 13)],
    )
    connection.execute("CREATE TABLE main_analytics.fct_payments (id INTEGER, card_number VARCHAR, iban VARCHAR)")
    connection.executemany(
        "INSERT INTO main_analytics.fct_payments VALUES (?, ?, ?)", [[i, VALID_VISA, VALID_IBAN] for i in range(1, 6)]
    )
    connection.execute("CREATE VIEW main_analytics.customer_emails AS SELECT id, email FROM main_analytics.customers")
    return connection


@pytest.fixture
def adapter(con):
    return DbtAdapter(MANIFEST, DuckDBAdapter(con))


def test_lists_only_models_that_have_a_relation(adapter):
    # the alias is used, ephemeral models, seeds and tests are skipped
    assert adapter.list_tables() == [
        "main_analytics.customer_emails",
        "main_analytics.customers",
        "main_analytics.fct_payments",
        "main_analytics.not_built_yet",
    ]


def test_get_schema_reads_the_relation_in_its_schema(adapter):
    schema = adapter.get_schema("main_analytics.customers")
    assert schema == {"id": "INTEGER", "email": "VARCHAR", "full_name": "VARCHAR"}


def test_unknown_and_unbuilt_models_give_clear_errors(adapter):
    with pytest.raises(ValueError, match="unknown dbt model"):
        adapter.get_schema("main_analytics.int_helper")  # ephemeral
    with pytest.raises(ValueError, match="run `dbt run` first"):
        adapter.get_schema("main_analytics.not_built_yet")


def test_profile_and_classify_without_literal_values(adapter):
    profile = profile_table(adapter, "main_analytics.customers")
    assert profile.row_count == 12
    assert "user1@example.com" not in json.dumps(profile.to_dict())
    tags = {r.column: r.tag for r in classify_table(adapter, "main_analytics.customers")}
    assert tags["email"] == "PII:EMAIL"


def test_checksum_classifiers_come_from_the_warehouse_adapter(adapter):
    assert adapter.has_checksum_functions() is True
    tags = {r.column: r.tag for r in classify_table(adapter, "main_analytics.fct_payments")}
    assert tags["card_number"] == "PII:CREDIT_CARD"
    assert tags["iban"] == "PII:IBAN"


def test_execute_plan_cleanses_a_table_model(adapter, con):
    step = {"operation": "trim_whitespace", "column": "full_name", "params": {}, "rationale": "x"}
    execute_plan(adapter, "main_analytics.customers", [step], confirm=True)
    assert con.execute("SELECT full_name FROM main_analytics.customers WHERE id = 1").fetchone()[0] == "Person 1"


def test_execute_plan_rejects_a_view_model(adapter):
    step = {"operation": "mask", "column": "email", "params": {"method": "hash"}, "rationale": "x"}
    with pytest.raises(ValueError, match="is a view"):
        execute_plan(adapter, "main_analytics.customer_emails", [step], confirm=True)
