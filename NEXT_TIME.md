# Next Time

Updated: 2026-07-31
Project: hermes-agent live local fork
Prepared by: Hermes

## Current state

- Live checkout: `/Users/matt/.hermes/hermes-agent`.
- Branch: `main`; normal publish target remains Matt's `fork/main`. Nous `origin` is intake-only unless Matt explicitly approves an upstream contribution.
- Current local HEAD: `e20e93bd7` — `test(ui-tui): stabilize Ink Vitest resolution`.
- The July 31 integration is anchored by `89360b793` — `Merge origin/main preserving local Hermes workflows`.
- Live working tree was verified clean after restoring a generated-only `package-lock.json` reordering.
- Default, Tate/business, and Aurelius/personal gateways were restarted on July 31 and each has a fresh Telegram connection/running marker. Tate/business holds the singleton Kanban dispatcher lock; Default and Aurelius correctly do not.
- The current TUI was relaunched after the update. Agent Lights is also running.

## What changed in the July 31 update

- Integrated the reviewed Nous intake while preserving Matt-local profile isolation, Codex OAuth routing, Telegram voice/TTS, Agent Lights, Kanban visibility, Apple Calendar safety, input compactor behavior, local slash commands, and profile-specific gateways.
- Hardened the approval/TUI/gateway regression suite after investigating order-sensitive test failures.
- Added the follow-up UI test-harness correction in `e20e93bd7`:
  - Vitest resolves `@hermes/ink` to in-tree source instead of a potentially stale local build artifact.
  - Vitest enforces `NODE_ENV=test` even when the interactive shell exports production mode.

## Verification completed

- Full UI suite: `1,463 passed, 4 skipped`.
- UI typecheck passed.
- All three launchd gateway definitions match the current live Hermes install.
- Fresh post-restart logs confirmed Telegram connection and gateway-running markers for Default, Tate/business, and Aurelius/personal.
- Live Git working tree was clean after the July 31 worktree cleanup.

## Upstream / fork posture

- The fetched `origin/main` currently has three newer commits not integrated locally:
  1. `5835201de` — queued paste payload atomicity fix; overlaps TUI/CLI queue behavior and should be reviewed in an isolated worktree before intake.
  2. `daa1befaf` — JavaScript formatting-only cleanup.
  3. `afc54ca80` — DeepSeek V4 Flash catalog entry.
- Do not run a generic updater or merge these directly into live `main`; the queued-paste change overlaps recently hardened local paths.
- Current `main` is ahead of the fetched `fork/main` by 3,067 commits. This is a publishing/history review item, not a runtime failure. Do not blindly push or force-push; reconcile the fork relationship deliberately in an isolated review.
- No push to Matt's fork or Nous upstream was performed in the July 31 rollout.

## Worktree housekeeping

- Removed six clean historical Hermes sync worktree checkouts from June 23 through July 21, reclaiming 8.06 GiB.
- Preserved their lightweight Git branches as rollback/history references.
- Retained the July 31 baseline/audit worktree and the July 31 sync worktree, along with the live checkout and Dropbox DEV checkout.

## Known preserved invariants

- Preserve Default / Tate / Aurelius profile separation and lane identity.
- Keep normal model routing on Codex OAuth; do not silently introduce API-key or xAI/Grok routing.
- Preserve Telegram STT/TTS, no-duplicate final delivery, Agent Lights lifecycle/slot behavior, Kanban visibility, Apple Calendar safeguards, and local slash commands such as `/nah` and `/repos`.
- Keep Nous upstream as update intake only; Matt's fork/local instance is the normal write target.

## Next likely actions

1. If queued paste behavior matters, review `5835201de` in a new isolated worktree, run focused queue/TUI tests, and ask before promotion or restart.
2. Separately audit the large local-main versus `fork/main` publishing divergence before any push.
3. Do new Hermes implementation work in `/Users/matt/Dropbox/CLIENTS/SAVANT SOFTWARE SYSTEMS/DEV/hermes-agent-dev` or a short-lived feature worktree; keep the live checkout on `main`.
4. Prune the two retained July 31 worktrees only after Matt is comfortable they are no longer useful as audit/rollback surfaces.

## Do not touch without approval

- Do not push to NousResearch/upstream.
- Do not force-push or blindly push `fork/main`.
- Do not merge/rebase upstream commits directly into live `main`.
- Do not restart gateways, Tate, Aurelius, dashboard, or production-like services without explicit approval.
- Do not edit global config, credentials, secrets, or profile runtime state without explicit approval.
- Do not initiate xAI/Grok OAuth or model setup unless Matt explicitly requests it.

## Source-of-truth docs to read next

- `NEXT_TIME.md` first.
- `AGENTS.md`.
- Relevant repo-local docs (`CONTEXT.md`, `docs/agents/`, `docs/adr/`, `docs/product-brief.md` where present).

## Sensitive information check

- [x] No API keys, passwords, tokens, cookies, raw credentials, or personal/private data are included.
- [x] Secrets are referenced only by provider/policy name or approved credential path.
