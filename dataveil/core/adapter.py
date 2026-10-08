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
from typing import Any


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
