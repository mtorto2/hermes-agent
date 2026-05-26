# Agent Lights Menu Bar

Minimal native macOS status item for Matt's active Hermes lanes.

## Contract

The app reads normal Hermes slot files from:

```text
~/.hermes/agent-lights/slots/1.json
~/.hermes/agent-lights/slots/2.json
~/.hermes/agent-lights/slots/3.json
~/.hermes/agent-lights/slots/4.json
```

Kanban worker agents use a separate capacity pool under:

```text
~/.hermes/agent-lights/agents/1.json
~/.hermes/agent-lights/agents/2.json
~/.hermes/agent-lights/agents/3.json
~/.hermes/agent-lights/agents/4.json
~/.hermes/agent-lights/agents/5.json
~/.hermes/agent-lights/agents/6.json
~/.hermes/agent-lights/agents/7.json
~/.hermes/agent-lights/agents/8.json
```

Each file is written by Hermes when a terminal/TUI instance or Kanban worker starts. Hermes will auto-assign the first available slot within the appropriate pool, capped at 4 active normal instances and 8 active Kanban workers, using per-slot lock files to avoid concurrent startup collisions. Setting `HERMES_SLOT=1..4` still forces a specific normal-Hermes slot; Kanban workers auto-assign within their 1..8 pool. Missing or invalid values such as `HERMES_SLOT=9` fall back to auto-assignment when capacity is available.

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

Normal Hermes CLI/TUI instances render as horizontal filled dots in the primary
menu-bar item. Kanban workers render as compact filled-circle groups in that
same visible item so Ice/menu-bar managers cannot hide a second status item
independently. The agent group appears only while at least one worker is active:
slots 1..4 render as the first 2×2 bank and slots 5..8 as a second 2×2 bank;
active worker circles use lifecycle colors; unused worker capacity circles
remain gray.

A slot renders when its status file is valid and either:

- `pid` is set and that process is still alive, or
- no live `pid` is available but the status file was modified in the last 120 seconds.

After that grace window, the companion app prunes stale `.json` and `.lock` files
from both pools so completed/abandoned Kanban worker rings do not persist as
ghosts. Normal Hermes shutdown also removes the slot owned by that process when
shutdown is clean.

Clicking the menu bar item opens a vertical status menu. The first row uses this
compact count format:

```text
Hermes: 3 active  Agents: 6 active
```

Each active process then gets its own clickable row with compact model/state text,
for example:

```text
1: gpt 5.5 - answer ready
2: claude 4.7 - working
3: gpt 5.2 - needs intervention
Agent 1: gpt 5.5 - working
```

Model names are shortened for readability by dropping provider prefixes and common
Claude family suffixes. Status labels use human wording (`answer ready`, `working`,
`needs intervention`) instead of raw lifecycle enum names. Clicking a normal
Hermes row uses the slot producer PID to find its TTY and asks Terminal.app to
select/raise the matching tab/window. Clicking an agent row with `kanban_task_id`
opens Terminal.app and runs `hermes kanban [--board <board>] show <task_id>` so
the associated Kanban card is pulled up directly. If the process has exited, has
no TTY, lacks card metadata, or Terminal automation permission is denied, the app
beeps and leaves focus unchanged.

The status menu also includes **Show Floating Monitor**. This opens a Sticky
Notes-style monitor window near the upper-right of the screen: translucent
off-white, rounded, resizable, always-on-top (`.floating` panel level), and
closable with a simple `×` control. The window shows the same live status feed at
a larger scale: normal Hermes TUI sessions render as large filled circles with no
outline, and Kanban worker capacity renders as a large filled 2×2 group with gray
placeholders. The monitor keeps the same left-to-right ordering as the menu-bar
item: each Hermes TUI circle occupies one visual unit, and the entire Kanban 2×2
grid occupies one matching unit to its right. The circle layout recalculates from
the current window size, so resizing the panel scales and repositions the
indicators instead of leaving a fixed-size icon. **Monitor More Transparent** and
**Monitor Less Transparent** menu items adjust the panel opacity in persisted
steps.

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
bar items once and pin "Hermes Agent Lights" visible for human QA. The Kanban
agent circles are intentionally drawn inside the primary Hermes item rather than
a second status item, so pinning that one item should reveal both Hermes dots and
agent circles.

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
mkdir -p ~/.hermes/agent-lights/agents
cat > ~/.hermes/agent-lights/agents/1.json <<'JSON'
{"slot":1,"state":"working","event":"working","updated_at":"manual","pid":0,"source":"kanban_worker"}
JSON
```

The menu should show two temporary filled dots plus a 2×2 filled-circle agent
group in the same menu-bar item, with one active colored circle and three gray
placeholders, then hide them after the status files become stale unless a live
`pid` is present.
