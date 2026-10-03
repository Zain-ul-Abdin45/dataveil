"""dataveil's own standalone MCP server: profile, classify, and cleanse
against whichever adapter is configured -- usable outside SQLMesh entirely,
not just via sqlmesh-mcp (PLAN.md Phase 6, built once there was more than
one adapter to prove this wasn't SQLMesh-specific).

Configure via DATAVEIL_ADAPTER (one of "duckdb", "postgres", "sqlmesh") plus
that adapter's own setting:

  - duckdb:   DATAVEIL_DUCKDB_PATH -- a file path, or ":memory:" (default)
  - postgres: DATAVEIL_POSTGRES_URL -- a SQLAlchemy URL (required)
  - sqlmesh:  SQLMESH_PROJECT_PATH -- a directory with config.yaml/models/
              (required; same env var sqlmesh-mcp uses, for anyone running
              both against the same project)

Every tool here (except list_tables) matches sqlmesh-mcp's
profile_model/propose_cleansing_plan/apply_cleansing_plan one for one, just
against `table` instead of `model_name` -- this server has no SQLMesh
Context, virtual layer, or physical-snapshot concept, only whatever the
configured Adapter exposes.
"""

from __future__ import annotations

import functools
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .audit import AuditLog
from .core.adapter import Adapter
from .core.classify import classify_table
from .core.execute import ExecutionError, execute_plan
from .core.plan import PlanValidationError, operation_vocabulary
from .core.profile import profile_table

server = MCPServer("dataveil")


class AdapterNotConfiguredError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_adapter() -> Adapter:
    kind = os.environ.get("DATAVEIL_ADAPTER")

    if kind == "duckdb":
        import duckdb

        from .adapters.duckdb import DuckDBAdapter

        path = os.environ.get("DATAVEIL_DUCKDB_PATH", ":memory:")
        return DuckDBAdapter(duckdb.connect(path))

    if kind == "postgres":
        import sqlalchemy as sa

        from .adapters.postgres import PostgresAdapter

        url = os.environ.get("DATAVEIL_POSTGRES_URL")
        if not url:
            raise AdapterNotConfiguredError("DATAVEIL_POSTGRES_URL is not set")
        return PostgresAdapter(sa.create_engine(url))

    if kind == "sqlmesh":
        from sqlmesh.core.context import Context

        from .adapters.sqlmesh import SQLMeshAdapter

        path = os.environ.get("SQLMESH_PROJECT_PATH")
        if not path:
            raise AdapterNotConfiguredError("SQLMESH_PROJECT_PATH is not set")
        return SQLMeshAdapter(Context(paths=path))

    raise AdapterNotConfiguredError(
        "DATAVEIL_ADAPTER is not set (or is unrecognized) -- must be one of "
        "'duckdb', 'postgres', 'sqlmesh'"
    )


def _audit_log() -> AuditLog:
    default_path = Path(".dataveil") / "audit.jsonl"
    return AuditLog(os.environ.get("DATAVEIL_AUDIT_LOG_PATH", str(default_path)))


def _translate_errors(fn):
    """Without this, the MCP SDK replaces any exception that isn't a
    ToolError with the generic "Error executing tool <name>", dropping the
    real message. PlanValidationError/ExecutionError are the operation
    vocabulary/confirm=true errors; ValueError/AdapterNotConfiguredError
    cover an adapter's own intentional errors (e.g. "apply a plan first",
    "DATAVEIL_POSTGRES_URL is not set").
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (PlanValidationError, ExecutionError, ValueError, AdapterNotConfiguredError) as e:
            raise ToolError(str(e)) from e

    return wrapper


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
@_translate_errors
def list_tables() -> list[str]:
    """List every table/model the configured adapter can see."""
    return get_adapter().list_tables()


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
@_translate_errors
def profile(table: str) -> dict:
    """Aggregate-only profile of a table's columns: null rates, distinct
    counts, numeric stats, generalized string format signatures (e.g.
    'ddd-dd-dddd'). Never returns a literal cell value.
    """
    adapter = get_adapter()
    result = profile_table(adapter, table).to_dict()
    _audit_log().record("profile", table, {"row_count": result["row_count"]})
    return result


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
@_translate_errors
def propose_cleansing_plan(table: str) -> dict:
    """Everything an agent needs to propose a data-cleansing plan for a
    table: the aggregate-only profile, a local sensitivity classification
    per column (PII:EMAIL, PII:SSN, PII:PHONE, ... or "none", each with a
    match_rate), and the closed operation vocabulary apply_cleansing_plan
    will accept.

    This tool does not call an LLM or propose a plan itself -- it's the
    documented contract an agent (you) reasons over to build one. A plan is
    a list of {"operation", "column", "params", "rationale"} objects;
    "operation" must be a key in operation_vocabulary, and only the params
    listed for it are accepted. Pass the finished plan to
    apply_cleansing_plan.
    """
    adapter = get_adapter()
    profile_result = profile_table(adapter, table)
    classifications = classify_table(adapter, table)
    _audit_log().record(
        "classify", table, {"tags": {c.column: c.tag for c in classifications}}
    )
    return {
        "table": table,
        "profile": profile_result.to_dict(),
        "classifications": [c.to_dict() for c in classifications],
        "operation_vocabulary": operation_vocabulary(),
    }


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
@_translate_errors
def apply_cleansing_plan(table: str, plan: list[dict], confirm: bool = False) -> dict:
    """Validate and apply a data-cleansing plan built from
    propose_cleansing_plan's output. THIS CHANGES REAL DATA via the
    configured adapter. Requires confirm=true.

    The whole plan is validated against the operation vocabulary and the
    table's schema before any step runs -- a plan with one bad step applies
    nothing, not just the steps before it.
    """
    if not confirm:
        raise ToolError("Refusing to apply without confirm=true -- this changes real data.")
    adapter = get_adapter()
    results = execute_plan(adapter, table, plan, confirm=True)
    _audit_log().record(
        "execute_plan",
        table,
        {"steps": [{"operation": r["operation"], "column": r["column"]} for r in results]},
    )
    return {"table": table, "applied_steps": results}


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
