"""Local git repository status summaries for CLI, gateway, and menu bar use."""

from __future__ import annotations

import os
import json
import re
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from hermes_constants import get_hermes_home


_BRANCH_RE = re.compile(
    r"^## (?P<branch>.+?)(?:\.\.\.(?P<upstream>[^ \[]+))?(?: \[(?P<flags>.+)\])?$"
)

_CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
_PRUNE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".next",
    ".nuxt",
    ".turbo",
    ".venv",
    ".worktrees",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


@dataclass(frozen=True)
class RepoStatusOptions:
    roots: tuple[Path, ...] = ()
    include_clean: bool = False
    show_paths: bool = False
    max_depth: int = 3
    limit: int = 30
    errors: tuple[str, ...] = ()
    fetch_mode: str = "none"
    fetch_interval_seconds: int = 300
    fetch_timeout_seconds: int = 20
    fetch_workers: int = 4


@dataclass(frozen=True)
class FetchSummary:
    attempted: bool = False
    skipped_reason: str = ""
    fetched: int = 0
    skipped: int = 0
    failed: int = 0
    last_fetch_epoch: float | None = None
    errors: tuple[str, ...] = ()

    @property
    def has_errors(self) -> bool:
        return bool(self.failed or self.errors)

    @property
    def menu_label(self) -> str:
        if self.attempted:
            if self.has_errors:
                return f"Remote fetch: {self.fetched} ok, {self.failed} failed"
            return f"Remote fetch: {self.fetched} repos updated"
        if self.skipped_reason:
            return f"Remote fetch: {self.skipped_reason}"
        if self.last_fetch_epoch:
            return f"Remote fetch: last {format_epoch_time(self.last_fetch_epoch)}"
        return "Remote fetch: not checked"


@dataclass(frozen=True)
class RepoStatus:
    path: Path
    branch: str = "unknown"
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    staged: int = 0
    unstaged: int = 0
    untracked: int = 0
    conflicted: int = 0
    error: str | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def changed(self) -> int:
        return self.staged + self.unstaged + self.untracked + self.conflicted

    @property
    def needs_attention(self) -> bool:
        return bool(self.error or self.changed or self.ahead or self.behind)

    @property
    def branch_label(self) -> str:
        if self.upstream and not self.ahead and not self.behind:
            return f"{self.branch} in sync"
        return self.branch

    @property
    def summary(self) -> str:
        if self.error:
            return f"error: {self.error}"

        parts: list[str] = []
        if self.ahead:
            parts.append(f"ahead {self.ahead}")
        if self.behind:
            parts.append(f"behind {self.behind}")
        if self.conflicted:
            parts.append(f"{self.conflicted} conflicted")
        if self.staged:
            parts.append(f"{self.staged} staged")
        if self.unstaged:
            parts.append(f"{self.unstaged} unstaged")
        if self.untracked:
            parts.append(f"{self.untracked} untracked")
        if not parts:
            return "clean"
        return ", ".join(parts)


def parse_branch_line(line: str) -> tuple[str, str | None, int, int]:
    """Parse the ``##`` line from ``git status --porcelain=v1 --branch``."""

    match = _BRANCH_RE.match(line.strip())
    if not match:
        return ("unknown", None, 0, 0)

    branch = match.group("branch") or "unknown"
    upstream = match.group("upstream")
    ahead = 0
    behind = 0

    flags = match.group("flags") or ""
    for flag in flags.split(","):
        flag = flag.strip()
        if flag.startswith("ahead "):
            ahead = _safe_int(flag.removeprefix("ahead "))
        elif flag.startswith("behind "):
            behind = _safe_int(flag.removeprefix("behind "))

    return (branch, upstream, ahead, behind)


def parse_porcelain_status(path: Path, output: str) -> RepoStatus:
    """Build a RepoStatus from porcelain status output."""

    lines = output.splitlines()
    branch = "unknown"
    upstream = None
    ahead = 0
    behind = 0
    status_lines = lines
    if lines and lines[0].startswith("## "):
        branch, upstream, ahead, behind = parse_branch_line(lines[0])
        status_lines = lines[1:]

    staged = unstaged = untracked = conflicted = 0
    for line in status_lines:
        if not line:
            continue
        code = line[:2]
        if code == "??":
            untracked += 1
            continue
        if code in _CONFLICT_CODES:
            conflicted += 1
            continue
        if len(code) > 0 and code[0] not in (" ", "?"):
            staged += 1
        if len(code) > 1 and code[1] not in (" ", "?"):
            unstaged += 1

    return RepoStatus(
        path=path,
        branch=branch,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        conflicted=conflicted,
    )


def collect_repo_statuses(options: RepoStatusOptions | None = None) -> list[RepoStatus]:
    """Discover repositories and collect local git status for each one."""

    options = options or RepoStatusOptions()
    roots = options.roots or default_roots()
    repos = discover_repositories(roots, max_depth=options.max_depth)
    statuses = [_git_status(path) for path in repos]
    statuses.sort(
        key=lambda status: (
            not status.needs_attention,
            status.name.lower(),
            str(status.path),
        )
    )
    return statuses


def collect_repo_status_report(
    options: RepoStatusOptions | None = None,
) -> tuple[list[RepoStatus], FetchSummary]:
    """Discover repos, optionally refresh remote refs, then collect status."""

    options = options or RepoStatusOptions()
    roots = options.roots or default_roots()
    repos = discover_repositories(roots, max_depth=options.max_depth)
    fetch_summary = maybe_fetch_repositories(repos, options)
    statuses = [_git_status(path) for path in repos]
    statuses.sort(
        key=lambda status: (
            not status.needs_attention,
            status.name.lower(),
            str(status.path),
        )
    )
    return statuses, fetch_summary


def discover_repositories(roots: Sequence[Path], *, max_depth: int = 3) -> list[Path]:
    """Find git repositories under the supplied roots without descending into repos."""

    repos: list[Path] = []
    seen: set[Path] = set()

    for root in _dedupe_existing_paths(roots):
        if _is_git_repo(root):
            _append_repo(repos, seen, root)
            continue

        try:
            root_resolved = root.resolve()
        except OSError:
            continue

        for current, dirs, _files in os.walk(root_resolved, followlinks=False):
            current_path = Path(current)
            rel_depth = _relative_depth(root_resolved, current_path)
            dirs[:] = [
                name
                for name in dirs
                if name not in _PRUNE_DIRS and not name.startswith(".Trash")
            ]

            if _is_git_repo(current_path):
                _append_repo(repos, seen, current_path)
                dirs[:] = []
                continue

            if rel_depth >= max_depth:
                dirs[:] = []

    return repos


def default_roots() -> tuple[Path, ...]:
    """Return configured roots, then practical local defaults."""

    env_roots = _roots_from_env()
    if env_roots:
        return env_roots

    config_roots = _roots_from_config()
    if config_roots:
        return config_roots

    home = Path.home()
    candidates = [
        home / "Dropbox" / "CLIENTS" / "SAVANT SOFTWARE SYSTEMS" / "DEV",
        home / "Developer",
        home / "dev",
        home / "code",
        home / "src",
        Path(__file__).resolve().parents[1],
    ]
    return tuple(_dedupe_existing_paths(candidates))


def parse_repo_status_args(arg_text: str | Sequence[str] = "") -> RepoStatusOptions:
    """Parse small, non-exiting command arguments for /repos and scripts."""

    if isinstance(arg_text, str):
        try:
            tokens = shlex.split(arg_text)
        except ValueError as exc:
            return RepoStatusOptions(errors=(f"Could not parse arguments: {exc}",))
    else:
        tokens = list(arg_text)

    roots: list[Path] = []
    include_clean = False
    show_paths = False
    max_depth = 3
    limit = 30
    fetch_mode = "none"
    fetch_interval_seconds = 300
    fetch_timeout_seconds = 20
    fetch_workers = 4
    errors: list[str] = []
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if token in ("all", "--all", "--clean"):
            include_clean = True
        elif token in ("paths", "--paths"):
            show_paths = True
        elif token in ("--root", "--roots"):
            if index + 1 >= len(tokens):
                errors.append(f"{token} requires a path")
            else:
                roots.append(Path(tokens[index + 1]).expanduser())
                index += 1
        elif token.startswith("--root="):
            roots.append(Path(token.split("=", 1)[1]).expanduser())
        elif token.startswith("--depth="):
            max_depth = max(0, _safe_int(token.split("=", 1)[1], default=max_depth))
        elif token == "--depth":
            if index + 1 >= len(tokens):
                errors.append("--depth requires a number")
            else:
                max_depth = max(0, _safe_int(tokens[index + 1], default=max_depth))
                index += 1
        elif token.startswith("--limit="):
            limit = max(1, _safe_int(token.split("=", 1)[1], default=limit))
        elif token == "--limit":
            if index + 1 >= len(tokens):
                errors.append("--limit requires a number")
            else:
                limit = max(1, _safe_int(tokens[index + 1], default=limit))
                index += 1
        elif token in ("--fetch", "--force-fetch"):
            fetch_mode = "force"
        elif token == "--fetch-stale":
            if fetch_mode != "force":
                fetch_mode = "stale"
        elif token == "--no-fetch":
            fetch_mode = "none"
        elif token.startswith("--fetch-interval="):
            fetch_interval_seconds = max(
                30,
                _safe_int(token.split("=", 1)[1], default=fetch_interval_seconds),
            )
        elif token == "--fetch-interval":
            if index + 1 >= len(tokens):
                errors.append("--fetch-interval requires seconds")
            else:
                fetch_interval_seconds = max(
                    30,
                    _safe_int(tokens[index + 1], default=fetch_interval_seconds),
                )
                index += 1
        elif token.startswith("--fetch-timeout="):
            fetch_timeout_seconds = max(
                3,
                _safe_int(token.split("=", 1)[1], default=fetch_timeout_seconds),
            )
        elif token == "--fetch-timeout":
            if index + 1 >= len(tokens):
                errors.append("--fetch-timeout requires seconds")
            else:
                fetch_timeout_seconds = max(
                    3,
                    _safe_int(tokens[index + 1], default=fetch_timeout_seconds),
                )
                index += 1
        elif token.startswith("--fetch-workers="):
            fetch_workers = min(
                8,
                max(1, _safe_int(token.split("=", 1)[1], default=fetch_workers)),
            )
        elif token == "--fetch-workers":
            if index + 1 >= len(tokens):
                errors.append("--fetch-workers requires a number")
            else:
                fetch_workers = min(
                    8,
                    max(1, _safe_int(tokens[index + 1], default=fetch_workers)),
                )
                index += 1
        else:
            errors.append(f"Unknown option: {token}")
        index += 1

    return RepoStatusOptions(
        roots=tuple(roots),
        include_clean=include_clean,
        show_paths=show_paths,
        max_depth=max_depth,
        limit=limit,
        errors=tuple(errors),
        fetch_mode=fetch_mode,
        fetch_interval_seconds=fetch_interval_seconds,
        fetch_timeout_seconds=fetch_timeout_seconds,
        fetch_workers=fetch_workers,
    )


def format_repo_status_gateway(arg_text: str | Sequence[str] = "") -> str:
    """Format repo status for CLI and messaging platforms."""

    options = parse_repo_status_args(arg_text)
    statuses, fetch_summary = collect_repo_status_report(options)
    return format_repo_status_report(
        statuses,
        options=options,
        fetch_summary=fetch_summary,
    )


def format_repo_status_report(
    statuses: Sequence[RepoStatus],
    *,
    options: RepoStatusOptions | None = None,
    fetch_summary: FetchSummary | None = None,
) -> str:
    """Format a status report from already-collected repository statuses."""

    options = options or RepoStatusOptions()
    fetch_summary = fetch_summary or FetchSummary()
    attention = [status for status in statuses if status.needs_attention]
    shown = list(statuses if options.include_clean else attention)
    hidden_clean = max(0, len(statuses) - len(shown))

    lines = ["Repo Status", ""]
    lines.append(f"{len(statuses)} repositories scanned")
    if attention:
        lines.append(f"{len(attention)} need attention")
    else:
        lines.append("All clean based on local refs")
    if fetch_summary.attempted or fetch_summary.skipped_reason:
        lines.append(fetch_summary.menu_label)

    if options.errors:
        lines.extend(["", "Arguments:"])
        lines.extend(f"- {error}" for error in options.errors)
    if fetch_summary.errors:
        lines.extend(["", "Remote fetch notes:"])
        lines.extend(f"- {error}" for error in fetch_summary.errors[:8])

    if not statuses:
        lines.extend(["", "No git repositories found."])
        return "\n".join(lines)

    if shown:
        lines.append("")
        for status in shown[:options.limit]:
            lines.append(_format_repo_line(status))
            if options.show_paths:
                lines.append(f"  {status.path}")
        if len(shown) > options.limit:
            lines.append(f"... {len(shown) - options.limit} more hidden by --limit")
    elif hidden_clean:
        lines.extend(
            ["", "Clean repositories hidden. Use /repos all to show everything."]
        )

    if hidden_clean and shown:
        lines.append("")
        lines.append(f"{hidden_clean} clean repositories hidden. Use /repos all to show them.")

    lines.append("")
    lines.append(
        "Note: ahead/behind uses your local refs; "
        "run git fetch if you need fresh remote counts."
    )
    return "\n".join(lines)


def format_swiftbar_menu(arg_text: str | Sequence[str] = "") -> str:
    """Format status as SwiftBar/xbar plugin output."""

    options = parse_repo_status_args(arg_text)
    if not options.include_clean:
        options = RepoStatusOptions(
            roots=options.roots,
            include_clean=True,
            show_paths=options.show_paths,
            max_depth=options.max_depth,
            limit=options.limit,
            errors=options.errors,
            fetch_mode=options.fetch_mode,
            fetch_interval_seconds=options.fetch_interval_seconds,
            fetch_timeout_seconds=options.fetch_timeout_seconds,
            fetch_workers=options.fetch_workers,
        )

    statuses, fetch_summary = collect_repo_status_report(options)
    attention = [status for status in statuses if status.needs_attention]
    errors = [status for status in statuses if status.error]
    title = "Git: OK" if not attention else f"Git: {len(attention)}"
    if errors:
        title = f"Git: ERR {len(errors)}"

    lines = [_menu_text(title), "---"]
    lines.append(_menu_text(f"{len(statuses)} repositories scanned"))
    lines.append(
        _menu_text(f"Last checked: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    )
    lines.append(_menu_text(fetch_summary.menu_label))
    lines.append(_force_fetch_menu_line())
    if options.errors:
        lines.append("---")
        lines.extend(_menu_text(f"Arg: {error}") for error in options.errors)
    if fetch_summary.errors:
        lines.append("---")
        lines.append("Remote fetch notes")
        lines.extend(_menu_text(error) for error in fetch_summary.errors[:8])
    if not statuses:
        lines.extend(["---", "No git repositories found"])
        return "\n".join(lines)

    if attention:
        lines.extend(["---", "Needs attention"])
        lines.extend(_format_menu_repo(status) for status in attention)

    clean = [status for status in statuses if not status.needs_attention]
    if clean:
        lines.extend(["---", "Clean"])
        lines.extend(_format_menu_repo(status) for status in clean[: options.limit])
        if len(clean) > options.limit:
            lines.append(_menu_text(f"... {len(clean) - options.limit} more clean repos"))

    return "\n".join(lines)


def maybe_fetch_repositories(
    repos: Sequence[Path],
    options: RepoStatusOptions,
) -> FetchSummary:
    """Refresh remote refs when requested or when the five-minute cache is stale."""

    if options.fetch_mode == "none":
        return FetchSummary(last_fetch_epoch=_read_fetch_state().get("last_fetch_epoch"))

    now = time.time()
    state = _read_fetch_state()
    last_fetch = _safe_float(state.get("last_fetch_epoch"))
    if (
        options.fetch_mode == "stale"
        and last_fetch
        and now - last_fetch < options.fetch_interval_seconds
    ):
        remaining = max(0, int(options.fetch_interval_seconds - (now - last_fetch)))
        return FetchSummary(
            skipped_reason=f"last {format_epoch_time(last_fetch)}, next in {remaining}s",
            last_fetch_epoch=last_fetch,
        )

    fetched = skipped = failed = 0
    errors: list[str] = []
    workers = min(max(1, options.fetch_workers), max(1, len(repos)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch_repo, repo, options.fetch_timeout_seconds): repo
            for repo in repos
        }
        for future in as_completed(futures):
            repo = futures[future]
            try:
                outcome, message = future.result()
            except Exception as exc:
                outcome = "failed"
                message = str(exc)
            if outcome == "fetched":
                fetched += 1
            elif outcome == "skipped":
                skipped += 1
            else:
                failed += 1
                errors.append(f"{repo.name}: {message}")

    completed_at = time.time()
    _write_fetch_state(completed_at)
    return FetchSummary(
        attempted=True,
        fetched=fetched,
        skipped=skipped,
        failed=failed,
        last_fetch_epoch=completed_at,
        errors=tuple(errors),
    )


def _git_status(path: Path) -> RepoStatus:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "status",
                "--porcelain=v1",
                "--branch",
                "--untracked-files=normal",
            ],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return RepoStatus(path=path, error="git status timed out")
    except OSError as exc:
        return RepoStatus(path=path, error=str(exc))

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip().splitlines()
        return RepoStatus(
            path=path,
            error=err[-1] if err else f"git status failed ({result.returncode})",
        )

    return parse_porcelain_status(path, result.stdout)


def _fetch_repo(path: Path, timeout_seconds: int) -> tuple[str, str]:
    try:
        remotes = subprocess.run(
            ["git", "-C", str(path), "remote"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ("failed", str(exc))

    if remotes.returncode != 0:
        err = (remotes.stderr or remotes.stdout or "").strip()
        return ("failed", err or f"git remote failed ({remotes.returncode})")
    if not remotes.stdout.strip():
        return ("skipped", "no remotes configured")

    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "fetch",
                "--prune",
                "--no-tags",
                "--quiet",
            ],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ("failed", "git fetch timed out")
    except OSError as exc:
        return ("failed", str(exc))

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip().splitlines()
        return ("failed", err[-1] if err else f"git fetch failed ({result.returncode})")
    return ("fetched", "")


def _format_repo_line(status: RepoStatus) -> str:
    return f"- {status.name}: {status.branch_label} - {status.summary}"


def _format_menu_repo(status: RepoStatus) -> str:
    color = "red" if status.error else "orange" if status.needs_attention else "green"
    label = _menu_text(f"{status.name}: {status.branch_label} - {status.summary}")
    return label + f" | color={color}"


def _menu_text(text: str) -> str:
    return text.replace("|", "-").replace("\n", " ").strip()


def _force_fetch_menu_line() -> str:
    script = os.environ.get("SWIFTBAR_PLUGIN_PATH", "").strip()
    if not script:
        script = str(Path(__file__).resolve().parents[1] / "scripts" / "repo-status.1m.py")
    return f"Force fetch now | bash={script} param1=--force-fetch terminal=false refresh=true"


def _roots_from_env() -> tuple[Path, ...]:
    raw = os.environ.get("HERMES_REPO_STATUS_ROOTS", "")
    if not raw.strip():
        return ()
    return tuple(Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip())


def _roots_from_config() -> tuple[Path, ...]:
    try:
        from hermes_cli.config import read_raw_config
    except Exception:
        return ()

    try:
        cfg = read_raw_config()
    except Exception:
        return ()

    repo_status = cfg.get("repo_status") if isinstance(cfg, dict) else None
    raw_roots: object = None
    if isinstance(repo_status, dict):
        raw_roots = repo_status.get("roots")
    elif isinstance(repo_status, list):
        raw_roots = repo_status

    if isinstance(raw_roots, str):
        raw_roots = [raw_roots]
    if not isinstance(raw_roots, list):
        return ()

    roots: list[Path] = []
    for item in raw_roots:
        if isinstance(item, str) and item.strip():
            roots.append(Path(item).expanduser())
    return tuple(roots)


def _dedupe_existing_paths(paths: Sequence[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        expanded = path.expanduser()
        try:
            resolved = expanded.resolve()
        except OSError:
            continue
        if not resolved.exists() or resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _append_repo(repos: list[Path], seen: set[Path], path: Path) -> None:
    try:
        resolved = path.resolve()
    except OSError:
        return
    if resolved not in seen:
        seen.add(resolved)
        repos.append(resolved)


def _relative_depth(root: Path, current: Path) -> int:
    try:
        rel = current.relative_to(root)
    except ValueError:
        return 0
    if str(rel) == ".":
        return 0
    return len(rel.parts)


def _safe_int(value: str, *, default: int = 0) -> int:
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_state_path() -> Path:
    return get_hermes_home() / "cache" / "repo-status" / "fetch-state.json"


def _read_fetch_state() -> dict[str, object]:
    path = _fetch_state_path()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_fetch_state(last_fetch_epoch: float) -> None:
    path = _fetch_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"last_fetch_epoch": last_fetch_epoch}, handle)
        tmp.replace(path)
    except OSError:
        pass


def format_epoch_time(epoch: float | None) -> str:
    if not epoch:
        return "never"
    return datetime.fromtimestamp(epoch).strftime("%H:%M:%S")


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--swiftbar", "--xbar", "--menubar"):
        print(format_swiftbar_menu(argv[1:]))
    else:
        print(format_repo_status_gateway(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
