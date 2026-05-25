# Agent Lights Menu Bar Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a narrow local status surface for Matt's four fixed Hermes lanes: each `HERMES_SLOT=1..4` writes a lifecycle status file, and a macOS menu bar app renders four small unnumbered dots left-to-right.

**Architecture:** Hermes owns the truth by emitting lifecycle events into atomic per-slot JSON files under `~/.hermes/agent-lights/slots/`. The native macOS menu bar app is a dumb renderer that watches/loads those files and maps states to dot colors. No Terminal scraping, no tab introspection, no Kanban dependency.

**Tech Stack:** Python 3.11 Hermes core, pytest/ruff, Swift/AppKit for the native status item.

---

## Contract decisions

- Four dots only, left-to-right slot mapping: 1, 2, 3, 4.
- `HERMES_SLOT` is the lane identity source.
- Gray/empty = idle/no file/stale.
- Green = working.
- Yellow = human intervention / approval / clarification needed.
- Red steady = final answer ready / Matt should look at that lane.
- Red flashing = error / urgent attention.
- Final answer stays red until the next prompt starts in that same slot; no auto-fade timeout for final answers.

---

## Task 1: Land the Hermes slot status file backend

**Objective:** Add a tested file writer to the existing light cue service so Hermes instances with `HERMES_SLOT=1..4` write per-slot lifecycle JSON.

**Files:**
- Modify: `agent/light_cues.py`
- Modify: `tests/agent/test_light_cues.py`
- Modify: `docs/dev-notes/agent-lights-menu-bar-slice.md`

**Steps:**
1. Write failing tests for `SlotStatusFileBackend.from_env()` accepting only slots 1-4.
2. Write failing tests for atomic JSON writes to `~/.hermes/agent-lights/slots/<slot>.json`.
3. Write failing test proving status files still update when physical light mode is `no-light`.
4. Implement `SlotStatusFileBackend` and wire it into `LightCueService` separately from WiZ/LED action emission.
5. Wire `build_light_cue_service_from_config()` to enable the slot backend automatically from `HERMES_SLOT`.
6. Run:
   - `uv run --extra dev pytest tests/agent/test_light_cues.py tests/agent/test_wiz_light_backend.py -q`
   - `uv run --extra dev ruff check agent/light_cues.py tests/agent/test_light_cues.py`

**Status:** Done in branch `feat/agent-lights-menu-bar-slice`.

---

## Task 2: Add a local slot-status smoke command or documented manual smoke

**Objective:** Make it easy to prove the file contract without launching the full menu app.

**Files:**
- Modify: `docs/dev-notes/agent-lights-menu-bar-slice.md`
- Optional create: `tools/smoke_slot_status.py`

**Steps:**
1. Decide whether this should be a tiny script or just shell commands in docs.
2. If scripting, write a small smoke that emits each lifecycle event through `build_light_cue_service_from_config({})` with `HERMES_SLOT` set.
3. Verify `~/.hermes/agent-lights/slots/<slot>.json` changes as expected.
4. Keep this non-production and low-noise.

---

## Task 3: Seed the native macOS menu bar renderer

**Objective:** Create the minimal AppKit status item that renders four dots from slot JSON files.

**Files:**
- Create: likely `apps/AgentLightsMenuBar/` or another agreed location after checking repo conventions.
- Create: Swift source for `NSStatusItem` rendering.
- Create: app README/setup note.

**Steps:**
1. Inspect repo packaging conventions before choosing the path.
2. Create minimal Swift/AppKit app with an `NSStatusItem` and custom view/image drawing four circles.
3. Poll or watch `~/.hermes/agent-lights/slots/1.json` through `4.json`.
4. Map states to colors exactly from the contract.
5. Render stale/missing/invalid files as gray.
6. Keep v1 display-only; no click-to-focus Terminal behavior yet.

---

## Task 4: End-to-end verification with two lanes

**Objective:** Prove real Hermes events update the files and the menu renderer reflects them.

**Files:**
- Modify docs as needed.

**Steps:**
1. Launch at least two Hermes terminals with different slots, e.g. `HERMES_SLOT=1 hermes` and `HERMES_SLOT=2 hermes`.
2. Send a prompt in slot 1 and verify slot 1 goes green while working, then steady red on final answer.
3. Send a prompt in slot 1 again and verify the red clears to green on the next prompt.
4. Trigger or simulate an error and verify flashing red behavior in the renderer.
5. Confirm slot 2 status is independent.

---

## Task 5: Cleanup and review gate

**Objective:** Prepare the slice for Matt review without hiding risk.

**Steps:**
1. Run focused tests and lint.
2. Check `git diff` for scope creep.
3. Summarize exactly what is implemented versus still planned.
4. Ask Matt before committing.
