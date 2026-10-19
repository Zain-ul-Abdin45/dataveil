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


class ConfigError(ValueError):
    pass


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
