"""Factories for the built-in adapters, registered under the
``dataveil.adapters`` entry point group like any adapter plugin.

Each takes string options and imports its backend only when called, so
listing the adapters needs none of the optional extras.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..core.adapter import Adapter, AdapterNotConfiguredError


def _require(options: Mapping[str, str], key: str, kind: str) -> str:
    value = options.get(key)
    if not value:
        raise AdapterNotConfiguredError(f"the {kind} adapter needs the {key!r} option")
    return value


def duckdb_adapter(options: Mapping[str, str]) -> Adapter:
    """Options: ``path``, a DuckDB file or ``:memory:`` (default)."""
    import duckdb

    from .duckdb import DuckDBAdapter

    return DuckDBAdapter(duckdb.connect(options.get("path") or ":memory:"))


def postgres_adapter(options: Mapping[str, str]) -> Adapter:
    """Options: ``url``, a SQLAlchemy URL (required)."""
    import sqlalchemy as sa

    from .postgres import PostgresAdapter

    return PostgresAdapter(sa.create_engine(_require(options, "url", "postgres")))


def sqlmesh_adapter(options: Mapping[str, str]) -> Adapter:
    """Options: ``project_path``, a SQLMesh project directory (required)."""
    from sqlmesh.core.context import Context

    from .sqlmesh import SQLMeshAdapter

    return SQLMeshAdapter(Context(paths=_require(options, "project_path", "sqlmesh")))
