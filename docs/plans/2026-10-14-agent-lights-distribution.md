# Agent Lights distribution plan (2026-10-14)

Goal: a friend running stock Hermes can install Agent Lights (the macOS
menu-bar dots for Hermes session activity) without running Matt's fork, and
Matt's own builds stop losing the Accessibility grant on every rebuild.

Status: **plan only, awaiting approval.** No runtime code has changed. Hermes
reviews every code change before it goes live; nothing is pushed (the fork on
GitHub is public and deliberately behind live main).

Baseline read for this plan: live main `c37d17fdbd`, upstream
`origin/main` `379cf1220f`, latest upstream tag `v2026.8.31`.

## 1. Recommendation: a plugin, not the fork

**Ship the producer as a standalone Hermes plugin (`agent-lights`) plus the
prebuilt app. Do not ask the friend to run the fork.**

Why:

- The fork's producer is core surgery: `agent/light_cues.py` and
  `agent/wiz_light.py` do not exist upstream, and the call sites are spread
  through `cli.py` (`_emit_light_cue`, `mark_slot_online`),
  `tui_gateway/server.py` (`_get_tui_light_cue_service`, `_block_with_light_cue`,
  retry thread), `tools/kanban_tools.py`, and the Telegram adapter. Running
  the fork means running all of Matt's other divergence too, and tracking a
  private branch that is far ahead of the public one.
- Upstream's plugin surface now reaches every state Agent Lights draws (table
  below). The v2 slot protocol (flock-serialized claims, `process_start_time`,
  claim-time reaping, `app.json` heartbeat) is pure file I/O plus `psutil`,
  which is already an upstream dependency, so a plugin can implement it
  byte-for-byte.
- It matches the repo's own policy for third-party integrations: ship as a
  standalone plugin installed into `~/.hermes/plugins/`, not as core edits.
- Plugins are opt-in upstream (`plugins.enabled` /
  `hermes plugins enable agent-lights`), so it stays off until the friend turns
  it on.

### What the hooks can do (verified against `origin/main`)

| Agent Lights state | Fork emits from | Plugin hook on upstream | Notes |
| --- | --- | --- | --- |
| slot claim | `mark_slot_online` at CLI banner / TUI session open | first relevant hook below | Protocol is reproducible exactly (see §1.2). |
| `working` | start of `chat()` turn | `pre_llm_call` (once per turn, `agent/turn_context.py`) | Also re-enter `working` on `on_human_input_resolved`. Return `None` so nothing is injected into the user message (cache-safe). |
| `human_intervention` | clarify, approval, sudo, secret prompts; `kanban_block` | `on_human_input_request` (`tools/human_input_hooks.py`; fired by clarify, approval CLI + gateway, sudo); `kanban_task_blocked` in workers | `on_human_input_*` landed 2026-10-04 (`c958e1a7b9`) and is **not in any tag yet**. Fallback for `v2026.8.31`: `pre_tool_call` with `tool_name == "clarify"` plus `pre_approval_request`. |
| `final_answer` | after a non-interrupted turn with a response | `post_llm_call` (has `assistant_response`) then `on_session_end` with `completed and not failed and not interrupted` | `on_session_end` is per turn (`agent/turn_finalizer.py`) despite the name. |
| `error` | `result.failed/partial`, or an exception in `chat()` | `on_session_end(failed=True)` | Gap: an exception that escapes `run_conversation` before finalize fires no turn hook. |
| `idle` | startup, interrupt | `on_session_end(interrupted=True)`; `on_session_finalize`/`atexit` releases the slot | |
| Kanban worker pool | `HERMES_KANBAN_TASK` env | same env var is set upstream for workers; `kanban_task_completed` / `kanban_task_blocked` | Same `agents/` pool, same "keep terminal state after exit" rule. |
| context sidecar | `_update_agent_lights_context` | `pre_llm_call` gives `model`, `user_message`, `session_id` | Tab name and chat summary are not available to plugins; the menu falls back to model/state text, which it already does. |

### What the hooks cannot do (gaps, all minor)

1. **No dot at launch.** Nothing fires between CLI/TUI startup and the first
   turn (`on_session_start` fires while building the first turn's prompt).
   `register(ctx)` runs in every process that discovers plugins (gateway,
   cron, `hermes tools`), and classifying the process from argv is a banned
   pattern here, so the plugin must not claim at load. Result: the outline
   dot appears on the first prompt, not when the tab opens. Fixing this
   properly needs an upstream "interactive surface ready" hook; out of scope.
2. **Secret prompts** (TUI `secret.request`) do not go through
   `human_input_request` upstream, so they do not turn the dot yellow.
3. **Pre-finalize exceptions** do not turn the dot red (see table).
4. **Subagents share the process.** `delegate_task` children fire their own
   `pre_llm_call`/`on_session_end`. The plugin keys state to the root session:
   it ignores `pre_llm_call` with a non-empty `parent_session_id` and
   remembers child session ids (`subagent_start`) so their `on_session_end`
   is ignored too. Persistence-disabled forks fire no hooks at all.
5. **Surface filter.** Only `platform in {"cli", "tui"}` or a Kanban worker
   claims a slot. Messaging gateways and the desktop `serve` backend host many
   sessions in one PID, which the one-slot-per-PID protocol cannot represent;
   they are excluded, same as in the fork today.
6. **Hook latency.** Hooks run on the agent thread. The plugin attempts the
   claim mutex non-blocking (fork waits up to 2 s) and retries on the next
   lifecycle event, so a wedged claimant never stalls a turn.

### 1.2 Protocol fidelity

The plugin carries its own copy of the v2 protocol (`SlotStatusFileBackend`
claim/reap/emit/`clear_if_owned`, `agent_lights_root`, the `app.json`
heartbeat check, and the launcher's launch lock), stripped of WiZ and config
plumbing. Because the pool is shared, a conformance test runs the fork's
producer and the plugin's producer against one temp pool concurrently and
asserts the invariants in the README (one owner per slot, no live file
reaped, pruner-compatible payloads).

`agent_lights_root()` uses `get_default_hermes_root()`, which exists upstream,
so profile sessions still share one surface.

**Fork coexistence:** if `agent.light_cues` is importable (Matt's fork), the
plugin logs once and registers nothing, so the two producers never claim
twice for one process.

## 2. App distribution

### Signing identity

Today the launcher ad-hoc signs (`codesign --sign -`), so the designated
requirement is the cdhash and every rebuild drops the Accessibility and
Automation (Terminal) grants.

This Mac has two **Apple Development** identities and **no Developer ID
Application** certificate.

Recommendation, in order:

1. **Developer ID Application + notarization** (needs a paid Apple Developer
   Program membership on one of those teams). The designated requirement is
   then the team-anchored certificate, so grants survive every rebuild on both
   Macs, and the friend's Gatekeeper accepts the download with no override.
2. **If there is no paid membership:** sign with one of the existing Apple
   Development identities. The requirement is still certificate-anchored, so
   grants survive rebuilds, but Gatekeeper will not pass a quarantined copy;
   the friend must use System Settings > Privacy & Security > Open Anyway once
   (or install with the script, see below).

Either way the signing identity becomes a build-script parameter
(`AGENT_LIGHTS_SIGN_IDENTITY`), never hard-coded, and ad-hoc stays the
fallback for contributors with no certificate.

Hardened runtime (`--options runtime`) is required for notarization; the app
needs no extra entitlements (Accessibility and Apple Events are TCC, not
entitlements; `NSAppleEventsUsageDescription` is already in the plist).

### Neutral bundle id

Current id is `com.savant.hermes-agent-lights`. Proposed:
`io.github.mtorto2.agent-lights` (see open questions). Changing it moves the
UserDefaults domain (monitor opacity, prompt marker) and the TCC entries, so
Matt grants Accessibility and Terminal automation once more after the switch.
The bundle **name** stays `AgentLightsMenuBar-v2.app`, so the bundle guard is
untouched; display name stays "Hermes Agent Lights".

### Release build

`scripts/build-release.sh` in `apps/agent-lights-menu-bar/`:

1. `swift test`
2. `swift build -c release --arch arm64 --arch x86_64` (universal, macOS 14+)
3. assemble `AgentLightsMenuBar-v2.app` from a checked-in
   `Resources/Info.plist` (moved out of the Python string in
   `light_cues.py`), with `CFBundleShortVersionString`/`CFBundleVersion` taken
   from a `VERSION` file
4. `codesign --force --options runtime --timestamp --sign "$IDENTITY"`;
   `codesign --verify --strict`; `lipo -archs` check
5. if Developer ID: `xcrun notarytool submit --wait` and `xcrun stapler staple`
6. produce `agent-lights-<version>.zip` containing the app, the plugin
   directory, `install.sh`, `uninstall.sh`, and a short README, plus a
   `SHA256SUMS`

### Install script (`install.sh`)

- Checks macOS 14+, finds the Hermes home (`HERMES_HOME` or `~/.hermes`).
- Gets the app: from the zip it ships in (default), or `--build` to build from
  source with `swift build` and local signing (needs Swift 6 toolchain).
- Quits a running Agent Lights app gracefully (asks first; never `kill -9`),
  then installs to `~/Applications/AgentLightsMenuBar-v2.app` with
  `ditto`, replacing the whole bundle (never writing through an existing
  bundle, matching the launcher's alias-safety rule). Refuses if that path is a
  symlink or if a sibling `AgentLightsMenuBar.app` resolves into it.
- Installs the plugin with `hermes plugins install <path>` and
  `hermes plugins enable agent-lights` (per profile with `--profile`, since
  plugin dirs are per `HERMES_HOME`).
- `--login-item`: writes `~/Library/LaunchAgents/<bundle-id>.plist`
  (`/usr/bin/open -g <app>`, `RunAtLoad`) and `launchctl bootstrap`s it. The
  app's single-instance lock makes a duplicate start harmless.
- Prints the two manual steps: Terminal > Settings > Profiles > Tab > TTY,
  and the Accessibility grant (the app prompts once).

### Uninstall script (`uninstall.sh`)

Quits the app, `launchctl bootout` + removes the LaunchAgent, removes the
app, `hermes plugins disable/remove agent-lights`, and with `--purge` also
`~/.hermes/agent-lights/v2`, the defaults domain, and
`tccutil reset Accessibility|AppleEvents <bundle-id>`.

### Launchers find the installed app

Both producers (the plugin, and the fork's `AgentLightsMenuBarLauncher`) look
for `~/Applications/AgentLightsMenuBar-v2.app` first and open it as-is. Only
the fork's development path still packages `.build/debug` into the repo bundle,
and only when no installed app exists. The plugin never builds or signs.

## 3. Dot-order review follow-ups

1. **Budget check in `TerminalTabBar.firstDescendant`'s child-role loop**
   (`Sources/AgentLightsMenuBar/TerminalTabBar.swift:126`). Each role read is
   an AX round trip; the budget is checked only on entry, so a window with
   many children can overrun the 3 s read budget. Add `try checkBudget(deadline)`
   at the top of the first `for child in children` loop.
2. **Monotonic clock for `TabBarCache` age**
   (`Sources/AgentLightsCore/SlotStatus.swift:560`). It takes `Date`, so a
   wall-clock change can keep stale order alive or drop good order. Switch the
   cache API to `ContinuousClock.Instant` (monotonic and keeps counting
   during sleep, so a pre-sleep read is not "fresh" after wake; plain
   `systemUptime` stops during sleep and would be wrong here). Do the same for
   `terminalSnapshotLoadedAt` in `main.swift`. Tests inject instants.
3. **Smoke test for the once-per-build Accessibility prompt**
   (`main.swift:462`). Extract the decision into a pure Core function
   `shouldPromptForAccessibility(trusted:buildKey:lastPromptedKey:)` with unit
   tests (trusted → never; same build → once; new build → again), and add a
   scripted manual smoke check to the README:
   `tccutil reset Accessibility <bundle-id>`, launch (one prompt), relaunch
   (none), rebuild + launch (one; with stable signing, none after the grant).

## 4. Work list (after approval)

Each step is its own commit, reviewed by Hermes before it goes live.

1. Swift follow-ups from §3, with tests (`swift test`).
2. `Resources/Info.plist` + `VERSION`; bundle id switch; launcher reads the
   plist instead of the inline string.
3. `scripts/build-release.sh` (universal, signing identity parameter,
   optional notarization) and verify on this Mac: `lipo`, `codesign
   --verify`, `spctl -a -vv` (Developer ID only).
4. Plugin `apps/agent-lights-menu-bar/plugin/agent-lights/`
   (`plugin.yaml`, `__init__.py`, `protocol.py`): hook mapping from §1,
   fork-detection no-op, surface filter, subagent filter.
5. Plugin tests: unit tests for the event mapping and protocol; the
   cross-producer conformance test; an E2E run against a temp `HERMES_HOME` on
   a clean upstream checkout (`git worktree` of `origin/main`) driving a real
   CLI turn with clarify and asserting the slot file transitions.
6. Fork launcher prefers the installed app (§2).
7. `install.sh` / `uninstall.sh`, tested on a second macOS user account (fresh
   TCC, fresh defaults) as a stand-in for the friend's Mac.
8. README: install/uninstall, signing, troubleshooting (Ice hiding the item,
   TTY titles, re-granting).

Development stays in this fork under `apps/agent-lights-menu-bar/`; the
plugin directory is outside `plugins/`, so the fork does not auto-discover it.
It can move to a standalone repo when publishing is no longer deferred.

## 5. Open questions for Matt

1. **Apple Developer Program:** is either Apple Development team a paid
   membership, so we can get a Developer ID certificate and notarize? If not,
   OK to sign with "Apple Development: Matthew Tortorich" and have the friend
   use Open Anyway once?
2. **Bundle id:** `io.github.mtorto2.agent-lights`, or something else? (Costs
   one re-grant on your Mac.)
3. **Friend's Hermes version:** do they track upstream `main`, or install
   tagged releases? `on_human_input_request` is only on `main` since
   2026-10-04; on `v2026.8.31` the plugin uses the clarify/approval fallback
   and sudo prompts will not show yellow.
4. **Delivery channel** while GitHub publishing is deferred: a zip via
   Dropbox/AirDrop, or a private repo they get access to?
5. **Should your fork switch to the plugin later?** That would delete the
   core producer edits (`cli.py`, `tui_gateway/server.py`,
   `tools/kanban_tools.py`) and shrink your divergence from upstream, at the
   cost of the gaps in §1 (no dot until the first turn, no yellow for secret
   prompts). Proposed as a separate follow-up, not part of this work.
6. **Interrupt state:** the plugin will show `idle` after an interrupted turn.
   The fork emits nothing on interrupt (neither `cli.py` nor
   `tui_gateway/server.py` ever emits `IDLE` after a turn), so its dot stays
   green until the next turn. Is `idle` right? If so, the fork could be fixed
   to match.
7. **Login item** on by default in the installer, or opt-in (`--login-item`)?
   Proposed opt-in, since any Hermes session launches the app anyway.
