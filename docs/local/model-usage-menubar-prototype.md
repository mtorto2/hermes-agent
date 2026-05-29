# Model Usage Menu Bar Prototype Checkpoint

Date: 2026-05-29

## Current prototype

SwiftBar plugin:

```text
/Users/matt/Dropbox/Apps/SwiftBar/model-usage.1m.py
  -> /Users/matt/.hermes/scripts/model_usage_menubar.py
```

Primary script:

```text
/Users/matt/.hermes/scripts/model_usage_menubar.py
```

Underlying read-only API gauge helper:

```text
/Users/matt/.hermes/scripts/model_usage_gauges.py
```

## Current UI shape

Menu bar title is icon-only:

```text
◕
```

Dropdown is grouped by provider/bucket:

```text
Model Usage
Updated: ...

OpenAI
GPT-5.5 / Hermes: <session %> session, <weekly %> weekly left
GPT-5.5 reset: <reset time>
Codex CLI: local OAuth bucket
OpenAI API: <$cost> / 1d
API tokens: <input> in / <output> out

Anthropic
Claude Code: local Pro/OAuth bucket
Claude Code remaining: not exposed
Anthropic API: <$cost> / 1d
API tokens: <input> in / <output> out

Lanes
Juror Codex lane: OpenAI local OAuth
Juror Claude lane: Claude Code local OAuth
Native API lanes: approval only
```

## Verified behavior

- SwiftBar plugin executes successfully.
- Codex/Hermes OAuth quota exposes session and weekly remaining percentages plus reset timing.
- OpenAI API/admin gauge exposes 1-day cost and token usage.
- Anthropic API/admin gauge exposes 1-day cost and token usage.
- Claude Code local OAuth/auth lane is represented honestly as local Pro/OAuth with global remaining quota not exposed.
- No API keys or secrets are printed.

## Product intent

This is intentionally a very basic prototype, not final formatting.

Goal: quick menu-bar visibility into model usage buckets and whether local OAuth/API lanes are being used, with clean grouping and no credential exposure.

Future cleanup ideas:

- Confirm exact OpenAI model labels Matt wants shown under OpenAI; currently only verified default is `GPT-5.5 / Hermes`.
- Add clearer icon/color state based on low remaining session/weekly quota.
- Add per-model API breakdown if OpenAI/Anthropic usage endpoints expose enough model-level grouping.
- Add native Swift/AppKit version later, keeping the Python helper as the privileged/read-only data layer.
- Keep Agent Lights, Model Usage, and Repo Watch as separate small menu bar surfaces for now; consider one Hermes Ops bundle later.
