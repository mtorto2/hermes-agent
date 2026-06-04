# NEXT_TIME — Hermes Agent Lights / Terminal tab titles

## Current status

- Branch: `main` in `/Users/matt/.hermes/hermes-agent`.
- Matt approved committing/pushing this slice to `fork/main` on 2026-06-03.
- Goal: Terminal tab names/manual Inspector titles should persist, and Agent Lights should eventually show human-friendly slot names.

## What is working now

- Manual Terminal tab titles no longer revert when sending prompts in a fresh Hermes TUI tab.
- TUI title writer is guarded so macOS Terminal.app does not receive Hermes OSC title writes.
- Agent Lights has been changed to read-only for Terminal titles:
  - no Agent Lights OSC title writer remains wired up;
  - old `TerminalTabTitleScript` implementation was removed;
  - Agent Lights no longer clears/sets Terminal title display components.
- Agent Lights app was rebuilt/relaunched from:
  - `apps/agent-lights-menu-bar/.build/AgentLightsMenuBar.app`

## Verified commands/results

From `apps/agent-lights-menu-bar`:

```bash
swift test
```

- Passed: 26/26 Agent Lights tests.

From `ui-tui`:

```bash
npm test -- terminalTitle.test.ts
npm run type-check
npm run build
```

- Terminal title unit tests passed: 6/6.
- Type-check passed.
- Build passed and rebuilt `ui-tui/dist/entry.js`.

## Current modified files

```text
M apps/agent-lights-menu-bar/README.md
M apps/agent-lights-menu-bar/Sources/AgentLightsCore/SlotStatus.swift
M apps/agent-lights-menu-bar/Sources/AgentLightsMenuBar/main.swift
M apps/agent-lights-menu-bar/Tests/AgentLightsCoreTests/SlotStatusTests.swift
M ui-tui/src/app/useMainApp.ts
M ui-tui/src/lib/terminalTitle.test.ts
M ui-tui/src/lib/terminalTitle.ts
```

## Remaining issue: Agent Lights picking up custom names

Matt created a new Terminal tab visibly called `Matt Custom Name`.
Agent Lights saw the slot/process, but Terminal did **not** expose that visible name through scriptable/AX properties.

Live mapping at the time:

- Slot B
- PID `52190`
- TTY `ttys000`
- State `idle`

Terminal reported for that tab:

```text
custom title: empty
title displays custom title: false
window_name: Terminal
AX-visible title: not Matt Custom Name
```

So Agent Lights currently cannot reliably read the Inspector-visible name directly from Terminal.app.

## Recommended next fix

Add a Hermes/Agent-Lights-owned alias source instead of relying on Terminal Inspector readback.

Suggested minimal implementation:

- Directory: `~/.hermes/agent-lights/aliases/`
- File per slot: `1.txt`, `2.txt`, etc.
- Agent Lights menu label precedence:
  1. alias file for slot, if non-empty;
  2. Terminal scriptable custom/window title, if human-looking;
  3. model fallback (`GPT-5.5`, `Claude`, etc.).

For immediate validation, set:

```bash
mkdir -p ~/.hermes/agent-lights/aliases
printf 'Matt Custom Name\n' > ~/.hermes/agent-lights/aliases/2.txt
```

Expected menu row:

```text
B: Matt Custom Name - idle
```

Later enhancement: add an Agent Lights menu action like `Rename Slot…` that writes this alias file without touching Terminal titles.

## Useful debug scripts left in /tmp

- `/tmp/read_agent_lights_menu.scpt`
- `/tmp/dump_terminal_titles_full.scpt`
- `/tmp/dump_terminal_names.scpt`
- `/tmp/dump_terminal_ax_titles.scpt`
- `/tmp/terminal_display_names_by_tty.scpt`
- `/tmp/dump_terminal_like_apps_ax.scpt`

## Safety notes

- Keep Agent Lights read-only for Terminal titles unless Matt explicitly wants generated Terminal title labels again.
- Do not reintroduce OSC title writes for Terminal.app; they caused manual Inspector titles to revert.
- If testing persistence, use a fresh Hermes TUI tab/process so the rebuilt TUI bundle is actually in use.
