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
per distinct value, for the ``ner_max_distinct_values`` most frequent values.
Its match rate is computed over the rows those values cover. Addresses are
not classified yet.

Teams can add their own regex classifiers (``[[classify.custom]]`` in
dataveil.toml, see core/config.py). They run exactly like the built-in
regexes: one aggregate COUNT per column. Installed packages can do the same
through the ``dataveil.classifiers`` entry point group (see
``registered_classifiers``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from importlib.metadata import entry_points
from typing import Any

from .adapter import Adapter
from .coltypes import is_string_type
from .config import ClassifyConfig, ConfigError, CustomClassifier, PluginError, check_portable_pattern
from .sql import quote_ident

# The thresholds below are the defaults of ClassifyConfig (core/config.py),
# which a dataveil.toml can change.

# A column must match a classifier's pattern (+ checksum, where applicable)
# for at least this fraction of its non-null values to be tagged.
MATCH_THRESHOLD = ClassifyConfig.match_threshold

# Lower bar used when the column name itself also hints at the tag.
NAME_HINT_THRESHOLD = ClassifyConfig.name_hint_threshold

# The NER model is slow (hundreds of values per second), so it only labels
# this many of a column's most frequent distinct values.
NER_MAX_DISTINCT_VALUES = ClassifyConfig.ner_max_distinct_values


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

CLASSIFIER_ENTRY_POINT_GROUP = "dataveil.classifiers"
_CHECKSUMS = (None, "luhn", "iban")


def _plugin_classifiers(name: str, obj: object) -> tuple[Classifier, ...]:
    """Check what a ``dataveil.classifiers`` entry point loaded.

    A plugin provides *definitions*, never code: a Classifier or
    CustomClassifier, or a list of them. dataveil itself turns each one into
    the same aggregate COUNT as a built-in classifier, so a plugin cannot
    return anything but counts.
    """
    where = f"classifier plugin {name!r}"
    items = obj if isinstance(obj, Iterable) and not isinstance(obj, (str, bytes)) else (obj,)
    result = []
    for item in items:
        if isinstance(item, CustomClassifier):
            item = Classifier(tag=item.tag, pattern=item.pattern, name_hints=item.name_hints)
        if not isinstance(item, Classifier):
            raise PluginError(f"{where} must provide Classifier or CustomClassifier objects, not {type(item).__name__}")
        if (item.pattern is None) == (item.ner_label is None):
            raise PluginError(f"{where}: {item.tag} needs exactly one of pattern or ner_label")
        if item.requires_checksum not in _CHECKSUMS:
            raise PluginError(f"{where}: {item.tag} has unknown requires_checksum {item.requires_checksum!r}")
        if item.ner_label is not None and item.requires_checksum is not None:
            raise PluginError(f"{where}: {item.tag} cannot combine ner_label with a checksum")
        if item.pattern is not None:
            check_portable_pattern(f"{where}: {item.tag}", item.pattern, PluginError)
        if item.ner_label is not None and not (item.ner_label.isascii() and item.ner_label.isalnum()):
            raise PluginError(f"{where}: {item.tag} ner_label must be an entity label such as PERSON")
        try:
            # same tag and hint rules as a classifier from the config file
            CustomClassifier(tag=item.tag, pattern="^$", name_hints=item.name_hints)
        except ConfigError as e:
            raise PluginError(f"{where}: {e}") from e
        result.append(dataclasses.replace(item, name_hints=tuple(h.lower() for h in item.name_hints)))
    return tuple(result)


def registered_classifiers() -> tuple[Classifier, ...]:
    """The built-in classifiers, then those of installed plugins.

    The built-ins are registered under the same entry point group, as
    ``builtin``. They are included even when that entry point is missing (a
    source checkout that was never installed). A broken plugin, or two
    classifiers with the same tag, is an error rather than skipped, so a
    tag always means one thing.
    """
    result = list(CLASSIFIERS)
    seen = {c.tag: "built-in" for c in CLASSIFIERS}
    for ep in sorted(entry_points(group=CLASSIFIER_ENTRY_POINT_GROUP), key=lambda e: e.name):
        try:
            obj = ep.load()
        except Exception as e:
            raise PluginError(f"classifier plugin {ep.name!r} ({ep.value}) failed to load: {e}") from e
        if obj is CLASSIFIERS:
            continue
        for clf in _plugin_classifiers(ep.name, obj):
            if clf.tag in seen:
                raise PluginError(f"classifier plugin {ep.name!r}: tag {clf.tag} is already defined by {seen[clf.tag]}")
            seen[clf.tag] = f"plugin {ep.name!r}"
            result.append(clf)
    return tuple(result)


def classifiers_for(config: ClassifyConfig) -> tuple[Classifier, ...]:
    """The registered classifiers that are not disabled, then the custom ones.

    A custom classifier may not reuse a registered tag: two definitions of
    ``PII:EMAIL`` would make a tag mean different things on different setups.
    """
    registered = registered_classifiers()
    registered_tags = {c.tag for c in registered}
    clashes = sorted(c.tag for c in config.custom if c.tag in registered_tags)
    if clashes:
        raise ConfigError(
            f"[[classify.custom]] tag(s) already used by a built-in or plugin classifier: {', '.join(clashes)}"
        )
    custom = tuple(Classifier(tag=c.tag, pattern=c.pattern, name_hints=c.name_hints) for c in config.custom)
    return tuple(c for c in registered if c.tag not in config.disabled) + custom


@dataclasses.dataclass
class ClassificationResult:
    column: str
    tag: str
    match_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {"column": self.column, "tag": self.tag, "match_rate": self.match_rate}


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def classify_column(
    adapter: Adapter, table: str, column: str, config: ClassifyConfig | None = None
) -> ClassificationResult:
    config = config or ClassifyConfig()
    schema = adapter.get_schema(table)
    return _classify_column(adapter, table, column, schema.get(column, ""), config, classifiers_for(config))


def _classify_column(
    adapter: Adapter,
    table: str,
    column: str,
    column_type: str,
    config: ClassifyConfig,
    classifiers: tuple[Classifier, ...],
) -> ClassificationResult:
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

    for clf in classifiers:
        if clf.requires_checksum and not adapter.has_checksum_functions():
            continue
        if clf.ner_label is not None:
            if not adapter.has_ner_function():
                continue
            match_rate = _ner_match_rate(adapter, qt, qc, clf.ner_label, config.ner_max_distinct_values)
            name_hinted = any(hint in column.lower() for hint in clf.name_hints)
            threshold = config.name_hint_threshold if name_hinted else config.match_threshold
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
        threshold = config.name_hint_threshold if name_hinted else config.match_threshold

        if match_rate >= threshold and match_rate > best_rate:
            best_tag, best_rate = clf.tag, match_rate

    return ClassificationResult(column=column, tag=best_tag, match_rate=best_rate)


def _ner_match_rate(adapter: Adapter, qt: str, qc: str, label: str, max_distinct_values: int) -> float:
    """Share of rows whose value has the given entity label.

    The model runs once per distinct value, for the ``max_distinct_values``
    most frequent ones; the rate is over the rows those values cover. Only
    the two counts come back.
    """
    row = adapter.run_aggregate_query(
        f"SELECT SUM(n) FILTER (WHERE dataveil_ner_label(v) = '{_escape_sql_literal(label)}') AS matched, "
        f"SUM(n) AS covered FROM ("
        f"SELECT CAST({qc} AS VARCHAR) AS v, COUNT(*) AS n FROM {qt} WHERE {qc} IS NOT NULL "
        f"GROUP BY v ORDER BY n DESC, v LIMIT {max_distinct_values}) AS top_values"
    )[0]
    covered = row["covered"] or 0
    return float(row["matched"] or 0) / covered if covered else 0.0


def classify_table(adapter: Adapter, table: str, config: ClassifyConfig | None = None) -> list[ClassificationResult]:
    config = config or ClassifyConfig()
    classifiers = classifiers_for(config)
    schema = adapter.get_schema(table)
    return [_classify_column(adapter, table, name, col_type, config, classifiers) for name, col_type in schema.items()]
