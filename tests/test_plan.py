import json

import pytest

from dataveil.core.plan import PlanValidationError, operation_vocabulary, validate_plan

SCHEMA = {"email": "VARCHAR", "age": "INTEGER", "name": "VARCHAR"}


def test_valid_plan_is_accepted():
    plan = [
        {
            "operation": "mask",
            "column": "email",
            "params": {"method": "hash"},
            "rationale": "PII:EMAIL detected at 100% match rate",
        },
        {
            "operation": "trim_whitespace",
            "column": "name",
            "params": {},
            "rationale": "leading/trailing whitespace observed",
        },
    ]
    steps = validate_plan(plan, SCHEMA)
    assert len(steps) == 2
    assert steps[0].operation == "mask"
    assert steps[1].column == "name"


def test_unknown_operation_rejected():
    plan = [
        {
            "operation": "DROP TABLE users; --",
            "column": "email",
            "params": {},
            "rationale": "anything",
        }
    ]
    with pytest.raises(PlanValidationError, match="unknown operation"):
        validate_plan(plan, SCHEMA)


def test_unknown_column_rejected():
    plan = [
        {
            "operation": "drop_column",
            "column": "ssn_that_doesnt_exist",
            "params": {},
            "rationale": "anything",
        }
    ]
    with pytest.raises(PlanValidationError, match="unknown column"):
        validate_plan(plan, SCHEMA)


def test_missing_required_param_rejected():
    plan = [
        {
            "operation": "mask",
            "column": "email",
            "params": {},  # missing 'method'
            "rationale": "anything",
        }
    ]
    with pytest.raises(PlanValidationError, match="missing required param"):
        validate_plan(plan, SCHEMA)


def test_invalid_choice_rejected():
    plan = [
        {
            "operation": "mask",
            "column": "email",
            "params": {"method": "encrypt_with_my_own_cipher"},
            "rationale": "anything",
        }
    ]
    with pytest.raises(PlanValidationError, match="must be one of"):
        validate_plan(plan, SCHEMA)


def test_unknown_param_rejected():
    plan = [
        {
            "operation": "trim_whitespace",
            "column": "name",
            "params": {"sql_injection": "'; DROP TABLE users; --"},
            "rationale": "anything",
        }
    ]
    with pytest.raises(PlanValidationError, match="unknown params"):
        validate_plan(plan, SCHEMA)


def test_missing_rationale_rejected():
    plan = [{"operation": "trim_whitespace", "column": "name", "params": {}}]
    with pytest.raises(PlanValidationError, match="rationale"):
        validate_plan(plan, SCHEMA)


def test_dedupe_rows_requires_known_key_columns():
    plan = [
        {
            "operation": "dedupe_rows",
            "params": {"key_columns": ["email", "ghost_column"]},
            "rationale": "exact duplicates on email",
        }
    ]
    with pytest.raises(PlanValidationError, match="ghost_column"):
        validate_plan(plan, SCHEMA)


def test_plan_must_be_a_list():
    with pytest.raises(PlanValidationError, match="must be a list"):
        validate_plan({"operation": "trim_whitespace"}, SCHEMA)


def test_operation_vocabulary_is_json_friendly_and_covers_every_operation():
    vocab = operation_vocabulary()
    assert set(vocab) == {
        "mask",
        "drop_column",
        "impute",
        "standardize_case",
        "trim_whitespace",
        "dedupe_rows",
        "bucket_numeric",
        "parse_date",
    }
    assert vocab["mask"]["requires_column"] is True
    assert vocab["mask"]["params"]["method"]["choices"] == ["hash", "partial", "constant"]
    assert vocab["mask"]["params"]["method"]["required"] is True
    assert vocab["dedupe_rows"]["requires_column"] is False
    json.dumps(vocab)  # must not raise
