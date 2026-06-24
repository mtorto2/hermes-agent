# Next Time

Updated: 2026-06-23
Project: hermes-agent live local fork
Prepared by: Hermes

## Last known state
- Matt's live local Hermes Agent checkout is `/Users/matt/.hermes/hermes-agent`.
- Live branch is `main`; Matt's fork remote is the normal preservation/push target (`fork/main`).
- Upstream Nous remote is intake only unless Matt explicitly asks for upstream push/PR.
- Default/Hermes, Tate/business, and Aurelius/personal gateways were updated, restarted, and verified after a two-round upstream sync.
- Active model route for all three profiles is Codex OAuth: `openai-codex / gpt-5.5` via `https://chatgpt.com/backend-api/codex`.
- Do not initiate xAI/Grok OAuth unless Matt explicitly asks; Matt currently lacks paid X/xAI access and xAI auth popups were an annoyance during QA.

## Last verified
- Hermes live repo was clean and synced to Matt's fork after the round-2 update:
  - `git status --short --branch`: `## main...fork/main`
  - live/fork HEAD: `607ef8cec Merge latest upstream Nous main into round-2 sync branch`
  - upstream Nous HEAD at final fetch: `6e88f7b6f feat(relay): Phase 5 Unit C — wake primitive (gateway side) (#51595)`
- Gateways running after restart:
  - default PID `67923`
  - business/Tate PID `67964`
  - personal/Aurelius PID `67991`
- Dashboard rebuilt/restarted on `127.0.0.1:9119`; `/openapi.json` reported version `0.17.0`.
- One-shot profile checks passed:
  - `default-round2-ok`
  - `business-round2-ok`
  - `personal-round2-ok`
- Matt also smoke-tested Telegram voice and reported it looked good.
- Juror Research checkout was fast-forwarded cleanly:
  - repo: `/Users/matt/Dropbox/CLIENTS/SAVANT SOFTWARE SYSTEMS/DEV/juror-research`
  - branch: `dev`
  - remote: `origin git@github.com:roryjavant/juror-research.git`
  - HEAD: `f06dc6a Add retained relevance enrichment workflow`
  - status: `## dev...origin/dev`

## QA summary from 2026-06-23 update
- Round 1 synced and stabilized Hermes through `2c4b83580 Stabilize desktop tests after upstream sync`.
- Round 2 merged latest fetched Nous updates through `607ef8cec`, preserving Matt-local features.
- Conflict resolutions preserved both sides:
  - `cli.py`: Matt-local input compactor plus upstream Petdex mascot state.
  - `tui_gateway/server.py`: Matt-local `/nah` plus upstream `/learn`.
- Additional local fix committed during round 2:
  - `06c2ce85d fix(slack): route update through Hermes catchall`
- Focused tests passed for gateway/Telegram voice/STT/approvals/TUI/Apple Calendar/Agent Lights/WiZ/relay/tool-call persistence.
- UI-TUI passed: 101 files, 1083 tests, 4 skipped; build passed.
- Web dashboard passed: typecheck/tests/build.
- Desktop passed after clean lockfile install with dev deps:
  - typecheck passed
  - platform tests: 224 passed, 1 skipped
  - UI tests: 107 files, 807 tests passed
  - build passed
- Final canonical runner still has known local/macOS/hermetic failures but improved versus baseline:
  - before: 26 files / 86 failures
  - after: 24 files / 84 failures

## Known non-blocking notes
- Remaining canonical failures are known local/macOS/hermetic buckets: Anthropic/Keychain, systemctl/live-system guard, WSL/gateway service assumptions, ntfy/session split baseline, file-tools temp/path guards, voice-mode Pulse/AF_UNIX path behavior, and terminal config env-sync harness issues.
- Kanban board picker counts include archived tasks. Hermes Agent showed `22` and Juror Research showed `6` because all tasks on those boards are archived; normal lanes hide archived tasks unless `Show archived` is checked. Later UI improvement: show `active / archived` counts.
- The round-2 sync worktree was intentionally left as an audit/rollback reference:
  - `/Users/matt/.hermes/worktrees/hermes-nous-sync-round2-20260623`
  - safe to prune later once Matt is comfortable.

## Next likely action
1. Before any Hermes Agent edits, read `AGENTS.md` and run repo/status/remotes preflight.
2. If work involves Hermes itself — gateway, profiles, voice, Kanban, cron, providers, dashboard, desktop, TUI, plugins, auth, or config — load the `hermes-agent` skill first.
3. If touching Juror Research, read that repo's `AGENTS.md`, `CONTEXT.md`, and `docs/agents/project-map.md` before domain-heavy work.
4. Optional cleanup when stable: prune the round-2 sync worktree and consider a small Kanban UI label fix for active vs archived task counts.

## Open questions / follow-ups
- [ ] Do we want to patch Kanban board labels to display active vs archived counts?
- [ ] Do we want to prune `/Users/matt/.hermes/worktrees/hermes-nous-sync-round2-20260623` after a day of stable use?
- [ ] Do we want to track/fix the known canonical local/macOS/hermetic failure buckets, or leave them as baseline noise?

## Do not touch without approval
- Do not push to NousResearch/upstream.
- Do not initiate xAI/Grok OAuth or xAI model setup unless Matt explicitly requests it.
- Do not commit, push, merge, rebase, force-push, restart gateway, edit global config, change credentials, deploy, or modify production services without explicit approval.
- Do not overwrite dirty work from another active Hermes/VS Code/terminal lane.

## Git / branch / commit
- Live repo: `/Users/matt/.hermes/hermes-agent`
- Branch: `main`
- Matt fork remote/tracking: `fork/main`
- Last verified HEAD: `607ef8cec047cb95e66dcfb18c29bb4d37deb5ba`
- Juror Research repo: `/Users/matt/Dropbox/CLIENTS/SAVANT SOFTWARE SYSTEMS/DEV/juror-research`
- Juror Research branch: `dev`
- Juror Research last verified HEAD: `f06dc6a157798e5d805da912acaee94f91e1c342`

## Source-of-truth docs to read next
- `NEXT_TIME.md` first
- `AGENTS.md`
- Relevant repo-local docs (`CONTEXT.md`, `docs/agents/`, `docs/adr/`, `docs/product-brief.md` where present)
- Relevant Hermes skills/reference docs for the requested slice

## Sensitive information check
- [x] No API keys, passwords, tokens, cookies, raw credentials, or personal/private data are included.
- [x] Secrets are referenced only by env var name, vault item, or approved credential path.
