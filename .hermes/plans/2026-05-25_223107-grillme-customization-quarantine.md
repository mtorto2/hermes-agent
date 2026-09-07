# GrillMe: Hermes customization quarantine implementation

Created: 2026-05-25 22:31
Branch prepared from: `refactor/quarantine-matt-customizations-20260525-220926`
Current fork baseline: `32cee06b6`
Upstream baseline at audit: `origin/main` `cea87d913`

## Objective

Make Matt-local Hermes customizations easier to preserve during future syncs with `NousResearch/hermes-agent` by moving personal behavior out of hot upstream files and behind plugins, local apps/scripts, or small extension seams.

Do **not** break the current working Hermes install. This is a staged refactor, not a rewrite.

## Current prepared artifacts

- Strategy note:
  - `docs/dev-notes/matt-customization-quarantine-strategy.md`
- Read-only inventory helper:
  - `scripts/matt_customization_inventory.py`

Smoke check:

```bash
venv/bin/python -m py_compile scripts/matt_customization_inventory.py
venv/bin/python scripts/matt_customization_inventory.py --max-commits 8 --max-files 30
```

## GrillMe opening question

Recommended first GrillMe prompt:

> Which quarantine slice do you want to tackle first?

Choices:

1. **Safest first: pluginize Apple Calendar / HeyGen / repo-status**
   - Lowest runtime risk.
   - Proves local-plugin pattern before touching Agent Lights.
   - Recommended default.

2. **Highest leverage first: add observer-only lifecycle hooks**
   - Enables Agent Lights quarantine later.
   - Slightly broader core touch, but behavior can remain unchanged.

3. **Most painful first: Agent Lights/WiZ extraction plan**
   - Directly reduces biggest future sync burden.
   - Highest regression risk; should only start after a test matrix is ready.

4. **Upstream cleanup first: split general bug fixes from Matt-local changes**
   - Prepares possible upstream PRs/backports.
   - Reduces fork-only delta without touching local UX.

Default recommendation: **1**, then **2**, then **3**.

## GrillMe blocker questions

Ask these one at a time, not as a wall:

1. Are we allowed to create `plugins/local/*` committed to Matt's fork, or should truly personal code live under `~/.hermes/plugins/*` only?
2. Should Apple Calendar remain available as a default tool in Matt's normal toolset, or only when a plugin toolset is explicitly enabled?
3. Should Agent Lights be treated as a Matt-local feature permanently, or as a candidate upstream extension once hook seams are clean?
4. Are we allowed to introduce new plugin hook names in core if they are observer-only and tested?
5. Do we want to push this quarantine branch to fork now, or keep it local until first implementation slice is reviewed?

## Slice 1: low-risk plugin quarantine proof

Goal: Move low-risk local-only items out of core where possible, without changing user-facing behavior.

Candidates:

- `scripts/heygen_generate.py` -> `plugins/local/heygen/` or leave script but document as local-only.
- `tools/apple_calendar_tool.py` -> `plugins/local/apple_calendar/` once plugin tool visibility/toolset behavior is confirmed.
- `hermes_cli/repo_status.py` -> `plugins/local/repo_status/` if plugin slash commands cover both CLI/gateway needs.

Risk control:

- Start with code-copy plugin scaffolding and tests.
- Do not delete core path until plugin path passes tests and tool visibility is verified.
- If plugin APIs are insufficient, stop and add a tiny seam instead of hacking around it.

## Slice 2: observer-only lifecycle hooks

Goal: Add hooks that let plugins observe runtime lifecycle without changing behavior.

Candidate hooks:

```python
on_turn_lifecycle(...)
on_light_cue(...)
on_status_update(...)
on_human_intervention_request(...)
on_human_intervention_response(...)
```

Rules:

- Start observer-only: return values ignored.
- Hook failures must be non-fatal.
- Preserve current direct light cue behavior until plugin replacement is tested.
- Add focused tests proving hooks fire and failures do not break runtime.

Likely files:

- `hermes_cli/plugins.py`
- `cli.py`
- `gateway/platforms/base.py`
- `tui_gateway/server.py`
- possibly `gateway/run.py`

## Slice 3: Agent Lights extraction

Only after Slice 2 is stable.

Goal: Move hardware/status implementation to plugin/local app while core emits semantic lifecycle.

Candidate destination:

```text
plugins/local/agent_lights/
  plugin.yaml
  __init__.py
  light_cues.py
  wiz_backend.py
  slot_status.py
  telegram_menu.py
apps/agent-lights-menu-bar/
```

Do not start by moving all code. Start by making existing core call plugin-compatible hooks, then migrate one backend at a time.

## Required gates before merging any implementation slice

Python focused gate:

```bash
venv/bin/python -m pytest \
  tests/agent/test_light_cues.py \
  tests/agent/test_wiz_light_backend.py \
  tests/gateway/test_telegram_light_cue_menu.py \
  tests/gateway/test_wiz_notification_light.py \
  tests/gateway/test_tts_media_routing.py \
  tests/gateway/test_dm_topics.py \
  tests/tools/test_file_tools.py \
  tests/tools/test_file_write_safety.py \
  tests/hermes_cli/test_config.py \
  -q -o 'addopts='
```

Swift Agent Lights gate:

```bash
cd apps/agent-lights-menu-bar
swift test
```

Inventory check:

```bash
venv/bin/python scripts/matt_customization_inventory.py --max-commits 12 --max-files 40
```

## Human review gates

Pause for Matt before:

- deleting or moving existing core implementations,
- changing default enabled tools/toolsets,
- changing gateway runtime behavior,
- restarting gateway/menu-bar,
- committing implementation code,
- pushing branches,
- merging to fork main.

## Done criteria for this feature

- Matt-local behavior is either plugin/local-app/local-script or behind a small documented seam.
- Future upstream sync inventory shows fewer hot upstream surfaces touched by personal behavior.
- Agent Lights, Telegram, TTS, Kanban, and Apple Calendar tests remain green.
- Gateway and Agent Lights relaunch cleanly after merge.
