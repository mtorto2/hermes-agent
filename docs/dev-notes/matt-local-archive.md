# Matt-local Hermes archive

Matt's private Dropbox-backed Hermes recovery/reference archive may live at:

```text
local/matt-hermes-archive/
```

This directory is intentionally gitignored via `/local/`. It is still important and searchable in Matt's working tree, but it is not part of the Hermes Agent source tree and should not be committed to Matt's fork or upstream NousResearch.

## Purpose

Use this archive for reviewed, non-secret local material such as:

- Hermes identity/user seeds intended for recovery after review;
- runbooks, policies, and setup/recovery notes;
- draft Hermes-created skills pending Matt's review;
- local reference assets that are not product assets.

## Boundaries

The archive is reference/recovery material only. Hermes must not depend on it at runtime.

Do not store plaintext secrets here, including:

- `.env`;
- `auth.json`;
- Telegram bot tokens;
- provider API keys;
- OAuth access or refresh tokens;
- raw session logs, gateway logs, or full `.hermes` copies.

Runtime state belongs under `~/.hermes`. Source changes belong in the Hermes Agent repo. Reusable workflow doctrine belongs in `ai-workflow-core` or repo-local project docs.
