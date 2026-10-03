"""The plan schema and the closed operation vocabulary.

A "plan" is the only thing an LLM is ever allowed to produce in this system:
a list of ``{operation, column, params, rationale}`` objects. Nothing here
accepts SQL, Python, or any other executable text from the plan itself —
every operation is a named, pre-written transformation with a fixed
parameter shape, and a plan step is only ever a *reference* into that
vocabulary plus its parameters.

``validate_plan`` rejects anything outside the vocabulary (unknown
operation, unknown column, malformed params) before a single call reaches
``core/execute.py``, let alone an adapter.
"""

from __future__ import annotations

import dataclasses
from typing import Any


class PlanValidationError(ValueError):
    """A plan step falls outside the operation vocabulary or its schema."""


@dataclasses.dataclass(frozen=True)
class ParamSpec:
    name: str
    type: Any  # a type or tuple of types, checked with isinstance
    required: bool = True
    choices: tuple[Any, ...] | None = None


@dataclasses.dataclass(frozen=True)
class OperationSpec:
    name: str
    params: tuple[ParamSpec, ...]
    requires_column: bool = True


OPERATIONS: dict[str, OperationSpec] = {
    "mask": OperationSpec(
        name="mask",
        params=(
            ParamSpec("method", str, choices=("hash", "partial", "constant")),
            ParamSpec("value", str, required=False),
            ParamSpec("keep_last", int, required=False),
        ),
    ),
    "drop_column": OperationSpec(
        name="drop_column",
        params=(),
    ),
    "impute": OperationSpec(
        name="impute",
        params=(
            ParamSpec("strategy", str, choices=("mean", "median", "mode", "constant")),
            ParamSpec("value", (str, int, float), required=False),
        ),
    ),
    "standardize_case": OperationSpec(
        name="standardize_case",
        params=(ParamSpec("case", str, choices=("upper", "lower", "title")),),
    ),
    "trim_whitespace": OperationSpec(
        name="trim_whitespace",
        params=(),
    ),
    "dedupe_rows": OperationSpec(
        name="dedupe_rows",
        params=(ParamSpec("key_columns", list),),
        requires_column=False,
    ),
    "bucket_numeric": OperationSpec(
        name="bucket_numeric",
        params=(
            ParamSpec("bin_width", (int, float), required=False),
            ParamSpec("bins", list, required=False),
            ParamSpec("target_column", str, required=False),
        ),
    ),
    "parse_date": OperationSpec(
        name="parse_date",
        params=(
            ParamSpec("source_format", str),
            ParamSpec("target_format", str, required=False),
        ),
    ),
}


@dataclasses.dataclass(frozen=True)
class PlanStep:
    operation: str
    column: str | None
    params: dict[str, Any]
    rationale: str


def operation_vocabulary() -> dict[str, dict[str, Any]]:
    """JSON-friendly serialization of OPERATIONS -- what an agent (or an MCP
    tool like propose_cleansing_plan) needs to know to build a valid plan:
    which operations exist, whether each needs a column, and each param's
    name/type/required-ness/choices.
    """

    def type_name(t: Any) -> str:
        if isinstance(t, tuple):
            return " | ".join(getattr(x, "__name__", str(x)) for x in t)
        return getattr(t, "__name__", str(t))

    return {
        name: {
            "requires_column": spec.requires_column,
            "params": {
                p.name: {
                    "type": type_name(p.type),
                    "required": p.required,
                    "choices": list(p.choices) if p.choices else None,
                }
                for p in spec.params
            },
        }
        for name, spec in OPERATIONS.items()
    }


def validate_plan(plan: Any, table_schema: dict[str, str]) -> list[PlanStep]:
    """Validate a raw plan (as an LLM/agent would submit it) against the
    operation vocabulary and a table's schema. Raises PlanValidationError on
    the first problem found; returns validated PlanStep objects otherwise.
    """
    if not isinstance(plan, list):
        raise PlanValidationError("plan must be a list of steps")
    return [_validate_step(raw, table_schema, i) for i, raw in enumerate(plan)]


def _validate_step(raw: Any, table_schema: dict[str, str], index: int) -> PlanStep:
    if not isinstance(raw, dict):
        raise PlanValidationError(f"step {index}: must be an object")

    operation = raw.get("operation")
    if operation not in OPERATIONS:
        raise PlanValidationError(f"step {index}: unknown operation {operation!r}; must be one of {sorted(OPERATIONS)}")
    spec = OPERATIONS[operation]

    column = raw.get("column")
    if spec.requires_column:
        if not isinstance(column, str) or not column:
            raise PlanValidationError(f"step {index} ({operation}): 'column' is required")
        if column not in table_schema:
            raise PlanValidationError(f"step {index} ({operation}): unknown column {column!r}")
    else:
        column = None

    rationale = raw.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise PlanValidationError(f"step {index} ({operation}): 'rationale' is required")

    params = raw.get("params", {})
    if not isinstance(params, dict):
        raise PlanValidationError(f"step {index} ({operation}): 'params' must be an object")

    allowed_names = {p.name for p in spec.params}
    unknown_params = set(params) - allowed_names
    if unknown_params:
        raise PlanValidationError(f"step {index} ({operation}): unknown params {sorted(unknown_params)}")

    for p in spec.params:
        if p.name not in params:
            if p.required:
                raise PlanValidationError(f"step {index} ({operation}): missing required param {p.name!r}")
            continue
        value = params[p.name]
        if not isinstance(value, p.type):
            raise PlanValidationError(f"step {index} ({operation}): param {p.name!r} must be of type {p.type}")
        if p.choices is not None and value not in p.choices:
            raise PlanValidationError(f"step {index} ({operation}): param {p.name!r} must be one of {p.choices}")

    _validate_cross_fields(operation, params, table_schema, index)

    return PlanStep(operation=operation, column=column, params=params, rationale=rationale)


def _validate_cross_fields(operation: str, params: dict[str, Any], table_schema: dict[str, str], index: int) -> None:
    """Checks that depend on more than one param, or on the table schema."""
    if operation == "mask":
        method = params.get("method")
        if method == "constant" and "value" not in params:
            raise PlanValidationError(f"step {index} (mask): method 'constant' requires 'value'")
        if method == "partial" and "keep_last" not in params:
            raise PlanValidationError(f"step {index} (mask): method 'partial' requires 'keep_last'")

    elif operation == "impute":
        if params.get("strategy") == "constant" and "value" not in params:
            raise PlanValidationError(f"step {index} (impute): strategy 'constant' requires 'value'")

    elif operation == "bucket_numeric":
        if "bin_width" not in params and "bins" not in params:
            raise PlanValidationError(f"step {index} (bucket_numeric): requires 'bin_width' or 'bins'")

    elif operation == "dedupe_rows":
        keys = params.get("key_columns")
        if not keys or not all(isinstance(k, str) for k in keys):
            raise PlanValidationError(
                f"step {index} (dedupe_rows): 'key_columns' must be a non-empty list of column names"
            )
        for k in keys:
            if k not in table_schema:
                raise PlanValidationError(f"step {index} (dedupe_rows): unknown column {k!r} in key_columns")
