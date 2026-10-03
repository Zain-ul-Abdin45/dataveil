"""Shared SQL-identifier quoting for core/ and adapters/.

Table identifiers reaching ``quote_ident`` always come from
``adapter.list_tables()``/``get_schema()`` or an already-validated
``PlanStep`` -- never from free-form text -- so quoting (not escaping
arbitrary input) is the only concern here. Table names may be dotted paths
(e.g. a SQLMesh model's ``schema.model``), so each dot-separated part is
quoted on its own rather than the whole string being treated as one
identifier.
"""

from __future__ import annotations


def quote_ident(name: str) -> str:
    return ".".join('"' + part.replace('"', '""') + '"' for part in name.split("."))
