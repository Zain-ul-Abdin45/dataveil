"""Settings for profiling and classification, with safe defaults.

The defaults are the values dataveil has always used. Privacy thresholds can
only be made stricter: a minimum count can be raised but not lowered, and
``max_format_signatures`` can be lowered but not raised. Anything else is
rejected with :class:`ConfigError` when the object is built, so a bad setting
fails before any query runs.

This module only defines the settings. Reading them from ``dataveil.toml``
lives in :mod:`dataveil.config`, so ``dataveil.core`` keeps importing only the
standard library.
"""

from __future__ import annotations

import dataclasses
import re


class ConfigError(ValueError):
    pass


class PluginError(ConfigError):
    """An installed adapter or classifier plugin is broken or conflicts with another."""


def _require_int(section: str, key: str, value: object) -> int:
    # bool is a subclass of int, but `min_signature_count = true` is a mistake.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"[{section}] {key} must be an integer, got {value!r}")
    return value


def _require_rate(section: str, key: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"[{section}] {key} must be a number, got {value!r}")
    if not 0.0 < value <= 1.0:
        raise ConfigError(f"[{section}] {key} must be greater than 0 and at most 1, got {value!r}")
    return float(value)


@dataclasses.dataclass(frozen=True)
class ProfileConfig:
    # A signature is only returned when at least this many non-null values have it.
    min_signature_count: int = 5
    # min/max are only returned when at least this many rows have that exact value.
    min_extreme_count: int = 5
    # Numeric stats are withheld below this many rows or distinct values.
    min_rows_for_numeric_stats: int = 10
    min_distinct_for_numeric_stats: int = 3
    # At most this many format signatures per string column.
    max_format_signatures: int = 20

    def __post_init__(self) -> None:
        defaults = ProfileConfig.__dataclass_fields__
        for key in (
            "min_signature_count",
            "min_extreme_count",
            "min_rows_for_numeric_stats",
            "min_distinct_for_numeric_stats",
        ):
            value = _require_int("profile", key, getattr(self, key))
            default = defaults[key].default
            if value < default:  # type: ignore[operator]
                raise ConfigError(
                    f"[profile] {key} = {value} is below the safe default of {default}; it can only be raised"
                )
        value = _require_int("profile", "max_format_signatures", self.max_format_signatures)
        default = defaults["max_format_signatures"].default
        if value > default:  # type: ignore[operator]
            raise ConfigError(
                f"[profile] max_format_signatures = {value} is above the safe default of {default}; "
                "it can only be lowered"
            )
        if value < 0:
            raise ConfigError(f"[profile] max_format_signatures must not be negative, got {value}")


_TAG_RE = re.compile(r"^[A-Za-z0-9_]+(:[A-Za-z0-9_]+)*$")

# Escapes that mean something different (or nothing) in one of the engines:
# \b is a word boundary in DuckDB (RE2) but a backspace in Postgres, and
# \A, \Z, \z and backreferences are not supported by both.
_NON_PORTABLE_ESCAPES = set("bBAZz123456789")


def check_portable_pattern(where: str, pattern: str, error: type[ConfigError] = ConfigError) -> None:
    """Reject a regex that is invalid, or would match differently on DuckDB
    and Postgres, before any query runs. ``where`` prefixes the message."""
    try:
        re.compile(pattern)
    except re.error as e:
        raise error(f"{where}: invalid pattern {pattern!r}: {e}") from e
    # DuckDB's ~ matches the whole string, Postgres's ~ any substring; an
    # anchored pattern means the same on both.
    # A $ after an odd number of backslashes is a literal dollar sign.
    trailing_backslashes = len(pattern[:-1]) - len(pattern[:-1].rstrip("\\"))
    if not pattern.startswith("^") or not pattern.endswith("$") or trailing_backslashes % 2:
        raise error(f"{where}: pattern must start with ^ and end with $, got {pattern!r}")
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            if pattern[i + 1] in _NON_PORTABLE_ESCAPES:
                raise error(f"{where}: \\{pattern[i + 1]} is not supported the same way on every engine")
            i += 2
            continue
        # Only non-capturing groups: lookarounds, named groups and inline
        # flags are missing from RE2 or Postgres, or spelled differently.
        if pattern.startswith("(?", i) and not pattern.startswith("(?:", i):
            raise error(f"{where}: only (?:...) groups are supported, not {pattern[i : i + 4]!r}...")
        i += 1


@dataclasses.dataclass(frozen=True)
class CustomClassifier:
    """A regex classifier defined in configuration, evaluated like the
    built-in ones: an aggregate COUNT of matching values, nothing else."""

    tag: str
    pattern: str
    name_hints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.tag, str) or not _TAG_RE.match(self.tag):
            raise ConfigError(
                f"[[classify.custom]] tag must look like PREFIX:NAME (letters, digits, _), got {self.tag!r}"
            )
        if self.tag.lower() == "none":
            raise ConfigError("[[classify.custom]] tag must not be 'none'")
        if not isinstance(self.pattern, str) or not self.pattern:
            raise ConfigError(f"[[classify.custom]] {self.tag}: pattern must be a non-empty string")
        check_portable_pattern(f"[[classify.custom]] {self.tag}", self.pattern)
        if not isinstance(self.name_hints, tuple) or not all(isinstance(h, str) and h for h in self.name_hints):
            raise ConfigError(f"[[classify.custom]] {self.tag}: name_hints must be a list of non-empty strings")
        # Column names are compared lower-cased.
        object.__setattr__(self, "name_hints", tuple(h.lower() for h in self.name_hints))


@dataclasses.dataclass(frozen=True)
class ClassifyConfig:
    # A column is tagged when at least this share of its non-null values match.
    match_threshold: float = 0.8
    # The lower bar used when the column name also hints at the tag.
    name_hint_threshold: float = 0.5
    # The NER classifier labels at most this many of the most frequent distinct values.
    ner_max_distinct_values: int = 10_000
    # Tags of built-in classifiers to skip, for example ("PII:PHONE",).
    disabled: tuple[str, ...] = ()
    # Extra regex classifiers, checked after the built-in ones.
    custom: tuple[CustomClassifier, ...] = ()

    def __post_init__(self) -> None:
        _require_rate("classify", "match_threshold", self.match_threshold)
        _require_rate("classify", "name_hint_threshold", self.name_hint_threshold)
        if self.name_hint_threshold > self.match_threshold:
            raise ConfigError(
                f"[classify] name_hint_threshold ({self.name_hint_threshold}) must not be above "
                f"match_threshold ({self.match_threshold})"
            )
        if _require_int("classify", "ner_max_distinct_values", self.ner_max_distinct_values) < 1:
            raise ConfigError(
                f"[classify] ner_max_distinct_values must be at least 1, got {self.ner_max_distinct_values}"
            )
        if not isinstance(self.disabled, tuple) or not all(isinstance(t, str) for t in self.disabled):
            raise ConfigError(f"[classify] disabled must be a list of tags, got {self.disabled!r}")
        if not isinstance(self.custom, tuple) or not all(isinstance(c, CustomClassifier) for c in self.custom):
            raise ConfigError("[classify] custom must be a list of custom classifiers")
        tags = [c.tag for c in self.custom]
        duplicates = sorted({t for t in tags if tags.count(t) > 1})
        if duplicates:
            raise ConfigError(f"[[classify.custom]] duplicate tag(s): {', '.join(duplicates)}")
