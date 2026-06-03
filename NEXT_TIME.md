# Next Time

Updated: 2026-06-03
Project: hermes-agent-dev
Prepared by: Hermes

## Last known state
- Matt's local Hermes Agent development checkout.
- Current lane is local/fork development; upstream Nous is update intake, Matt's fork is the normal preservation remote unless explicitly overridden.
- Recent local history includes agent-lights/profile sharing, web audit patching, and uv lock refresh.

## Last verified
- `git status --short --branch`: `## dev...fork/dev`.
- Recent commits at time of handoff: `45ac499d5 fix: share agent lights slots across profiles`, `2254407c1 fix(web): patch brace-expansion audit advisory`, `47d4d668b chore: refresh uv lock after upstream sync`.
- Source docs found: `README.md`, `AGENTS.md`.

## Next likely action
1. Before any Hermes Agent edits, read `AGENTS.md` and run a repo/status/remotes preflight.
2. If work involves gateway, profiles, voice, Kanban, cron, providers, or Hermes config, load the `hermes-agent` skill first.
3. Preserve dirty work on Matt's fork/local branch before any upstream sync or restart-sensitive change.

## Open questions
- [ ] What is the next Hermes Agent slice: upstream sync, agent lights QA, voice/gateway reliability, Kanban, or profile operations?

## Do not touch without approval
- Do not push to NousResearch/upstream.
- Do not commit, push, merge, rebase, force-push, restart gateway, edit global config, or change credentials without explicit approval.
- Do not overwrite dirty work from another active Hermes/VS Code/terminal lane.

## Git / branch / commit
- Repo: `/Users/matt/Dropbox/CLIENTS/SAVANT SOFTWARE SYSTEMS/DEV/hermes-agent-dev`
- Branch: `dev`
- Commit: see current `git log`; last observed `45ac499d5`.
- Working tree: clean when checked on 2026-06-03 before this `NEXT_TIME.md` was created.
- Remote/tracking: `fork/dev`

## Source-of-truth docs to read next
- `NEXT_TIME.md` first
- `AGENTS.md`
- `README.md`
- Relevant Hermes skill/reference docs for the requested slice

## Sensitive information check
- [x] No API keys, passwords, tokens, cookies, raw credentials, or personal/private data are included.
- [x] Secrets are referenced only by env var name, vault item, or approved credential path.
