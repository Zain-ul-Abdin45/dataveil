"""Aggregate-only profiling.

Every query built here is an aggregate: COUNT/MIN/MAX/AVG/STDDEV/quantiles,
or a GROUP BY over a *generalized* format signature (digits/letters folded
down to 'd'/'a' inside SQL, before anything comes back to this process) —
never a raw value, never a sample of rows. See PLAN.md's "aggregate-only"
rule and the adapter contract in adapter.py.

Low-cardinality columns (booleans, small enums) are deliberately NOT given
an exception to enumerate their literal values here, even though the whole
domain of a 2-3 value column arguably isn't a leak — the structural
guarantee ("no literal cell value ever appears in a profile") is kept
absolute for v1. See PLAN.md's open questions.
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

    if is_numeric_type(type_name):
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


def _format_signatures(adapter: Adapter, table: str, column: str) -> list[FormatSignature]:
    qt = quote_ident(table)
    qc = quote_ident(column)
    signature_expr = f"regexp_replace(regexp_replace(CAST({qc} AS VARCHAR), '[0-9]', 'd', 'g'), '[A-Za-z]', 'a', 'g')"
    rows = adapter.run_aggregate_query(
        f"SELECT {signature_expr} AS signature, COUNT(*) AS n FROM {qt} "
        f"WHERE {qc} IS NOT NULL GROUP BY signature ORDER BY n DESC LIMIT {MAX_FORMAT_SIGNATURES}"
    )
    return [FormatSignature(signature=r["signature"], count=r["n"]) for r in rows]
