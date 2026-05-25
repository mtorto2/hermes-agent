#!/usr/bin/env python3
"""Generate a short HeyGen avatar video with quota/cost tracking.

Uses HEYGEN_API_KEY from the environment. Never prints the API key.

Examples:
  # Estimate only, no API generation call:
  scripts/heygen_generate.py --estimate-only --text "Quick test"

  # Generate with defaults and download to ~/Desktop:
  scripts/heygen_generate.py --text "Quick HeyGen test from Hermes."

  # Pick explicit media:
  scripts/heygen_generate.py \
    --avatar-id Abigail_expressive_2024112501 \
    --voice-id f38a635bee7a4d1f9b0a654a31d050d2 \
    --text "Hello from Hermes."
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_BASE = "https://api.heygen.com"
DEFAULT_AVATAR_ID = "Abigail_expressive_2024112501"
DEFAULT_VOICE_ID = "f38a635bee7a4d1f9b0a654a31d050d2"
DEFAULT_TEXT = "Quick HeyGen test from Hermes. If you can see this, the integration is working."
# Empirical from Matt's first test: 5 credits cost about $0.08.
DEFAULT_USD_PER_CREDIT = 0.016


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "X-Api-Key": api_key,
        "User-Agent": "hermes-local-heygen-helper",
    }


def request_json(
    method: str,
    url: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 60,
) -> tuple[int, dict[str, Any]]:
    headers = _headers(api_key)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(body or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body or "{}")
        except Exception:
            parsed = {"raw": body[:1000]}
        return exc.code, parsed


def remaining_quota(api_key: str) -> dict[str, Any]:
    status, body = request_json(
        "GET", f"{API_BASE}/v2/user/remaining_quota", api_key, timeout=30
    )
    if not (200 <= status < 300):
        raise RuntimeError(f"quota check failed HTTP {status}: {_safe_error(body)}")
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError("quota response did not include data object")
    return data


def _safe_error(body: Any) -> str:
    if isinstance(body, dict):
        for key in ("message", "error", "error_msg", "code"):
            if key in body and body[key] is not None:
                return str(body[key])[:500]
    return str(body)[:500]


def estimate_credits_for_text(text: str) -> int:
    """Rough preflight estimate for simple text-to-avatar generations.

    HeyGen bills on rendered duration, which is only known after completion.
    This estimates narration duration from word count, then rounds up to whole
    credits. Conservative enough for short tests without overengineering.
    """
    words = max(1, len(text.split()))
    estimated_seconds = max(3.0, words / 2.4)  # ~144 wpm narration pace.
    return max(1, math.ceil(estimated_seconds))


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    character_type = "talking_photo" if args.talking_photo_id else "avatar"
    if character_type == "talking_photo":
        character = {
            "type": "talking_photo",
            "talking_photo_id": args.talking_photo_id,
        }
    else:
        character = {
            "type": "avatar",
            "avatar_id": args.avatar_id,
            "avatar_style": args.avatar_style,
        }
    return {
        "video_inputs": [
            {
                "character": character,
                "voice": {
                    "type": "text",
                    "input_text": args.text,
                    "voice_id": args.voice_id,
                },
            }
        ],
        "dimension": {"width": args.width, "height": args.height},
    }


def submit_generation(api_key: str, payload: dict[str, Any]) -> str:
    status, body = request_json(
        "POST", f"{API_BASE}/v2/video/generate", api_key, payload=payload, timeout=60
    )
    if not (200 <= status < 300):
        raise RuntimeError(f"generation submit failed HTTP {status}: {_safe_error(body)}")
    data = body.get("data") if isinstance(body, dict) else None
    video_id = data.get("video_id") if isinstance(data, dict) else None
    if not video_id:
        raise RuntimeError(f"generation response missing video_id: {_safe_error(body)}")
    return str(video_id)


def poll_video(api_key: str, video_id: str, timeout_seconds: int, interval: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    status_url = f"{API_BASE}/v1/video_status.get?video_id={urllib.parse.quote(video_id)}"
    last_state = None
    while time.monotonic() < deadline:
        status, body = request_json("GET", status_url, api_key, timeout=30)
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            state = data.get("status") or data.get("state")
            if state != last_state:
                print(
                    json.dumps(
                        {
                            "event": "poll",
                            "http_status": status,
                            "job_status": state,
                            "has_video_url": bool(data.get("video_url")),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                last_state = state
            if state in {"completed", "complete", "success", "done"} and data.get("video_url"):
                return data
            if state in {"failed", "failure", "error"}:
                raise RuntimeError(f"generation failed: {data.get('error') or data.get('error_msg')}")
        else:
            print(
                json.dumps({"event": "poll", "http_status": status, "response": "unexpected"}),
                flush=True,
            )
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for video {video_id}")


def download(url: str, output_dir: Path, filename: str | None = None) -> Path:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not filename:
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"heygen-hermes-{stamp}.mp4"
    out = output_dir / filename
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-local-heygen-download"})
    with urllib.request.urlopen(req, timeout=180) as resp, out.open("wb") as fh:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Script text to speak.")
    parser.add_argument("--avatar-id", default=DEFAULT_AVATAR_ID, help="HeyGen avatar_id.")
    parser.add_argument("--talking-photo-id", default="", help="Use a talking_photo_id instead of avatar_id.")
    parser.add_argument("--voice-id", default=DEFAULT_VOICE_ID, help="HeyGen voice_id.")
    parser.add_argument("--avatar-style", default="normal", help="Avatar style for avatar generations.")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--output-dir", default="~/Desktop")
    parser.add_argument("--filename", default="", help="Optional output filename, e.g. test.mp4")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--usd-per-credit", type=float, default=DEFAULT_USD_PER_CREDIT)
    parser.add_argument("--estimate-only", action="store_true", help="Print estimate and quota; do not generate.")
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation.")
    args = parser.parse_args(argv)

    api_key = os.environ.get("HEYGEN_API_KEY", "").strip()
    if not api_key:
        print(json.dumps({"ok": False, "error": "HEYGEN_API_KEY is not set"}, indent=2))
        return 2

    quota_before = remaining_quota(api_key)
    before = quota_before.get("remaining_quota")
    est_credits = estimate_credits_for_text(args.text)
    estimate = {
        "event": "estimate",
        "estimated_credits": est_credits,
        "estimated_usd": round(est_credits * args.usd_per_credit, 4),
        "remaining_quota_before": before,
        "text_words": len(args.text.split()),
        "mode": "talking_photo" if args.talking_photo_id else "avatar",
    }
    print(json.dumps(estimate, indent=2, sort_keys=True), flush=True)

    if args.estimate_only:
        return 0
    if not args.yes and sys.stdin.isatty():
        answer = input("Submit HeyGen generation now? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print(json.dumps({"ok": False, "cancelled": True}, indent=2))
            return 1

    video_id = submit_generation(api_key, build_payload(args))
    print(json.dumps({"event": "submitted", "video_id": video_id}, sort_keys=True), flush=True)
    video_data = poll_video(api_key, video_id, args.timeout_seconds, args.poll_interval)
    video_url = video_data.get("video_url")
    if not video_url:
        raise RuntimeError("completed video did not include video_url")

    path = download(video_url, Path(args.output_dir), args.filename or None)
    quota_after = remaining_quota(api_key)
    after = quota_after.get("remaining_quota")
    credits_spent = None
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        credits_spent = before - after

    result = {
        "ok": True,
        "video_id": video_id,
        "path": str(path),
        "bytes": path.stat().st_size,
        "duration_seconds": video_data.get("duration"),
        "remaining_quota_before": before,
        "remaining_quota_after": after,
        "credits_spent": credits_spent,
        "estimated_usd_from_actual_credits": (
            round(float(credits_spent) * args.usd_per_credit, 4)
            if isinstance(credits_spent, (int, float))
            else None
        ),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        raise SystemExit(1)
