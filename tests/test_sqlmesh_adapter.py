"""End-to-end tests for the SQLMesh adapter against a small, self-contained
SQLMesh project (tests/fixtures/sqlmesh_project) -- not a mock, a real
Context against a real (temp-copied) DuckDB-backed project, plan-and-applied
before each test so models have an actual physical snapshot to read/write.

Requires the 'sqlmesh' extra (``pip install -e '.[dev]'`` pulls it in);
skipped entirely if sqlmesh isn't installed, since the core engine must stay
usable without it (see PLAN.md Phase 4).
"""

import json
import shutil
import uuid
from pathlib import Path

import pytest

sqlmesh = pytest.importorskip("sqlmesh")

from dataveil.adapters.sqlmesh import SQLMeshAdapter  # noqa: E402
from dataveil.core.classify import classify_table  # noqa: E402
from dataveil.core.execute import execute_plan  # noqa: E402
from dataveil.core.profile import profile_table  # noqa: E402

FIXTURE_PROJECT = Path(__file__).parent / "fixtures" / "sqlmesh_project"
LITERAL_EMAIL = "alice.wonderland@example.com"
LITERAL_SSN = "123-45-6789"


@pytest.fixture
def sqlmesh_ctx(tmp_path, monkeypatch):
    from sqlmesh.core.context import Context

    project_dir = tmp_path / "project"
    shutil.copytree(FIXTURE_PROJECT, project_dir)
    # config.yaml's duckdb 'database: db.db' is resolved relative to the
    # process cwd, not the project directory -- without this chdir, every
    # test would silently share one real db.db file in the repo root.
    monkeypatch.chdir(project_dir)
    ctx = Context(paths=str(project_dir))
    ctx.plan(no_prompts=True, auto_apply=True)
    return ctx


@pytest.fixture
def sqlmesh_adapter(sqlmesh_ctx):
    return SQLMeshAdapter(sqlmesh_ctx)


def test_list_tables_and_get_schema(sqlmesh_adapter):
    assert "dataveil_test.people" in sqlmesh_adapter.list_tables()
    schema = sqlmesh_adapter.get_schema("dataveil_test.people")
    assert set(schema) == {"id", "email", "ssn", "full_name"}


def test_profile_reads_through_virtual_layer_without_literal_values(sqlmesh_adapter):
    profile = profile_table(sqlmesh_adapter, "dataveil_test.people")
    assert profile.row_count == 3
    serialized = json.dumps(profile.to_dict())
    assert LITERAL_EMAIL not in serialized
    assert LITERAL_SSN not in serialized
    assert "bob@example.com" not in serialized


def test_classify_tags_email_and_ssn(sqlmesh_adapter):
    results = {r.column: r.tag for r in classify_table(sqlmesh_adapter, "dataveil_test.people")}
    assert results["email"] == "PII:EMAIL"
    assert results["ssn"] == "PII:SSN"
    assert results["full_name"] == "none"


def test_checksum_classifiers_work_on_a_duckdb_project(sqlmesh_adapter):
    assert sqlmesh_adapter.has_checksum_functions() is True
    results = {r.column: r for r in classify_table(sqlmesh_adapter, "dataveil_test.payments")}
    # 2 of 3 card numbers pass the Luhn check; the column name hint lowers the bar to 0.5
    assert results["card_number"].tag == "PII:CREDIT_CARD"
    assert results["card_number"].match_rate == pytest.approx(2 / 3)
    assert results["iban"].tag == "PII:IBAN"


def test_checksum_classifiers_are_skipped_when_registration_is_off(sqlmesh_ctx):
    adapter = SQLMeshAdapter(sqlmesh_ctx, register_checksum_functions=False)
    assert adapter.has_checksum_functions() is False
    results = {r.column: r.tag for r in classify_table(adapter, "dataveil_test.payments")}
    assert results["card_number"] == "none"
    assert results["iban"] == "none"


def test_execute_plan_writes_to_the_physical_snapshot_table(sqlmesh_adapter, sqlmesh_ctx):
    execute_plan(
        sqlmesh_adapter,
        "dataveil_test.people",
        [
            {
                "operation": "mask",
                "column": "email",
                "params": {"method": "hash"},
                "rationale": "PII:EMAIL detected",
            },
            {
                "operation": "trim_whitespace",
                "column": "full_name",
                "params": {},
                "rationale": "leading/trailing whitespace observed",
            },
        ],
        confirm=True,
    )
    df = sqlmesh_ctx.fetchdf("SELECT email, full_name FROM dataveil_test.people ORDER BY id")
    assert df["email"].iloc[0] != LITERAL_EMAIL
    assert len(df["email"].iloc[0]) == 32  # md5 hex digest
    assert df["full_name"].iloc[0] == "Alice"


def test_execute_plan_against_unapplied_model_raises_clear_error(tmp_path, monkeypatch):
    # SQLMesh's snapshot categorization cache lives in ~/.sqlmesh, keyed by
    # content fingerprint, *not* scoped to this project copy -- a byte-for-byte
    # reuse of the shared fixture model would come back "already categorized"
    # from an earlier test's apply. A unique model name/body keeps this test
    # genuinely unapplied.
    project_dir = tmp_path / "project"
    shutil.copytree(FIXTURE_PROJECT, project_dir)
    unique_model = f"dataveil_test.never_applied_{uuid.uuid4().hex}"
    (project_dir / "models" / "people.sql").write_text(
        f"MODEL (\n  name {unique_model},\n  kind FULL,\n  grain id,\n);\n\n"
        f"SELECT * FROM (VALUES (1, 'x')) AS t(id, val)\n"
    )
    monkeypatch.chdir(project_dir)
    from sqlmesh.core.context import Context

    ctx = Context(paths=str(project_dir))  # never planned/applied
    adapter = SQLMeshAdapter(ctx)
    with pytest.raises(ValueError, match="apply a plan first"):
        execute_plan(
            adapter,
            unique_model,
            [{"operation": "trim_whitespace", "column": "val", "params": {}, "rationale": "x"}],
            confirm=True,
        )


def test_no_checksum_functions_on_other_engines():
    # Snowflake, BigQuery, ...: nothing is registered, so the card and IBAN
    # classifiers are skipped instead of failing on a missing function.
    from types import SimpleNamespace

    context = SimpleNamespace(engine_adapter=SimpleNamespace(dialect="snowflake"))
    assert SQLMeshAdapter(context).has_checksum_functions() is False  # type: ignore[arg-type]
