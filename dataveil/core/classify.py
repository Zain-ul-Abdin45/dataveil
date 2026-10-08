"""Local, deterministic sensitivity classification.

Every classifier here is a regex (and, for checksum-backed types, a
predicate evaluated inside the adapter's own engine — see adapter.py's
contract) run as an aggregate COUNT over the table. The only thing that
ever reaches this module is a match count and a non-null count; no matched
value, nor any other value, crosses into Python here.

The starting set follows the well-established Presidio pattern set (email,
phone, SSN, credit card, IBAN) rather than inventing new regexes. Person
names need NER, not regex: the ``PII:PERSON_NAME`` classifier calls the
adapter's optional ``dataveil_ner_label`` function (see adapter.py), once
per distinct value, for the ``NER_MAX_DISTINCT_VALUES`` most frequent values.
Its match rate is computed over the rows those values cover. Addresses are
not classified yet.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from .adapter import Adapter
from .coltypes import is_string_type
from .sql import quote_ident

# A column must match a classifier's pattern (+ checksum, where applicable)
# for at least this fraction of its non-null values to be tagged.
MATCH_THRESHOLD = 0.8

# Lower bar used when the column name itself also hints at the tag.
NAME_HINT_THRESHOLD = 0.5

# The NER model is slow (hundreds of values per second), so it only labels
# this many of a column's most frequent distinct values.
NER_MAX_DISTINCT_VALUES = 10_000


@dataclasses.dataclass(frozen=True)
class Classifier:
    tag: str
    pattern: str | None
    name_hints: tuple[str, ...]
    requires_checksum: str | None = None  # "luhn" | "iban" | None
    ner_label: str | None = None  # e.g. "PERSON": matched by dataveil_ner_label, not a regex


CLASSIFIERS: tuple[Classifier, ...] = (
    Classifier(
        tag="PII:EMAIL",
        pattern=r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$",
        name_hints=("email", "e_mail", "mail"),
    ),
    Classifier(
        tag="PII:SSN",
        pattern=r"^[0-9]{3}-[0-9]{2}-[0-9]{4}$",
        name_hints=("ssn", "social_security"),
    ),
    Classifier(
        # NANP-shaped (optional +1, optional parens around the area code,
        # -/./space separators, optional "x"-prefixed extension) rather than
        # a generic bounded-length digit run -- that was both too loose
        # (matched unpunctuated 16-digit card numbers) and too tight (missed
        # real formats like "206.269.0743x9150"). Structurally requires
        # exactly 10 (or 11 with a leading 1) digits before any extension,
        # so it can't collide with a 13-19 digit card number.
        tag="PII:PHONE",
        pattern=r"^\+?1?[-.\s]?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}(\s?x\s?[0-9]{1,6})?$",
        name_hints=("phone", "mobile", "tel"),
    ),
    Classifier(
        tag="PII:CREDIT_CARD",
        pattern=r"^[0-9](?:[0-9 -]{11,22})[0-9]$",
        name_hints=("card", "credit_card", "cc_number", "pan"),
        requires_checksum="luhn",
    ),
    Classifier(
        tag="PII:IBAN",
        pattern=r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}$",
        name_hints=("iban",),
        requires_checksum="iban",
    ),
    Classifier(
        tag="PII:PERSON_NAME",
        pattern=None,
        name_hints=("name", "customer", "contact", "person"),
        ner_label="PERSON",
    ),
)

NONE_TAG = "none"


@dataclasses.dataclass
class ClassificationResult:
    column: str
    tag: str
    match_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {"column": self.column, "tag": self.tag, "match_rate": self.match_rate}


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def classify_column(adapter: Adapter, table: str, column: str) -> ClassificationResult:
    schema = adapter.get_schema(table)
    return _classify_column(adapter, table, column, schema.get(column, ""))


def _classify_column(adapter: Adapter, table: str, column: str, column_type: str) -> ClassificationResult:
    # Casting a non-string column (DATE, BOOLEAN, ...) to VARCHAR for a regex
    # match produces false positives unrelated to its actual content -- e.g.
    # a DATE like '2020-01-01' matches the phone-number pattern below.
    if not is_string_type(column_type):
        return ClassificationResult(column=column, tag=NONE_TAG, match_rate=0.0)

    qt = quote_ident(table)
    qc = quote_ident(column)

    non_null = adapter.run_aggregate_query(f"SELECT COUNT({qc}) AS n FROM {qt}")[0]["n"]
    if non_null == 0:
        return ClassificationResult(column=column, tag=NONE_TAG, match_rate=0.0)

    best_tag = NONE_TAG
    best_rate = 0.0

    for clf in CLASSIFIERS:
        if clf.requires_checksum and not adapter.has_checksum_functions():
            continue
        if clf.ner_label is not None:
            if not adapter.has_ner_function():
                continue
            match_rate = _ner_match_rate(adapter, qt, qc, clf.ner_label)
            name_hinted = any(hint in column.lower() for hint in clf.name_hints)
            threshold = NAME_HINT_THRESHOLD if name_hinted else MATCH_THRESHOLD
            if match_rate >= threshold and match_rate > best_rate:
                best_tag, best_rate = clf.tag, match_rate
            continue
        assert clf.pattern is not None

        # The '~' POSIX match operator (not the regexp_matches() function) --
        # DuckDB and Postgres both support it as a boolean predicate, but
        # Postgres's regexp_matches() is a set-returning function and can't
        # be used inside a FILTER (WHERE ...) clause at all.
        predicate = f"CAST({qc} AS VARCHAR) ~ '{_escape_sql_literal(clf.pattern)}'"
        if clf.requires_checksum == "luhn":
            predicate += f" AND dataveil_luhn_valid(CAST({qc} AS VARCHAR))"
        elif clf.requires_checksum == "iban":
            predicate += f" AND dataveil_iban_valid(CAST({qc} AS VARCHAR))"

        matches = adapter.run_aggregate_query(
            f"SELECT COUNT(*) FILTER (WHERE {predicate}) AS n FROM {qt} WHERE {qc} IS NOT NULL"
        )[0]["n"]
        match_rate = matches / non_null

        name_hinted = any(hint in column.lower() for hint in clf.name_hints)
        threshold = NAME_HINT_THRESHOLD if name_hinted else MATCH_THRESHOLD

        if match_rate >= threshold and match_rate > best_rate:
            best_tag, best_rate = clf.tag, match_rate

    return ClassificationResult(column=column, tag=best_tag, match_rate=best_rate)


def _ner_match_rate(adapter: Adapter, qt: str, qc: str, label: str) -> float:
    """Share of rows whose value has the given entity label.

    The model runs once per distinct value, for the NER_MAX_DISTINCT_VALUES
    most frequent ones; the rate is over the rows those values cover. Only
    the two counts come back.
    """
    row = adapter.run_aggregate_query(
        f"SELECT SUM(n) FILTER (WHERE dataveil_ner_label(v) = '{_escape_sql_literal(label)}') AS matched, "
        f"SUM(n) AS covered FROM ("
        f"SELECT CAST({qc} AS VARCHAR) AS v, COUNT(*) AS n FROM {qt} WHERE {qc} IS NOT NULL "
        f"GROUP BY v ORDER BY n DESC, v LIMIT {NER_MAX_DISTINCT_VALUES}) AS top_values"
    )[0]
    covered = row["covered"] or 0
    return float(row["matched"] or 0) / covered if covered else 0.0


def classify_table(adapter: Adapter, table: str) -> list[ClassificationResult]:
    schema = adapter.get_schema(table)
    return [_classify_column(adapter, table, name, col_type) for name, col_type in schema.items()]
