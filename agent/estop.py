"""Global emergency stop (ESTOP) — a resumable pause for NEW work only.

``hermes pause`` writes a sentinel file at ``$HERMES_HOME/ESTOP``;
``hermes resume`` removes it. While the sentinel exists:

* the cron scheduler skips dispatching due jobs (``cron/scheduler.py:tick``),
* the embedded kanban dispatcher skips spawning workers
  (``gateway/kanban_watchers.py``),
* new gateway turns get a brief "Hermes is paused" reply instead of an
  agent run (``gateway/run.py:_handle_message``).

In-flight work is NEVER killed — this is pause-new-work, not panic/exit.
The check is one or two ``os.stat`` calls (process home + fleet root when
they differ) so callers may run it every tick; no caching beyond the OS is
performed, so engaging/disengaging takes effect on the very next check.

The sentinel body is optional JSON ``{"reason": ..., "engaged_at": ...}``.
A corrupt or empty file still counts as engaged (fail safe): the pause must
hold even if the file was created by ``touch ~/.hermes/ESTOP``.

Ported from: gastownhall/gastown estop.go (MIT). Related prior art:
#26778 (/panic — kill/exit semantics; deliberately different, ours is
resumable) and #44617 (interrupting in-flight cron; deliberately out of
scope here).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SENTINEL_NAME = "ESTOP"

# Per-component "logged already for this engagement" flags so a paused
# dispatch loop logs once per engagement instead of once per tick.
_log_lock = threading.Lock()
_logged_components: set[str] = set()


def _hermes_home() -> Path:
    """Resolve the active HERMES_HOME (profile-aware) at call time."""
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home()
    except Exception:
        return Path(os.path.expanduser("~/.hermes"))


def _canonical_root() -> Path:
    """Fleet-wide Hermes root, even when this process is a profile gateway.

    Profile gateways launch with HERMES_HOME=~/.hermes/profiles/<name>.
    ``hermes pause`` from an operator seat writes ~/.hermes/ESTOP. If we
    only inspect the profile home, the emergency stop does not bind
    (jarvis-os/t_7b65ff88: fleet-analyst kept dispatching through pause).
    """
    try:
        from hermes_constants import get_default_hermes_root
        return Path(get_default_hermes_root())
    except Exception:
        return Path(os.path.expanduser("~/.hermes"))


def sentinel_path(home: Optional[Path | str] = None) -> Path:
    """Path of the ESTOP sentinel under *home* or the active HERMES_HOME.

    This is the sentinel `hermes pause` writes; reads additionally consult
    the fleet root (see :func:`_candidate_sentinel_paths`).
    """
    return Path(home) / SENTINEL_NAME if home is not None else _hermes_home() / SENTINEL_NAME


def _candidate_sentinel_paths(home: Optional[Path | str] = None) -> list:
    """Profile home first, then the fleet root if it is a different directory.

    *home* selects an explicit profile (a Kanban dispatcher checking the
    assignee's pause); the fleet root is always appended so an operator's
    ``hermes pause`` binds every profile.
    """
    # Call with no argument when unscoped so test doubles that replace
    # ``sentinel_path`` with a zero-arg callable keep working.
    primary = sentinel_path(home) if home is not None else sentinel_path()
    paths = [primary]
    try:
        root = _canonical_root() / SENTINEL_NAME
    except Exception:
        return paths
    try:
        if root.resolve() != primary.resolve():
            paths.append(root)
    except Exception:
        # Non-Path test doubles (fail-safe stat fixture) fail .resolve();
        # the generic comparison below still dedupes plain equal paths.
        if root != primary:
            paths.append(root)
    return paths


def is_engaged(home: Optional[Path | str] = None) -> bool:
    """Cheap check: is the emergency stop engaged for *home*?

    Engaged if ANY candidate sentinel exists: the profile home (*home*, or
    the process HERMES_HOME) or the fleet canonical root (~/.hermes). Fail
    SAFE on stat errors so an unreadable sentinel still holds the pause.
    """
    saw_stat_error = False
    for path in _candidate_sentinel_paths(home):
        try:
            if path.exists():
                return True
        except OSError:
            saw_stat_error = True
    return saw_stat_error


def engage(reason: Optional[str] = None, *, home: Optional[Path | str] = None) -> Path:
    """Create the ESTOP sentinel for *home*; re-engaging updates the file."""
    path = sentinel_path(home)
    payload = {
        "engaged_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason or None,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        # Best effort: an empty/partial sentinel still pauses (fail safe).
        try:
            path.touch(exist_ok=True)
        except OSError:
            pass
    return path


def disengage(*, home: Optional[Path | str] = None) -> bool:
    """Remove only the ESTOP sentinel selected by *home*.

    A profile-scoped resume may lift its own pause, but it never clears the
    fleet-root sentinel.  Calling without *home* targets the active
    HERMES_HOME, which is the fleet root for the root operator.
    """
    path = sentinel_path(home) if home is not None else sentinel_path()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        # If removal fails, pause remains engaged.  Caller reports failure.
        return False

def get_state(home: Optional[Path | str] = None) -> Optional[dict]:
    """Return ``{"reason": ..., "engaged_at": ...}`` or None when not engaged.

    A sentinel with an unreadable/corrupt body still reports engaged, with
    both fields None — the pause is authoritative, the metadata is not.
    """
    if not is_engaged(home):
        return None
    reason = None
    engaged_at = None
    found = False
    for path in _candidate_sentinel_paths(home):
        try:
            exists = path.exists()
        except OSError:
            return {"reason": None, "engaged_at": None}
        except AttributeError:
            continue
        if not exists:
            continue
        found = True
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                reason = raw.get("reason") or None
                engaged_at = raw.get("engaged_at") or None
                break
        except (OSError, ValueError, AttributeError):
            continue
    if not found:
        return None
    return {"reason": reason, "engaged_at": engaged_at}


def paused_reply(home: Optional[Path | str] = None) -> Optional[str]:
    """Short user-facing notice for new gateway turns, or None if not paused."""
    state = get_state(home)
    if state is None:
        return None
    reason = state.get("reason")
    if reason:
        return (
            f"⏸️ Hermes is paused ({reason}). New work is on hold; "
            "run `hermes resume` to pick things back up."
        )
    return (
        "⏸️ Hermes is paused. New work is on hold; "
        "run `hermes resume` to pick things back up."
    )


def check_paused(
    component: str,
    logger: logging.Logger,
    *,
    home: Optional[Path | str] = None,
) -> bool:
    """Return True when engaged, logging once per engagement per component.

    Dispatch loops call this every tick; the log fires on the disengaged→
    engaged transition for that component and re-arms after a resume, so a
    long pause doesn't spam one line per tick.
    """
    if not is_engaged(home):
        with _log_lock:
            _logged_components.discard(component)
        return False
    with _log_lock:
        first = component not in _logged_components
        if first:
            _logged_components.add(component)
    if first:
        state = get_state(home) or {}
        reason = state.get("reason")
        suffix = f" (reason: {reason})" if reason else ""
        logger.info(
            "%s dispatch paused by global emergency stop%s — remove with "
            "`hermes resume` (%s)",
            component,
            suffix,
            sentinel_path(home),
        )
    return True


def _reset_log_state_for_tests() -> None:
    """Clear the log-once bookkeeping (test isolation helper)."""
    with _log_lock:
        _logged_components.clear()
