"""Abstract adapter interface.

An adapter is the only thing in this project allowed to touch real rows.
Everything in ``core/`` is adapter-agnostic: it builds SQL strings and reads
back aggregates, but never opens a connection to a data source itself.

Contract an adapter must uphold, not just implement:

- ``run_aggregate_query`` must never return a literal cell value unless the
  query was deliberately written to aggregate (``COUNT``, ``MIN``, ``MAX``,
  ``AVG``, ``STDDEV``, percentile functions, ``GROUP BY`` over a *generalized*
  expression, etc). The core modules only ever send aggregate queries, but the
  adapter is the last line of defense if that ever slips.
- Column/table identifiers passed to ``run_aggregate_query`` and
  ``execute_operation`` come from ``get_schema``/``list_tables`` or from a
  validated :class:`dataveil.core.plan.PlanStep`, never from free-form user
  text, so adapters may safely interpolate them into SQL after quoting.
- Checksum-based classifiers (credit card, IBAN) need a per-value predicate
  evaluated inside the adapter's own engine so raw values never cross into
  ``core/classify.py``. An adapter that wants those classifiers enabled must
  register two boolean SQL functions reachable from
  ``run_aggregate_query``-issued SQL:

  - ``dataveil_luhn_valid(value TEXT) -> BOOLEAN``
  - ``dataveil_iban_valid(value TEXT) -> BOOLEAN``

  An adapter that does not register them simply won't get those two
  classifiers considered (see ``classify.py``'s capability check).
- The free-text name classifier works the same way, with one optional SQL
  function: ``dataveil_ner_label(value TEXT) -> TEXT``, returning a named
  entity label such as ``'PERSON'``, or ``''``. An adapter that registers it
  returns ``True`` from ``has_ner_function()``.
"""

from __future__ import annotations

import abc
from collections.abc import Callable, Mapping
from importlib.metadata import EntryPoint, entry_points
from typing import Any

from .config import PluginError


class Adapter(abc.ABC):
    """What a data source has to implement to plug into dataveil."""

    @abc.abstractmethod
    def run_aggregate_query(self, sql: str) -> list[dict[str, Any]]:
        """Run a SQL query and return rows as a list of column->value dicts.

        The caller (core/profile.py, core/classify.py) is responsible for
        only ever constructing aggregate SQL here. The adapter is free to
        add its own guardrails (e.g. rejecting SQL that returns more than a
        handful of rows) but is not required to parse the SQL.
        """

    @abc.abstractmethod
    def list_tables(self) -> list[str]:
        """Return the names of tables/models this adapter can see."""

    @abc.abstractmethod
    def get_schema(self, table: str) -> dict[str, str]:
        """Return {column_name: type_name} for a table."""

    @abc.abstractmethod
    def has_checksum_functions(self) -> bool:
        """Whether dataveil_luhn_valid/dataveil_iban_valid are registered."""

    def has_ner_function(self) -> bool:
        """Whether dataveil_ner_label is registered. Optional: False by default."""
        return False

    @abc.abstractmethod
    def execute_operation(
        self, table: str, operation: str, column: str | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute one whitelisted operation against real data.

        ``operation``/``params`` have already been validated against the
        vocabulary in core/plan.py before this is called. Returns a small
        result dict (e.g. {"rows_affected": n}) suitable for logging.
        """


# --- plugin registry --------------------------------------------------------
#
# An adapter plugin registers a factory under the ``dataveil.adapters`` entry
# point group. The entry point's name is the adapter kind (DATAVEIL_ADAPTER or
# ``[adapter] kind``), and the factory takes the string options from
# ``[adapter.options]`` and returns an Adapter. The built-in adapters are
# registered the same way.

ADAPTER_ENTRY_POINT_GROUP = "dataveil.adapters"

AdapterFactory = Callable[[Mapping[str, str]], Adapter]

BUILTIN_ADAPTERS = {
    "duckdb": "dataveil.adapters.factories:duckdb_adapter",
    "postgres": "dataveil.adapters.factories:postgres_adapter",
    "sqlmesh": "dataveil.adapters.factories:sqlmesh_adapter",
}


class AdapterNotConfiguredError(RuntimeError):
    pass


def _adapter_entry_points() -> dict[str, EntryPoint]:
    found: dict[str, EntryPoint] = {}
    for ep in entry_points(group=ADAPTER_ENTRY_POINT_GROUP):
        if ep.name in BUILTIN_ADAPTERS:
            if ep.value != BUILTIN_ADAPTERS[ep.name]:
                raise PluginError(f"adapter plugin {ep.value!r} uses the built-in name {ep.name!r}")
            continue
        if ep.name in found and found[ep.name].value != ep.value:
            raise PluginError(f"two adapter plugins use the name {ep.name!r}: {found[ep.name].value}, {ep.value}")
        found[ep.name] = ep
    return found


def adapter_names() -> list[str]:
    """Every adapter kind that can be selected: built-in first, then plugins."""
    return [*BUILTIN_ADAPTERS, *sorted(_adapter_entry_points())]


def load_adapter_factory(name: str) -> AdapterFactory:
    """The factory for an adapter kind; ``AdapterNotConfiguredError`` if none is registered."""
    if name in BUILTIN_ADAPTERS:
        value = BUILTIN_ADAPTERS[name]
    else:
        plugins = _adapter_entry_points()
        if name not in plugins:
            raise AdapterNotConfiguredError(
                f"no adapter named {name!r}; must be one of {', '.join(repr(n) for n in adapter_names())}"
            )
        value = plugins[name].value
    try:
        factory = EntryPoint(name=name, value=value, group=ADAPTER_ENTRY_POINT_GROUP).load()
    except Exception as e:
        raise PluginError(f"adapter plugin {name!r} ({value}) failed to load: {e}") from e
    if not callable(factory):
        raise PluginError(f"adapter plugin {name!r} ({value}) is not callable")
    return checked_factory(name, factory)


def checked_factory(name: str, factory: Callable[[Mapping[str, str]], object]) -> AdapterFactory:
    """Wrap a factory so that what it returns must be an Adapter."""

    def build(options: Mapping[str, str]) -> Adapter:
        adapter = factory(options)
        if not isinstance(adapter, Adapter):
            raise PluginError(f"adapter plugin {name!r} returned {type(adapter).__name__}, not an Adapter")
        return adapter

    return build
