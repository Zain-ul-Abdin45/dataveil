"""Plan execution: the only stage that touches real data, and only after a
plan has passed whitelist + schema validation in full.

The full plan is validated before any step is executed. A plan with a bad
step at index 5 never executes steps 0-4 against the adapter either — the
contract is "nothing runs unless the whole plan is clean," not best-effort.
"""

from __future__ import annotations

from typing import Any

from .adapter import Adapter
from .plan import PlanValidationError, validate_plan


class ExecutionError(RuntimeError):
    pass


def execute_plan(
    adapter: Adapter,
    table: str,
    plan: list[dict[str, Any]],
    *,
    confirm: bool = False,
) -> list[dict[str, Any]]:
    """Validate and execute a plan against ``table`` via ``adapter``.

    Raises PlanValidationError (without calling the adapter at all) if the
    plan references anything outside the operation vocabulary, an unknown
    column, or malformed params. Raises ExecutionError if ``confirm`` is not
    explicitly True — this is a destructive stage.
    """
    if not confirm:
        raise ExecutionError("execute_plan requires confirm=True")

    table_schema = adapter.get_schema(table)
    steps = validate_plan(plan, table_schema)

    results = []
    for step in steps:
        outcome = adapter.execute_operation(table, step.operation, step.column, step.params)
        results.append(
            {
                "operation": step.operation,
                "column": step.column,
                "params": step.params,
                "rationale": step.rationale,
                "result": outcome,
            }
        )
    return results


__all__ = ["ExecutionError", "PlanValidationError", "execute_plan"]
