#!/Users/matt/.hermes/hermes-agent/venv/bin/python
"""Tiny menu-bar/Shell feed for Matt's model usage gauges.

This script is intentionally display-only and secret-safe:
- imports the read-only API usage helper;
- optionally reads Hermes/Codex account quota via local authenticated Hermes code;
- never prints API key values;
- emits either SwiftBar text or normalized JSON.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sys

GAUGE_PATH = Path(
    os.environ.get(
        "MODEL_USAGE_GAUGE_PATH",
        str(Path(__file__).with_name("model_usage_gauges.py")),
    )
)
if not GAUGE_PATH.exists():
    GAUGE_PATH = Path("/Users/matt/.hermes/scripts/model_usage_gauges.py")
HERMES_AGENT_PATH = Path(os.environ.get("HERMES_AGENT_PATH", "/Users/matt/.hermes/hermes-agent"))


def load_gauge_module():
    spec = importlib.util.spec_from_file_location("model_usage_gauges", GAUGE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {GAUGE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def money(value) -> str:
    try:
        return f"${float(value):.2f}"
    except Exception:
        return "$?"


def intish(value) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def short_tokens(value) -> str:
    n = intish(value)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def get_codex_quota() -> dict:
    """Fetch Hermes/OpenAI-Codex OAuth account quota when available."""
    try:
        sys.path.insert(0, str(HERMES_AGENT_PATH))
        from agent.account_usage import fetch_account_usage  # type: ignore

        snap = fetch_account_usage("openai-codex", base_url="https://chatgpt.com/backend-api/codex")
        if not snap:
            return {"ok": False, "status": "unavailable"}

        # Be defensive: account_usage internals may evolve. Preserve the raw public attrs too.
        raw = snap if isinstance(snap, dict) else getattr(snap, "__dict__", {})
        text = str(raw)

        def first_percent(label: str):
            import re
            # Prefer explicit fields if present.
            for key in (
                f"{label}_remaining_percent",
                f"{label}_remaining",
                f"{label}_percent_remaining",
            ):
                if isinstance(raw, dict) and key in raw:
                    try:
                        val = float(raw[key])
                        return val * 100 if val <= 1 else val
                    except Exception:
                        pass
            # Fallback for dataclass repr / rendered-ish text.
            m = re.search(label + r"[^0-9]{0,40}(\d+(?:\.\d+)?)%[^\n]{0,40}remaining", text, re.I)
            if m:
                return float(m.group(1))
            m = re.search(label + r"[^0-9]{0,40}remaining[^0-9]{0,20}(\d+(?:\.\d+)?)", text, re.I)
            if m:
                val = float(m.group(1))
                return val * 100 if val <= 1 else val
            return None

        # Rendered lines are more stable and human-checked in Hermes.
        try:
            from agent.account_usage import render_account_usage_lines  # type: ignore
            lines = render_account_usage_lines(snap)
        except Exception:
            lines = []
        rendered = "\n".join(lines)

        import re
        session_remaining = None
        weekly_remaining = None
        session_reset = None
        weekly_reset = None
        for line in lines:
            if line.lower().startswith("session:"):
                m = re.search(r"(\d+(?:\.\d+)?)% remaining", line)
                if m:
                    session_remaining = float(m.group(1))
                session_reset = line.split("resets", 1)[1].strip() if "resets" in line else None
            if line.lower().startswith("weekly:"):
                m = re.search(r"(\d+(?:\.\d+)?)% remaining", line)
                if m:
                    weekly_remaining = float(m.group(1))
                weekly_reset = line.split("resets", 1)[1].strip() if "resets" in line else None

        if session_remaining is None:
            session_remaining = first_percent("session")
        if weekly_remaining is None:
            weekly_remaining = first_percent("weekly")

        return {
            "ok": True,
            "provider": "openai-codex",
            "session_remaining_percent": session_remaining,
            "weekly_remaining_percent": weekly_remaining,
            "session_reset": session_reset,
            "weekly_reset": weekly_reset,
            "rendered": rendered,
        }
    except Exception as exc:
        return {"ok": False, "status": type(exc).__name__, "message": str(exc)}


def collect(days: int) -> dict:
    gauge = load_gauge_module()
    env = gauge.merged_env()
    openai = gauge.openai(days, env)
    anthropic = gauge.anthropic(days, env)
    codex = get_codex_quota()
    return {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "window_days": days,
        "lanes": {
            "codex_oauth": codex,
            "openai_api": openai,
            "anthropic_api": anthropic,
            "claude_code_local": {
                "ok": None,
                "status": "auth/per-run only; global remaining quota not exposed by CLI/API yet",
                "confidence": "limited",
            },
        },
    }


def swiftbar(data: dict) -> str:
    lanes = data["lanes"]
    codex = lanes["codex_oauth"]
    openai = lanes["openai_api"]
    anthropic = lanes["anthropic_api"]

    sess = codex.get("session_remaining_percent") if codex.get("ok") else None
    week = codex.get("weekly_remaining_percent") if codex.get("ok") else None
    openai_cost = openai.get("organization_costs", {}).get("cost_usd")
    anthropic_cost = anthropic.get("cost_report", {}).get("cost_usd")

    def pct(v):
        return f"{int(round(v))}%" if isinstance(v, (int, float)) else "?"

    # Menu-bar title: icon only. Details belong in the vertical dropdown.
    title = "◕"
    lines = [title, "---"]
    lines.append("Model Usage")
    lines.append(f"Updated: {data['generated_at_utc'].replace('T', ' ').replace('+00:00', ' UTC')}")
    lines.append("---")

    ou = openai.get("organization_usage", {})
    oc = openai.get("organization_costs", {})
    lines.append("OpenAI")
    lines.append(f"GPT-5.5 / Hermes: {pct(sess)} session, {pct(week)} weekly left")
    if codex.get("session_reset"):
        lines.append(f"GPT-5.5 reset: {codex['session_reset']}")
    lines.append("Codex CLI: local OAuth bucket")
    lines.append(f"OpenAI API: {money(oc.get('cost_usd'))} / {data['window_days']}d")
    lines.append(f"API tokens: {short_tokens(ou.get('input_tokens'))} in / {short_tokens(ou.get('output_tokens'))} out")
    if not codex.get("ok"):
        lines.append(f"Codex quota unavailable: {codex.get('status')} | color=red")
    if not ou.get("ok") or not oc.get("ok"):
        lines.append(f"OpenAI API gauge degraded: usage={ou.get('status')} cost={oc.get('status')} | color=orange")
    lines.append("---")

    au = anthropic.get("usage_report", {})
    ac = anthropic.get("cost_report", {})
    lines.append("Anthropic")
    lines.append("Claude Code: local Pro/OAuth bucket")
    lines.append("Claude Code remaining: not exposed")
    lines.append(f"Anthropic API: {money(ac.get('cost_usd'))} / {data['window_days']}d")
    lines.append(f"API tokens: {short_tokens(au.get('input_tokens'))} in / {short_tokens(au.get('output_tokens'))} out")
    if not au.get("ok") or not ac.get("ok"):
        lines.append(f"Anthropic API gauge degraded: usage={au.get('status')} cost={ac.get('status')} | color=orange")
    lines.append("---")
    lines.append("Lanes")
    lines.append("Juror Codex lane: OpenAI local OAuth")
    lines.append("Juror Claude lane: Claude Code local OAuth")
    lines.append("Native API lanes: approval only")
    lines.append("---")
    lines.append("Refresh | refresh=true")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Basic model usage feed for SwiftBar/menu bar")
    parser.add_argument("--days", type=int, default=int(os.environ.get("MODEL_USAGE_DAYS", "1")))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    data = collect(args.days)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(swiftbar(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
