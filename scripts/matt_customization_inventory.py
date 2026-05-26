#!/usr/bin/env python3
"""Summarize Matt-local Hermes fork customization surfaces.

This script is intentionally read-only. It compares the current branch against an
upstream ref, lists fork-only commits/files, and highlights hot upstream files
where local customizations are most likely to create future merge conflicts.

Usage:
    python scripts/matt_customization_inventory.py
    python scripts/matt_customization_inventory.py --upstream origin/main
"""

from __future__ import annotations

import argparse
import collections
import subprocess
import sys
from pathlib import Path

HOT_SURFACES = {
    "cli.py",
    "gateway/run.py",
    "gateway/platforms/base.py",
    "gateway/platforms/telegram.py",
    "tui_gateway/server.py",
    "tools/kanban_tools.py",
    "toolsets.py",
    "hermes_cli/commands.py",
    "hermes_cli/config.py",
    "hermes_cli/kanban.py",
    "hermes_cli/kanban_db.py",
}

FEATURE_HINTS = {
    "agent-lights": ("light_cue", "LightCue", "agent-lights", "AgentLights", "wiz", "WiZ"),
    "telegram-voice-ux": ("voice_transcript", "audio_as_voice", "tts_media", "stt"),
    "apple-calendar": ("apple_calendar", "EventKit"),
    "personality-voice": ("personality_voice", "personality_voices"),
    "repo-status": ("repo_status", "repo-status"),
    "kanban-policy": ("kanban", "workspace_preflight", "repo", "dispatch"),
    "holographic-memory": ("holographic", "fts", "fact_store"),
}


def run_git(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as exc:
        print(exc.output, file=sys.stderr)
        raise SystemExit(exc.returncode) from exc


def repo_root() -> Path:
    return Path(run_git(["rev-parse", "--show-toplevel"]).strip())


def classify_feature(path: str) -> str:
    haystack = path.lower()
    for feature, hints in FEATURE_HINTS.items():
        if any(hint.lower() in haystack for hint in hints):
            return feature
    if path.startswith("apps/agent-lights-menu-bar/"):
        return "agent-lights"
    if path.startswith("gateway/platforms/telegram"):
        return "telegram-gateway"
    if path.startswith("tools/apple"):
        return "apple-calendar"
    if path.startswith("plugins/memory/holographic"):
        return "holographic-memory"
    return "uncategorized"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", default="origin/main", help="Upstream ref to compare against")
    parser.add_argument("--max-commits", type=int, default=60)
    parser.add_argument("--max-files", type=int, default=120)
    args = parser.parse_args()

    root = repo_root()
    branch = run_git(["branch", "--show-current"]).strip() or "(detached)"
    head = run_git(["rev-parse", "--short", "HEAD"]).strip()
    upstream = run_git(["rev-parse", "--short", args.upstream]).strip()
    ahead_behind = run_git(["rev-list", "--left-right", "--count", f"{args.upstream}...HEAD"]).strip()
    behind, ahead = ahead_behind.split()

    print("Hermes fork customization inventory")
    print("=" * 43)
    print(f"repo:     {root}")
    print(f"branch:   {branch}")
    print(f"HEAD:     {head}")
    print(f"upstream: {args.upstream} ({upstream})")
    print(f"delta:    {behind} behind / {ahead} ahead relative to {args.upstream}")
    print()

    status = run_git(["status", "--short"]).strip()
    if status:
        print("Working tree has uncommitted changes:")
        print(status)
        print()

    commits = run_git(["log", "--oneline", f"{args.upstream}..HEAD", f"--max-count={args.max_commits}"]).strip()
    print("Fork-only commits")
    print("-" * 17)
    print(commits or "(none)")
    print()

    raw_stats = run_git(["diff", "--numstat", f"{args.upstream}...HEAD"]).splitlines()
    rows: list[tuple[int, int, str]] = []
    for line in raw_stats:
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added_s, deleted_s, path = parts
        added = int(added_s) if added_s.isdigit() else 0
        deleted = int(deleted_s) if deleted_s.isdigit() else 0
        rows.append((added, deleted, path))

    rows.sort(key=lambda item: (item[0] + item[1], item[2]), reverse=True)

    by_feature: collections.Counter[str] = collections.Counter()
    hot_rows: list[tuple[int, int, str]] = []
    for added, deleted, path in rows:
        feature = classify_feature(path)
        by_feature[feature] += added + deleted
        if path in HOT_SURFACES:
            hot_rows.append((added, deleted, path))

    print("Hot upstream surfaces touched")
    print("-" * 29)
    if hot_rows:
        for added, deleted, path in hot_rows:
            print(f"{added:>5} + {deleted:<5} - {path}")
    else:
        print("(none)")
    print()

    print("Feature/churn buckets")
    print("-" * 21)
    for feature, churn in by_feature.most_common():
        print(f"{churn:>6}  {feature}")
    print()

    print(f"Top changed files (max {args.max_files})")
    print("-" * 31)
    for added, deleted, path in rows[: args.max_files]:
        marker = "HOT" if path in HOT_SURFACES else "   "
        feature = classify_feature(path)
        print(f"{marker} {added:>5} + {deleted:<5} {feature:<22} {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
