"""Read dataveil's settings from a ``dataveil.toml`` file.

The file is found in this order:

1. the ``path`` argument of :func:`load_config`,
2. the ``DATAVEIL_CONFIG`` environment variable,
3. ``./dataveil.toml``.

A file named by (1) or (2) must exist. Without either, a missing
``./dataveil.toml`` means the defaults. Every section is optional::

    [profile]
    min_signature_count = 10          # can only be raised (default 5)
    min_extreme_count = 10            # can only be raised (default 5)
    min_rows_for_numeric_stats = 50   # can only be raised (default 10)
    min_distinct_for_numeric_stats = 5  # can only be raised (default 3)
    max_format_signatures = 10        # can only be lowered (default 20)

    [classify]
    match_threshold = 0.9             # default 0.8
    name_hint_threshold = 0.6         # default 0.5
    ner_max_distinct_values = 5000    # default 10000
    disabled = ["PII:PHONE"]          # built-in classifiers to skip

    [[classify.custom]]               # extra regex classifiers, any number
    tag = "PII:CUSTOMER_ID"
    pattern = "^CUS-[0-9]{8}$"        # must be anchored with ^ and $
    name_hints = ["customer_id"]      # optional; lowers the bar to name_hint_threshold

    [adapter]
    kind = "duckdb"                   # "duckdb", "postgres", "sqlmesh" or a plugin
    duckdb_path = "warehouse.duckdb"
    postgres_url = "postgresql+psycopg2://localhost/analytics"
    sqlmesh_project_path = "path/to/project"

    [adapter.options]                 # for an adapter plugin, passed to its factory
    account = "acme"

    [audit]
    log_path = ".dataveil/audit.jsonl"

Unknown sections and keys are errors, so a typo is not silently ignored.
Environment variables such as ``DATAVEIL_ADAPTER`` still work and take
precedence over ``[adapter]``. Keep passwords out of the file: put a
``postgres_url`` with credentials in ``DATAVEIL_POSTGRES_URL`` instead.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path
from typing import Any

from .core.adapter import adapter_names
from .core.classify import classifiers_for, registered_classifiers
from .core.config import ClassifyConfig, ConfigError, CustomClassifier, ProfileConfig

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the Python 3.10 CI job
    import tomli as tomllib

__all__ = ["AdapterConfig", "Config", "ConfigError", "DEFAULT_CONFIG_PATH", "load_config", "parse_config"]

DEFAULT_CONFIG_PATH = Path("dataveil.toml")


@dataclasses.dataclass(frozen=True)
class AdapterConfig:
    kind: str | None = None
    duckdb_path: str | None = None
    postgres_url: str | None = None
    sqlmesh_project_path: str | None = None
    # Passed to a plugin adapter's factory; see dataveil.core.adapter.
    options: dict[str, str] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            if field.name != "options" and value is not None and not isinstance(value, str):
                raise ConfigError(f"[adapter] {field.name} must be a string, got {value!r}")
        if not isinstance(self.options, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in self.options.items()
        ):
            raise ConfigError(f"[adapter.options] values must be strings, got {self.options!r}")


@dataclasses.dataclass(frozen=True)
class AuditConfig:
    log_path: str | None = None

    def __post_init__(self) -> None:
        if self.log_path is not None and not isinstance(self.log_path, str):
            raise ConfigError(f"[audit] log_path must be a string, got {self.log_path!r}")


@dataclasses.dataclass(frozen=True)
class Config:
    profile: ProfileConfig = dataclasses.field(default_factory=ProfileConfig)
    classify: ClassifyConfig = dataclasses.field(default_factory=ClassifyConfig)
    adapter: AdapterConfig = dataclasses.field(default_factory=AdapterConfig)
    audit: AuditConfig = dataclasses.field(default_factory=AuditConfig)


_SECTIONS: dict[str, type[Any]] = {
    "profile": ProfileConfig,
    "classify": ClassifyConfig,
    "adapter": AdapterConfig,
    "audit": AuditConfig,
}


def _section(name: str, cls: type[Any], data: object) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"[{name}] must be a table")
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"[{name}] unknown key(s): {', '.join(unknown)} (known: {', '.join(sorted(known))})")
    values = dict(data)
    if name == "classify" and "disabled" in values:
        disabled = values["disabled"]
        if not isinstance(disabled, list) or not all(isinstance(t, str) for t in disabled):
            raise ConfigError(f"[classify] disabled must be a list of tags, got {disabled!r}")
        tags = {c.tag for c in registered_classifiers()}
        unknown_tags = sorted(set(disabled) - tags)
        if unknown_tags:
            raise ConfigError(
                f"[classify] disabled has unknown tag(s): {', '.join(unknown_tags)} (known: {', '.join(sorted(tags))})"
            )
        values["disabled"] = tuple(disabled)
    if name == "adapter" and values.get("kind") is not None:
        names = adapter_names()
        if values["kind"] not in names:
            raise ConfigError(f"[adapter] kind must be one of {', '.join(names)}, got {values['kind']!r}")
    if name == "classify" and "custom" in values:
        values["custom"] = _custom_classifiers(values["custom"])
        classifiers_for(cls(**values))  # rejects a custom tag that clashes with a built-in one
    return cls(**values)


def _custom_classifiers(data: object) -> tuple[CustomClassifier, ...]:
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ConfigError("[classify] custom must be an array of tables ([[classify.custom]])")
    known = {f.name for f in dataclasses.fields(CustomClassifier)}
    result = []
    for item in data:
        unknown = sorted(set(item) - known)
        if unknown:
            raise ConfigError(
                f"[[classify.custom]] unknown key(s): {', '.join(unknown)} (known: {', '.join(sorted(known))})"
            )
        missing = sorted({"tag", "pattern"} - set(item))
        if missing:
            raise ConfigError(f"[[classify.custom]] missing key(s): {', '.join(missing)}")
        hints = item.get("name_hints", [])
        if not isinstance(hints, list):
            raise ConfigError(f"[[classify.custom]] {item['tag']}: name_hints must be a list of strings")
        result.append(CustomClassifier(tag=item["tag"], pattern=item["pattern"], name_hints=tuple(hints)))
    return tuple(result)


def parse_config(data: dict[str, Any]) -> Config:
    """Build a :class:`Config` from already-parsed TOML data."""
    unknown = sorted(set(data) - set(_SECTIONS))
    if unknown:
        raise ConfigError(f"unknown section(s): {', '.join(unknown)} (known: {', '.join(_SECTIONS)})")
    return Config(**{name: _section(name, cls, data[name]) for name, cls in _SECTIONS.items() if name in data})


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load the settings file; see the module docstring for where it is looked up."""
    if path is None and os.environ.get("DATAVEIL_CONFIG"):
        path = os.environ["DATAVEIL_CONFIG"]
    if path is None:
        if not DEFAULT_CONFIG_PATH.is_file():
            return Config()
        path = DEFAULT_CONFIG_PATH
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")
    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{config_path} is not valid TOML: {e}") from e
    try:
        return parse_config(data)
    except ConfigError as e:
        raise ConfigError(f"{config_path}: {e}") from e
