# Audit log and approval

`dataveil.audit.AuditLog` is an append-only JSON-lines log — one entry per
profile/classify/execute call, with a timestamp and enough detail to
reconstruct what happened without re-running anything. `record()` stores
whatever detail the caller passes: dataveil's MCP server logs row counts,
tags and plans, not cell values, but a caller that logs a full profile also
logs its numeric stats (see Known limits). It's a reusable component, not wired automatically into every core
call: whichever integration drives this (sqlmesh-mcp's tools, a future
standalone server) calls `log.record(...)` around the calls it wants logged.

`dataveil.core.approval` adds an optional second gate in front of
`execute_plan`: register a plan (`PlanRegistry.register`), get it approved
by id (`PlanRegistry.approve`), then run it with `execute_approved_plan` —
`confirm=True` is still required on top of that approval, not instead of
it. Useful once this touches anything with real compliance stakes; not
required for every caller.

The MCP server writes its log to `.dataveil/audit.jsonl`, or to the path in
`DATAVEIL_AUDIT_LOG_PATH` or `[audit] log_path`.
