import json

from dataveil.audit import AuditLog


def test_record_appends_and_returns_event(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    event = log.record("profile", "people", {"row_count": 3})
    assert event.kind == "profile"
    assert event.table == "people"
    assert event.detail == {"row_count": 3}
    assert event.id
    assert event.timestamp > 0


def test_read_all_returns_events_in_order(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("profile", "people", {})
    log.record("classify", "people", {})
    log.record("execute_plan", "people", {"steps": 2})

    events = log.read_all()
    assert [e.kind for e in events] == ["profile", "classify", "execute_plan"]


def test_find_by_id(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    event = log.record("execute_plan", "people", {"applied": True})
    found = log.find(event.id)
    assert found is not None
    assert found.detail == {"applied": True}
    assert log.find("not-a-real-id") is None


def test_read_all_on_missing_file_returns_empty_list(tmp_path):
    log = AuditLog(tmp_path / "does_not_exist_yet.jsonl")
    assert log.read_all() == []


def test_log_file_is_one_json_object_per_line(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record("profile", "people", {"row_count": 3})
    log.record("profile", "orders", {"row_count": 10})

    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # must not raise


def test_log_never_contains_a_literal_value_beyond_what_was_recorded(tmp_path):
    """Sanity check that AuditLog itself doesn't do anything surprising with
    detail payloads -- it's the caller's responsibility (per core/profile.py,
    core/classify.py's own guarantees) not to pass literal cell values in."""
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("profile", "people", {"row_count": 3, "null_rate": 0.1})
    events = log.read_all()
    assert events[0].detail == {"row_count": 3, "null_rate": 0.1}
