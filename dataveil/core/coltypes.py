"""Shared, adapter-reported-type classification used by core/profile.py and
core/classify.py: is a column's reported SQL type numeric or string-like.

Neither module applies its string/numeric-only logic (format signatures,
regex classifiers) to a date, boolean, or other non-string type -- casting
e.g. a DATE to VARCHAR for a regex match can produce false positives (a date
like '2020-01-01' matches a phone-number pattern) that have nothing to do
with the column's actual content.
"""

from __future__ import annotations

_NUMERIC_TYPE_MARKERS = (
    "INT",
    "DECIMAL",
    "NUMERIC",
    "FLOAT",
    "DOUBLE",
    "REAL",
    "HUGEINT",
)
_STRING_TYPE_MARKERS = ("VARCHAR", "TEXT", "CHAR", "STRING", "BLOB", "UUID")


def is_numeric_type(type_name: str) -> bool:
    upper = type_name.upper()
    return any(marker in upper for marker in _NUMERIC_TYPE_MARKERS)


def is_string_type(type_name: str) -> bool:
    upper = type_name.upper()
    return any(marker in upper for marker in _STRING_TYPE_MARKERS)
