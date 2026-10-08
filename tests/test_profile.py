import json

from dataveil.core.profile import MAX_FORMAT_SIGNATURES, MIN_SIGNATURE_COUNT, OTHER_SIGNATURES, profile_table

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
    # 2 emails, each with its own shape: both are below MIN_SIGNATURE_COUNT
    assert [s.to_dict() for s in email_col.format_signatures] == [{"signature": OTHER_SIGNATURES, "count": 2}]

    # a 3-row table is below MIN_ROWS_FOR_NUMERIC_STATS, so numeric stats are withheld
    age_col = by_name["age"]
    assert age_col.min is None
    assert age_col.max is None
    assert age_col.mean is None


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


def _signatures(con, adapter, values, table="sig_t"):
    # Each value is inserted MIN_SIGNATURE_COUNT times, so its signature is
    # common enough to be returned. These tests check how a signature is built.
    con.execute(f"CREATE TABLE {table} (v VARCHAR)")
    con.executemany(f"INSERT INTO {table} VALUES (?)", [[v] for v in values for _ in range(MIN_SIGNATURE_COUNT)])
    column = profile_table(adapter, table).to_dict()["columns"][0]
    return sorted(s["signature"] for s in column["format_signatures"])


def test_ssn_signature_keeps_digits_as_d(adapter, con):
    assert _signatures(con, adapter, ["123-45-6789"]) == ["ddd-dd-dddd"]


def test_letters_and_digits_are_distinct_placeholders(adapter, con):
    assert _signatures(con, adapter, ["Abc1"]) == ["aaad"]


def test_letter_case_is_folded(adapter, con):
    assert _signatures(con, adapter, ["ALICE", "alice"]) == ["aaaaa"]


def test_non_ascii_letters_fold_to_a(adapter, con):
    assert _signatures(con, adapter, ["josé"]) == ["aaaa"]


def test_punctuation_only_value_never_appears_verbatim(adapter, con):
    assert _signatures(con, adapter, ["---"]) == ["<punctuation or whitespace only>"]


def test_single_row_numeric_column_does_not_expose_its_value(adapter, con):
    con.execute("CREATE TABLE one (amount DOUBLE)")
    con.execute("INSERT INTO one VALUES (1234.5)")
    column = profile_table(adapter, "one").to_dict()["columns"][0]
    for key in ("min", "max", "mean", "p25", "p50", "p75"):
        assert key not in column
    assert "1234.5" not in json.dumps(column)


def test_constant_numeric_column_does_not_expose_its_value(adapter, con):
    con.execute("CREATE TABLE const (n INTEGER)")
    con.executemany("INSERT INTO const VALUES (?)", [[42]] * 50)
    column = profile_table(adapter, "const").to_dict()["columns"][0]
    assert "min" not in column and "max" not in column


def test_numeric_stats_returned_for_large_varied_columns(adapter, con):
    con.execute("CREATE TABLE big (n INTEGER)")
    con.executemany("INSERT INTO big VALUES (?)", [[i] for i in range(1, 21)])
    column = profile_table(adapter, "big").to_dict()["columns"][0]
    assert column["min"] == 1
    assert column["max"] == 20


def _signature_counts(con, adapter, values, table="sig_counts"):
    con.execute(f"CREATE TABLE {table} (v VARCHAR)")
    con.executemany(f"INSERT INTO {table} VALUES (?)", [[v] for v in values])
    column = profile_table(adapter, table).to_dict()["columns"][0]
    return {s["signature"]: s["count"] for s in column["format_signatures"]}


def test_rare_signature_is_reported_only_in_the_other_bucket(adapter, con):
    values = ["123-45-6789"] * MIN_SIGNATURE_COUNT + ["bob@example.com", None]
    assert _signature_counts(con, adapter, values) == {"ddd-dd-dddd": MIN_SIGNATURE_COUNT, OTHER_SIGNATURES: 1}


def test_only_the_other_bucket_when_every_signature_is_rare(adapter, con):
    values = ["alice@example.com", "bob@example.com", "123-45-6789"]
    assert _signature_counts(con, adapter, values) == {OTHER_SIGNATURES: 3}


def test_signatures_beyond_the_limit_go_to_the_other_bucket(adapter, con):
    # MAX_FORMAT_SIGNATURES + 1 common signatures: "d", "dd", "ddd", ...
    values = ["1" * length for length in range(1, MAX_FORMAT_SIGNATURES + 2) for _ in range(MIN_SIGNATURE_COUNT)]
    counts = _signature_counts(con, adapter, values)
    assert len(counts) == MAX_FORMAT_SIGNATURES + 1
    assert counts[OTHER_SIGNATURES] == MIN_SIGNATURE_COUNT
    assert sum(counts.values()) == len(values)
