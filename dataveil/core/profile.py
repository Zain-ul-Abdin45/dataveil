"""Aggregate-only profiling.

Every query built here is an aggregate: COUNT/MIN/MAX/AVG/STDDEV/quantiles,
or a GROUP BY over a *generalized* format signature (letters folded to 'a',
digits to 'd', inside SQL before anything comes back to this process). A
signature is never a raw value and no sample of rows is taken.

This is not a guarantee that no value can be inferred. Numeric extremes
(min/max) are literal cell values, so all numeric stats are suppressed for
small or constant columns, and min/max are only returned when enough rows
share them. See README's "Known limits".
"""

from __future__ import annotations

import dataclasses
from typing import Any

from .adapter import Adapter
from .coltypes import is_numeric_type, is_string_type
from .config import ProfileConfig
from .sql import quote_ident

# The thresholds below are the defaults of ProfileConfig; a dataveil.toml
# can make them stricter (see core/config.py), never looser.

# Bound on how many distinct generalized format signatures we return per
# string column — a format signature is not a raw value, but an unbounded
# list of them for a high-cardinality column is still unnecessary detail.
MAX_FORMAT_SIGNATURES = ProfileConfig.max_format_signatures

# Numeric stats are withheld unless the column has enough rows and enough
# distinct values that no single row is trivially recoverable from them.
# Conservative heuristics, not a privacy guarantee.
MIN_ROWS_FOR_NUMERIC_STATS = ProfileConfig.min_rows_for_numeric_stats
MIN_DISTINCT_FOR_NUMERIC_STATS = ProfileConfig.min_distinct_for_numeric_stats

# min and max are literal cell values. One is only returned when at least this
# many rows have that exact value, so a unique outlier (one person's salary)
# is withheld, while a shared bound (age 18 for many rows) is kept.
MIN_EXTREME_COUNT = ProfileConfig.min_extreme_count

NO_ALPHANUMERIC_SIGNATURE = "<punctuation or whitespace only>"

# A signature shared by only a few rows describes those rows (one person's
# email shape, for example), so it is only returned when at least this many
# non-null values have it. Rarer signatures are reported together as one
# bucket with their total count.
MIN_SIGNATURE_COUNT = ProfileConfig.min_signature_count
OTHER_SIGNATURES = "<other signatures>"


@dataclasses.dataclass
class FormatSignature:
    signature: str
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {"signature": self.signature, "count": self.count}


@dataclasses.dataclass
class ColumnProfile:
    name: str
    type: str
    null_count: int
    null_rate: float
    distinct_count: int
    cardinality_ratio: float

    min: float | None = None
    max: float | None = None
    mean: float | None = None
    stddev: float | None = None
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None

    min_length: int | None = None
    max_length: int | None = None
    avg_length: float | None = None
    format_signatures: list[FormatSignature] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "null_count": self.null_count,
            "null_rate": self.null_rate,
            "distinct_count": self.distinct_count,
            "cardinality_ratio": self.cardinality_ratio,
        }
        for key in (
            "min",
            "max",
            "mean",
            "stddev",
            "p25",
            "p50",
            "p75",
            "min_length",
            "max_length",
            "avg_length",
        ):
            value = getattr(self, key)
            if value is not None:
                d[key] = value
        if self.format_signatures is not None:
            d["format_signatures"] = [s.to_dict() for s in self.format_signatures]
        return d


@dataclasses.dataclass
class TableProfile:
    table: str
    row_count: int
    columns: list[ColumnProfile]

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "row_count": self.row_count,
            "columns": [c.to_dict() for c in self.columns],
        }


def profile_table(adapter: Adapter, table: str, config: ProfileConfig | None = None) -> TableProfile:
    config = config or ProfileConfig()
    schema = adapter.get_schema(table)
    qt = quote_ident(table)

    row_count = adapter.run_aggregate_query(f"SELECT COUNT(*) AS n FROM {qt}")[0]["n"]

    columns = [
        _profile_column(adapter, table, name, type_name, row_count, config) for name, type_name in schema.items()
    ]
    return TableProfile(table=table, row_count=row_count, columns=columns)


def _profile_column(
    adapter: Adapter, table: str, name: str, type_name: str, row_count: int, config: ProfileConfig
) -> ColumnProfile:
    qt = quote_ident(table)
    qc = quote_ident(name)

    base = adapter.run_aggregate_query(
        f"SELECT COUNT(*) - COUNT({qc}) AS null_count, COUNT(DISTINCT {qc}) AS distinct_count FROM {qt}"
    )[0]
    null_count = base["null_count"]
    distinct_count = base["distinct_count"]
    null_rate = (null_count / row_count) if row_count else 0.0
    cardinality_ratio = (distinct_count / row_count) if row_count else 0.0

    profile = ColumnProfile(
        name=name,
        type=type_name,
        null_count=null_count,
        null_rate=null_rate,
        distinct_count=distinct_count,
        cardinality_ratio=cardinality_ratio,
    )

    if is_numeric_type(type_name) and _numeric_stats_allowed(row_count, distinct_count, config):
        # The standard ordered-set aggregate syntax (not DuckDB's
        # QUANTILE_CONT(col, frac) shorthand) -- both DuckDB and Postgres
        # support this form, so profile_table works unmodified across adapters.
        # The min_extreme_count check runs in SQL, so a withheld extreme never
        # leaves the database.
        stats = adapter.run_aggregate_query(
            f"WITH v AS (SELECT {qc} AS x FROM {qt} WHERE {qc} IS NOT NULL), "
            f"e AS (SELECT MIN(x) AS lo, MAX(x) AS hi FROM v), "
            f"c AS (SELECT SUM(CASE WHEN v.x = e.lo THEN 1 ELSE 0 END) AS lo_n, "
            f"SUM(CASE WHEN v.x = e.hi THEN 1 ELSE 0 END) AS hi_n FROM v CROSS JOIN e), "
            f"s AS (SELECT AVG(x) AS mean, STDDEV(x) AS stddev, "
            f"PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY x) AS p25, "
            f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY x) AS p50, "
            f"PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY x) AS p75 FROM v) "
            f"SELECT CASE WHEN c.lo_n >= {config.min_extreme_count} THEN e.lo END AS min, "
            f"CASE WHEN c.hi_n >= {config.min_extreme_count} THEN e.hi END AS max, "
            f"s.mean, s.stddev, s.p25, s.p50, s.p75 FROM e CROSS JOIN c CROSS JOIN s"
        )[0]
        profile.min = stats["min"]
        profile.max = stats["max"]
        profile.mean = stats["mean"]
        profile.stddev = stats["stddev"]
        profile.p25 = stats["p25"]
        profile.p50 = stats["p50"]
        profile.p75 = stats["p75"]

    elif is_string_type(type_name):
        length_stats = adapter.run_aggregate_query(
            f"SELECT MIN(LENGTH({qc})) AS min_length, MAX(LENGTH({qc})) AS max_length, "
            f"AVG(LENGTH({qc})) AS avg_length FROM {qt} WHERE {qc} IS NOT NULL"
        )[0]
        profile.min_length = length_stats["min_length"]
        profile.max_length = length_stats["max_length"]
        profile.avg_length = length_stats["avg_length"]
        profile.format_signatures = _format_signatures(adapter, table, name, row_count - null_count, config)

    return profile


def _numeric_stats_allowed(row_count: int, distinct_count: int, config: ProfileConfig) -> bool:
    return row_count >= config.min_rows_for_numeric_stats and distinct_count >= config.min_distinct_for_numeric_stats


def _format_signatures(
    adapter: Adapter, table: str, column: str, non_null_count: int, config: ProfileConfig
) -> list[FormatSignature]:
    qt = quote_ident(table)
    qc = quote_ident(column)
    value = f"CAST({qc} AS VARCHAR)"
    # Folding order matters: letters become 'a' before digits become 'd',
    # otherwise the 'd' placeholders are themselves folded to 'a'. Anything
    # outside printable ASCII (accents, non-Latin scripts) also folds to 'a'.
    generalized = (
        f"regexp_replace(regexp_replace(regexp_replace({value}, '[^ -~]', 'a', 'g'), "
        f"'[A-Za-z]', 'a', 'g'), '[0-9]', 'd', 'g')"
    )
    # Anchored on purpose: DuckDB's ~ is a full-string match while Postgres's
    # is a substring match, so only anchored patterns behave the same on both.
    signature_expr = f"CASE WHEN {generalized} ~ '^[^ad]*$' THEN '{NO_ALPHANUMERIC_SIGNATURE}' ELSE {generalized} END"
    # HAVING drops rare signatures inside the database, so they never reach
    # this process. ORDER BY signature makes ties deterministic.
    rows = adapter.run_aggregate_query(
        f"SELECT {signature_expr} AS signature, COUNT(*) AS n FROM {qt} "
        f"WHERE {qc} IS NOT NULL GROUP BY signature HAVING COUNT(*) >= {config.min_signature_count} "
        f"ORDER BY n DESC, signature LIMIT {config.max_format_signatures}"
    )
    signatures = [FormatSignature(signature=r["signature"], count=r["n"]) for r in rows]
    # Values with a rare signature, or beyond max_format_signatures.
    other_count = non_null_count - sum(s.count for s in signatures)
    if other_count > 0:
        signatures.append(FormatSignature(signature=OTHER_SIGNATURES, count=other_count))
    return signatures
