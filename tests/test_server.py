"""Direct function-call tests for dataveil's own standalone MCP server
(PLAN.md Phase 6), run against the DuckDB adapter -- fast, and proves the
server's own logic (adapter selection, confirm=true gate, audit logging) is
correct in isolation. See test_server_protocol.py for the real wire-protocol
version.
"""

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError


@pytest.fixture(autouse=True)
def duckdb_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAVEIL_ADAPTER", "duckdb")
    monkeypatch.setenv("DATAVEIL_DUCKDB_PATH", str(tmp_path / "test.duckdb"))
    from dataveil.server import get_adapter

    get_adapter.cache_clear()
    yield
    get_adapter.cache_clear()


def _make_table():
    from dataveil.server import get_adapter

    adapter = get_adapter()
    # reach into the DuckDB connection directly to set up test data --
    # exactly what an MCP client can't do, which is the point of this helper.
    con = adapter._con  # noqa: SLF001
    con.execute("CREATE TABLE people (id INTEGER, email VARCHAR, name VARCHAR)")
    con.executemany(
        "INSERT INTO people VALUES (?, ?, ?)",
        [(1, "alice@example.com", "  Alice  "), (2, "bob@example.com", "Bob")],
    )
    return con


def test_adapter_not_configured_raises_tool_error(monkeypatch):
    monkeypatch.delenv("DATAVEIL_ADAPTER", raising=False)
    from dataveil.server import get_adapter, list_tables

    get_adapter.cache_clear()
    with pytest.raises(ToolError, match="DATAVEIL_ADAPTER"):
        list_tables()


def test_postgres_adapter_requires_url(monkeypatch):
    monkeypatch.setenv("DATAVEIL_ADAPTER", "postgres")
    monkeypatch.delenv("DATAVEIL_POSTGRES_URL", raising=False)
    from dataveil.server import get_adapter, list_tables

    get_adapter.cache_clear()
    with pytest.raises(ToolError, match="DATAVEIL_POSTGRES_URL"):
        list_tables()


def test_list_tables():
    from dataveil.server import list_tables

    _make_table()
    assert "people" in list_tables()


def test_profile_never_contains_a_literal_value():
    from dataveil.server import profile

    _make_table()
    result = profile("people")
    assert result["row_count"] == 2
    serialized = json.dumps(result)
    assert "alice@example.com" not in serialized
    assert "bob@example.com" not in serialized


def test_propose_cleansing_plan_classifies_email_and_lists_vocabulary():
    from dataveil.server import propose_cleansing_plan

    _make_table()
    proposal = propose_cleansing_plan("people")
    tags = {c["column"]: c["tag"] for c in proposal["classifications"]}
    assert tags["email"] == "PII:EMAIL"
    assert "mask" in proposal["operation_vocabulary"]
    assert "drop_column" in proposal["operation_vocabulary"]


def test_apply_cleansing_plan_refuses_without_confirm():
    from dataveil.server import apply_cleansing_plan

    _make_table()
    with pytest.raises(ToolError, match="confirm=true"):
        apply_cleansing_plan("people", [], confirm=False)


def test_apply_cleansing_plan_rejects_unknown_operation_before_touching_data():
    from dataveil.server import apply_cleansing_plan

    con = _make_table()
    bad_plan = [{"operation": "drop_table", "column": "email", "params": {}, "rationale": "x"}]
    with pytest.raises(ToolError, match="unknown operation"):
        apply_cleansing_plan("people", bad_plan, confirm=True)
    assert con.execute("SELECT email FROM people WHERE id = 1").fetchone()[0] == "alice@example.com"


def test_apply_cleansing_plan_masks_and_trims():
    from dataveil.server import apply_cleansing_plan

    con = _make_table()
    plan = [
        {"operation": "mask", "column": "email", "params": {"method": "hash"}, "rationale": "PII:EMAIL"},
        {"operation": "trim_whitespace", "column": "name", "params": {}, "rationale": "whitespace"},
    ]
    result = apply_cleansing_plan("people", plan, confirm=True)
    assert len(result["applied_steps"]) == 2

    row = con.execute("SELECT email, name FROM people WHERE id = 1").fetchone()
    assert row[0] != "alice@example.com"
    assert len(row[0]) == 32
    assert row[1] == "Alice"


def test_profile_classify_and_apply_are_all_audit_logged(tmp_path, monkeypatch):
    from dataveil.audit import AuditLog
    from dataveil.server import apply_cleansing_plan, profile, propose_cleansing_plan

    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("DATAVEIL_AUDIT_LOG_PATH", str(log_path))
    _make_table()

    profile("people")
    propose_cleansing_plan("people")
    apply_cleansing_plan(
        "people",
        [{"operation": "trim_whitespace", "column": "name", "params": {}, "rationale": "x"}],
        confirm=True,
    )

    events = AuditLog(log_path).read_all()
    assert [e.kind for e in events] == ["profile", "classify", "execute_plan"]
    assert all(e.table == "people" for e in events)
    assert "alice@example.com" not in json.dumps([e.to_dict() for e in events])
