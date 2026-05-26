# Matt-local customization quarantine strategy

Date: 2026-05-25
Branch: `refactor/quarantine-matt-customizations-20260525-220926`
Base upstream checked during audit: `origin/main` at `cea87d913`
Fork head checked during audit: `32cee06b6`

## Objective

Keep Matt's Hermes fork easy to sync with `NousResearch/hermes-agent` by moving personal/local behavior out of hot upstream files wherever possible.

The goal is **not** to remove Matt's features. The goal is to make each feature either:

1. **Upstreamable** — a general bug fix or extension seam that belongs in Hermes core.
2. **Quarantined** — isolated in a plugin, local app, local script, or documented profile config.
3. **Seam-backed** — implemented behind a small, stable hook in core, with the personal behavior outside core.

Future upstream syncs should become a predictable check of a few seam files, not a large manual comparison of fork-specific behavior across CLI, gateway, TUI, tools, and plugins.

## Current fork delta snapshot

As of this audit:

```text
origin/main...HEAD = 0 behind / 38 ahead
```

Matt-local changes currently touch several high-churn upstream surfaces:

- `cli.py`
- `gateway/run.py`
- `gateway/platforms/base.py`
- `gateway/platforms/telegram.py`
- `tui_gateway/server.py`
- `tools/kanban_tools.py`
- `toolsets.py`
- `hermes_cli/commands.py`
- `hermes_cli/config.py`
- `hermes_cli/kanban*.py`

Those files should be treated as **conflict-prone surfaces**. New personal behavior should not be added there unless it creates a general hook/seam or fixes a general upstream bug.

## Policy for future Matt-local work

### Default rule

Do not implement Matt-personal behavior directly in Hermes core.

Prefer, in order:

1. User plugin under `~/.hermes/plugins/<name>/` when it should not be committed to the fork.
2. Bundled local plugin under `plugins/local/<name>/` when it should travel with Matt's fork.
3. Local app under `apps/<local-app>/` when it is a companion UI/process.
4. Local script under `scripts/<name>.py` when it is operator-only and not part of Hermes runtime.
5. Tiny upstream-style hook/seam in core, with implementation in one of the locations above.

### Merge-risk rule

If a change edits `cli.py`, `gateway/run.py`, `gateway/platforms/telegram.py`, `tui_gateway/server.py`, or `tools/kanban_tools.py`, ask:

- Is this a general upstream bug fix? If yes, keep it small and test it.
- Is this a stable extension seam? If yes, keep the core diff minimal and plugin-neutral.
- Is this Matt-personal behavior? If yes, stop and move it behind a plugin/hook/local module.

### Config rule

Use namespaced config for local behavior:

```yaml
local:
  agent_lights: {}
  telegram_voice_ux: {}
  personality_voice: {}
  repo_status: {}
```

Keep broadly useful/core config under existing upstream namespaces only when the behavior is general.

## Classification of current local features

| Area | Current merge risk | Preferred destination | Notes |
|---|---:|---|---|
| Agent Lights / slot files / menu bar | High | `plugins/local/agent_lights/` + `apps/agent-lights-menu-bar/` | Needs lifecycle hooks so core only emits semantic events. |
| WiZ light cues | High | `plugins/local/agent_lights/` backend/provider | Hardware-specific. Keep implementation outside core once backend registry exists. |
| Telegram light-cue menu | High | `plugins/local/agent_lights/telegram_menu.py` | Needs gateway command/callback extension seam. |
| Telegram voice transcript UX | Medium | `plugins/local/telegram_voice_ux/` | Personal UX; use response transform/media hooks when available. |
| TTS media routing/session context | Low/Medium | Upstream bug fix | General correctness. Do not quarantine if upstream accepts it. |
| Apple Calendar tool | Medium | `plugins/local/apple_calendar/` | macOS/EventKit/user-local. Avoid `toolsets.py` core edits. |
| Personality-to-TTS voice mapping | Medium | `plugins/local/personality_voice/` | Personal preference. Needs personality/session hook or TTS override hook. |
| HeyGen helper | Low | `plugins/local/heygen/` or `scripts/` | API-key metadata should eventually be plugin-contributed. |
| Repo status command | Medium/High | `plugins/local/repo_status/` | Needs shared slash-command extension seam. |
| Kanban repo guardrails | High | Core policy hooks + local policy plugin | The seam is general; Matt's local policy can be plugin/config. |
| Kanban run usage ledger | Medium | Upstreamable generic metadata | Keep generic and tested. |
| Kanban audio status report | Medium | Local gateway/kanban UX plugin | Personal delivery UX. |
| Holographic FTS query normalization | Low | Upstream bug fix | General plugin bug fix; do not quarantine. |
| macOS temp-path file safety fix | Low/Medium | Upstream bug fix | General safety correctness. |
| launchd gateway status probe | Low | Upstream bug fix | General macOS reliability. |
| Telegram robustness fixes | Low | Upstream bug fix | General adapter correctness. |

## Extension seams to prioritize

These are the highest-ROI seams for making future syncs easy.

### 1. `on_turn_lifecycle`

Core emits semantic lifecycle state. Plugins decide whether to update LEDs, menu bar, status files, dashboards, or notifications.

Proposed hook:

```python
on_turn_lifecycle(
    *,
    state: str,  # queued | started | waiting_for_human | resumed | completed | failed | interrupted | idle
    surface: str,  # cli | tui | gateway | kanban | subagent
    session_id: str | None = None,
    platform: str | None = None,
    chat_id: str | None = None,
    user_id: str | None = None,
    reason: str | None = None,
    metadata: dict | None = None,
) -> None
```

First mappings for Agent Lights:

```text
started            -> working
waiting_for_human  -> human_intervention
resumed            -> working
completed          -> final_answer
failed             -> error
idle               -> idle
```

### 2. `on_light_cue`

A transitional hook around existing light-cue emitters. Useful while migrating current implementation.

Proposed hook:

```python
on_light_cue(
    *,
    event: str,
    surface: str,
    session_id: str | None = None,
    chat_id: str | None = None,
    platform: str | None = None,
    source: str | None = None,
    metadata: dict | None = None,
) -> None
```

Start observer-only. Do not let plugins suppress core behavior until tests are solid.

### 3. Plugin-contributed gateway commands and callbacks

Needed to remove Telegram-specific menu code and local slash commands from core.

Proposed capability:

```python
ctx.register_gateway_command(
    name: str,
    handler: Callable,
    description: str = "",
    platforms: list[str] | None = None,
    auth: str = "same_as_chat",
)

ctx.register_gateway_callback(
    prefix: str,
    handler: Callable,
    platforms: list[str] | None = None,
)
```

For Telegram light controls, the plugin would own callback data like `lc:<mode>`.

### 4. Plugin-contributed toolsets/categories

Needed to move Apple Calendar out of core without losing model visibility.

Proposed capability:

```python
ctx.register_toolset(
    name: str,
    tools: list[str],
    description: str = "",
)
```

### 5. Plugin-contributed credential/env metadata

Needed for HeyGen and future local integrations without editing `hermes_cli/config.py`.

Proposed capability:

```python
ctx.register_env_var(
    name: str,
    description: str,
    category: str = "tool",
    secret: bool = True,
    install_hint: str = "",
)
```

### 6. Kanban policy hooks

Needed to keep Matt's repo-safety rules local while making the seam generally useful.

Proposed hooks:

```python
kanban_pre_dispatch(task, workspace, board, metadata) -> allow/block/advisory
kanban_post_run_complete(task, run, metadata) -> None
kanban_task_state_changed(task, old_status, new_status, reason, metadata) -> None
```

## Target local layout

Preferred fork-local committed layout:

```text
plugins/local/
  agent_lights/
    plugin.yaml
    __init__.py
    light_cues.py
    wiz_backend.py
    slot_status.py
    telegram_menu.py
  apple_calendar/
    plugin.yaml
    __init__.py
    apple_calendar_tool.py
  personality_voice/
    plugin.yaml
    __init__.py
    voice_mapping.py
  repo_status/
    plugin.yaml
    __init__.py
    repo_status.py
  telegram_voice_ux/
    plugin.yaml
    __init__.py
  heygen/
    plugin.yaml
    __init__.py
    heygen_generate.py
```

Preferred user-local/private layout when code should not be in the fork:

```text
~/.hermes/plugins/<name>/
~/.hermes/scripts/<name>.py
```

## Sync workflow after quarantine

For each upstream sync:

1. Fetch upstream and fork.
2. Create a sync branch from fork main.
3. Merge upstream main.
4. Check fork delta:

   ```bash
   git diff --name-status origin/main...HEAD
   git log --oneline origin/main..HEAD
   ```

5. Pay special attention only to seam files and upstreamable bug-fix files.
6. Run targeted quarantine gates:

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
   
   cd apps/agent-lights-menu-bar && swift test
   ```

7. If all pass, merge to fork main with human approval.

## Immediate recommendation

Do not try to migrate all current customizations tonight. That would create regression risk.

Best immediate move:

1. Commit this strategy note plus a read-only inventory helper.
2. In the next implementation session, add observer-only lifecycle hooks first.
3. Move Apple Calendar/HeyGen/repo-status to plugins before touching Agent Lights.
4. Move Agent Lights only after lifecycle hooks are tested across CLI, TUI, gateway, and Kanban.

This sequencing gives Matt stability now and lowers future merge pain without destabilizing the working Hermes build.
