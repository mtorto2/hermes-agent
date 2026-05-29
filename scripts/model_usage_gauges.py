#!/usr/bin/env python3
"""Read-only model/API usage gauges for Matt's Hermes setup.

Secret discipline:
- Reads keys from ~/.hermes/.env or process env.
- Never prints key values.
- Uses admin/read-only endpoints where available.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

ENV_PATH = Path(os.environ.get("HERMES_ENV_PATH", "/Users/matt/.hermes/.env"))


def load_env_file(path: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    if not path.exists():
        return vals
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        vals[key.strip()] = value.strip().strip('"').strip("'")
    return vals


def merged_env() -> dict[str, str]:
    vals = load_env_file(ENV_PATH)
    vals.update({k: v for k, v in os.environ.items() if v})
    return vals


def request_json(url: str, headers: dict[str, str], timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return {"ok": True, "status": resp.status, "json": json.loads(body or "{}")}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"raw": body[:500]}
        return {"ok": False, "status": exc.code, "json": payload}
    except Exception as exc:
        return {"ok": False, "status": None, "json": {"error": type(exc).__name__, "message": str(exc)}}


def sum_numbers(obj, keys: set[str]) -> int | float:
    total: int | float = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys:
                if isinstance(v, (int, float)):
                    total += v
                elif isinstance(v, str):
                    try:
                        total += float(v)
                    except ValueError:
                        pass
                elif isinstance(v, dict) and "value" in v:
                    try:
                        total += float(v["value"])
                    except (TypeError, ValueError):
                        pass
            elif isinstance(v, (dict, list)):
                total += sum_numbers(v, keys)
    elif isinstance(obj, list):
        for item in obj:
            total += sum_numbers(item, keys)
    return total


def anthropic(days: int, env: dict[str, str]) -> dict:
    admin = env.get("ANTHROPIC_ADMIN_KEY")
    runtime = env.get("ANTHROPIC_API_KEY")
    out: dict = {
        "configured": {"runtime_api_key": bool(runtime), "admin_key": bool(admin)},
        "note": "Anthropic API/admin usage is separate from Claude Code local subscription/OAuth usage.",
    }
    if not admin:
        out["status"] = "missing_admin_key"
        return out

    today = dt.datetime.now(dt.timezone.utc).date()
    start = (today - dt.timedelta(days=days)).isoformat()
    end = today.isoformat()
    headers = {"x-api-key": admin, "anthropic-version": "2023-06-01"}
    qs = urllib.parse.urlencode({"starting_at": start, "ending_at": end, "bucket_width": "1d"})

    usage = request_json(f"https://api.anthropic.com/v1/organizations/usage_report/messages?{qs}", headers)
    cost = request_json(f"https://api.anthropic.com/v1/organizations/cost_report?{qs}", headers)

    out["usage_report"] = {
        "ok": usage["ok"],
        "status": usage["status"],
        "input_tokens": sum_numbers(usage.get("json"), {"input_tokens"}),
        "output_tokens": sum_numbers(usage.get("json"), {"output_tokens"}),
        "cache_creation_input_tokens": sum_numbers(usage.get("json"), {"cache_creation_input_tokens"}),
        "cache_read_input_tokens": sum_numbers(usage.get("json"), {"cache_read_input_tokens"}),
        "raw_bucket_count": len(usage.get("json", {}).get("data", [])) if isinstance(usage.get("json"), dict) else None,
    }
    if not usage["ok"]:
        out["usage_report"]["error"] = usage.get("json")

    out["cost_report"] = {
        "ok": cost["ok"],
        "status": cost["status"],
        "cost_usd": sum_numbers(cost.get("json"), {"amount", "cost", "cost_usd"}),
        "raw_bucket_count": len(cost.get("json", {}).get("data", [])) if isinstance(cost.get("json"), dict) else None,
    }
    if not cost["ok"]:
        out["cost_report"]["error"] = cost.get("json")
    return out


def openai(days: int, env: dict[str, str]) -> dict:
    # Organization usage endpoints require a key with api.usage.read.
    key = env.get("OPENAI_ADMIN_KEY") or env.get("OPENAI_API_KEY")
    source = "OPENAI_ADMIN_KEY" if env.get("OPENAI_ADMIN_KEY") else "OPENAI_API_KEY" if env.get("OPENAI_API_KEY") else None
    out: dict = {
        "configured": {"api_key": bool(env.get("OPENAI_API_KEY")), "admin_key": bool(env.get("OPENAI_ADMIN_KEY"))},
        "key_used_for_usage_probe": source,
        "note": "OpenAI API usage is separate from ChatGPT/Codex OAuth/subscription usage.",
    }
    if not key:
        out["status"] = "missing_key"
        return out

    now = dt.datetime.now(dt.timezone.utc)
    start_time = int((now - dt.timedelta(days=days)).timestamp())
    headers = {"Authorization": f"Bearer {key}"}
    org_usage = request_json(
        f"https://api.openai.com/v1/organization/usage/completions?start_time={start_time}&bucket_width=1d",
        headers,
    )
    org_cost = request_json(
        f"https://api.openai.com/v1/organization/costs?start_time={start_time}&bucket_width=1d",
        headers,
    )
    legacy_today = request_json(f"https://api.openai.com/v1/usage?date={now.date().isoformat()}", headers)

    out["organization_usage"] = {
        "ok": org_usage["ok"],
        "status": org_usage["status"],
        "input_tokens": sum_numbers(org_usage.get("json"), {"input_tokens", "input_cached_tokens"}),
        "output_tokens": sum_numbers(org_usage.get("json"), {"output_tokens"}),
    }
    if not org_usage["ok"]:
        out["organization_usage"]["error"] = org_usage.get("json")

    out["organization_costs"] = {
        "ok": org_cost["ok"],
        "status": org_cost["status"],
        "cost_usd": sum_numbers(org_cost.get("json"), {"amount", "cost", "cost_usd"}),
    }
    if not org_cost["ok"]:
        out["organization_costs"]["error"] = org_cost.get("json")

    out["legacy_daily_usage"] = {
        "ok": legacy_today["ok"],
        "status": legacy_today["status"],
        "has_payload": bool(legacy_today.get("json")),
    }
    if not legacy_today["ok"]:
        out["legacy_daily_usage"]["error"] = legacy_today.get("json")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only model/API usage gauges")
    parser.add_argument("--days", type=int, default=1, help="UTC lookback window in days")
    parser.add_argument("--json", action="store_true", help="Print raw JSON")
    args = parser.parse_args()
    env = merged_env()
    result = {
        "window_days": args.days,
        "env_path": str(ENV_PATH),
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "anthropic": anthropic(args.days, env),
        "openai": openai(args.days, env),
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print(f"Model/API usage gauges — last {args.days} day(s) UTC")
    print(f"Generated: {result['generated_at_utc']}")
    a = result["anthropic"]
    print("\nAnthropic API/admin")
    print(f"  keys: runtime={a['configured']['runtime_api_key']} admin={a['configured']['admin_key']}")
    if "usage_report" in a:
        u = a["usage_report"]
        print(f"  usage: ok={u['ok']} status={u['status']} input={u.get('input_tokens')} output={u.get('output_tokens')} cache_create={u.get('cache_creation_input_tokens')} cache_read={u.get('cache_read_input_tokens')}")
        c = a["cost_report"]
        print(f"  cost:  ok={c['ok']} status={c['status']} cost_usd={c.get('cost_usd')}")
    else:
        print(f"  status: {a.get('status')}")

    o = result["openai"]
    print("\nOpenAI API/admin")
    print(f"  keys: api={o['configured']['api_key']} admin={o['configured']['admin_key']} probe_key={o.get('key_used_for_usage_probe')}")
    if "organization_usage" in o:
        u = o["organization_usage"]
        print(f"  org usage: ok={u['ok']} status={u['status']} input={u.get('input_tokens')} output={u.get('output_tokens')}")
        c = o["organization_costs"]
        print(f"  org cost:  ok={c['ok']} status={c['status']} cost_usd={c.get('cost_usd')}")
        l = o["legacy_daily_usage"]
        print(f"  legacy daily usage: ok={l['ok']} status={l['status']} has_payload={l.get('has_payload')}")
    else:
        print(f"  status: {o.get('status')}")
    print("\nSubscription/OAuth lanes such as Claude Code and Codex CLI are separate buckets and are not counted by these API admin gauges.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
