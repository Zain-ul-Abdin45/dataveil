import pytest

from dataveil.core.execute import ExecutionError, execute_plan
from dataveil.core.plan import PlanValidationError


def test_requires_confirm(adapter, con):
    con.execute("CREATE TABLE t (name VARCHAR)")
    with pytest.raises(ExecutionError, match="confirm"):
        execute_plan(adapter, "t", [], confirm=False)


def test_unknown_operation_rejected_before_any_adapter_call(con):
    con.execute("CREATE TABLE t (name VARCHAR)")
    con.execute("INSERT INTO t VALUES ('x')")

    calls = []

    class CountingAdapter:
        def get_schema(self, table):
            return {"name": "VARCHAR"}

        def execute_operation(self, table, operation, column, params):
            calls.append((table, operation, column, params))
            return {}

    fake_adapter = CountingAdapter()
    plan = [
        {"operation": "trim_whitespace", "column": "name", "params": {}, "rationale": "ok"},
        {"operation": "exec_arbitrary_sql", "column": "name", "params": {}, "rationale": "bad"},
    ]
    with pytest.raises(PlanValidationError, match="unknown operation"):
        execute_plan(fake_adapter, "t", plan, confirm=True)

    assert calls == []  # not even the valid first step ran


def test_trim_whitespace(adapter, con):
    con.execute("CREATE TABLE t (name VARCHAR)")
    con.execute("INSERT INTO t VALUES ('  bob  ')")
    execute_plan(
        adapter,
        "t",
        [{"operation": "trim_whitespace", "column": "name", "params": {}, "rationale": "ws"}],
        confirm=True,
    )
    assert con.execute("SELECT name FROM t").fetchone()[0] == "bob"


def test_drop_column(adapter, con):
    con.execute("CREATE TABLE t (name VARCHAR, ssn VARCHAR)")
    con.execute("INSERT INTO t VALUES ('bob', '123-45-6789')")
    execute_plan(
        adapter,
        "t",
        [{"operation": "drop_column", "column": "ssn", "params": {}, "rationale": "PII"}],
        confirm=True,
    )
    cols = [d[0] for d in con.execute("SELECT * FROM t").description]
    assert "ssn" not in cols


def test_mask_hash(adapter, con):
    con.execute("CREATE TABLE t (email VARCHAR)")
    con.execute("INSERT INTO t VALUES ('alice@example.com')")
    execute_plan(
        adapter,
        "t",
        [
            {
                "operation": "mask",
                "column": "email",
                "params": {"method": "hash"},
                "rationale": "PII",
            }
        ],
        confirm=True,
    )
    value = con.execute("SELECT email FROM t").fetchone()[0]
    assert value != "alice@example.com"
    assert len(value) == 32  # md5 hex digest


def test_mask_partial(adapter, con):
    con.execute("CREATE TABLE t (card VARCHAR)")
    con.execute("INSERT INTO t VALUES ('4111111111111111')")
    execute_plan(
        adapter,
        "t",
        [
            {
                "operation": "mask",
                "column": "card",
                "params": {"method": "partial", "keep_last": 4},
                "rationale": "PII",
            }
        ],
        confirm=True,
    )
    assert con.execute("SELECT card FROM t").fetchone()[0] == "************1111"


def test_standardize_case(adapter, con):
    con.execute("CREATE TABLE t (name VARCHAR)")
    con.execute("INSERT INTO t VALUES ('john doe')")
    execute_plan(
        adapter,
        "t",
        [
            {
                "operation": "standardize_case",
                "column": "name",
                "params": {"case": "title"},
                "rationale": "x",
            }
        ],
        confirm=True,
    )
    assert con.execute("SELECT name FROM t").fetchone()[0] == "John Doe"


def test_impute_mean(adapter, con):
    con.execute("CREATE TABLE t (age INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [[10], [20], [None]])
    execute_plan(
        adapter,
        "t",
        [
            {
                "operation": "impute",
                "column": "age",
                "params": {"strategy": "mean"},
                "rationale": "x",
            }
        ],
        confirm=True,
    )
    rows = sorted(r[0] for r in con.execute("SELECT age FROM t").fetchall())
    assert rows == [10, 15, 20]


def test_dedupe_rows(adapter, con):
    con.execute("CREATE TABLE t (email VARCHAR, name VARCHAR)")
    con.executemany(
        "INSERT INTO t VALUES (?, ?)",
        [["a@x.com", "A"], ["a@x.com", "A dup"], ["b@x.com", "B"]],
    )
    execute_plan(
        adapter,
        "t",
        [
            {
                "operation": "dedupe_rows",
                "params": {"key_columns": ["email"]},
                "rationale": "dup key",
            }
        ],
        confirm=True,
    )
    n = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert n == 2


def test_bucket_numeric(adapter, con):
    con.execute("CREATE TABLE t (age INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [[5], [25], [45]])
    execute_plan(
        adapter,
        "t",
        [
            {
                "operation": "bucket_numeric",
                "column": "age",
                "params": {"bin_width": 20},
                "rationale": "x",
            }
        ],
        confirm=True,
    )
    rows = sorted(r[0] for r in con.execute("SELECT age_bucket FROM t").fetchall())
    assert rows == ["0-20", "20-40", "40-60"]


def test_parse_date(adapter, con):
    con.execute("CREATE TABLE t (joined VARCHAR)")
    con.execute("INSERT INTO t VALUES ('03/15/2024')")
    execute_plan(
        adapter,
        "t",
        [
            {
                "operation": "parse_date",
                "column": "joined",
                "params": {"source_format": "%m/%d/%Y", "target_format": "%Y-%m-%d"},
                "rationale": "x",
            }
        ],
        confirm=True,
    )
    assert con.execute("SELECT joined FROM t").fetchone()[0] == "2024-03-15"
