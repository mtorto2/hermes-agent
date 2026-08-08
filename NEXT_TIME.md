# Next Time

Updated: 2026-08-07
Project: Hermes Agent — Matt local fork / live runtime
Prepared by: Hermes

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
- Two unfinished local states were preserved as explicit remote WIP archives rather than merged into production:
  - `fork/archive/queued-paste-wip-20260731` at `8bf081b55` — queued/collapsed paste submission feature and tests.
  - `fork/archive/interrupted-nous-sync-20260802` at `167ad5c70` — interrupted intake index plus terminal snapshot-hardening WIP.
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
