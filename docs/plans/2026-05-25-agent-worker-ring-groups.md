# Agent Lights Menu Bar + Floating Monitor Implementation Notes

Date: 2026-05-25

## Goal

Keep normal Hermes/TUI instances visually protected from Kanban worker capacity while making both states obvious in the macOS Agent Lights menu bar and the larger floating monitor.

## Final visual contract

- Normal Hermes/TUI instances render left-to-right as filled circles.
- Kanban workers render separately from normal Hermes slots.
- Kanban workers use their own `~/.hermes/agent-lights/agents/` capacity pool.
- In the menu-bar item, Kanban capacity renders as one compact 2×2 filled-circle group inside the primary Hermes status item.
- In the floating monitor, normal Hermes/TUI instances render as large filled circles from left to right.
- In the floating monitor, the Kanban worker capacity renders as one 2×2 filled-circle grid to the right of the Hermes circles.
- The total 2×2 Kanban grid footprint matches one large Hermes/TUI circle, so the worker grid reads as one visual unit.
- Circles are solid fill only: active lifecycle color or inactive gray. No outlines/strokes.
- Floating monitor background is translucent off-white/white, not Sticky Notes yellow.
- Floating monitor is resizable, always-on-top, closable with `×`, and has menu-level transparency controls.

## Runtime contract

- Normal Hermes/TUI slot files live under `~/.hermes/agent-lights/slots/`.
- Kanban worker slot files live under `~/.hermes/agent-lights/agents/`.
- Legacy Kanban worker files found in the old normal slot namespace are still recognized as a compatibility fallback.
- Renderer should keep exactly one primary `NSStatusItem`; do not add a second item for agents because Ice/menu-bar managers can hide it independently.
- Pulling/launching a Hermes instance should best-effort instantiate the companion menu bar `.app` when the slot producer comes online.

## Verification gates

```bash
cd apps/agent-lights-menu-bar
swift test -q
swift build -q

cd /Users/matt/.hermes/hermes-agent
./venv/bin/python -m pytest tests/agent/test_light_cues.py -q
git diff --check
```

## macOS/Ice QA checklist

1. Verify exactly one `AgentLightsMenuBar` process is running from the intended `.app` bundle.
2. Verify Ice is not hiding the primary Hermes status item; if hidden, reveal/pin that one primary item.
3. Open the status menu and confirm it includes:
   - Refresh
   - Show Floating Monitor
   - Monitor More Transparent
   - Monitor Less Transparent
   - Open Status Folder
   - Quit Hermes Agent Lights
4. Open the floating monitor and confirm:
   - off-white translucent background
   - no circle outlines
   - left-to-right Hermes circles
   - 2×2 Kanban grid to the right
   - grid footprint roughly equals one Hermes circle
   - `×` close control visible

## Notes

The first design considered unfilled worker rings and multiple worker glyph groups. Visual QA rejected this: yellow outlines were too low-contrast at menu-bar size, and the final contract is filled circles only.
