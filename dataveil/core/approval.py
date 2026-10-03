"""Optional human-approval gate for cleansing plans.

execute_plan() in execute.py only ever requires confirm=True -- sufficient
for v1 (see PLAN.md's open questions). This module adds an optional second
gate in front of it: a plan must be registered, then explicitly approved by
id, before execute_approved_plan() will run it. Nothing here changes
execute_plan()'s own contract or its tests; this is a layer in front of it,
for integrations that want it (useful once this touches anything with real
compliance stakes -- not required for every caller).
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from typing import Any

from .adapter import Adapter
from .execute import execute_plan


class ApprovalError(RuntimeError):
    pass


@dataclasses.dataclass
class PendingPlan:
    id: str
    table: str
    plan: list[dict[str, Any]]
    created_at: float
    approved: bool = False
    approved_by: str | None = None
    approved_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class PlanRegistry:
    """In-memory registry of pending/approved plans, keyed by id.

    Scoped to a single process's lifetime -- the same posture sqlmesh-mcp's
    own plan()/apply_plan preview cache already has (plans aren't kept
    across server restarts).
    """

    def __init__(self) -> None:
        self._plans: dict[str, PendingPlan] = {}

    def register(self, table: str, plan: list[dict[str, Any]]) -> PendingPlan:
        pending = PendingPlan(id=uuid.uuid4().hex, table=table, plan=plan, created_at=time.time())
        self._plans[pending.id] = pending
        return pending

    def get(self, plan_id: str) -> PendingPlan | None:
        return self._plans.get(plan_id)

    def approve(self, plan_id: str, approved_by: str) -> PendingPlan:
        pending = self._plans.get(plan_id)
        if pending is None:
            raise ApprovalError(f"no pending plan with id {plan_id!r}")
        pending.approved = True
        pending.approved_by = approved_by
        pending.approved_at = time.time()
        return pending

    def discard(self, plan_id: str) -> None:
        """Remove a plan once it's been executed (or abandoned) -- mirrors
        how a preview-plan cache is cleaned up after use, so an approved
        plan can't be replayed and the registry doesn't grow unbounded over
        a long-lived server process. A no-op if the id isn't present.
        """
        self._plans.pop(plan_id, None)


def execute_approved_plan(
    adapter: Adapter,
    registry: PlanRegistry,
    plan_id: str,
    *,
    confirm: bool = False,
) -> list[dict[str, Any]]:
    """Like execute_plan(), but only runs a plan that's both registered in
    `registry` AND explicitly approved. confirm=True is still required on
    top of that approval, not instead of it.
    """
    pending = registry.get(plan_id)
    if pending is None:
        raise ApprovalError(f"no pending plan with id {plan_id!r}")
    if not pending.approved:
        raise ApprovalError(
            f"plan {plan_id!r} has not been approved yet -- call registry.approve(plan_id, approved_by) first"
        )
    return execute_plan(adapter, pending.table, pending.plan, confirm=confirm)
