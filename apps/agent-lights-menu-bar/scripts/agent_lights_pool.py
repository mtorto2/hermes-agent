#!/usr/bin/env python3
"""Check or clear the Agent Lights v2 status pool safely (used by uninstall.sh).

Clearing the pool while a Hermes session still owns a slot breaks slot
ownership: the session keeps writing its cached slot number while a new
session claims the same slot in the emptied pool. So this refuses while any
slot, worker, or app owner is still alive, and clears under the same
``.claim.lock`` flocks every producer and the app hold while claiming, so no
claim can interleave. The ``.claim.lock`` files themselves are kept (a
claimant may already hold one open; replacing it would split the lock).

Liveness is PID-only here (no start-time match), which can only err toward
refusing. Standard library only.

    agent_lights_pool.py check [POOL]   exit 0 if clearable, 3 if owners live
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


def live_owners(pool: Path) -> list[tuple[Path, int]]:
    """Status, lock, and heartbeat files whose owning process is alive."""
    candidates: list[Path] = []
    for name in POOL_DIRS:
        directory = pool / name
        if directory.is_dir():
            candidates.extend(
                path for path in sorted(directory.iterdir())
                if path.suffix in {".json", ".lock"} and path.name != CLAIM_LOCK
            )
    candidates.append(pool / "app.json")
    owners = []
    for path in candidates:
        pid = _owner_pid(path)
        if pid is not None and pid != os.getpid() and _pid_alive(pid):
            owners.append((path, pid))
    return owners


@contextmanager
def _flock(path: Path, timeout_s: float):
    """Exclusive flock on an existing lock file (never creates one)."""
    fd = os.open(path, os.O_RDWR)
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
        if entry.name in POOL_DIRS and entry.is_dir() and not entry.is_symlink():
            for item in entry.iterdir():
                if item.name == CLAIM_LOCK:
                    continue
                if item.is_dir() and not item.is_symlink():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        elif entry.name == CLAIM_LOCK:
            continue
        elif entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def purge(pool: Path, *, lock_timeout_s: float = 2.0) -> int:
    if not pool.is_dir():
        return 0
    lock_files = [pool / CLAIM_LOCK] + [pool / name / CLAIM_LOCK for name in POOL_DIRS]
    try:
        with ExitStack() as stack:
            # Fixed order; producers never hold two of these at once.
            for lock_file in lock_files:
                if lock_file.exists():
                    stack.enter_context(_flock(lock_file, lock_timeout_s))
            owners = live_owners(pool)
            if owners:
                _report(owners)
                return EXIT_LIVE
            _clear(pool)
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
    pool = Path(argv[1]) if len(argv) > 1 else default_pool()
    if argv[0] == "check":
        owners = live_owners(pool) if pool.is_dir() else []
        if owners:
            _report(owners)
            return EXIT_LIVE
        return 0
    return purge(pool)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
