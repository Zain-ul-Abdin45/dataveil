"""SQLMesh adapter on a Postgres-backed SQLMesh project: the checksum
functions are registered as PL/pgSQL through SQLMesh's engine adapter.

Uses the same DATAVEIL_TEST_POSTGRES_URL as tests/test_postgres_adapter.py
and skips if SQLMesh, SQLAlchemy or a reachable Postgres is missing. Each
test run uses its own schemas and drops them afterwards.
"""

import getpass
import os
import shutil
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlmesh")
sa = pytest.importorskip("sqlalchemy")

from dataveil.adapters.sqlmesh import SQLMeshAdapter  # noqa: E402
from dataveil.core.classify import classify_table  # noqa: E402

TEST_DB_URL = os.environ.get("DATAVEIL_TEST_POSTGRES_URL", "postgresql+psycopg2://localhost/dataveil_test")
PAYMENTS_MODEL = Path(__file__).parent / "fixtures" / "sqlmesh_project" / "models" / "payments.sql"


def _postgres_available() -> bool:
    try:
        engine = sa.create_engine(TEST_DB_URL)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="no Postgres instance reachable")


@pytest.fixture
def pg_sqlmesh_ctx(tmp_path, monkeypatch):
    from sqlmesh.core.context import Context

    url = sa.engine.make_url(TEST_DB_URL)
    schema = f"dv_test_{uuid.uuid4().hex[:8]}"
    state_schema = f"{schema}_state"
    project_dir = tmp_path / "project"
    (project_dir / "models").mkdir(parents=True)
    shutil.copy(PAYMENTS_MODEL, project_dir / "models" / "payments.sql")
    model_sql = project_dir / "models" / "payments.sql"
    model_sql.write_text(model_sql.read_text().replace("dataveil_test.payments", f"{schema}.payments"))
    (project_dir / "config.yaml").write_text(
        "gateways:\n"
        "  pg:\n"
        "    connection:\n"
        "      type: postgres\n"
        f"      host: {url.host or 'localhost'}\n"
        f"      port: {url.port or 5432}\n"
        f"      user: {url.username or getpass.getuser()}\n"
        f"      password: '{url.password or ''}'\n"
        f"      database: {url.database}\n"
        f"    state_schema: {state_schema}\n"
        "default_gateway: pg\n"
        "model_defaults:\n"
        "  dialect: postgres\n"
        "  start: 2024-01-01\n"
    )
    monkeypatch.chdir(project_dir)
    ctx = Context(paths=str(project_dir))
    ctx.plan(no_prompts=True, auto_apply=True)
    yield ctx, f"{schema}.payments"
    ctx.close()
    engine = sa.create_engine(TEST_DB_URL)
    with engine.begin() as conn:
        for name in (schema, f"sqlmesh__{schema}", state_schema):
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
    engine.dispose()


def test_checksum_classifiers_work_on_a_postgres_project(pg_sqlmesh_ctx):
    ctx, model = pg_sqlmesh_ctx
    adapter = SQLMeshAdapter(ctx)
    assert adapter.has_checksum_functions() is True
    results = {r.column: r.tag for r in classify_table(adapter, model)}
    assert results["card_number"] == "PII:CREDIT_CARD"
    assert results["iban"] == "PII:IBAN"
    # registering again (a second adapter on the same project) must not fail
    assert SQLMeshAdapter(ctx).has_checksum_functions() is True
