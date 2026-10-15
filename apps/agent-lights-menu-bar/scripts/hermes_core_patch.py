#!/usr/bin/env python3
"""Inventory, back up, and remove an old Agent Lights patch from a Hermes checkout.

Older Agent Lights builds were installed by changing Hermes's own files. This
takes that patch out without losing anything else in the checkout. Nothing it
infers from text is treated as permission: the classification only decides
what is *eligible* for automatic removal, and every removal also needs an
approval digest of the exact changes being discarded.

    inventory --hermes-dir DIR [--baseline REF]
        Read-only. Baseline = merge-base of HEAD and its upstream (else
        origin/main; or --baseline). Lists commits since the baseline,
        staged, unstaged and untracked changes, and classifies each path,
        judging the index (baseline -> index: commits + staged) and the
        working tree (index -> working tree: unstaged) separately:
          agent-lights-file  new file at a known Agent Lights path
          agent-lights-only  in both layers, every added line (what a
                             restore discards) is Agent Lights code (names
                             an Agent Lights symbol) or bare syntax
                             (brackets, try:, pass), and no hunk only
                             deletes lines
          mixed              anything else alongside Agent Lights changes
          review             a new file elsewhere that mentions Agent Lights
          unrelated          no Agent Lights changes
        Only the first two are eligible for `restore`; edit mixed files by
        hand (only the Agent Lights hunks) and decide review files yourself.

    backup --hermes-dir DIR [--baseline REF]
        Creates a new, uniquely named ~/.hermes/agent-lights-core-backup-<...>
        (never reused): staged.patch, unstaged.patch, committed.patch,
        untracked.tar, HEAD/baseline ids, the inventory, a fingerprint of
        the exact state, a README with absolute recovery commands, and git
        refs refs/agent-lights-backup/<id>/{head,worktree}. Changes nothing.

    review --hermes-dir DIR PATH...
        Prints the exact changes that restoring PATH would discard (both
        layers, and new files' contents) and an approval digest of them.

    restore --hermes-dir DIR --backup BACKUP --approved DIGEST --yes PATH...
        Takes eligible PATHs back to the baseline in both the index and the
        working tree (new untracked files are moved into BACKUP/removed/).
        Refuses unless BACKUP fingerprints the current state, DIGEST matches
        what `review` printed for these PATHs now, and every PATH is
        agent-lights-file or agent-lights-only.

    recover --backup BACKUP --yes
        Puts the checkout back exactly as it was at backup time (hard reset
        to the recorded HEAD, re-apply staged and unstaged changes, restore
        untracked files) and verifies it against the recorded fingerprint.

Standard library + git only.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import shlex
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# Names that only Agent Lights code uses (old and current producers).
AL_SYMBOLS = re.compile(
    r"light_cue|LightCue|agent_lights|agent-lights|AgentLights|wiz_light|WiZLight"
    r"|HERMES_SLOT|SlotStatus|slot_status|_light_slot|menu_bar_launcher|mark_slot_online"
    r"|retry_slot_registration|notification_light"
)
# Lines that carry no behavior of their own.
BARE_SYNTAX = re.compile(
    r"^\s*(?:[\)\]\}\(\[\{:,]*|try:|else:|finally:|pass|return(?: None| True| False)?"
    r"|except(?: [\w\.]+(?: as \w+)?| \([\w\., ]+\)(?: as \w+)?)?:)\s*$"
)
# New files that only ever came from an Agent Lights install.
AL_PATH = re.compile(
    r"^(agent/light_cues\.py|agent/wiz_light\.py|tests/agent/test_light_cues\.py"
    r"|tests/agent/test_wiz_light\.py|apps/agent-lights-menu-bar/.*)$"
)
ELIGIBLE = {"agent-lights-file", "agent-lights-only"}
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
    category: str = ""
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
    """Every added line is Agent Lights code or bare syntax, and at least one names it.

    Added lines are what restoring would discard (in the index layer, content
    beyond the baseline; in the working-tree layer, content beyond the index).
    Removed lines come back on restore, so they don't count, but a hunk that
    only removes lines is never Agent Lights: undoing it would undo someone's
    deletion.
    """
    added = [line[1:] for line in hunk.splitlines()[1:] if line[:1] == "+" and not line.startswith("+++")]
    named = False
    for line in added:
        if AL_SYMBOLS.search(line):
            named = True
        elif not BARE_SYNTAX.match(line):
            return False
    return named


def _layers(repo: Path, baseline_sha: str, path: str) -> tuple[str, str]:
    index = git(repo, "diff", "--cached", "-U0", "--no-renames", baseline_sha, "--", path).stdout
    worktree = git(repo, "diff", "-U0", "--no-renames", "--", path).stdout
    return index, worktree


def inventory(repo: Path, baseline_sha: str) -> list[PathReport]:
    reports: dict[str, PathReport] = {}
    index_status = {}
    for line in git(repo, "diff", "--cached", "--name-status", "--no-renames", baseline_sha).stdout.splitlines():
        code, path = line.split("\t", 1)
        index_status[path] = code[:1]
    worktree_paths = git(repo, "diff", "--name-only", "--no-renames").stdout.splitlines()
    for path in set(index_status) | set(worktree_paths):
        code = index_status.get(path, "M")
        reports[path] = PathReport(path, {"A": "added", "D": "deleted"}.get(code, "modified"))
    for path in git(repo, "ls-files", "--others", "--exclude-standard").stdout.splitlines():
        reports[path] = PathReport(path, "untracked")
    for report in reports.values():
        if report.status in {"added", "untracked"}:
            _, worktree = _layers(repo, baseline_sha, report.path) if report.status == "added" else ("", "")
            mentions = False
            try:
                mentions = bool(AL_SYMBOLS.search((repo / report.path).read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                pass
            if AL_PATH.match(report.path) and not worktree:
                report.category = "agent-lights-file"
            elif AL_PATH.match(report.path) or mentions:
                report.category = "review"
            else:
                report.category = "unrelated"
            continue
        for layer in _layers(repo, baseline_sha, report.path):
            for hunk in _hunks(layer):
                (report.al_hunks if _is_al_hunk(hunk) else report.other_hunks).append(hunk)
        has_al = bool(report.al_hunks) or any(AL_SYMBOLS.search(h) for h in report.other_hunks)
        if report.al_hunks and not report.other_hunks:
            report.category = "agent-lights-only"
        elif has_al:
            report.category = "mixed"
        else:
            report.category = "unrelated"
    return sorted(reports.values(), key=lambda r: r.path)


def describe(repo: Path, label: str, baseline_sha: str, reports: list[PathReport]) -> str:
    out = [f"Hermes checkout: {repo}", f"Baseline: {label} = {baseline_sha}",
           f"HEAD: {git(repo, 'rev-parse', 'HEAD').stdout.strip()}"]
    commits = git(repo, "log", "--oneline", f"{baseline_sha}..HEAD").stdout.strip()
    out.append("Commits since the baseline:\n" + (_indent(commits) if commits else "  (none)"))
    for title, args in (("Staged", ["diff", "--cached", "--name-only"]), ("Unstaged", ["diff", "--name-only"])):
        names = git(repo, *args).stdout.strip()
        out.append(f"{title}:\n" + (_indent(names) if names else "  (none)"))
    stashes = git(repo, "stash", "list").stdout.strip()
    if stashes:
        out.append("Stashes (left alone):\n" + _indent(stashes))
    out.append("Paths that differ from the baseline:")
    for r in reports:
        out.append(f"  {r.category:18} {r.status:9} {r.path}")
    for r in reports:
        if r.category == "mixed":
            out.append(f"\n--- {r.path}: Agent Lights hunks ---")
            out += r.al_hunks
            out.append(f"--- {r.path}: other hunks (keep these) ---")
            out += r.other_hunks
    out.append("\nOnly agent-lights-file and agent-lights-only paths can be restored, after `review`.")
    return "\n".join(out) + "\n"


def _indent(text: str) -> str:
    return "\n".join("  " + line for line in text.splitlines())


def state_fingerprint(repo: Path) -> str:
    digest = hashlib.sha256()
    for args in (["rev-parse", "HEAD"], ["status", "--porcelain=v1", "--untracked-files=all"],
                 ["diff", "--cached", "--binary"], ["diff", "--binary"]):
        digest.update(git(repo, *args, text=False).stdout)
        digest.update(b"\0")
    for raw in sorted(filter(None, git(repo, "ls-files", "--others", "--exclude-standard", "-z",
                                       text=False).stdout.split(b"\0"))):
        digest.update(raw + b"\0")
        try:
            digest.update((repo / raw.decode("utf-8", "surrogateescape")).read_bytes())
        except OSError:
            pass
        digest.update(b"\0")
    return digest.hexdigest()


def review_digest(repo: Path, baseline_sha: str, paths: list[str]) -> tuple[str, str]:
    """The exact changes restoring `paths` discards, and a digest of them."""
    reports = {r.path: r for r in inventory(repo, baseline_sha)}
    chosen = _expand(reports, paths)
    digest = hashlib.sha256(baseline_sha.encode())
    text = []
    for r in chosen:
        index, worktree = _layers(repo, baseline_sha, r.path) if r.status != "untracked" else ("", "")
        content = b""
        if r.status in {"added", "untracked"} and (repo / r.path).is_file():
            content = (repo / r.path).read_bytes()
        for part in (r.path, r.category, index, worktree):
            digest.update(part.encode("utf-8", "surrogateescape") + b"\0")
        digest.update(content + b"\0")
        text.append(f"=== {r.path} ({r.category}, {r.status}) ===")
        if r.status in {"added", "untracked"}:
            text.append(content.decode("utf-8", "replace"))
        else:
            text.append("--- index (commits + staged) vs baseline ---\n" + (index or "(none)"))
            text.append("--- working tree vs index (unstaged) ---\n" + (worktree or "(none)"))
    return digest.hexdigest(), "\n".join(text) + "\n"


def _expand(reports: dict[str, PathReport], paths: list[str]) -> list[PathReport]:
    chosen: dict[str, PathReport] = {}
    for arg in paths:
        rel = arg.rstrip("/")
        matches = [r for p, r in reports.items() if p == rel or p.startswith(rel + "/")]
        if not matches:
            raise SystemExit(f"error: {arg} does not differ from the baseline.")
        for r in matches:
            chosen[r.path] = r
    return [chosen[p] for p in sorted(chosen)]


def backup(repo: Path, label: str, baseline_sha: str, parent: Path = DEFAULT_BACKUP_PARENT) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    backup_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    target = parent / f"agent-lights-core-backup-{backup_id}"
    target.mkdir()  # exclusive: never reuse an existing backup
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    (target / "HEAD.txt").write_text(f"{head}\n{branch}\n{repo.resolve()}\n")
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
    (target / "inventory.txt").write_text(describe(repo, label, baseline_sha, inventory(repo, baseline_sha)))
    (target / "fingerprint.txt").write_text(state_fingerprint(repo))
    q = shlex.quote
    tool = Path(__file__).resolve()
    (target / "README.txt").write_text(
        "Backup of a Hermes checkout taken before removing an old Agent Lights patch.\n"
        f"Checkout: {repo.resolve()}\nHEAD: {head} ({branch})\nBaseline: {baseline_sha} ({label})\n\n"
        "To put the checkout back exactly as it was (discards changes made since):\n"
        f"  python3 -I {q(str(tool))} recover --backup {q(str(target))} --yes\n\n"
        "Or by hand (skip a patch file that is empty):\n"
        f"  git -C {q(str(repo.resolve()))} reset --hard {head}\n"
        f"  git -C {q(str(repo.resolve()))} apply --index --binary {q(str(target / 'staged.patch'))}\n"
        f"  git -C {q(str(repo.resolve()))} apply --binary {q(str(target / 'unstaged.patch'))}\n"
        f"  tar -xf {q(str(target / 'untracked.tar'))} -C {q(str(repo.resolve()))}\n\n"
        f"Git refs kept in the checkout: refs/agent-lights-backup/{backup_id}/head and /worktree.\n")
    return target


def restore(repo: Path, baseline_sha: str, backup_dir: Path, paths: list[str], approved: str) -> int:
    fingerprint = backup_dir / "fingerprint.txt"
    if not fingerprint.is_file():
        print(f"error: {backup_dir} is not a backup made by this tool.", file=sys.stderr)
        return 2
    if fingerprint.read_text() != state_fingerprint(repo):
        print("error: the checkout changed since that backup; run `backup` again first.", file=sys.stderr)
        return 2
    if (backup_dir / "baseline.txt").read_text().split()[0] != baseline_sha:
        print("error: the baseline differs from the backup's.", file=sys.stderr)
        return 2
    digest, _ = review_digest(repo, baseline_sha, paths)
    if digest != approved:
        print("error: --approved does not match `review` for these paths now; review them again.", file=sys.stderr)
        return 2
    chosen = _expand({r.path: r for r in inventory(repo, baseline_sha)}, paths)
    refused = [r for r in chosen if r.category not in ELIGIBLE]
    if refused:
        for r in refused:
            print(f"error: refusing {r.path}: {r.category}; it is not provably Agent Lights only. "
                  "Edit it by hand (reviewed) instead.", file=sys.stderr)
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
            print(f"removed {r.path} (added since the baseline; saved in the backup)")
        else:
            git(repo, "restore", f"--source={baseline_sha}", "--staged", "--worktree", "--", r.path)
            print(f"restored {r.path} from the baseline")
    still = {r.path for r in inventory(repo, baseline_sha)} & {r.path for r in chosen}
    if still:
        print("error: still differ from the baseline: " + ", ".join(sorted(still)), file=sys.stderr)
        return 1
    return 0


def recover(backup_dir: Path) -> int:
    head_lines = (backup_dir / "HEAD.txt").read_text().splitlines()
    head, repo = head_lines[0], Path(head_lines[2])
    git(repo, "reset", "--quiet", "--hard", head)
    for name, extra in (("staged.patch", ["--index"]), ("unstaged.patch", [])):
        patch = backup_dir / name
        if patch.stat().st_size:
            git(repo, "apply", *extra, "--binary", str(patch))
    with tarfile.open(backup_dir / "untracked.tar") as archive:
        if hasattr(tarfile, "data_filter"):
            archive.extractall(repo, filter="tar")
        else:
            archive.extractall(repo)
    if state_fingerprint(repo) != (backup_dir / "fingerprint.txt").read_text():
        print("error: the checkout does not match the backup after recovery.", file=sys.stderr)
        return 1
    print(f"Recovered {repo} to its state at backup time.")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["inventory", "backup", "review", "restore", "recover"])
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--hermes-dir")
    parser.add_argument("--baseline")
    parser.add_argument("--backup")
    parser.add_argument("--approved")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_intermixed_args(argv)
    if args.command == "recover":
        if not (args.backup and args.yes):
            print("error: recover needs --backup DIR and --yes.", file=sys.stderr)
            return 2
        return recover(Path(args.backup))
    if not args.hermes_dir:
        print("error: --hermes-dir is required.", file=sys.stderr)
        return 2
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
    if not args.paths:
        print(f"error: {args.command} needs the paths.", file=sys.stderr)
        return 2
    if args.command == "review":
        digest, text = review_digest(repo, baseline_sha, args.paths)
        print(text + f"APPROVAL DIGEST: {digest}")
        return 0
    if not (args.backup and args.approved and args.yes):
        print("error: restore needs --backup DIR, --approved DIGEST (from `review`), and --yes.", file=sys.stderr)
        return 2
    return restore(repo, baseline_sha, Path(args.backup), args.paths, args.approved)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
