#!/usr/bin/env python3
"""Check or clear the Agent Lights v2 status pool safely (used by uninstall.sh).

Clearing the pool while a Hermes session still owns a slot breaks slot
ownership: the session keeps writing its cached slot number while a new
session claims the same slot in the emptied pool. So this refuses while any
slot, worker, or app owner is still alive. Before it even looks, it holds
every lock a producer or the app takes to claim anything:

- the ``.claim.lock`` of the pool root (app launch) and of ``slots/`` and
  ``agents/``, creating any that do not exist yet (a pool that never had a
  Kanban worker has no ``agents/.claim.lock``, and a first worker must not be
  able to claim between the scan and the clear);
- ``app.lock``, the app's single-instance flock, which the app takes before
  it writes its ``app.json`` heartbeat. If it is held, an app is running or
  starting, and purge refuses. (An app that starts while purge holds it
  exits as "already running"; the next Hermes session relaunches it.)

Lock files are opened like the producers open them (create if missing, never
truncate or replace) and are kept when clearing: replacing a lock file would
let two holders lock different files.

Liveness is PID-only here (no start-time match), which can only err toward
refusing. Standard library only.

    agent_lights_pool.py check [--ignore-app] [POOL]
                                        exit 0 if clearable, 3 if owners live;
                                        --ignore-app skips the app itself (for
                                        a caller about to quit it)
    agent_lights_pool.py purge [POOL]   clear it; 3 if owners live, 4 if busy
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

CLAIM_LOCK = ".claim.lock"
APP_LOCK = "app.lock"
POOL_DIRS = ("slots", "agents")
EXIT_LIVE = 3
EXIT_BUSY = 4


def default_pool() -> Path:
    return Path.home() / ".hermes" / "agent-lights" / "v2"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _owner_pid(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = payload.get("pid") if isinstance(payload, dict) else None
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None


def live_owners(pool: Path, *, include_app: bool = True) -> list[tuple[Path, int]]:
    """Status, lock, and heartbeat files whose owning process is alive."""
    candidates: list[Path] = []
    for name in POOL_DIRS:
        directory = pool / name
        if directory.is_dir():
            candidates.extend(
                path for path in sorted(directory.iterdir())
                if path.suffix in {".json", ".lock"} and path.name != CLAIM_LOCK
            )
    if include_app:
        candidates.append(pool / "app.json")
    owners = []
    for path in candidates:
        pid = _owner_pid(path)
        if pid is not None and pid != os.getpid() and _pid_alive(pid):
            owners.append((path, pid))
    return owners


class AppRunning(Exception):
    """app.lock is held: an Agent Lights app is running or starting."""


@contextmanager
def _flock(path: Path, timeout_s: float):
    """Exclusive flock, opening the file as producers and the app do.

    O_CREAT without O_TRUNC: an existing lock file keeps its inode, so anyone
    already holding or waiting on it contends with this lock.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(str(path)) from None
                time.sleep(0.01)
        yield
    finally:
        os.close(fd)


def _clear(pool: Path) -> None:
    for entry in pool.iterdir():
        if entry.name in (CLAIM_LOCK, APP_LOCK):
            continue
        if entry.name in POOL_DIRS and entry.is_dir() and not entry.is_symlink():
            for item in entry.iterdir():
                if item.name == CLAIM_LOCK:
                    continue
                if item.is_dir() and not item.is_symlink():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        elif entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def purge(pool: Path, *, lock_timeout_s: float = 2.0) -> int:
    if not pool.is_dir():
        return 0
    try:
        with ExitStack() as stack:
            # Fixed order. Producers never hold two claim locks at once, and
            # the app only ever tries its claim lock without waiting, so this
            # cannot deadlock with them.
            stack.enter_context(_flock(pool / CLAIM_LOCK, lock_timeout_s))
            for name in POOL_DIRS:
                (pool / name).mkdir(exist_ok=True)
                stack.enter_context(_flock(pool / name / CLAIM_LOCK, lock_timeout_s))
            # The app holds this for its whole life: don't wait for it.
            try:
                stack.enter_context(_flock(pool / APP_LOCK, 0))
            except TimeoutError:
                raise AppRunning() from None
            owners = live_owners(pool)
            if owners:
                _report(owners)
                return EXIT_LIVE
            _clear(pool)
    except AppRunning:
        print("An Agent Lights app is running or starting (it holds app.lock); quit it, then try again.",
              file=sys.stderr)
        return EXIT_LIVE
    except TimeoutError as exc:
        print(f"Agent Lights pool is busy (a session is claiming a slot: {exc}); try again.", file=sys.stderr)
        return EXIT_BUSY
    return 0


def _report(owners: list[tuple[Path, int]]) -> None:
    print("Agent Lights status files are still owned by running processes:", file=sys.stderr)
    for path, pid in owners:
        print(f"  pid {pid}: {path}", file=sys.stderr)
    print("Exit those Hermes sessions (and quit the app), then run --purge again.", file=sys.stderr)


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"check", "purge"}:
        print(__doc__, file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    include_app = True
    if command == "check" and rest[:1] == ["--ignore-app"]:
        include_app, rest = False, rest[1:]
    pool = Path(rest[0]) if rest else default_pool()
    if command == "check":
        if not pool.is_dir():
            return 0
        if include_app:
            try:
                with _flock(pool / APP_LOCK, 0):
                    pass
            except TimeoutError:
                print("An Agent Lights app is running or starting (it holds app.lock).", file=sys.stderr)
                return EXIT_LIVE
        owners = live_owners(pool, include_app=include_app)
        if owners:
            _report(owners)
            return EXIT_LIVE
        return 0
    return purge(pool)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
