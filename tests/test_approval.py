import pytest

from dataveil.core.approval import ApprovalError, PlanRegistry, execute_approved_plan
from dataveil.core.execute import ExecutionError

VALID_PLAN = [
    {
        "operation": "trim_whitespace",
        "column": "name",
        "params": {},
        "rationale": "whitespace observed",
    }
]


def test_register_returns_pending_unapproved_plan():
    registry = PlanRegistry()
    pending = registry.register("t", VALID_PLAN)
    assert pending.approved is False
    assert pending.approved_by is None
    assert registry.get(pending.id) is pending


def test_approve_marks_plan_approved():
    registry = PlanRegistry()
    pending = registry.register("t", VALID_PLAN)
    approved = registry.approve(pending.id, approved_by="alice@example.com")
    assert approved.approved is True
    assert approved.approved_by == "alice@example.com"
    assert approved.approved_at is not None


def test_approve_unknown_plan_id_raises():
    registry = PlanRegistry()
    with pytest.raises(ApprovalError, match="no pending plan"):
        registry.approve("not-a-real-id", approved_by="alice")


def test_discard_removes_a_plan():
    registry = PlanRegistry()
    pending = registry.register("t", VALID_PLAN)
    registry.discard(pending.id)
    assert registry.get(pending.id) is None


def test_discard_unknown_plan_id_is_a_noop():
    registry = PlanRegistry()
    registry.discard("not-a-real-id")  # must not raise


def test_execute_approved_plan_rejects_unapproved_plan(adapter, con):
    con.execute("CREATE TABLE t (name VARCHAR)")
    con.execute("INSERT INTO t VALUES ('  bob  ')")
    registry = PlanRegistry()
    pending = registry.register("t", VALID_PLAN)

    with pytest.raises(ApprovalError, match="has not been approved"):
        execute_approved_plan(adapter, registry, pending.id, confirm=True)

    # nothing should have run
    assert con.execute("SELECT name FROM t").fetchone()[0] == "  bob  "


def test_execute_approved_plan_rejects_unknown_plan_id(adapter):
    registry = PlanRegistry()
    with pytest.raises(ApprovalError, match="no pending plan"):
        execute_approved_plan(adapter, registry, "not-a-real-id", confirm=True)


def test_execute_approved_plan_still_requires_confirm(adapter, con):
    con.execute("CREATE TABLE t (name VARCHAR)")
    con.execute("INSERT INTO t VALUES ('  bob  ')")
    registry = PlanRegistry()
    pending = registry.register("t", VALID_PLAN)
    registry.approve(pending.id, approved_by="alice@example.com")

    with pytest.raises(ExecutionError, match="confirm"):
        execute_approved_plan(adapter, registry, pending.id, confirm=False)


def test_execute_approved_plan_runs_once_approved_and_confirmed(adapter, con):
    con.execute("CREATE TABLE t (name VARCHAR)")
    con.execute("INSERT INTO t VALUES ('  bob  ')")
    registry = PlanRegistry()
    pending = registry.register("t", VALID_PLAN)
    registry.approve(pending.id, approved_by="alice@example.com")

    results = execute_approved_plan(adapter, registry, pending.id, confirm=True)
    assert results[0]["operation"] == "trim_whitespace"
    assert con.execute("SELECT name FROM t").fetchone()[0] == "bob"
