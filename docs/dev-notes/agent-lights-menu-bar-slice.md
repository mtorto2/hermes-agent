# Agent Lights menu bar slice

Captured: 2026-05-25 02:48 CDT
Branch: `feat/agent-lights-menu-bar-slice`

## Goal

Build a narrow Hermes-native status indicator for Matt's four-terminal workflow: a macOS menu bar item with **four small dots in one horizontal row**, left-to-right mapping to Terminal/Hermes lanes 1, 2, 3, 4.

This is not a Kanban dashboard v1. It is an ambient local status surface for Matt's physical workflow: he opens four Terminal tabs, keeps them ordered, uses Command-1/2/3/4 to switch, and needs to know which lane is working or needs attention without constantly viewing every terminal.

## Existing Hermes foundation

Hermes already has core lifecycle events in `agent/light_cues.py`:

- `working`
- `human_intervention`
- `final_answer`
- `error`
- `idle`

Classic CLI and TUI gateway already emit prompt lifecycle cues:

- `cli.py` emits `working`, `final_answer`, `error`, and intervention cues.
- `tui_gateway/server.py` has TUI light-cue emission and tests proving `working -> final_answer` on successful prompt submission.
- WiZ/LED behavior is already implemented as a backend in `agent/wiz_light.py` / `gateway/wiz_light.py`.

The menu bar slice should reuse this lifecycle layer instead of scraping Terminal text.

## Proposed v1 architecture

Add a second local status backend beside the WiZ backend:

```text
LightCueEvent + HERMES_SLOT -> ~/.hermes/agent-lights/slots/<slot>.json
macOS menu app watches/reads those files -> renders four dots
```

Slot identity should be explicit, not inferred from Terminal tabs:

```bash
HERMES_SLOT=1 hermes --tui
HERMES_SLOT=2 hermes --tui
HERMES_SLOT=3 hermes --tui
HERMES_SLOT=4 hermes --tui
```

Matt may still physically keep these as Terminal tabs 1-4 and switch with Command-1/2/3/4. The env var makes the system robust if Terminal tab metadata is unavailable or brittle.

## v1 visual decision

Menu bar icon:

```text
● ● ● ●
1 2 3 4 implied left-to-right, not rendered as numbers
```

No numbers in the icon. Four small circles only.

Agreed v1 color semantics:

| State | Dot behavior |
| --- | --- |
| no slot / stale | gray outline or dim gray |
| idle | unfilled/gray |
| working | green filled |
| human_intervention | yellow, optionally flashing/pulsing |
| final_answer | red steady, meaning Matt should look at that lane |
| error | red flashing, meaning urgent/error attention |

Decision: red is not reserved only for errors. In Matt's physical workflow, red means “this lane needs my eyes now”; flashing red distinguishes actual error/urgent intervention.

Decision: `final_answer` stays red until the next prompt starts in that same slot. It should not auto-fade on a timer; the next `working` event naturally clears it.

## Proposed implementation slice

1. Add a file/status backend to the existing light-cue service. — **done in branch**
2. Read `HERMES_SLOT` from environment; ignore file backend when missing or outside 1-4. — **done in branch**
3. Write atomic per-slot JSON under `~/.hermes/agent-lights/slots/`. — **done in branch**
4. Include at least: `slot`, `event`, `state`, `updated_at`, `pid`, optional `session_id`, optional `title`. — **core fields done; optional fields later**
5. Add tests for backend mapping and atomic writes. — **done in branch**
6. Add/seed a minimal native macOS status item app only after the file contract is stable. — **seeded in branch**

## Non-goals for v1

- Do not scrape Terminal text.
- Do not infer state from Terminal tab title/window content.
- Do not require Kanban.
- Do not support more than four dots.
- Do not build a full dashboard.
- Do not introduce a broad smart-home framework.
- Do not make background/cron/Kanban workers noisy by default.

## Open GrillMe decisions

Resolved:

1. `working` is green.
2. `final_answer` is steady red / needs Matt's eyes; red is not only for errors.
3. A final-answer dot stays red until the next prompt in that same slot.

Still open / later:

4. Should yellow flash, pulse, or stay steady?
5. Should clicking the menu bar item eventually focus Terminal and send Command-1/2/3/4, or should v1 only display status?
6. Should stale slot files automatically decay to gray after N minutes/hours?

## First recommended decision

Start with the file/status backend before the Swift menu app. This proves the Hermes lifecycle and slot contract from tests and real terminal sessions, then the menu bar app becomes a simple renderer.
