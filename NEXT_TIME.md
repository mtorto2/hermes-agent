# Next Time

Updated: 2026-09-07
Project: Hermes Agent — Matt local fork / live runtime
Prepared by: Hermes

## Active handoff — Blue promotion completed (September 7, 2026)

- **Read this section first.** The August material below is retained as historical background; it does not describe the current live runtime.
- **Live Blue:** `/Users/matt/.hermes/hermes-agent` on local `main` at `d4f4dafee` (`chore: drop case-colliding contributor email file (#88168)`). No remote was pushed.
- **Green evidence checkout:** `/Users/matt/.hermes/runtime/releases/hermes-agent-v0.21.0-20260831-integration-20260906-084314`, branch `candidate/green-integration-20260906-084314`, at the same `d4f4dafee` commit.
- The original frozen Green checkpoint was `d456ba514`; its preserved Blue handoff merge is `1ed8e1863`. The final necessary exception was the deletion-only upstream fix `fcdae2cf0b`, cherry-picked into Green as `d4f4dafee`.
  - It removes `contributors/emails/agent@Agents-Mac-mini.local`, a local-machine artifact that collided with the lowercase sibling on macOS/Windows case-insensitive filesystems.
  - Green then passed a case-fold uniqueness guard, a fresh non-shared macOS checkout with `core.ignorecase=true` and zero dirty paths, and the standard runtime import closure.
- Prior Green evidence remains applicable: focused TUI lifecycle Python suite (`773 passed`), Desktop route/gateway suite (`140 passed`) plus typecheck, Python compile/diff hygiene, and five retries-disabled reruns of the unchanged WeCom/performance cluster (`29 passed, 2 expected xfails`). The old all-extras runner result remains a runner-load caveat, not a clean-pass claim.
- **Approved live rollout completed in order:** Tate/business → Hermes/default → Aurelius/personal.
  - Each Blue runtime was synchronized with `uv sync --locked --extra dev --extra messaging --extra acp`, passed dependency/import closure, and had a clean case-fold source tree before restart.
  - LaunchAgent readiness and human Telegram canaries passed for all profiles: `BUSINESS CANARY OK`, `DEFAULT CANARY OK`, and `PERSONAL CANARY OK`.
  - Temporary one-shot restart scripts/jobs were removed after each verification. No credentials, global profile configuration, plist definitions, or remotes were changed.
- A local source rollback ref remains: `backup/blue-pre-green-20260907-111014` at pre-promotion Blue commit `17f0a34fb0`. It is retained; do not delete it without a separate review.
- **Next safe work:** observe normal traffic. No restart, remote push, upstream intake, or additional source change is implied by this handoff; each requires separate approval.

## Current status — Agent Lights six-slot reconciliation (August 14, 2026)

- **Read this section before the older August 7 closeout below.** That closeout remains historical background; it does not describe the deployed six-slot work.
- The six-slot work was semantically reconciled in `/Users/matt/.hermes/worktrees/agent-lights-reconcile-20260814`, then reviewed, promoted, and published to Matt's fork with Matt's approval.
  - Normal TUI capacity is `1..6`; Kanban worker capacity remains a separate `1..8` pool.
  - Producers and the native menu-bar app use the shared root `~/.hermes/agent-lights` across default, Tate/business, and Aurelius/personal profiles.
  - The primary menu bar renders live idle normal slots as dim outlines and active slots as filled dots; the floating monitor supports up to six normal sessions and retains the eight-worker 4×2 grid.
  - The launcher ad-hoc signs the freshly assembled local app bundle before opening it.
  - The pre-promotion live diff is retained at `~/.hermes/backups/agent-lights-live-prepromotion-20260814-081925/`.
- Fresh isolated verification passed: `52` Agent Lights Python tests; `30` native menu-bar tests; native menu-bar build; and `git diff --check`. Two independent reviews corrected documentation-only contract mismatches.
- Deployment/runtime confirmation completed:
  - The native Agent Lights app was rebuilt, signature-verified, and relaunched from the promoted checkout; exactly one companion process is running.
  - Default, Tate/business, and Aurelius/personal gateways were restarted in the approved order (business → default → personal), reconnected to Telegram, and each processed a normal post-restart message.
  - The live checkout is clean and matches Matt's `fork/main`.
  - The accessible Agent Lights menu currently reports `Hermes: 0 active  Agents: 0 active`, which is expected while no TUI sessions are open.
- Do not create or terminate TUI sessions solely to validate this change.

### Deferred visual check and separate follow-up

1. When Matt naturally has fresh TUI sessions open, capture the Agent Lights menu bar and confirm normal slots/circles `1..6` render as expected. This is the only remaining six-slot validation.
2. Valid PID-matching normal slot artifacts have sometimes disappeared after registration. This is a separate, currently inactive producer-side pruning/cleanup issue; do not claim the six-slot change resolves it. Resume root-cause investigation only if a live TUI loses its slot/light again.
3. No code, gateway, or companion-app change is needed unless that visual check or recurrence reveals a new failure.

## Closed update state

- Live runtime checkout: `/Users/matt/.hermes/hermes-agent`.
- Live branch: `main`; normal publish target: Matt's `fork/main`.
- Nous `origin` remains intake-only unless Matt explicitly approves a new isolated reconciliation.
- This handoff closeout follows the approved frozen runtime target `ad9f67902`:
  - `30d55aa16` — preserved upstream intake plus Matt-local repairs.
  - `a931a117e` — preservation/LSP cleanup repair.
  - `ad9f67902` — Radix focus-cleanup teardown regression coverage.
- The handoff commit itself changes documentation only. It does not require a gateway restart.

## Runtime verification — August 7

- The live tree was clean and passed `git diff --check` before this documentation closeout.
- Default/Hermes, Tate/business, and Aurelius/personal gateway LaunchAgent definitions match the live install and are running.
- All three profiles resolve to `openai-codex / gpt-5.6-terra / fast`.
- Direct one-shot inference smokes passed for all three profiles.
- Parsed logs from each current gateway start showed zero post-start error blocks.
- No gateway, profile configuration, credential, or runtime-service change is part of this closeout.

## Fork and upstream posture

- The prior `fork/main` checkpoint was `ed040aa09`; it is an ancestor of the approved live target.
- The full preserved update range is published to `fork/main` as a normal fast-forward; no force-push and no Nous/upstream push.
- Historical local backup refs remain intentionally preserved at `a942c2bb47`:
  - `backup/frozen-live-20260807-175337-branch`
  - `backup/frozen-live-20260807-175337-tag`
- The obsolete `fork/dev` staging lane was retired after review: it was 10,163 commits behind live `main`, had no unique code, and its one historical handoff patch was already represented on `main`.
- Two unfinished local states were preserved as explicit remote archives rather than merged into production:
  - `fork/archive/queued-paste-wip-20260731` at `8bf081b55` — redundant preservation copy only: the queued/collapsed-paste feature and its tests already exist on `main` at `5835201de`. Do not merge this archive.
  - `fork/archive/interrupted-nous-sync-20260802` at `167ad5c70` — broad obsolete partial upstream intake plus the distinct local profile-isolation commit `d9a153721` and terminal snapshot-hardening WIP. Do not merge wholesale; a future Tate review must carry forward only narrow, tested local slices from a fresh current-`main` worktree.
- The current remote Nous `origin/main` is an unreviewed future intake. Do not merge it merely because this update is complete.

## Worktree closure

- Retired only after clean/idle/reachability checks: the July baseline, the August 6 upstream baseline, two clean July sync worktrees, the obsolete Dropbox `hermes-agent-dev` checkout, and the two now-archived WIP worktrees.
- Retained intentionally:
  - live runtime checkout;
  - August 6 preservation worktree with active process CWDs;
- Git's missing `/private/tmp/hermes-baseline-f4a8eb960` registration was pruned as metadata cleanup only. A full non-Git source snapshot still exists at that path; it is retained and must not be deleted or altered without a separate source-classification review.

## Next safe work

1. Resume normal project work through Tate; Hermes is stable and is not a blocker.
2. Tate remains the normal coding/execution lane. Only when changing Hermes Agent source itself should Tate create a fresh dedicated feature worktree from current `fork/main` or the approved live target; the old `dev` lane no longer exists.
3. Treat a future Nous update as a new, isolated intake with its own preservation review and test gates.
4. Keep the two remote `archive/` branches out of `main` until a focused Tate-led review decides whether to revive, integrate, or retire their WIP.

## Do not touch without a separate approval

- Do not push to NousResearch/upstream.
- Do not recreate a catch-all `dev` lane implicitly; any future Hermes staging/worktree purpose needs a named, reviewed branch.
- Do not merge/rebase future Nous updates directly into live `main`.
- Do not restart gateways, dashboard, Tate, or Aurelius solely for this documentation closeout.
- Do not edit global configuration, credentials, secrets, or profile runtime state.
- Do not delete retained dirty/active worktrees.
- Do not initiate xAI/Grok OAuth or model setup unless Matt explicitly requests it.

## Source-of-truth re-entry

Read this file first, then `AGENTS.md`, then the focused skill/reference for the requested slice. Confirm live Git state and active service status before making any new Hermes operational change.
