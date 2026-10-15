"""Agent Lights v2 slot protocol for the stock-Hermes plugin.

Writes the per-session status files the Agent Lights menu-bar app renders, under
the shared, cross-profile pool ``~/.hermes/agent-lights/v2``. See the app's
README ("Contract") for the protocol: flock-serialized claims on
``<pool>/.claim.lock``, numeric ``process_start_time``, claim-time reaping of a
dead owner's files, and the app's ``app.json`` heartbeat.
"""

from __future__ import annotations

import atexit
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from hermes_constants import get_default_hermes_root, get_hermes_home

logger = logging.getLogger(__name__)

# Same pool and heartbeat as the fork's producer and the current app.
_AGENT_LIGHTS_POOL = "v2"
_APP_HEARTBEAT_FILE = "app.json"


def agent_lights_root() -> Path:
    """Shared, cross-profile Agent Lights directory for this protocol version.

    The app watches the root Hermes home while profile sessions run with
    ``HERMES_HOME=~/.hermes/profiles/<name>``, so this uses the default Hermes
    root. ``HERMES_AGENT_LIGHTS_HOME`` overrides the base for tests.
    """
    raw_override = os.environ.get("HERMES_AGENT_LIGHTS_HOME", "").strip()
    base_home = Path(raw_override) if raw_override else get_default_hermes_root()
    return base_home / "agent-lights" / _AGENT_LIGHTS_POOL


class LightCueEvent(str, Enum):
    WORKING = "working"
    HUMAN_INTERVENTION = "human_intervention"
    FINAL_ANSWER = "final_answer"
    ERROR = "error"
    IDLE = "idle"


@dataclass
class SlotStatusFileBackend:
    """Write per-agent slot lifecycle status for local renderers.

    Copied from the Hermes fork Agent Lights started in
    (``agent/light_cues.py``) so stock Hermes can produce the same files. Keep
    the claim protocol identical: both producers share one pool, and the
    app's pruner relies on it.
    """

    slot: int
    directory: Path | None = None
    model_name: str | None = None

    @classmethod
    def from_env(
        cls,
        *,
        auto_assign: bool = False,
        directory: Path | None = None,
        model_name: str | None = None,
    ) -> "SlotStatusFileBackend | None":
        resolved_directory = directory or cls._default_directory_for_process()
        raw_slot = os.environ.get("HERMES_SLOT", "").strip()
        try:
            slot = int(raw_slot)
        except ValueError:
            slot = 0
        preferred = slot if slot in cls._valid_slots(resolved_directory) else None
        if preferred is None and not auto_assign:
            return None
        # HERMES_SLOT is a preference, claimed under the same ownership rules
        # as auto-assignment: it never overwrites a live session's slot. When
        # it is taken, auto-assigning callers fall back to the first free slot.
        claimed = cls._claim_available_slot(
            resolved_directory,
            preferred=preferred,
            fallback_to_any=auto_assign,
        )
        if claimed is None:
            return None
        return cls._with_exit_cleanup(cls(slot=claimed, directory=resolved_directory, model_name=model_name))

    @staticmethod
    def _default_directory_for_process() -> Path:
        """Return this process's pool: Kanban workers or normal Hermes slots."""
        base = agent_lights_root()
        if os.environ.get("HERMES_KANBAN_TASK"):
            return base / "agents"
        return base / "slots"

    @classmethod
    def _with_exit_cleanup(cls, backend: "SlotStatusFileBackend") -> "SlotStatusFileBackend":
        atexit.register(backend.clear_if_owned)
        return backend

    @classmethod
    def _valid_slots(cls, directory: Path) -> range:
        return range(1, cls._slot_capacity(directory) + 1)

    @classmethod
    def _slot_capacity(cls, directory: Path) -> int:
        if os.environ.get("HERMES_KANBAN_TASK") or directory.name == "agents":
            return 8
        return 6

    @classmethod
    def _claim_available_slot(
        cls,
        directory: Path,
        *,
        preferred: int | None = None,
        fallback_to_any: bool = True,
    ) -> int | None:
        """Atomically claim the current process's slot or a free one.

        Tries ``preferred`` first; other slots only when ``fallback_to_any``.
        """
        directory.mkdir(parents=True, exist_ok=True)
        current_pid = os.getpid()
        # Serialize the whole check-reap-claim scan across processes. Without
        # this, one claimant can unlink a lock another claimant created a
        # moment earlier (both judged the previous lock stale), and two
        # sessions end up sharing one slot.
        with cls._claim_mutex(directory) as acquired:
            if not acquired:
                # Another claimant is wedged holding the mutex (e.g. a Hermes
                # startup suspended with Ctrl-Z). Skip rather than block; the
                # service retries slot registration on later lifecycle events.
                return None
            # Invariant on every successful return: this process holds the
            # slot's lock and the slot's status file is absent or its own. A
            # dead owner's status file must never outlive a claim, or the menu
            # app could prune it just as this session's first emit replaces it,
            # deleting the new owner's file.
            for slot in cls._valid_slots(directory):
                path = directory / f"{slot}.json"
                lock_path = cls._lock_path(directory, slot)
                if cls._slot_pid_matches(path, current_pid):
                    return slot
                if not cls._slot_pid_matches(lock_path, current_pid):
                    continue
                # Our lock survives from an earlier attempt whose cleanup or
                # rollback failed. Finish that claim before adopting it.
                if cls._payload_is_live(cls._slot_payload(path)):
                    # Another live process writes this slot: not ours to adopt.
                    cls._unlink_quietly(lock_path)
                    continue
                if cls._reap_status_file(path):
                    return slot
                # Still cannot clean it: keep the lock and retry later.
                return None
            candidates: list[int] = []
            if preferred is not None:
                candidates.append(preferred)
            if fallback_to_any:
                candidates.extend(slot for slot in cls._valid_slots(directory) if slot != preferred)
            for slot in candidates:
                path = directory / f"{slot}.json"
                lock_path = cls._lock_path(directory, slot)
                if not cls._slot_is_available(path, lock_path=lock_path):
                    continue
                if not cls._try_claim_lock(lock_path):
                    continue
                if cls._reap_status_file(path):
                    return slot
                # The claim is not safe to use: roll it back. If even that
                # fails, the adoption pass above finishes it on a later retry.
                cls._unlink_quietly(lock_path)
        return None

    @staticmethod
    def _reap_status_file(path: Path) -> bool:
        """Remove a dead owner's status file; True when it is gone."""
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.debug("Failed to reap stale Agent Lights slot file %s: %s", path, exc)
            return False
        return True

    @staticmethod
    def _unlink_quietly(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    # The fork waits up to 2 s. Plugin hooks run on the agent thread, so wait
    # only briefly; a session that misses the lock retries on its next event.
    _CLAIM_MUTEX_TIMEOUT_S = 0.2

    @classmethod
    @contextmanager
    def _claim_mutex(cls, directory: Path):
        """Hold an exclusive advisory lock on ``<directory>/.claim.lock``.

        Yields whether the lock was acquired within ``_CLAIM_MUTEX_TIMEOUT_S``.
        The kernel drops ``flock`` locks when the holder dies, so a crashed
        claimant cannot wedge later sessions; the timeout covers a holder that
        is alive but stopped. Platforms without ``fcntl`` fall back to the
        unserialized O_EXCL claim.
        """
        try:
            import fcntl
        except ImportError:
            yield True
            return
        fd = os.open(directory / ".claim.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            deadline = time.monotonic() + cls._CLAIM_MUTEX_TIMEOUT_S
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        yield False
                        return
                    time.sleep(0.01)
            yield True
        finally:
            os.close(fd)

    @staticmethod
    def _lock_path(directory: Path, slot: int) -> Path:
        return directory / f"{slot}.lock"

    @classmethod
    def _try_claim_lock(cls, lock_path: Path) -> bool:
        payload = cls._process_payload()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(lock_path, flags, 0o600)
        except FileExistsError:
            return False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
        except Exception:
            try:
                lock_path.unlink()
            except OSError:
                pass
            raise
        return True

    @classmethod
    def _process_payload(cls, model_name: str | None = None) -> dict[str, Any]:
        payload = {
            "pid": os.getpid(),
            "process_start_time": cls._process_start_time(os.getpid()),
        }
        resolved_model = cls._first_nonempty(
            os.environ.get("HERMES_MODEL"),
            os.environ.get("HERMES_INFERENCE_MODEL"),
            model_name,
        )
        if resolved_model:
            payload["model_name"] = resolved_model
        profile = cls._first_nonempty(
            os.environ.get("HERMES_PROFILE"),
            cls._profile_name_from_home(),
        )
        if profile:
            payload["profile"] = profile
        session_id = cls._first_nonempty(os.environ.get("HERMES_SESSION_ID"))
        if session_id:
            payload["session_id"] = session_id
        if os.environ.get("HERMES_KANBAN_TASK"):
            payload["source"] = "kanban_worker"
            payload["kanban_task_id"] = os.environ.get("HERMES_KANBAN_TASK")
            if os.environ.get("HERMES_KANBAN_BOARD"):
                payload["kanban_board"] = os.environ.get("HERMES_KANBAN_BOARD")
            task_title = cls._first_nonempty(os.environ.get("HERMES_KANBAN_TASK_TITLE"))
            if task_title:
                payload["kanban_task_title"] = task_title
        else:
            payload["source"] = "hermes"
        return payload

    @staticmethod
    def _profile_name_from_home() -> str:
        """Infer a stable lane name when the launcher did not export one."""
        try:
            home = Path(get_hermes_home()).resolve()
        except OSError:
            return "default"
        if home.parent.name == "profiles" and home.name:
            return home.name
        return "default"

    @staticmethod
    def _first_nonempty(*values: str | None) -> str | None:
        for value in values:
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    return stripped
        return None

    @classmethod
    def _slot_payload(cls, path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def _slot_pid_matches(cls, path: Path, expected_pid: int) -> bool:
        payload = cls._slot_payload(path)
        if not payload:
            return False
        return cls._payload_pid(payload) == expected_pid and cls._payload_process_matches(payload)

    @staticmethod
    def _payload_pid(payload: dict[str, Any]) -> int | None:
        pid = payload.get("pid")
        return pid if isinstance(pid, int) and pid > 0 else None

    @classmethod
    def _slot_is_available(cls, path: Path, *, lock_path: Path | None = None) -> bool:
        if lock_path is not None and lock_path.exists():
            if cls._payload_is_live(cls._slot_payload(lock_path)):
                return False
            try:
                lock_path.unlink()
            except OSError:
                return False
        if not path.exists():
            return True
        if cls._payload_is_live(cls._slot_payload(path)):
            return False
        return True

    @classmethod
    def _payload_is_live(cls, payload: dict[str, Any] | None) -> bool:
        if not payload:
            return False
        pid = cls._payload_pid(payload)
        return pid is not None and cls._pid_is_running(pid) and cls._payload_process_matches(payload)

    # Two start-time readings of one process can differ by float rounding
    # (JSON round-trip, psutil vs. sysctl); a recycled PID cannot start within
    # this window of the original.
    _START_TIME_TOLERANCE_S = 1.0

    @classmethod
    def _payload_process_matches(cls, payload: dict[str, Any]) -> bool:
        """Return whether the payload's PID still names the process that wrote it.

        Compares numeric process start times (epoch seconds). Older payloads
        carried a ``ps -o lstart`` string, whose text depends on the reader's
        locale and timezone; those are trusted on PID liveness alone rather
        than risk declaring a live session dead.
        """
        pid = cls._payload_pid(payload)
        if pid is None:
            return False
        expected = payload.get("process_start_time")
        if isinstance(expected, bool) or not isinstance(expected, (int, float)):
            return True
        actual = cls._process_start_time(pid)
        if actual is None:
            return False
        return abs(actual - float(expected)) <= cls._START_TIME_TOLERANCE_S

    @staticmethod
    def _process_start_time(pid: int) -> float | None:
        """Return the process start time in epoch seconds, or None if gone."""
        try:
            import psutil  # type: ignore

            return float(psutil.Process(pid).create_time())
        except Exception:
            return None

    @staticmethod
    def _pid_is_running(pid: int) -> bool:
        """Return process liveness without sending a Windows console signal."""
        try:
            import psutil  # type: ignore

            return psutil.pid_exists(pid)
        except Exception:
            return False

    @property
    def _directory(self) -> Path:
        return self.directory or (agent_lights_root() / "slots")

    def emit_event(self, event: LightCueEvent) -> bool:
        path = self._directory / f"{self.slot}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "slot": self.slot,
            "event": event.value,
            "state": event.value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **self._process_payload(self.model_name),
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return True

    def emit_context(
        self,
        *,
        session_id: str | None = None,
        current_context: str | None = None,
        chat_summary: str | None = None,
        tab_name: str | None = None,
        provider: str | None = None,
        model_name: str | None = None,
    ) -> bool:
        """Write the per-slot menu context sidecar consumed by Agent Lights."""
        if os.environ.get("HERMES_KANBAN_TASK"):
            return False
        path = self._directory.parent / "context" / f"{self.slot}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        process_payload = self._process_payload(model_name or self.model_name)
        payload: dict[str, Any] = {
            "slot": self.slot,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "process_start_time": self._process_start_time(os.getpid()),
            "source": "hermes",
        }
        for key in ("profile", "session_id", "model_name"):
            if process_payload.get(key):
                payload[key] = process_payload[key]
        for key, value in {
            "session_id": session_id,
            "current_context": current_context,
            "chat_summary": chat_summary,
            "tab_name": tab_name,
            "provider": provider,
            "model_name": model_name,
        }.items():
            text = self._first_nonempty(value)
            if text:
                payload[key] = text
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return True

    def clear_if_owned(self) -> bool:
        """Release this process's Agent Lights files during normal shutdown."""
        removed = False
        slot_path = self._directory / f"{self.slot}.json"
        if self._slot_pid_matches(slot_path, os.getpid()):
            payload = self._slot_payload(slot_path) or {}
            preserve_terminal_kanban_state = payload.get("source") == "kanban_worker" and payload.get("state") in {
                LightCueEvent.FINAL_ANSWER.value,
                LightCueEvent.HUMAN_INTERVENTION.value,
                LightCueEvent.ERROR.value,
            }
            if not preserve_terminal_kanban_state:
                try:
                    slot_path.unlink()
                    removed = True
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    logger.debug("Failed to clear Agent Lights slot file %s: %s", slot_path, exc)

        for path in (
            self._lock_path(self._directory, self.slot),
            self._directory.parent / "context" / f"{self.slot}.json",
        ):
            if not self._slot_pid_matches(path, os.getpid()):
                continue
            try:
                path.unlink()
                removed = True
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.debug("Failed to clear Agent Lights slot file %s: %s", path, exc)
        return removed


class InstalledAppLauncher:
    """Open the installed Agent Lights app when no Agent Lights app is running.

    Never builds, signs, or terminates anything. Opening is serialized with
    every other current producer through the pool's claim lock, rechecked
    under it, and waits (holding the lock) until the app is visible, matching
    the fork's launcher so the two never open a second copy.
    """

    executable_name = "AgentLightsMenuBar"
    bundle_name = "AgentLightsMenuBar-v2.app"
    launch_ready_timeout_s = 3.0

    def candidate_app_paths(self) -> list[Path]:
        return [Path.home() / "Applications" / self.bundle_name, Path("/Applications") / self.bundle_name]

    def installed_app(self) -> Path | None:
        for path in self.candidate_app_paths():
            if (path / "Contents" / "MacOS" / self.executable_name).is_file():
                return path
        return None

    def ensure_running(self) -> bool:
        if str(os.environ.get("HERMES_AGENT_LIGHTS_AUTO_LAUNCH", "true")).lower() in {"0", "false", "no", "off"}:
            return False
        if self.is_running():
            return True
        app_path = self.installed_app()
        if app_path is None:
            logger.info("Agent Lights launch skipped: app not installed (run install.sh).")
            return False
        root = agent_lights_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.debug("Cannot create Agent Lights directory %s: %s", root, exc)
            return False
        with SlotStatusFileBackend._claim_mutex(root) as acquired:
            if not acquired:
                return False
            if self.is_running():
                return True
            running = self.any_app_process()
            if running is None or running:
                # Unknown, or an app without a live v2 heartbeat (starting up,
                # or a pre-v2 build the user quits once): leave it alone.
                return bool(running)
            try:
                completed = subprocess.run(
                    ["/usr/bin/open", "-g", str(app_path)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=3,
                )
            except Exception as exc:
                logger.info("Agent Lights launch failed: %s", exc)
                return False
            if completed.returncode != 0:
                logger.info("Agent Lights launch failed: open exited %s: %s", completed.returncode, (completed.stderr or "").strip())
                return False
            deadline = time.monotonic() + self.launch_ready_timeout_s
            while time.monotonic() < deadline:
                if self.is_running() or self.any_app_process():
                    return True
                time.sleep(0.05)
            logger.info("Agent Lights app was opened but did not appear within %.0fs.", self.launch_ready_timeout_s)
            return False

    def is_running(self) -> bool:
        """Whether a current (v2) app is running, per its heartbeat."""
        heartbeat = SlotStatusFileBackend._slot_payload(agent_lights_root() / _APP_HEARTBEAT_FILE)
        if not heartbeat or heartbeat.get("pool") != _AGENT_LIGHTS_POOL:
            return False
        pid = SlotStatusFileBackend._payload_pid(heartbeat)
        start = heartbeat.get("process_start_time")
        if pid is None or isinstance(start, bool) or not isinstance(start, (int, float)):
            return False
        return SlotStatusFileBackend._pid_is_running(pid) and SlotStatusFileBackend._payload_process_matches(heartbeat)

    def any_app_process(self) -> bool | None:
        """Whether any Agent Lights app process is running; None if unknown."""
        try:
            import psutil  # type: ignore

            return any(proc.info["name"] == self.executable_name for proc in psutil.process_iter(["name"]))
        except Exception:
            return None
