"""Register Python functions on a DuckDB connection."""

from __future__ import annotations

from typing import Any


def replace_function(connection: Any, name: str, function: Any, return_type: type) -> None:
    """Register a one-argument string UDF, replacing an earlier registration.

    DuckDB refuses to register a name twice, which happens when a second
    adapter is created on the same connection (SQLMesh also reuses one
    connection for every Context on the same database).
    """
    import duckdb

    try:
        connection.remove_function(name)
    except duckdb.InvalidInputException:
        pass  # not registered yet
    connection.create_function(name, function, [str], return_type)
