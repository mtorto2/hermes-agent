# Review request: Agent Lights distribution (2026-10-14)

For: Hermes. From: a Claude Code session, at Matt's request.

- **Branch:** `claude/intelligent-euclid-c3fce5` (worktree
  `~/.hermes/hermes-agent/.claude/worktrees/intelligent-euclid-c3fce5`).
- **Base:** live `main` `c37d17fdbd`. Review range: `c37d17fdbd..claude/intelligent-euclid-c3fce5`
  (10 commits, 18 files). Not pushed, not merged, not live.
- **Plan and decisions:** `docs/plans/2026-10-14-agent-lights-distribution.md`.

## What it does

Lets a friend on stock upstream Hermes use Agent Lights from a source tarball,
without the fork and without an Apple Developer account. Matt approved the plan
and its defaults on 2026-10-14.

| Commit | Change | Runtime impact on live Blue |
| --- | --- | --- |
| `311ede61ad` | Swift: budget check per child role read in `TerminalTabBar.firstDescendant`; `TabBarCache` + snapshot age on `ContinuousClock`; prompt decision extracted to `AccessibilityPrompt` with tests | App rebuild only |
| `b422994271` | `Resources/Info.plist` checked in; bundle id `com.savant.hermes-agent-lights` → `io.github.mtorto2.agent-lights`; fork launcher copies it | **One Accessibility + Terminal automation re-grant**; monitor opacity default resets |
| `5f0614854d` | Plugin `apps/agent-lights-menu-bar/plugin/agent-lights/` for stock Hermes | None: inactive on this fork (`agent.light_cues` present) and outside `plugins/`, so not discovered |
| `e396b66f30` | `scripts/plugin_e2e.py` | None |
| `4c414f0c4a` | Fork launcher opens `~/Applications` or `/Applications` `AgentLightsMenuBar-v2.app` first | None until an app is installed there |
| `c2f7523a34` | `install.sh`, `uninstall.sh`, `scripts/package-source.sh` | None unless run |
| `9bebc9ef91` + plan commits | README install guide; plan doc | Docs |

## Please check in particular

1. **Protocol copy.** `plugin/agent-lights/protocol.py` copies
   `SlotStatusFileBackend` from `agent/light_cues.py`. Intended differences
   only: not frozen (model name updated per turn), `_CLAIM_MUTEX_TIMEOUT_S`
   0.2 s instead of 2 s (hooks run on the agent thread; retried next event),
   no WiZ/config plumbing, installed-app launcher instead of repo packaging.
   Check nothing else drifted. `test_core_and_plugin_claimants_racing_never_share_a_slot`
   races both producers over one pool.
2. **Hook mapping** (`plugin/agent-lights/__init__.py`): cli/tui platforms and
   Kanban workers only; subagents filtered via `subagent_start`;
   `pre_llm_call` returns `None` (no prompt injection, cache-safe);
   `on_session_end` is treated as per-turn. Interrupt → idle (the fork leaves
   the dot green; deliberate divergence, see plan §5).
3. **Bundle id switch** and the re-grant it costs on Matt's Mac.
4. **install.sh safety:** replaces the bundle with `rm -rf` + `ditto` after
   refusing symlinked destinations and a sibling `AgentLightsMenuBar.app`
   that resolves into it; quits a running app with SIGTERM only after asking.
5. **Fork launcher preference** (`_installed_app`): once Matt installs with
   `install.sh`, repo `swift build` output is no longer opened; re-run
   `install.sh` to update.

## Evidence (all run 2026-10-14)

- `swift test`: 62 passed.
- `scripts/run_tests.sh tests/agent/test_light_cues.py tests/plugins/test_agent_lights_plugin.py tests/test_tui_gateway_server.py`: 790 passed.
- `scripts/plugin_e2e.py` (real plugin enable + one real CLI-platform agent
  turn against a local fake model calling `clarify`): states
  `working → human_intervention → working → final_answer` on upstream main
  `348f145164` (`on_human_input_*` hooks) and on `v2026.8.31` (fallback
  hooks). Python 3.14 venvs; upstream pins need 3.14.
- Packaged tarball, unpacked, `install.sh --profile work --login-item` and
  `uninstall.sh --profile work --purge --yes` in a throwaway `HOME` with
  `pgrep/pkill/open/launchctl/tccutil/defaults` stubbed: bundle valid
  (ad-hoc, `nlink=1`), plugin enabled in both homes, LaunchAgent lints; full
  removal. Symlinked destination refused. The live app was never touched.

## Not verified

- No run on a second macOS account or the friend's Mac: real TCC prompts,
  Gatekeeper behavior for a locally built app, and a real `open` of the
  installed bundle are unobserved.
- The installed app was not launched (Matt's live v2 app holds the instance
  lock).

## Not requested

No merge to `main`, no push to `fork`, no app rebuild/relaunch on the live
machine. Each needs Matt's separate approval after review.
