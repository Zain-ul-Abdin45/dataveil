"""Audit logging: every profile call, every proposed plan, every execution
logged with a timestamp and enough detail to reconstruct what happened
without re-running anything.

An entry contains whatever detail the caller passes to record(). dataveil's
MCP server logs row counts, classification tags and plans (operation
references and configuration an agent proposed), not cell values. A caller
that logs a full profile also logs its numeric min/max, which can be literal
cell values on large tables (see README "Known limits").

This is a reusable component, not something wired automatically into every
core/ call: core/profile.py, classify.py, execute.py stay pure (adapter in,
result out), and whichever integration drives them (sqlmesh-mcp's tools, a
future standalone server) calls AuditLog.record() explicitly around the
calls it wants logged. That keeps the three stages independently testable
without a logging dependency forced on every caller.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

EventKind = Literal["profile", "classify", "propose_plan", "execute_plan", "approve_plan"]


@dataclasses.dataclass(frozen=True)
class AuditEvent:
    id: str
    kind: EventKind
    timestamp: float  # unix epoch seconds, UTC
    table: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "timestamp": self.timestamp,
            "table": self.table,
            "detail": self.detail,
        }


class AuditLog:
    """Append-only JSON-lines audit log: one file, one JSON object per line.

    Thread-safe within a single process (a lock around each append) -- not a
    substitute for a real multi-writer datastore, but sufficient for the
    single long-lived server process this is meant to run inside.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, kind: EventKind, table: str, detail: dict[str, Any]) -> AuditEvent:
        event = AuditEvent(
            id=uuid.uuid4().hex,
            kind=kind,
            timestamp=time.time(),
            table=table,
            detail=detail,
        )
        line = json.dumps(event.to_dict(), default=str)
        with self._lock, self._path.open("a") as f:
            f.write(line + "\n")
        return event

    def read_all(self) -> list[AuditEvent]:
        if not self._path.exists():
            return []
        events = []
        with self._path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                events.append(AuditEvent(**data))
        return events

    def find(self, event_id: str) -> AuditEvent | None:
        return next((e for e in self.read_all() if e.id == event_id), None)
