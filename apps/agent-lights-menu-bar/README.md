# Agent Lights Menu Bar

Minimal native macOS status item for Matt's active Hermes lanes.

## Contract

The app reads Hermes slot files from:

```text
~/.hermes/agent-lights/slots/1.json
~/.hermes/agent-lights/slots/2.json
~/.hermes/agent-lights/slots/3.json
~/.hermes/agent-lights/slots/4.json
```

Each file is written by Hermes when a terminal/TUI instance starts. Hermes will auto-assign the first available slot, capped at 4 active instances, using per-slot lock files to avoid concurrent startup collisions. Setting `HERMES_SLOT=1..4` still forces a specific slot; missing or invalid values such as `HERMES_SLOT=6` fall back to auto-assignment when capacity is available.

## Dot mapping

Dots represent active running Hermes instances only. Missing, stale, or dead-PID slots are hidden instead of rendered as gray unused lanes.

Left-to-right dots follow active slot order: slot 1, then slot 2, then slot 3, then slot 4. If only slot 1 is active, only one dot is shown; if slots 1 and 3 are active, two dots are shown.

| State | Dot |
| --- | --- |
| working | green |
| human_intervention | yellow |
| final_answer | red steady |
| error | red steady for MVP; flashing/pulse is next polish |
| idle | gray, but only when the slot's producing process is alive |

Kanban worker slots use the same state colors, but render as thick unfilled rings
instead of filled dots. Normal Hermes CLI/TUI instances remain filled dots.

A slot renders when its status file is valid and either:

- `pid` is set and that process is still alive, or
- no live `pid` is available but the status file was modified in the last 120 seconds.

After that grace window, the companion app prunes the stale `.json` and `.lock`
files so completed/abandoned Kanban worker rings do not persist as ghosts. Normal
Hermes shutdown also removes the slot owned by that process when shutdown is
clean.

Clicking the menu bar item opens a vertical status menu: the first row summarizes
active slot count, then each active process gets its own disabled row. Rows include
slot number, lifecycle state, dot/ring type, model/profile when available, and
Kanban board/task/title details when the producer wrote them.

## Development

Hermes will best-effort launch the companion app when a CLI/TUI instance claims
an Agent Lights slot. The launcher uses the built Swift debug binary if present
and packages it into `.build/AgentLightsMenuBar.app` before opening it in the
background. If the Swift binary has not been built yet, run:

```bash
cd apps/agent-lights-menu-bar
swift build
```

Then start a fresh Hermes instance; it should create/update its slot file and
open the companion app if it is not already running.

If Ice or another menu bar manager is installed, a freshly packaged app bundle
may be treated as a new menu bar item and placed in the hidden section. The app
can be running correctly while the dots are hidden by Ice; reveal the hidden menu
bar items once and pin "Hermes Agent Lights" visible for human QA.

```bash
cd apps/agent-lights-menu-bar
swift test
swift build
swift run AgentLightsMenuBar
```

For local smoke testing without a full Hermes turn:

```bash
mkdir -p ~/.hermes/agent-lights/slots
cat > ~/.hermes/agent-lights/slots/1.json <<'JSON'
{"slot":1,"state":"working","event":"working","updated_at":"manual","pid":0}
JSON
cat > ~/.hermes/agent-lights/slots/2.json <<'JSON'
{"slot":2,"state":"final_answer","event":"final_answer","updated_at":"manual","pid":0}
JSON
```

The menu item should show two temporary dots, then hide them after the status files become stale unless a live `pid` is present.
