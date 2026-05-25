# Agent Lights Menu Bar

Minimal native macOS status item for Matt's four Hermes lanes.

## Contract

The app reads Hermes slot files from:

```text
~/.hermes/agent-lights/slots/1.json
~/.hermes/agent-lights/slots/2.json
~/.hermes/agent-lights/slots/3.json
~/.hermes/agent-lights/slots/4.json
```

Each file is written by Hermes when the corresponding terminal was launched with `HERMES_SLOT=1..4`.

## Dot mapping

Left-to-right dots map to slots 1, 2, 3, 4.

| State | Dot |
| --- | --- |
| missing/invalid | dim gray |
| idle | gray |
| working | green |
| human_intervention | yellow |
| final_answer | red steady |
| error | red steady for MVP; flashing/pulse is next polish |

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

The menu item should show: green, red, gray, gray.
