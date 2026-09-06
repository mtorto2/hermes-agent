# Hermes Green Runtime Candidate Provenance

## Candidate identity

- **Candidate path:** `/Users/matt/.hermes/runtime/releases/hermes-agent-v0.21.0-20260831`
- **Source (Blue, read-only):** `/Users/matt/.hermes/hermes-agent`
- **Frozen tag:** `v2026.8.31`
- **Required and resolved commit:** `29112bef099274229cadff79cdff7bf7b99c4b77`
- **Checkout mode:** detached `HEAD` at the frozen tag
- **Candidate remote:** `origin = git@github.com:NousResearch/hermes-agent.git`

## Local-only construction

This candidate was created with a local, no-network Git clone from the Blue source using `git clone --no-local --no-checkout`, then checked out from the locally available frozen tag. No fetch, push, remote clone, package/venv install, extension install, commit, or service action was performed. After the local clone, the candidate-local `origin` remote URL was set to the known upstream URL above without fetching.

## macOS case-collision handling

A normal detached checkout of the frozen tag could not be clean on this case-insensitive macOS filesystem because the tag contains this case-colliding pair:

- `contributors/emails/agent@Agents-Mac-mini.local`
- `contributors/emails/agent@agents-Mac-mini.local`

Only the permitted non-runtime sparse exclusion was applied:

```text
/*
!/contributors/emails/
```

No other source paths were excluded or altered.

## Pre-provenance checks

Before this file was created, the candidate was verified as follows:

- Repository root exactly equals the candidate path.
- `HEAD` equals `29112bef099274229cadff79cdff7bf7b99c4b77`.
- `HEAD` is detached and exactly describes as `v2026.8.31`.
- Candidate porcelain status was clean.
- Candidate Git metadata is an independent `.git` directory at `<candidate>/.git`, not a Blue shared worktree.
- Candidate `origin` equals the known upstream URL above.

## Blue and runtime safety boundary

At preflight, Blue `HEAD` was `3429bf1f1d82f8b0da9ad794bd4cb2cb893278b4`; its only reported working-tree path was the pre-existing modified `NEXT_TIME.md`. Blue was read only and was not modified. `runtime/current` was absent, and no runtime/current symlink was created or changed.

No active state, profile, session, auth, credential, cron, configuration, gateway, TUI, LaunchAgent, or other service action was taken. Pre-existing Hermes-related processes were observed only and not touched.

## TG-VOICE runtime compatibility checkpoint (candidate-local)

- **Local candidate branch:** `candidate/tg-voice-01`
- **Applied source checkpoint:** `8db885ebd352218cce3e0942e59c888e568f9934` (local detached worktree only)
- **Cherry-pick result:** `7649f0286c84c5b3219fc4c14414b8c553f8017d` (`-x` provenance retained)
- **Parent baseline:** `29112bef099274229cadff79cdff7bf7b99c4b77` (`v2026.8.31`)
- **Activation state:** **NOT ACTIVE**. No runtime/current link, installation, state copy, service action, or gateway/TUI activation occurred.

## Candidate-only extension installation

- **Extension source checkpoint:** `35d2e44342a08102fd735824ab55aede91ae3799` (`matt-hermes-extensions` local main; unpushed)
- **Distribution:** `matt-hermes-extensions==0.1.0`
- **Wheel SHA-256:** `e4af8e6c89668fcc2484d004d0d2456f30bb62f0b055d7fd3bbe5f73faeaf37c`
- **Installed under:** candidate `.venv` only; 19 wheel-record hashes and 5 console entry points were verified from a scrubbed neutral directory.
- **Persistent rollback record:** `/Users/matt/.hermes/runtime/evidence/green-v2026.8.31-tgvoice-35d2e44/rollback-manifest.json` (SHA-256 `c97a9901200eb4897833c9bb363848b17f35399350faeda0ecc0305155527a6c`; previous candidate distribution absent)
- **Persistent source wheel:** `/Users/matt/.hermes/runtime/evidence/green-v2026.8.31-tgvoice-35d2e44/matt_hermes_extensions-0.1.0-py3-none-any.whl` (same wheel SHA-256 above)
- **Candidate environment:** CPython 3.11.15, created with `uv sync --locked --extra dev`; the candidate gateway scope passed 12/12 using its own `.venv` with candidate-source import binding verified.
- **Clean extension verification checkout:** `/Users/matt/.hermes/runtime/extensions/matt-hermes-extensions-35d2e44`, detached at the exact extension checkpoint; its full suite passed 183/183 using the candidate interpreter.
- **Activation state:** **NOT ACTIVE**. No runtime/current link, process, gateway, provider, profile/state, credential, config, or service action occurred.
