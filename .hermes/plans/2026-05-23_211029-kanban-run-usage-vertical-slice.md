# Kanban Run Usage Visibility — Vertical Slice Plan

## Goal

Shape the Kanban run-usage work as a ready-for-agent vertical slice instead of continuing ad hoc patching during the GrillMe session.

The feature should make completed Kanban runs show a compact usage/process receipt, with dashboard visibility prioritized from the jump when practical.

## Product decision from GrillMe

- Dashboard visibility is the preferred first-class UX.
- If dashboard integration becomes too costly for the first round, it may slip from v1, but the implementation should still preserve the data shape needed for dashboard display.
- CLI/readable outputs are useful support surfaces, not the primary product goal.

## Proposed vertical slice

When a Kanban task run completes, Hermes should record normalized run usage metadata and make it visible in the run/task surfaces.

Canonical storage:

- `task_runs.metadata.run_usage`

Human-readable audit/export artifact:

- best-effort append-only JSONL ledger per board or Kanban data directory
- this is not canonical and must not be required for dashboard correctness

Primary display target:

- Kanban dashboard task/run detail view, if the dashboard surface is straightforward enough for v1

Secondary display targets:

- `hermes kanban show <task_id>`
- `hermes kanban runs`
- optional JSON output fields for scripts/export

## Desired metadata shape

Suggested shape under `task_runs.metadata.run_usage`:

```json
{
  "provider": "openai-codex",
  "model": "gpt-5.5",
  "model_tier": null,
  "started_at": "2026-05-23T...Z",
  "ended_at": "2026-05-23T...Z",
  "runtime_ms": 123456,
  "tokens": {
    "input": null,
    "output": null,
    "total": null
  },
  "cost": {
    "amount": null,
    "currency": "USD",
    "source": "not_available"
  },
  "status": "complete",
  "source": "kanban_run_completion"
}
```

Principles:

- Missing accounting data must not block task completion.
- Unknown token/cost fields should be `null` or explicitly marked `not_available`, consistently.
- Existing metadata must be preserved and merged, not overwritten wholesale.
- Ledger write failures must be non-blocking.

## Files likely to change

Likely implementation files:

- `hermes_cli/kanban_db.py`
  - completion path / `task_runs.metadata` write
  - helper for generating or merging `run_usage`
  - optional ledger append helper

- `hermes_cli/kanban.py`
  - CLI visibility in `show` / `runs`
  - JSON output preservation

Dashboard files still need focused discovery before commitment:

- Search for Kanban dashboard API/route/components before implementation.
- If the dashboard stack requires larger frontend/API work, keep v1 canonical data + CLI visibility and create a follow-up dashboard slice.

## Acceptance criteria

1. Completing a normal Kanban worker run stores `metadata.run_usage` on the corresponding `task_runs` row.
2. Completing/synthesizing an ended run also stores/derives a compatible `run_usage` block where data is available.
3. Existing run metadata survives completion and is merged with the new usage block.
4. Missing provider/model/token/cost data does not fail the run.
5. Ledger append is best effort; write failure is logged or ignored safely and does not fail completion.
6. At least one human-facing surface displays the receipt in v1.
7. Dashboard visibility is included in v1 if the existing dashboard task/run detail surface can be patched cleanly without expanding scope.
8. If dashboard visibility slips, the implementation leaves `metadata.run_usage` shaped exactly for a follow-up dashboard slice.

## Tests / validation

Focused tests should cover:

- active run completion writes `metadata.run_usage`
- synthetic/manual completion path writes compatible metadata
- existing metadata preservation / merge behavior
- missing accounting fields remain non-fatal
- ledger append success and ledger append failure do not affect completion
- CLI or dashboard display formatting, depending on v1 display surface

Targeted commands to identify before implementation:

- existing Kanban DB tests
- CLI snapshot/output tests, if present
- dashboard/API tests, if present

## Non-goals for v1

- perfect cost accounting across every provider
- pricing tables or billing-grade cost math
- blocking task completion on usage capture
- large dashboard redesign
- multi-board analytics/reporting
- model intelligence/speed scoring beyond optional nullable metadata

## Recommended lane

- Direct Hermes: keep coordinating, maintain the vertical-slice contract, and avoid more ad hoc patches.
- Codex: implement the v1 slice with tests once Matt approves the scope.
- Claude Code: optional review pass for dashboard/data-model tradeoffs if the dashboard path looks bigger than expected.

## Open implementation question

One remaining GrillMe question before agent implementation:

Should v1 treat dashboard visibility as a **must-have acceptance criterion**, or as a **best-effort stretch goal** once canonical metadata + CLI visibility are working?

Recommended answer: best-effort stretch goal for v1, but require the canonical metadata shape to be dashboard-ready.
