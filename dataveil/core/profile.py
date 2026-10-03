"""Aggregate-only profiling.

Every query built here is an aggregate: COUNT/MIN/MAX/AVG/STDDEV/quantiles,
or a GROUP BY over a *generalized* format signature (letters folded to 'a',
digits to 'd', inside SQL before anything comes back to this process). A
signature is never a raw value and no sample of rows is taken.

This is not a guarantee that no value can be inferred. Numeric extremes
(min/max) are literal cell values, so they are suppressed for small or
constant columns below; larger tables still return them. See README's
"Known limits".
"""

from __future__ import annotations

import dataclasses
from typing import Any

from .adapter import Adapter
from .coltypes import is_numeric_type, is_string_type
from .sql import quote_ident

# Bound on how many distinct generalized format signatures we return per
# string column — a format signature is not a raw value, but an unbounded
# list of them for a high-cardinality column is still unnecessary detail.
MAX_FORMAT_SIGNATURES = 20

# Numeric stats are withheld unless the column has enough rows and enough
# distinct values that no single row is trivially recoverable from them.
# Conservative heuristics, not a privacy guarantee.
MIN_ROWS_FOR_NUMERIC_STATS = 10
MIN_DISTINCT_FOR_NUMERIC_STATS = 3

NO_ALPHANUMERIC_SIGNATURE = "<punctuation or whitespace only>"


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


def profile_table(adapter: Adapter, table: str) -> TableProfile:
    schema = adapter.get_schema(table)
    qt = quote_ident(table)

    row_count = adapter.run_aggregate_query(f"SELECT COUNT(*) AS n FROM {qt}")[0]["n"]

    columns = [_profile_column(adapter, table, name, type_name, row_count) for name, type_name in schema.items()]
    return TableProfile(table=table, row_count=row_count, columns=columns)


def _profile_column(adapter: Adapter, table: str, name: str, type_name: str, row_count: int) -> ColumnProfile:
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

    if is_numeric_type(type_name) and _numeric_stats_allowed(row_count, distinct_count):
        # The standard ordered-set aggregate syntax (not DuckDB's
        # QUANTILE_CONT(col, frac) shorthand) -- both DuckDB and Postgres
        # support this form, so profile_table works unmodified across adapters.
        stats = adapter.run_aggregate_query(
            f"SELECT MIN({qc}) AS min, MAX({qc}) AS max, AVG({qc}) AS mean, STDDEV({qc}) AS stddev, "
            f"PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {qc}) AS p25, "
            f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {qc}) AS p50, "
            f"PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {qc}) AS p75 "
            f"FROM {qt} WHERE {qc} IS NOT NULL"
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
        profile.format_signatures = _format_signatures(adapter, table, name)

    return profile


def _numeric_stats_allowed(row_count: int, distinct_count: int) -> bool:
    return row_count >= MIN_ROWS_FOR_NUMERIC_STATS and distinct_count >= MIN_DISTINCT_FOR_NUMERIC_STATS


def _format_signatures(adapter: Adapter, table: str, column: str) -> list[FormatSignature]:
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
    rows = adapter.run_aggregate_query(
        f"SELECT {signature_expr} AS signature, COUNT(*) AS n FROM {qt} "
        f"WHERE {qc} IS NOT NULL GROUP BY signature ORDER BY n DESC LIMIT {MAX_FORMAT_SIGNATURES}"
    )
    return [FormatSignature(signature=r["signature"], count=r["n"]) for r in rows]
