import json

from dataveil.core.profile import profile_table

LITERAL_EMAIL = "alice.wonderland@example.com"
LITERAL_SSN = "123-45-6789"
LITERAL_NAME = "Alice Wonderland"


def _make_table(con):
    con.execute(
        """
        CREATE TABLE people (
            id INTEGER,
            email VARCHAR,
            ssn VARCHAR,
            full_name VARCHAR,
            age INTEGER,
            is_active BOOLEAN
        )
        """
    )
    con.execute(
        "INSERT INTO people VALUES (?, ?, ?, ?, ?, ?)",
        [1, LITERAL_EMAIL, LITERAL_SSN, LITERAL_NAME, 29, True],
    )
    con.execute(
        "INSERT INTO people VALUES (?, ?, ?, ?, ?, ?)",
        [2, "bob@example.com", "987-65-4321", "Bob Builder", 41, False],
    )
    con.execute("INSERT INTO people VALUES (3, NULL, NULL, NULL, NULL, NULL)")


def test_profile_shape(adapter, con):
    _make_table(con)
    profile = profile_table(adapter, "people")

    assert profile.table == "people"
    assert profile.row_count == 3

    by_name = {c.name: c for c in profile.columns}
    assert set(by_name) == {"id", "email", "ssn", "full_name", "age", "is_active"}

    email_col = by_name["email"]
    assert email_col.null_count == 1
    assert email_col.null_rate == 1 / 3
    assert email_col.distinct_count == 2
    assert email_col.format_signatures is not None
    assert all("@" in s.signature for s in email_col.format_signatures)

    age_col = by_name["age"]
    assert age_col.min == 29
    assert age_col.max == 41
    assert age_col.mean == 35


def test_profile_never_contains_literal_values(adapter, con):
    _make_table(con)
    profile = profile_table(adapter, "people")
    serialized = json.dumps(profile.to_dict())

    assert LITERAL_EMAIL not in serialized
    assert LITERAL_SSN not in serialized
    assert LITERAL_NAME not in serialized
    assert "bob@example.com" not in serialized
    assert "987-65-4321" not in serialized
    assert "Bob Builder" not in serialized


def test_low_cardinality_column_still_not_enumerated(adapter, con):
    """Even a 2-value boolean column must not have its literal values
    surfaced — the aggregate-only rule is kept absolute for v1 (see
    PLAN.md's open question on low-cardinality columns)."""
    _make_table(con)
    profile = profile_table(adapter, "people")
    by_name = {c.name: c for c in profile.columns}

    is_active = by_name["is_active"]
    assert is_active.distinct_count == 2
    # boolean columns get no numeric/string stat block at all -- just counts.
    d = is_active.to_dict()
    assert set(d) == {
        "name",
        "type",
        "null_count",
        "null_rate",
        "distinct_count",
        "cardinality_ratio",
    }
