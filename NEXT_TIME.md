# Next Time

Updated: 2026-07-16
Project: hermes-agent live local fork
Prepared by: Hermes

## Current state
- Live checkout: `/Users/matt/.hermes/hermes-agent`.
- Branch: `main`, tracking Matt's `fork/main` production branch.
- Matt fork remote (`fork`) is the normal push target; Nous upstream (`origin`) is intake-only unless Matt explicitly asks otherwise.
- Latest functional change: `0b301a6a699dd7c506ac4bd56983e52f3dca83b7` — `fix(agent-lights): retain lifecycle state during slot retries`.
- The change preserves Agent Lights lifecycle state across concurrent updates, identifies profile-owned slots correctly, and retries allocation when a slot becomes available.
- Targeted verification passed: `429` tests across Agent Lights and TUI gateway/resume coverage.
- This source change is not runtime-active until a gateway/TUI process is explicitly restarted and smoke-tested.

## What changed in the 2026-07-16 Agent Lights/TUI update
- Serialized Agent Lights slot-status writes so the newest lifecycle event wins during concurrent notification polling.
- Propagated `HERMES_HOME` and `HERMES_PROFILE` to TUI launches, preserving Default/Tate/Aurelius slot ownership.
- Retained TUI slots through session teardown and retry registration every 30 seconds when capacity was initially full.
- Added focused regression coverage for profile ownership, lifecycle preservation, retries, and TUI resume behavior.

## Prior 2026-07-08 wrap-up
- Applied PR #2's Desktop onboarding/model-preservation fix into the live checkout via cherry-pick:
  - `c77e334f0 fix(desktop): preserve working model during onboarding (#2)`
- Merged latest fetched Nous upstream into live local `main`:
  - `e83e38c06 Merge latest Nous upstream into live local main`
- Created safety refs before the upstream merge:
  - branch: `backup/pre-nous-local-merge-20260708-144605`
  - tag: `backup/pre-nous-local-merge-20260708-144605-tag`
- Because `fork/main` had the squash commit `19f2354f3` while live had equivalent cherry-pick `c77e334f0`, created a tree-identical ancestry merge so `fork/main` could fast-forward without force:
  - `c12d53dfa Merge remote-tracking branch 'fork/main'`
  - tree before/after that merge was identical.
- Removed the temporary registered PR worktree `/private/tmp/hermes-pr1-narrow` and pruned stale worktree metadata.

## Verification from 2026-07-08
- Desktop focused verification passed:
  - `npm run typecheck --workspace apps/desktop`
  - `npm run test:ui --workspace apps/desktop -- src/store/onboarding.test.ts` (`15 passed`)
  - `npm run build --workspace apps/desktop`
- Upstream-focused Python verification passed:
  - `.venv/bin/python -m pytest tests/agent/test_compression_small_ctx_threshold_floor.py tests/agent/test_context_compressor.py tests/run_agent/test_infinite_compaction_loop.py -q -o 'addopts='` (`180 passed`)
  - `.venv/bin/python -m pytest tests/gateway/test_session_hygiene.py -q -o 'addopts='` (`26 passed`)
- CLI/runtime smoke passed:
  - imports: `cli`, `run_agent`, `hermes_cli.main`, `gateway.run`, `agent.context_compressor`
  - `hermes chat -Q --toolsets safe -q 'Smoke test. Reply exactly: OK'` returned `OK`
- Desktop packaged and launched from:
  - `/Users/matt/.hermes/hermes-agent/apps/desktop/release/mac-arm64/Hermes.app`
  - package build stamp commit: `e83e38c06b590eb95678cc5248b309c76eb14b4b`
  - System Events saw one `Hermes` process after launch.
- Config safety check after Desktop launch stayed intact:
  - `model.provider: openai-codex`
  - `model.default: gpt-5.5`
  - `model.base_url: ''`
- No gateway/Tate/Aurelius restart was performed during this wrap-up.

## Upstream note
- Nous `origin/main` is intake-only. Re-fetch and re-measure divergence before proposing any future upstream sync.
- Do not treat upstream divergence as an error; preserve the local fork's protected behavior and use a reviewable sync branch if Matt requests an update.

## Active/runtime notes
- Desktop app was left running from the newly packaged live build.
- Gateways were not restarted; any already-running gateway/profile processes may still be using their previous loaded code until explicitly restarted.
- Do not restart gateways, Tate, Aurelius, dashboard, or other running processes without explicit approval.

## Known preserved invariants
- Preserve Matt-local features: profile separation, Telegram voice/TTS, Codex OAuth route, Agent Lights/WiZ cues, Kanban worker visibility, Apple Calendar, input compactor, local slash commands such as `/nah`, and profile-specific gateways.
- Active model route should remain Codex OAuth: `openai-codex / gpt-5.5`.
- Do not initiate xAI/Grok OAuth or xAI model setup unless Matt explicitly requests it.

## Next likely actions
1. If Matt wants newer Nous work, start a controlled upstream-sync review with a fresh fetch, scope review, and focused tests before any push.
2. If Matt wants runtime activation, ask before restarting gateways/profiles; then restart and run cheap one-shot checks per profile.
3. Do new Hermes implementation work in the dev worktree or a short-lived feature worktree before promoting it to `main`.
4. Optional later cleanup: review/prune older sync worktrees under `/Users/matt/.hermes/worktrees/` once Matt is comfortable they are no longer needed.
5. Continue using `fork/main` as the live local-fork branch unless Matt explicitly chooses a different branch policy.

## Do not touch without approval
- Do not push to NousResearch/upstream.
- Do not force-push `fork/main`.
- Do not initiate xAI/Grok OAuth or xAI model setup unless Matt explicitly requests it.
- Do not restart gateway, Tate, Aurelius, dashboard, or production-like services without explicit approval.
- Do not edit global config, credentials, secrets, or profile runtime state without explicit approval.
- Do not overwrite dirty work from another active Hermes/VS Code/terminal lane.

## Source-of-truth docs to read next
- `NEXT_TIME.md` first.
- `AGENTS.md`.
- Relevant repo-local docs (`CONTEXT.md`, `docs/agents/`, `docs/adr/`, `docs/product-brief.md` where present).
- Relevant Hermes skills/reference docs for the requested slice.

## Sensitive information check
- [x] No API keys, passwords, tokens, cookies, raw credentials, or personal/private data are included.
- [x] Secrets are referenced only by env var name, vault item, or approved credential path.
