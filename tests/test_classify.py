import json

from dataveil.adapters.duckdb import DuckDBAdapter
from dataveil.core.classify import classify_column, classify_table

VALID_VISA = "4111111111111111"  # passes Luhn
VALID_IBAN = "GB29NWBK60161331926819"  # well-known valid test IBAN


def test_classifies_email(adapter, con):
    con.execute("CREATE TABLE t (contact_email VARCHAR)")
    con.executemany(
        "INSERT INTO t VALUES (?)",
        [["a@example.com"], ["b@example.org"], ["c@example.net"], ["not an email"]],
    )
    result = classify_column(adapter, "t", "contact_email")
    assert result.tag == "PII:EMAIL"
    assert result.match_rate >= 0.5


def test_classifies_ssn(adapter, con):
    con.execute("CREATE TABLE t (ssn VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?)", [["123-45-6789"], ["987-65-4321"]])
    result = classify_column(adapter, "t", "ssn")
    assert result.tag == "PII:SSN"
    assert result.match_rate == 1.0


def test_classifies_phone_across_real_world_formats(adapter, con):
    """Covers the formats Faker's phone_number() actually produces -- the
    gap this closes was caught by examples/messy_customers.py, not a
    synthetic unit test."""
    con.execute("CREATE TABLE t (phone VARCHAR)")
    con.executemany(
        "INSERT INTO t VALUES (?)",
        [
            ["(591)341-7776"],
            ["206.269.0743x9150"],
            ["+1-780-563-6083x7783"],
            ["797-386-9073x6625"],
            ["848-439-6030"],
            ["5272868387"],
        ],
    )
    result = classify_column(adapter, "t", "phone")
    assert result.tag == "PII:PHONE"
    assert result.match_rate == 1.0


def test_phone_pattern_does_not_collide_with_credit_card_or_ssn(adapter, con):
    con.execute("CREATE TABLE t (phone VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?)", [[VALID_VISA], ["123-45-6789"]])
    result = classify_column(adapter, "t", "phone")
    assert result.tag == "none"


def test_classifies_credit_card_with_luhn(adapter, con):
    con.execute("CREATE TABLE t (card_number VARCHAR)")
    con.executemany(
        "INSERT INTO t VALUES (?)",
        [[VALID_VISA], ["4111111111111112"]],  # second fails Luhn
    )
    result = classify_column(adapter, "t", "card_number")
    # only 1 of 2 passes luhn -> 0.5, but name hint lowers threshold to 0.5
    assert result.tag == "PII:CREDIT_CARD"
    assert result.match_rate == 0.5


def test_classifies_iban(adapter, con):
    con.execute("CREATE TABLE t (iban VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?)", [[VALID_IBAN], ["not an iban"]])
    result = classify_column(adapter, "t", "iban")
    assert result.tag == "PII:IBAN"
    assert result.match_rate == 0.5


def test_date_column_not_misclassified_as_phone(adapter, con):
    """A DATE cast to VARCHAR (e.g. '2020-01-01') satisfies the phone regex
    by coincidence; classification must be scoped to string-typed columns so
    this doesn't happen (caught via a real SQLMesh demo project, not a
    synthetic case)."""
    con.execute("CREATE TABLE t (event_date DATE)")
    con.executemany("INSERT INTO t VALUES (?)", [["2020-01-01"], ["2020-01-05"], ["2020-02-10"]])
    result = classify_column(adapter, "t", "event_date")
    assert result.tag == "none"


def test_non_pii_column_tagged_none(adapter, con):
    con.execute("CREATE TABLE t (favorite_color VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?)", [["red"], ["blue"], ["green"]])
    result = classify_column(adapter, "t", "favorite_color")
    assert result.tag == "none"


def test_classify_results_never_contain_literal_values(adapter, con):
    con.execute("CREATE TABLE t (contact_email VARCHAR, card_number VARCHAR)")
    con.execute("INSERT INTO t VALUES (?, ?)", ["secret.person@example.com", VALID_VISA])
    results = classify_table(adapter, "t")
    serialized = json.dumps([r.to_dict() for r in results])
    assert "secret.person@example.com" not in serialized
    assert VALID_VISA not in serialized


def test_two_adapters_on_one_connection(con):
    # registering the UDFs a second time on the same connection used to fail
    DuckDBAdapter(con)
    second = DuckDBAdapter(con)
    con.execute("CREATE TABLE cards (card_number VARCHAR)")
    con.executemany("INSERT INTO cards VALUES (?)", [["4111111111111111"]] * 3)
    assert classify_column(second, "cards", "card_number").tag == "PII:CREDIT_CARD"
