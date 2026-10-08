"""The PII:PERSON_NAME classifier (optional spaCy model) on DuckDB.

Tests that need the model skip without it; the fallback tests run anyway.
All names are synthetic.
"""

import pytest

duckdb = pytest.importorskip("duckdb")

import dataveil.adapters.duckdb as duckdb_adapter_module  # noqa: E402
import dataveil.core.classify as classify_module  # noqa: E402
from dataveil.adapters._ner import ner_available  # noqa: E402
from dataveil.adapters.duckdb import DuckDBAdapter  # noqa: E402
from dataveil.core.classify import classify_table  # noqa: E402

needs_model = pytest.mark.skipif(not ner_available(), reason="spaCy model en_core_web_sm not installed")

NAMES = [
    "Alice Wonderland",
    "Bob Smith",
    "Priya Patel",
    "Mohammed Ali",
    "Chen Wei",
    "John O'Neil",
    "Dr. Jane Doe",
    "Maria Rossi",
    "Liam Murphy",
    "Sofia Novak",
]
NOT_NAMES = [
    "Premium plan",
    "Blue",
    "Order shipped",
    "Small widget",
    "Refund requested",
    "N/A",
    "Gift card",
    "Standard delivery",
    "Out of stock",
    "Back order",
]


def _classify(con, adapter, column, values):
    con.execute(f"CREATE TABLE t_{column} ({column} VARCHAR)")
    con.executemany(f"INSERT INTO t_{column} VALUES (?)", [[v] for v in values])
    return {r.column: r for r in classify_table(adapter, f"t_{column}")}[column]


@pytest.fixture
def con():
    return duckdb.connect(":memory:")


@needs_model
def test_a_column_of_names_is_tagged(con):
    result = _classify(con, DuckDBAdapter(con), "contact", NAMES)
    assert result.tag == "PII:PERSON_NAME"
    assert result.match_rate >= 0.8


@needs_model
def test_a_column_of_other_text_is_not_tagged(con):
    assert _classify(con, DuckDBAdapter(con), "note", NOT_NAMES).tag == "none"


@needs_model
def test_a_name_hint_lowers_the_threshold(con):
    # 6 of 10 are names: below 0.8, but "full_name" hints at the tag (0.5)
    values = NAMES[:6] + NOT_NAMES[:4]
    assert _classify(con, DuckDBAdapter(con), "full_name", values).tag == "PII:PERSON_NAME"
    assert _classify(con, DuckDBAdapter(con), "remark", values).tag == "none"


@needs_model
def test_rate_is_over_the_most_frequent_distinct_values(con, monkeypatch):
    # only the 2 most frequent distinct values are labelled; both are names
    monkeypatch.setattr(classify_module, "NER_MAX_DISTINCT_VALUES", 2)
    values = ["Alice Wonderland"] * 5 + ["Bob Smith"] * 4 + NOT_NAMES
    result = _classify(con, DuckDBAdapter(con), "contact", values)
    assert result.tag == "PII:PERSON_NAME"
    assert result.match_rate == 1.0


def test_classifier_is_skipped_when_registration_is_off(con):
    adapter = DuckDBAdapter(con, register_ner_function=False)
    assert adapter.has_ner_function() is False
    assert _classify(con, adapter, "contact", NAMES).tag == "none"


def test_classifier_is_skipped_without_the_model(con, monkeypatch):
    monkeypatch.setattr(duckdb_adapter_module, "ner_available", lambda: False)
    adapter = DuckDBAdapter(con)
    assert adapter.has_ner_function() is False
    assert _classify(con, adapter, "contact", NAMES).tag == "none"
