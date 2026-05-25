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

A slot renders when its status file is valid and either:

- `pid` is set and that process is still alive, or
- no live `pid` is available but the status file was modified in the last 120 seconds.

## Development

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
