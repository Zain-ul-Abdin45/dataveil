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

or the [adapter] section of a dataveil.toml (see dataveil/config.py). The
environment variables take precedence over the file. The file's [profile]
and [classify] thresholds apply to every tool call.

Every tool here (except list_tables) matches sqlmesh-mcp's
profile_model/propose_cleansing_plan/apply_cleansing_plan one for one, just
against `table` instead of `model_name` -- this server has no SQLMesh
Context, virtual layer, or physical-snapshot concept, only whatever the
configured Adapter exposes.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .audit import AuditLog
from .config import Config, load_config
from .core.adapter import Adapter
from .core.classify import classify_table
from .core.execute import ExecutionError, execute_plan
from .core.plan import PlanValidationError, operation_vocabulary
from .core.profile import profile_table

P = ParamSpec("P")
R = TypeVar("R")

server = MCPServer("dataveil")

# Tools that return dict[str, Any] pass structured_output=False: without it the
# SDK derives an output schema from the annotation and changes what clients receive.


class AdapterNotConfiguredError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()


def _setting(env_var: str, from_file: str | None) -> str | None:
    return os.environ.get(env_var) or from_file


@lru_cache(maxsize=1)
def get_adapter() -> Adapter:
    settings = get_config().adapter
    kind = _setting("DATAVEIL_ADAPTER", settings.kind)

    if kind == "duckdb":
        import duckdb

        from .adapters.duckdb import DuckDBAdapter

        path = _setting("DATAVEIL_DUCKDB_PATH", settings.duckdb_path) or ":memory:"
        return DuckDBAdapter(duckdb.connect(path))

    if kind == "postgres":
        import sqlalchemy as sa

        from .adapters.postgres import PostgresAdapter

        url = _setting("DATAVEIL_POSTGRES_URL", settings.postgres_url)
        if not url:
            raise AdapterNotConfiguredError("DATAVEIL_POSTGRES_URL is not set (nor [adapter] postgres_url)")
        return PostgresAdapter(sa.create_engine(url))

    if kind == "sqlmesh":
        from sqlmesh.core.context import Context

        from .adapters.sqlmesh import SQLMeshAdapter

        project_path = _setting("SQLMESH_PROJECT_PATH", settings.sqlmesh_project_path)
        if not project_path:
            raise AdapterNotConfiguredError("SQLMESH_PROJECT_PATH is not set (nor [adapter] sqlmesh_project_path)")
        return SQLMeshAdapter(Context(paths=project_path))

    raise AdapterNotConfiguredError(
        "DATAVEIL_ADAPTER is not set (or is unrecognized), nor [adapter] kind in dataveil.toml -- "
        "must be one of 'duckdb', 'postgres', 'sqlmesh'"
    )


def _audit_log() -> AuditLog:
    default_path = Path(".dataveil") / "audit.jsonl"
    return AuditLog(_setting("DATAVEIL_AUDIT_LOG_PATH", get_config().audit.log_path) or str(default_path))


def _translate_errors(fn: Callable[P, R]) -> Callable[P, R]:
    """Without this, the MCP SDK replaces any exception that isn't a
    ToolError with the generic "Error executing tool <name>", dropping the
    real message. PlanValidationError/ExecutionError are the operation
    vocabulary/confirm=true errors; ValueError/AdapterNotConfiguredError
    cover an adapter's own intentional errors (e.g. "apply a plan first",
    "DATAVEIL_POSTGRES_URL is not set").
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
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


@server.tool(annotations=ToolAnnotations(read_only_hint=True), structured_output=False)
@_translate_errors
def profile(table: str) -> dict[str, Any]:
    """Aggregate-only profile of a table's columns: null rates, distinct
    counts, numeric stats, generalized string format signatures (e.g.
    'ddd-dd-dddd'). Returns no raw rows or samples; numeric stats are withheld
    for small or constant columns. See README's Known limits.
    """
    adapter = get_adapter()
    result = profile_table(adapter, table, get_config().profile).to_dict()
    _audit_log().record("profile", table, {"row_count": result["row_count"]})
    return result


@server.tool(annotations=ToolAnnotations(read_only_hint=True), structured_output=False)
@_translate_errors
def propose_cleansing_plan(table: str) -> dict[str, Any]:
    """Everything an agent needs to propose a data-cleansing plan for a
    table: the aggregate-only profile, a local sensitivity classification
    per column (PII:EMAIL, PII:SSN, PII:PHONE, ..., a custom tag from
    dataveil.toml, or "none", each with a match_rate), and the closed operation vocabulary apply_cleansing_plan
    will accept.

    This tool does not call an LLM or propose a plan itself -- it's the
    documented contract an agent (you) reasons over to build one. A plan is
    a list of {"operation", "column", "params", "rationale"} objects;
    "operation" must be a key in operation_vocabulary, and only the params
    listed for it are accepted. Pass the finished plan to
    apply_cleansing_plan.
    """
    adapter = get_adapter()
    config = get_config()
    profile_result = profile_table(adapter, table, config.profile)
    classifications = classify_table(adapter, table, config.classify)
    _audit_log().record("classify", table, {"tags": {c.column: c.tag for c in classifications}})
    return {
        "table": table,
        "profile": profile_result.to_dict(),
        "classifications": [c.to_dict() for c in classifications],
        "operation_vocabulary": operation_vocabulary(),
    }


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True), structured_output=False)
@_translate_errors
def apply_cleansing_plan(table: str, plan: list[dict[str, Any]], confirm: bool = False) -> dict[str, Any]:
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
