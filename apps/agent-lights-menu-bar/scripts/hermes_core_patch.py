#!/usr/bin/env python3
"""Inventory, back up, and remove an old Agent Lights patch from a Hermes checkout.

Older Agent Lights builds were installed by changing Hermes's own files. This
helps take that patch out again without losing anything else in the checkout:

    hermes_core_patch.py inventory --hermes-dir DIR [--baseline REF]
        Read-only. Finds the stock baseline (the merge-base of HEAD and its
        upstream, else origin/main; or --baseline), then lists committed,
        staged, unstaged and untracked changes, and classifies every path that
        differs from the baseline:
          agent-lights-file   added since the baseline and part of Agent Lights
          agent-lights-only   existed in the baseline; every change is Agent Lights
          mixed               has Agent Lights changes AND other changes
          unrelated           no Agent Lights changes
        Changes are judged per zero-context hunk (`git diff -U0`): a hunk is
        Agent Lights when one of its changed lines names an Agent Lights
        symbol. Review the printed hunks; the classification is a guide.

    hermes_core_patch.py backup --hermes-dir DIR [--baseline REF]
        Creates a new, uniquely named ~/.hermes/agent-lights-core-backup-<...>
        (never reuses one) holding: HEAD and baseline ids, `git status`,
        staged.patch, unstaged.patch, committed.patch (baseline..HEAD),
        untracked.tar (all untracked, non-ignored files), the inventory, and
        git refs refs/agent-lights-backup/<id>/{head,worktree} pointing at
        HEAD and a snapshot of the index + working tree. Changes nothing in
        the checkout's files.

    hermes_core_patch.py restore --hermes-dir DIR --backup BACKUP --yes PATH...
        Takes the named paths (files, or directories meaning every changed
        path under them) back to the baseline, in both the index and the
        working tree: an agent-lights-file is removed (untracked files are
        moved into BACKUP/removed/), an agent-lights-only file is restored
        from the baseline. Refuses mixed and unrelated paths (edit those by
        hand, reviewed), and refuses unless BACKUP was taken from exactly the
        current state (same HEAD and same status). Commits are not rewritten:
        if the patch was committed, the result is an uncommitted change on
        top; commit it if you want a clean tree.

Standard library + git only.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# Names that only Agent Lights code uses (old and current producers).
AL_SYMBOLS = re.compile(
    r"light_cue|LightCue|agent_lights|agent-lights|AgentLights|wiz_light|WiZLight|WiZ"
    r"|HERMES_SLOT|SlotStatus|slot_status|_light_slot|menu_bar_launcher|mark_slot_online"
    r"|retry_slot_registration|notification_light"
)
AL_PATH = re.compile(r"(^|/)(light_cues\.py|wiz_light\.py|test_light_cues\.py|agent-lights-menu-bar)(/|$)")
DEFAULT_BACKUP_PARENT = Path.home() / ".hermes"


def git(repo: Path, *args: str, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=text, check=check)


def resolve_baseline(repo: Path, explicit: str | None) -> tuple[str, str]:
    if explicit:
        sha = git(repo, "rev-parse", "--verify", f"{explicit}^{{commit}}").stdout.strip()
        return explicit, sha
    upstream = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False)
    candidates = [upstream.stdout.strip()] if upstream.returncode == 0 and upstream.stdout.strip() else []
    candidates.append("origin/main")
    for ref in candidates:
        if git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).returncode == 0:
            sha = git(repo, "merge-base", "HEAD", ref).stdout.strip()
            return f"merge-base(HEAD, {ref})", sha
    raise SystemExit("error: no upstream or origin/main to compare with; pass --baseline <stock ref>.")


@dataclass
class PathReport:
    path: str
    status: str  # added / modified / deleted / untracked
    category: str
    al_hunks: list[str] = field(default_factory=list)
    other_hunks: list[str] = field(default_factory=list)


def _hunks(diff: str) -> list[str]:
    hunks, current = [], []
    for line in diff.splitlines():
        if line.startswith("@@"):
            if current:
                hunks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        hunks.append("\n".join(current))
    return hunks


def _is_al_hunk(hunk: str) -> bool:
    changed = [line[1:] for line in hunk.splitlines()[1:] if line[:1] in "+-"]
    return any(AL_SYMBOLS.search(line) for line in changed)


def inventory(repo: Path, baseline_sha: str) -> list[PathReport]:
    reports: dict[str, PathReport] = {}
    # Working tree (committed + staged + unstaged) against the baseline.
    for line in git(repo, "diff", "--name-status", "--no-renames", baseline_sha).stdout.splitlines():
        code, path = line.split("\t", 1)
        status = {"A": "added", "D": "deleted"}.get(code[:1], "modified")
        reports[path] = PathReport(path, status, "")
    for path in git(repo, "ls-files", "--others", "--exclude-standard").stdout.splitlines():
        reports[path] = PathReport(path, "untracked", "")
    for report in reports.values():
        file = repo / report.path
        if report.status in {"added", "untracked"}:
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            is_al = bool(AL_PATH.search(report.path)) or bool(AL_SYMBOLS.search(text))
            report.category = "agent-lights-file" if is_al else "unrelated"
            continue
        diff = git(repo, "diff", "-U0", "--no-renames", baseline_sha, "--", report.path).stdout
        for hunk in _hunks(diff):
            (report.al_hunks if _is_al_hunk(hunk) else report.other_hunks).append(hunk)
        if report.al_hunks and not report.other_hunks:
            report.category = "agent-lights-only"
        elif report.al_hunks:
            report.category = "mixed"
        else:
            report.category = "unrelated"
    return sorted(reports.values(), key=lambda r: r.path)


def describe(repo: Path, label: str, baseline_sha: str, reports: list[PathReport]) -> str:
    out = [f"Hermes checkout: {repo}", f"Baseline: {label} = {baseline_sha}",
           f"HEAD: {git(repo, 'rev-parse', 'HEAD').stdout.strip()}"]
    commits = git(repo, "log", "--oneline", f"{baseline_sha}..HEAD").stdout.strip()
    out.append("Commits since the baseline:\n" + (indent(commits) if commits else "  (none)"))
    staged = git(repo, "diff", "--cached", "--name-only").stdout.strip()
    unstaged = git(repo, "diff", "--name-only").stdout.strip()
    out.append("Staged:\n" + (indent(staged) if staged else "  (none)"))
    out.append("Unstaged:\n" + (indent(unstaged) if unstaged else "  (none)"))
    stashes = git(repo, "stash", "list").stdout.strip()
    if stashes:
        out.append("Stashes (left alone):\n" + indent(stashes))
    out.append("Paths that differ from the baseline:")
    for r in reports:
        out.append(f"  {r.category:18} {r.status:9} {r.path}")
    for r in reports:
        if r.category == "mixed":
            out.append(f"\n--- {r.path}: Agent Lights hunks ---")
            out += r.al_hunks
            out.append(f"--- {r.path}: other hunks (keep these) ---")
            out += r.other_hunks
    return "\n".join(out) + "\n"


def indent(text: str) -> str:
    return "\n".join("  " + line for line in text.splitlines())


def state_fingerprint(repo: Path) -> str:
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout
    staged = git(repo, "diff", "--cached", "--binary").stdout
    unstaged = git(repo, "diff", "--binary").stdout
    digest = hashlib.sha256()
    for part in (head, status, staged, unstaged):
        digest.update(part.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
    return digest.hexdigest()


def backup(repo: Path, label: str, baseline_sha: str, parent: Path = DEFAULT_BACKUP_PARENT) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    backup_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    target = parent / f"agent-lights-core-backup-{backup_id}"
    target.mkdir()  # exclusive: never reuse an existing backup
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    (target / "HEAD.txt").write_text(f"{head}\n{branch}\n")
    (target / "baseline.txt").write_text(f"{baseline_sha}\n{label}\n")
    (target / "status.txt").write_text(git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout)
    for name, args in (("staged.patch", ["diff", "--cached", "--binary"]),
                       ("unstaged.patch", ["diff", "--binary"]),
                       ("committed.patch", ["diff", "--binary", baseline_sha, "HEAD"])):
        (target / name).write_bytes(git(repo, *args, text=False).stdout)
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z", text=False).stdout.split(b"\0")
    with tarfile.open(target / "untracked.tar", "w") as archive:
        for raw in filter(None, untracked):
            rel = raw.decode("utf-8", "surrogateescape")
            archive.add(repo / rel, arcname=rel, recursive=False)
    git(repo, "update-ref", f"refs/agent-lights-backup/{backup_id}/head", head)
    snapshot = git(repo, "stash", "create", f"agent-lights backup {backup_id}").stdout.strip()
    if snapshot:
        git(repo, "update-ref", f"refs/agent-lights-backup/{backup_id}/worktree", snapshot)
    reports = inventory(repo, baseline_sha)
    (target / "inventory.txt").write_text(describe(repo, label, baseline_sha, reports))
    (target / "fingerprint.txt").write_text(state_fingerprint(repo))
    (target / "README.txt").write_text(
        "Backup of a Hermes checkout taken before removing an old Agent Lights patch.\n"
        f"Checkout: {repo}\nHEAD: {head} ({branch})\nBaseline: {baseline_sha} ({label})\n\n"
        "Restore everything as it was:\n"
        f"  git -C {repo} reset --hard {head}\n"
        f"  git -C {repo} apply --index staged.patch      # then\n"
        f"  git -C {repo} apply unstaged.patch\n"
        f"  tar -xf untracked.tar -C {repo}\n"
        f"Git refs kept in the checkout: refs/agent-lights-backup/{backup_id}/head and /worktree\n"
        "(`git stash apply <worktree ref>` re-applies the tracked changes).\n")
    return target


def restore(repo: Path, baseline_sha: str, backup_dir: Path, paths: list[str]) -> int:
    fingerprint = backup_dir / "fingerprint.txt"
    if not fingerprint.is_file():
        print(f"error: {backup_dir} is not a backup made by this tool.", file=sys.stderr)
        return 2
    if fingerprint.read_text() != state_fingerprint(repo):
        print("error: the checkout changed since that backup; run `backup` again first.", file=sys.stderr)
        return 2
    recorded_baseline = (backup_dir / "baseline.txt").read_text().split()[0]
    if recorded_baseline != baseline_sha:
        print(f"error: baseline {baseline_sha} differs from the backup's {recorded_baseline}.", file=sys.stderr)
        return 2
    reports = {r.path: r for r in inventory(repo, baseline_sha)}
    chosen: list[PathReport] = []
    for arg in paths:
        rel = arg.rstrip("/")
        matches = [r for p, r in reports.items() if p == rel or p.startswith(rel + "/")]
        if not matches:
            print(f"error: {arg} does not differ from the baseline.", file=sys.stderr)
            return 2
        chosen += matches
    refused = [r for r in chosen if r.category not in {"agent-lights-file", "agent-lights-only"}]
    if refused:
        for r in refused:
            print(f"error: refusing {r.path}: {r.category}; edit it by hand (reviewed) instead.", file=sys.stderr)
        return 3
    removed_dir = backup_dir / "removed"
    for r in chosen:
        if r.status == "untracked":
            dest = removed_dir / r.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if os.path.lexists(dest):
                print(f"error: {dest} already exists; refusing to overwrite.", file=sys.stderr)
                return 2
            os.rename(repo / r.path, dest)
            print(f"moved untracked {r.path} -> {dest}")
        elif r.status == "added":
            git(repo, "rm", "--quiet", "--force", "--", r.path)
            print(f"removed {r.path} (added since the baseline; saved in the backup patches)")
        else:
            git(repo, "restore", f"--source={baseline_sha}", "--staged", "--worktree", "--", r.path)
            print(f"restored {r.path} from the baseline")
    leftover = [p for p, r in ((r.path, r) for r in inventory(repo, baseline_sha))
                if p in {c.path for c in chosen}]
    if leftover:
        print("error: still differ from the baseline: " + ", ".join(leftover), file=sys.stderr)
        return 1
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["inventory", "backup", "restore"])
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--hermes-dir", required=True)
    parser.add_argument("--baseline")
    parser.add_argument("--backup")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_intermixed_args(argv)
    repo = Path(args.hermes_dir)
    if git(repo, "rev-parse", "--is-inside-work-tree", check=False).returncode != 0:
        print(f"error: {repo} is not a git checkout.", file=sys.stderr)
        return 2
    label, baseline_sha = resolve_baseline(repo, args.baseline)
    if args.command == "inventory":
        print(describe(repo, label, baseline_sha, inventory(repo, baseline_sha)), end="")
        return 0
    if args.command == "backup":
        print(backup(repo, label, baseline_sha))
        return 0
    if not (args.backup and args.paths and args.yes):
        print("error: restore needs --backup DIR, the paths to restore, and --yes.", file=sys.stderr)
        return 2
    return restore(repo, baseline_sha, Path(args.backup), args.paths)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
