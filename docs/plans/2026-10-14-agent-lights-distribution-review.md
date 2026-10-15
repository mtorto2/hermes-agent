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

## Round 2: response to review (changes requested → fixed)

Re-review range: `a881951994..HEAD` (fixes `b11e11269c`, `775839a345`,
`94ab2dec6f`, plus this note). Paths relative to `apps/agent-lights-menu-bar/`.

| Finding | Fix | Proof |
| --- | --- | --- |
| P1 purge under live sessions | New `scripts/agent_lights_pool.py`: `check` before anything is removed; `purge` takes every `.claim.lock` (root, `slots/`, `agents/`; never creates or replaces them), refuses if any `.json`/`.lock`/`app.json` owner PID is alive (PID-only, errs toward refusing), else clears everything but the `.claim.lock` files | `test_purge_refuses_while_a_session_owns_a_slot_and_keeps_its_files` (real owner process; a second claimant still gets slot 2), `..._keeps_the_claim_locks` (lock inode unchanged), `..._waits_out_a_claim_in_progress_then_gives_up`, `test_uninstall_purge_refuses_up_front_while_a_session_owns_a_slot` |
| P2 wrong profile | Both scripts: `env -u HERMES_HOME hermes -p <profile> plugins enable/disable`; targets are profile names (`default` always) mapped to `~/.hermes[/profiles/NAME]` | Stubbed argv tests for install and uninstall; real upstream `hermes` with sticky `active_profile=work` and `HERMES_HOME=<root>` exported: install/uninstall touched only default. The old command line in the same setup went to `work` ("only be enabled or disabled…") |
| P2 blocked worker loses yellow | Worker task outcome kept past the turn: failed → error > blocked → human_intervention > completed → final_answer > interrupted → idle | `test_blocked_kanban_worker_stays_yellow_through_its_turn_end` (reply / no reply / interrupted), `..._completed_kanban_task_stays_red_and_a_failed_turn_still_shows_error`, `..._keeps_its_state_when_the_process_exits` (real files + `clear_if_owned`) |
| P2 subagent approvals | Human-input/approval/clarify events count only while the root turn is open and only from its session id (known children and other ids ignored; events without an id still need the open turn) | `test_background_subagent_approvals_never_touch_a_finished_parent` (both hook paths, with and without `session_id`), `test_subagent_human_input_during_the_parent_turn_is_ignored`; E2E still passes, so the root clarify carries the root session id on both releases |
| P2 uninstall continues if app won't quit | Aborts, removing nothing | `test_uninstall_aborts_before_removing_anything_when_the_app_will_not_quit` (persistent `pgrep` stub) |
| P2 dangling legacy alias | `install.sh` resolves both `AgentLightsMenuBar.app` and its executable path with non-strict `realpath` (follows dangling links) before building | `test_install_refuses_a_dangling_legacy_alias_before_building` |
| P2 relative package path | Output dir made absolute before `git -C` | `test_package_source_honors_a_relative_output_directory` |
| Minor `/Users/matt` fixtures | Swift fixture uses `/Users/someone` | `swift test` 62 passed |

Every new script test fails when run against the reviewed scripts (6 of 6)
and passes on the fixes. Rerun: 808 Python tests across
`tests/agent/test_light_cues.py`, `tests/plugins/test_agent_lights_plugin.py`,
`tests/plugins/test_agent_lights_scripts.py`, `tests/test_tui_gateway_server.py`;
`swift test` 62; `scripts/plugin_e2e.py` PASS on upstream main `7bda2a3368`
and `v2026.8.31`; `git diff --check` clean.

New dependency for `install.sh` / `uninstall.sh --purge`: `python3` (from the
Command Line Tools, which Swift already requires). Release gates in "Not
verified" above are unchanged.

## Round 3: purge synchronization (changes requested → fixed)

Re-review range: `fff204b808..HEAD` (fix `f98c7bbd64`, plus this note). Only
`scripts/agent_lights_pool.py`, `uninstall.sh` (one line) and
`tests/plugins/test_agent_lights_scripts.py` changed.

| Finding | Fix | Proof |
| --- | --- | --- |
| P1 missing claim lock skipped | `purge` creates `slots/`, `agents/` if absent and opens each `.claim.lock` (root, `slots/`, `agents/`) with `O_RDWR|O_CREAT`, no truncate/replace, holding all three before the scan; they are kept by `_clear` | `test_a_first_kanban_worker_cannot_claim_between_the_scan_and_the_clear`: no `agents/` at start; a real core-producer Kanban worker runs between the scan and the clear and gets `none` (blocked, retries later); after purge a worker claims slot 1 normally |
| P2 held `app.lock` deleted | `purge` takes `app.lock` exactly as the app does (`O_RDWR|O_CREAT`, `LOCK_EX|LOCK_NB`); held → refuse (exit 3); file and inode kept by `_clear`. `check` reports a held `app.lock` too. While purge holds it, an app starting then exits as "already running"; the next Hermes session relaunches it | `test_purge_refuses_while_an_app_holds_its_instance_lock_before_its_heartbeat` (lock held, no `app.json`: refused, inode unchanged, second lock still blocked), `test_purge_keeps_the_app_lock_file_so_a_later_app_locks_the_same_file` |
| Found while fixing: pre-check refused whenever the app ran | `uninstall.sh` pre-check uses `check --ignore-app` (it quits the app before purging); the final `purge` still covers `app.json`/`app.lock` | `test_uninstall_purge_quits_a_running_app_then_clears_the_pool` (fails without `--ignore-app`) |

Lock order: root, `slots/`, `agents/` (waiting up to 2 s each), then
`app.lock` (no wait). Producers never hold two claim locks at once and the
app only tries `slots/.claim.lock` non-blocking, so no deadlock.

All four tests touching changed behavior fail on the round-2 helper (4 of 4)
and pass now. Rerun: 812 Python tests across the same four files, `git diff
--check` clean. Swift and E2E unchanged since round 2 (no Swift or plugin
changes).

## Review outcome (2026-10-15)

Hermes, round 3: **PASS** for `fff204b808..efc8eef0df`; the independent
reviewer also passed it. Rounds 1 and 2 findings are all resolved. Hermes
reran 811 Python tests (excluding the Swift-building install test) plus
temporary-pool lock probes.

This is a code-review pass, not release verification. Still open before the
tarball goes to a friend or the branch goes live:

- a fresh macOS account (or the friend's Mac): first Accessibility and
  Terminal-automation prompts, and that a locally built app opens without a
  Gatekeeper prompt;
- a real launch of the installed `~/Applications/AgentLightsMenuBar-v2.app`;
- the visible dot-order smoke test with real Terminal tabs.

Merging to live `main` additionally costs one Accessibility + Terminal
automation re-grant on Matt's Mac (bundle id change) and needs Matt's
approval.

## Round 4 (not yet reviewed): replacing an older install

Range: `792583a141..5ee4d3df4c`. Requested 2026-10-15 because the friend's
Mac has an older Agent Lights that his Hermes agent installed.

- `scripts/legacy_agent_lights.py`: `scan` finds old app bundles (in
  `~/Applications`, `/Applications`, the Hermes checkout's
  `apps/agent-lights-menu-bar/.build`, and via Spotlight by bundle id
  `com.savant.hermes-agent-lights` or name), running old copies, LaunchAgents,
  plugins and pre-v2 pools in every profile, and a Hermes core patch
  (`agent/light_cues.py`). `remove` moves the removable ones into
  `~/.hermes/agent-lights-legacy-backup-<time>/` with `MANIFEST.txt`; never
  deletes, never edits Hermes source; a core patch is reported as blocking.
- `install.sh`: runs the scan before building; stops with the list unless
  `--remove-legacy` (applied after quitting old copies); a core patch stops it
  unless `--allow-core-producer`. Hermes checkout found via `hermes --version`.
- Plugin: stands down only for a core producer whose `_AGENT_LIGHTS_POOL` is
  `"v2"`; beside an older patch it runs (that patch writes a pool the app
  ignores).
- `package-source.sh` writes a `.zip`.

Evidence: 818 Python tests (new: legacy scan/remove/core-block, install.sh
refusal then `--remove-legacy`, core-patch refusal, plugin beside a pre-v2
core), all with `ps`/`mdfind` stubbed so tests never see or move real apps.
The packaged zip, unpacked with `ditto -x -k` (execute bits kept), was run
from a throwaway HOME with an old install planted: refused, then set all six
pieces aside and installed. A read-only `scan` on Matt's Mac correctly
reported his fork's core producer (blocking), the repo-built bundle, the
running app and old per-profile pools.

Delivered to Matt's Desktop: `agent-lights-src-20261015-5ee4d3df4c.zip`
(+ `.sha256`) and `agent-lights-prompt-for-hermes.md`, a step-by-step prompt
for the friend's Hermes agent (inventory, git-restore of a core patch with
his OK and a backup, `install.sh --remove-legacy`, verification).

## Round 5: response to round 4 (changes requested → fixed, not yet reviewed)

Re-review range: `6857d34177..HEAD` (fixes `2ef7220ac7`, `dd0765e75e`, plus
this note) and the rewritten `~/Desktop/agent-lights-prompt-for-hermes.md`.
Zip rebuilt from `dd0765e75e`: `~/Desktop/agent-lights-src-20261015-dd0765e75e.zip`
(+ `.sha256`); the earlier zips were moved off the Desktop.

| # | Finding | Fix | Proof |
| --- | --- | --- | --- |
| 1 | Prompt's `git diff` + `git checkout --` | New `scripts/hermes_core_patch.py` (`inventory` / `backup` / `restore`); the prompt now uses only it. Baseline = merge-base with upstream (else `origin/main`, or `--baseline`); classification per `-U0` hunk; backup = new unique folder with staged/unstaged/committed patches, untracked tar, refs to HEAD and an index+worktree snapshot; restore takes index **and** worktree to the baseline only for `agent-lights-file` / `agent-lights-only` paths, only against a backup fingerprinting the exact current state; `mixed` refused | `tests/plugins/test_agent_lights_core_patch.py`: the reviewer's fixture (staged AL hunk + unrelated unstaged edit) is `mixed`, refused, both preserved in the backup; committed + staged + untracked patch removed with index and worktree equal to stock and unrelated work untouched; stale backup refused; `--yes` required |
| 2 | Backup reuse overwrote data | Backup folder created exclusively (`mkdir`, time + random id), never reused; existing destination refused; manifest appended + fsync per move | `test_backups_are_never_reused_or_overwritten` |
| 3 | Tests could reach real `/Applications` | `AGENT_LIGHTS_SYSTEM_APPLICATIONS` replaces `/Applications`; `AGENT_LIGHTS_LEGACY_CONFINE` refuses moves outside a root; every legacy/install test sets both and stubs `ps`/`launchctl`/`mdfind`/`hermes` | `test_moves_outside_the_allowed_root_are_refused`; sandbox env |
| 4 | Text mention removed a plugin | Only producer code (imports `agent.light_cues`, `SlotStatusFileBackend`, pool paths, `AgentLightsMenuBar`) makes a plugin removable; mentions are report-only and don't block | `test_a_plugin_that_only_mentions_agent_lights_is_reported_not_moved`, `test_report_only_items_and_running_copies_do_not_block` |
| 5 | Spotlight re-found backups | Paths inside `agent-lights-legacy-backup-*`, `agent-lights-core-backup-*` or `.Trash` are ignored | `test_backed_up_apps_found_by_spotlight_are_not_active_installs` |
| 6 | Standalone scan flagged the new login item | Default `--new-bundle-id` read from `Resources/Info.plist` | `test_standalone_scan_knows_the_current_login_item` |
| 7 | Old `agent-lights` plugin deleted unsaved | Existing `plugins/agent-lights` is backed up unless identical to the new one (a previous install of this plugin automatically, via `remove --auto-only`; anything else needs `--remove-legacy`). Same for anything at the new app path whose bundle id isn't the new one | `test_an_existing_agent_lights_plugin_is_backed_up_unless_identical`, `test_another_app_at_the_new_apps_path_is_backed_up`, `test_reinstalling_after_a_local_plugin_edit_backs_it_up_without_extra_flags` |
| 8 | Alias moved, real app left | Alias and target are separate findings (dedup on the unresolved path); post-move check that every item is gone | `test_an_alias_and_the_app_it_points_to_are_both_set_aside` |
| 9 | Core status masked failures | Any failure returns 12 before the core status is considered | `test_failures_win_over_an_allowed_core_patch` |
| 10 | Unload / process failures ignored | `launchctl print` before and after `bootout` (not loaded = fine; still loaded = failure, plist not moved); old processes rechecked after removal; plugin-disable failure is noted but the directory is still moved (that is what stops it loading) | `test_a_login_item_that_will_not_unload_or_an_old_copy_still_running_fails` |

Also found while fixing: a report-only plugin made every later install (the
update path) demand `--remove-legacy`; now report-only items don't block. A
running old copy's executable path now leads the scan to its bundle.

Evidence: 835 Python tests across `tests/agent/test_light_cues.py`,
`tests/plugins/test_agent_lights_plugin.py`, `test_agent_lights_scripts.py`,
`test_agent_lights_core_patch.py`, `tests/test_tui_gateway_server.py`.
The Desktop zip, unpacked with `ditto -x -k` into a throwaway HOME with every
old-install shape planted: plain install refused; `--remove-legacy` set all
six aside and installed, keeping the mention-only plugin and new pieces; a
plain re-run then succeeded. Read-only `scan` and `inventory` on Matt's Mac
ran cleanly (baseline = merge-base with `fork/main`). Swift code unchanged
since round 2.

## Round 6: response to round 5 (changes requested → fixed, not yet reviewed)

Re-review range: `46e3efadda..HEAD` (fixes `f42dbca5af`, README `dcc2afdd47`,
plus this note) and the updated `~/Desktop/agent-lights-prompt-for-hermes.md`.
Zip rebuilt from `dcc2afdd47`: `~/Desktop/agent-lights-src-20261015-dcc2afdd47.zip`
(+ `.sha256`); earlier zips moved off the Desktop.

| # | Finding | Fix | Proof |
| --- | --- | --- | --- |
| 1 | Text authorized removal | Classification is eligibility only. A hunk is Agent Lights only if **every added line** (what a restore discards) names an Agent Lights symbol or is bare syntax (brackets, `try:`, `pass`, `except …:`), and pure-deletion hunks never are. New files are eligible only at known Agent Lights paths (`agent/light_cues.py`, `agent/wiz_light.py`, their tests, `apps/agent-lights-menu-bar/…`); others that mention it are `review`. `restore` additionally needs `--approved` = the digest printed by the new `review` command over exactly the changes it will discard. The prompt now calls categories "the tool's guess", has the agent show `review` output and get the friend's confirmation, and never restores `mixed`/`review` paths | `test_adjacent_agent_lights_and_unrelated_edits_are_mixed_and_refused`, `test_a_new_file_that_only_mentions_agent_lights_is_review_and_stays`, `test_restore_needs_the_digest_of_exactly_what_was_reviewed` |
| 2 | Hidden staged change | Index (baseline→index) and working tree (index→working tree) classified separately; either layer's unrelated hunk makes the file `mixed` | `test_an_unrelated_staged_edit_hidden_by_the_working_tree_blocks_restore` (staged tweak survives in the index) |
| 3 | Auto plugin removal outside install targets | Scanner gets `--install-profile` per profile this install writes (none with `--no-plugin`); automatic backup/replacement only there; a current plugin in another profile is left alone | `test_automatic_plugin_backup_only_in_profiles_this_install_writes`, `test_no_plugin_install_and_other_profiles_leave_plugins_alone` (replaces the test that accepted removal) |
| 4 | Unrecognized destination contents | Anything at `~/Applications/AgentLightsMenuBar-v2.app` that isn't a previous install of this app (directory with the executable and the new bundle id), and any file or link at an install-target `plugins/agent-lights`, is a finding before replacement | `test_files_and_links_at_the_install_paths_are_caught`, `test_unrecognized_contents_at_the_new_app_path_survive_install` (refused, then backed up with `--remove-legacy`) |
| 5 | Comments counted as code | Producer evidence from the Python AST only: imports of `agent.light_cues`, Agent Lights class/function names, string literals in code naming the app or pool; bare string statements (docstrings) and comments ignored | `test_comments_and_docstrings_are_not_producer_code` |
| 6 | Symlinked plugins skipped | Links are resolved and judged by their target's code; a producer link is moved (link only, target kept); links to the current plugin are left alone | `test_a_symlinked_old_plugin_is_found_and_only_the_link_moves` |
| 7 | README recovery paths | New `recover --backup DIR --yes`: reset to the recorded HEAD, apply non-empty staged (`--index`) and unstaged patches, restore untracked files, verify against the recorded fingerprint. README gives that command and by-hand commands with absolute, `shlex`-quoted paths | `test_recover_puts_the_checkout_back_exactly`, `test_the_readme_commands_also_recover_exactly` (runs the README lines with bash; committed + staged + unstaged + untracked + binary state, fingerprint equal) |

Evidence: 846 Python tests across the five suites. All three helper
scripts run on the Command Line Tools' Python 3.9.6 (what the friend's
`python3` likely is). From the unzipped Desktop zip: the install harness
(old install planted in a throwaway HOME) refused, set all six aside with
`--remove-legacy` keeping all six new/unrelated pieces, and a plain re-run
succeeded; the core flow `inventory → backup → review → restore → recover`
under `/usr/bin/python3` on a git fixture removed only the Agent Lights
changes (untracked `notes.md` kept) and `recover` returned the exact prior
state.
