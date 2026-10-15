"""Shared light cue service for Hermes notification/busy-light surfaces."""

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
import threading
import time
from typing import Any, Callable, Protocol

from agent.wiz_light import WiZLightCueBackend, WiZLightCueConfig

from hermes_constants import get_default_hermes_root, get_hermes_home

logger = logging.getLogger(__name__)

_STATE_FILE = "light_cues.json"

# Agent Lights pool for the current ownership protocol (flock-serialized
# claims, numeric process start times, claim-time reaping). Producers from
# before it wrote ``agent-lights/slots`` and ``agent-lights/agents`` without
# that protocol; keeping current producers and the current app in a separate
# pool means the two generations can never contend for one slot, so upgrading
# needs no coordinated stop. Bump only for an incompatible protocol change.
_AGENT_LIGHTS_POOL = "v2"
_APP_HEARTBEAT_FILE = "app.json"


def agent_lights_root() -> Path:
    """Shared, cross-profile Agent Lights directory for this protocol version.

    The menu-bar app watches the root Hermes home while profile sessions run
    with ``HERMES_HOME=~/.hermes/profiles/<name>``, so this uses the default
    Hermes root (all profiles share one surface). ``HERMES_AGENT_LIGHTS_HOME``
    overrides the base for tests and unusual deployments.
    """
    raw_override = os.environ.get("HERMES_AGENT_LIGHTS_HOME", "").strip()
    base_home = Path(raw_override) if raw_override else get_default_hermes_root()
    return base_home / "agent-lights" / _AGENT_LIGHTS_POOL


class LightCueMode(str, Enum):
    DEFAULT = "default"
    NIGHT = "night"
    DIM_DEFAULT = "dim-default"
    NO_LIGHT = "no-light"

    @classmethod
    def from_value(cls, value: Any) -> "LightCueMode":
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().lower().replace("_", "-")
        aliases = {
            "normal": cls.DEFAULT,
            "default": cls.DEFAULT,
            "night": cls.NIGHT,
            "dim": cls.DIM_DEFAULT,
            "dim-default": cls.DIM_DEFAULT,
            "dimmed": cls.DIM_DEFAULT,
            "off": cls.NO_LIGHT,
            "none": cls.NO_LIGHT,
            "no-light": cls.NO_LIGHT,
            "nolight": cls.NO_LIGHT,
        }
        return aliases.get(text, cls.DEFAULT)


class LightCueEvent(str, Enum):
    WORKING = "working"
    HUMAN_INTERVENTION = "human_intervention"
    FINAL_ANSWER = "final_answer"
    ERROR = "error"
    IDLE = "idle"
    @classmethod
    def from_value(cls, value: Any) -> "LightCueEvent":
        if isinstance(value, cls):
            return value
        return cls(str(value))


@dataclass(frozen=True)
class LightCueAction:
    cue: str
    brightness: int = 100
    flashing: bool = False


class LightCueBackend(Protocol):
    def emit(self, action: LightCueAction) -> bool:
        ...


class SlotStatusBackend(Protocol):
    def emit_event(self, event: LightCueEvent) -> bool:
        ...


class MenuBarLauncher(Protocol):
    def ensure_running(self) -> bool:
        ...


class NullLightCueBackend:
    def emit(self, action: LightCueAction) -> bool:
        return False


class AgentLightsMenuBarLauncher:
    """Best-effort launcher for the native Hermes Agent Lights status item."""

    executable_name = "AgentLightsMenuBar"
    # Versioned like the pool: pre-v2 launchers package and open
    # .build/AgentLightsMenuBar.app without taking the v2 launch lock, so the
    # current app must never run from (or be rewritten through) that bundle.
    bundle_relative_path = Path("apps/agent-lights-menu-bar/.build/AgentLightsMenuBar-v2.app")
    # How long to wait, still holding the launch lock, for an opened app to
    # appear, so the next launcher sees it instead of launching again.
    launch_ready_timeout_s = 3.0
    binary_relative_path = Path("apps/agent-lights-menu-bar/.build/debug/AgentLightsMenuBar")
    arch_binary_relative_glob = "apps/agent-lights-menu-bar/.build/*/debug/AgentLightsMenuBar"

    def __init__(self, *, repo_root: Path | None = None):
        self.repo_root = repo_root or Path(__file__).resolve().parents[1]

    def ensure_running(self) -> bool:
        """Open the current (v2) app if no Agent Lights app is running at all.

        Never terminates anything. A running app without a live v2 heartbeat
        is either a pre-v2 build (which the user quits once to upgrade) or a
        current app still starting; both are left alone. Bundle refresh and
        launch are serialized across Hermes processes, and the state is
        rechecked under the lock, so two sessions cannot rewrite or open the
        bundle at the same time.
        """
        if str(os.environ.get("HERMES_AGENT_LIGHTS_AUTO_LAUNCH", "true")).lower() in {"0", "false", "no", "off"}:
            return False
        if self._is_running():
            return True
        root = agent_lights_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.debug("Cannot create Agent Lights directory %s: %s", root, exc)
            return False
        with SlotStatusFileBackend._claim_mutex(root) as acquired:
            if not acquired:
                logger.info("Agent Lights launch skipped: another Hermes process is launching the app.")
                return False
            if self._is_running():
                return True
            running = self._any_app_process()
            if running is None:
                logger.info("Agent Lights launch skipped: cannot list processes to check for a running app.")
                return False
            if running:
                logger.info(
                    "Agent Lights launch skipped: an Agent Lights app without a v2 heartbeat is "
                    "running. If it is a pre-v2 build, quit it (menu > Quit Hermes Agent Lights) "
                    "so the current app can start."
                )
                return False
            app_path = self._ensure_app_bundle()
            if app_path is None:
                logger.info("Agent Lights launch skipped: no built app (run `swift build` in apps/agent-lights-menu-bar).")
                return False
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
            # LaunchServices starts the app asynchronously. Keep the lock until
            # it is visible (or the timeout passes) so a launcher waiting on the
            # lock rechecks against a running app rather than opening another.
            deadline = time.monotonic() + self.launch_ready_timeout_s
            while time.monotonic() < deadline:
                if self._is_running() or self._any_app_process():
                    return True
                time.sleep(0.05)
            logger.info("Agent Lights app was opened but did not appear within %.0fs.", self.launch_ready_timeout_s)
            return False

    def _candidate_binary_paths(self) -> list[Path]:
        candidates = [self.repo_root / self.binary_relative_path]
        candidates.extend(self.repo_root.glob(self.arch_binary_relative_glob))
        unique: dict[Path, Path] = {}
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                unique[candidate.resolve()] = candidate
        return sorted(
            unique.values(),
            key=lambda path: (
                path.stat().st_mtime,
                1 if "-apple-macosx" in str(path) else 0,
            ),
            reverse=True,
        )

    def _built_binary_path(self) -> Path | None:
        candidates = self._candidate_binary_paths()
        return candidates[0] if candidates else None

    def _is_running(self) -> bool:
        """Whether a current (v2) app is running, per the heartbeat it writes.

        App heartbeats are new in v2, so unlike slot payloads there is no
        legacy form to accept: the pool must match and the PID must still be
        the process that wrote it (numeric start time required).
        """
        heartbeat = SlotStatusFileBackend._slot_payload(agent_lights_root() / _APP_HEARTBEAT_FILE)
        if not heartbeat or heartbeat.get("pool") != _AGENT_LIGHTS_POOL:
            return False
        pid = SlotStatusFileBackend._payload_pid(heartbeat)
        start = heartbeat.get("process_start_time")
        if pid is None or isinstance(start, bool) or not isinstance(start, (int, float)):
            return False
        return SlotStatusFileBackend._pid_is_running(pid) and SlotStatusFileBackend._payload_process_matches(heartbeat)

    def _any_app_process(self) -> bool | None:
        """Whether any Agent Lights app process (of any version) is running.

        None when processes cannot be listed; callers treat that as "maybe"
        rather than risk rewriting the bundle under a live app.
        """
        try:
            import psutil  # type: ignore

            return any(proc.info["name"] == self.executable_name for proc in psutil.process_iter(["name"]))
        except Exception:
            return None

    def _bundle_alias_problem(self, app_path: Path) -> str | None:
        """Why the v2 bundle path is unsafe to package into, or None.

        Pre-v2 launchers write ``AgentLightsMenuBar.app/Contents/MacOS/
        AgentLightsMenuBar`` next to it. If that path reaches into the v2
        bundle through any symlink (bundle root, ``Contents``, ``MacOS`` or
        the executable), or the v2 path itself is an alias, packaging here
        and their packaging there would share storage. The app refuses to run
        from such a bundle as well.
        """
        if app_path.is_symlink():
            return f"{app_path} is a symlink"
        bundle = os.path.realpath(app_path)
        if os.path.basename(bundle) != app_path.name:
            return f"{app_path} resolves to {bundle}"
        legacy_executable = app_path.with_name("AgentLightsMenuBar.app") / "Contents" / "MacOS" / self.executable_name
        reached = os.path.realpath(legacy_executable)
        if reached == bundle or reached.startswith(bundle + os.sep):
            return f"{legacy_executable} resolves into {bundle}"
        return None

    def _ensure_app_bundle(self) -> Path | None:
        app_path = self.repo_root / self.bundle_relative_path
        executable_path = app_path / "Contents" / "MacOS" / self.executable_name
        problem = self._bundle_alias_problem(app_path)
        if problem:
            logger.info("Agent Lights launch skipped: %s; remove the alias.", problem)
            return None
        binary_path = self._built_binary_path()
        if binary_path is None:
            return app_path if executable_path.exists() else None

        try:
            executable_path.parent.mkdir(parents=True, exist_ok=True)
            # Write a new file and rename it into place: never writes through a
            # hard link into another bundle, and never leaves a half-written
            # executable behind.
            staged_path = executable_path.with_name(f".{self.executable_name}.{os.getpid()}.tmp")
            staged_path.write_bytes(binary_path.read_bytes())
            staged_path.chmod(0o755)
            os.replace(staged_path, executable_path)
            plist_path = app_path / "Contents" / "Info.plist"
            plist_path.write_text(
                """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
  <key>CFBundleExecutable</key>
  <string>AgentLightsMenuBar</string>
  <key>CFBundleIdentifier</key>
  <string>com.savant.hermes-agent-lights</string>
  <key>CFBundleName</key>
  <string>Hermes Agent Lights</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSAppleEventsUsageDescription</key>
  <string>Hermes Agent Lights uses Terminal automation to focus the Terminal tab for the selected Hermes instance.</string>
</dict>
</plist>
""",
                encoding="utf-8",
            )
            signing = subprocess.run(
                ["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(app_path)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            if signing.returncode != 0:
                logger.debug("Failed to ad-hoc sign Agent Lights menu bar app")
                return None
        except Exception as exc:
            logger.debug("Failed to prepare Agent Lights menu bar app bundle: %s", exc)
            return None
        return app_path


@dataclass(frozen=True)
class SlotStatusFileBackend:
    """Write per-agent slot lifecycle status for local renderers.

    This is intentionally separate from physical light mode. Matt may disable
    LEDs while still wanting a menu bar surface to know which Hermes lane needs
    attention.
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

    _CLAIM_MUTEX_TIMEOUT_S = 2.0

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


def _state_path() -> Path:
    return Path(get_hermes_home()) / _STATE_FILE


def load_light_cue_mode(default: LightCueMode = LightCueMode.DEFAULT) -> LightCueMode:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as exc:
        logger.debug("Failed to read light cue state: %s", exc)
        return default
    if not isinstance(data, dict):
        return default
    return LightCueMode.from_value(data.get("mode"))


def save_light_cue_mode(mode: LightCueMode | str) -> LightCueMode:
    mode = LightCueMode.from_value(mode)
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"mode": mode.value}, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return mode


class LightCueService:
    def __init__(
        self,
        backend: LightCueBackend | None = None,
        *,
        mode: LightCueMode | str | None = None,
        slot_status_backend: SlotStatusBackend | None = None,
        slot_status_backend_factory: Callable[[], SlotStatusBackend | None] | None = None,
        menu_bar_launcher: MenuBarLauncher | None = None,
    ):
        self.backend = backend or NullLightCueBackend()
        self.mode = LightCueMode.from_value(mode) if mode is not None else load_light_cue_mode()
        self.slot_status_backend = slot_status_backend
        self.slot_status_backend_factory = slot_status_backend_factory
        self.menu_bar_launcher = menu_bar_launcher
        # The notification poller retries slot allocation while prompt/lifecycle
        # code emits concurrently. Keep allocation and status writes linearized
        # so the newest lifecycle state wins.
        self._slot_status_lock = threading.RLock()
        self._last_slot_event: LightCueEvent | None = None

    def _ensure_slot_status_backend(self) -> SlotStatusBackend | None:
        if self.slot_status_backend is not None:
            return self.slot_status_backend
        if self.slot_status_backend_factory is None:
            return None
        try:
            self.slot_status_backend = self.slot_status_backend_factory()
        except Exception as exc:
            logger.debug("Slot status backend retry failed: %s", exc)
            self.slot_status_backend = None
        return self.slot_status_backend

    def set_mode(self, mode: LightCueMode | str, *, persist: bool = True) -> LightCueMode:
        self.mode = LightCueMode.from_value(mode)
        if persist:
            save_light_cue_mode(self.mode)
        return self.mode

    def _emit_slot_status_locked(self, event: LightCueEvent, *, launch_menu_bar: bool) -> bool:
        """Write one lifecycle state while ``_slot_status_lock`` is held."""
        slot_status_backend = self._ensure_slot_status_backend()
        if slot_status_backend is None:
            return False
        if launch_menu_bar:
            if self.menu_bar_launcher is None:
                self.menu_bar_launcher = AgentLightsMenuBarLauncher()
            try:
                self.menu_bar_launcher.ensure_running()
            except Exception as exc:
                logger.debug("Agent Lights menu bar launch failed during slot update: %s", exc)
        try:
            return bool(slot_status_backend.emit_event(event))
        except Exception as exc:
            logger.debug("Slot status backend failed while emitting %s: %s", event.value, exc)
            return False

    def mark_slot_online(self) -> bool:
        """Register a passive session without overwriting a newer lifecycle event."""
        with self._slot_status_lock:
            if self._last_slot_event is None:
                self._last_slot_event = LightCueEvent.IDLE
            return self._emit_slot_status_locked(self._last_slot_event, launch_menu_bar=True)

    def retry_slot_registration(self) -> bool:
        """Claim a freed slot and restore the latest deferred lifecycle state."""
        with self._slot_status_lock:
            if self.slot_status_backend is not None:
                return False
            event = self._last_slot_event or LightCueEvent.IDLE
            return self._emit_slot_status_locked(event, launch_menu_bar=True)

    def action_for(self, event: LightCueEvent | str) -> LightCueAction | None:
        event = LightCueEvent.from_value(event)
        if self.mode is LightCueMode.NO_LIGHT:
            return None

        brightness = {
            LightCueMode.DEFAULT: 100,
            LightCueMode.NIGHT: 10,
            LightCueMode.DIM_DEFAULT: 35,
        }.get(self.mode, 100)

        if event is LightCueEvent.WORKING:
            return LightCueAction(
                cue="night_working" if self.mode is LightCueMode.NIGHT else "busy",
                brightness=brightness,
            )
        if event is LightCueEvent.HUMAN_INTERVENTION:
            if self.mode is LightCueMode.NIGHT:
                return LightCueAction(cue="night_intervention", brightness=brightness, flashing=True)
            return LightCueAction(cue="intervention", brightness=brightness, flashing=True)
        if event is LightCueEvent.FINAL_ANSWER:
            return LightCueAction(cue="final", brightness=brightness)
        if event is LightCueEvent.ERROR:
            return LightCueAction(cue="error", brightness=brightness, flashing=True)
        if event is LightCueEvent.IDLE:
            return LightCueAction(cue="idle", brightness=brightness)
        return None

    def emit(self, event: LightCueEvent | str) -> bool:
        # Reload the sticky profile-level mode before every cue so long-lived
        # terminal and gateway processes observe menu changes made elsewhere.
        event = LightCueEvent.from_value(event)
        with self._slot_status_lock:
            self._last_slot_event = event
            self._emit_slot_status_locked(event, launch_menu_bar=False)
        self.mode = load_light_cue_mode(self.mode)
        action = self.action_for(event)
        if action is None:
            return False
        try:
            return bool(self.backend.emit(action))
        except Exception as exc:
            logger.debug("Light cue backend failed: %s", exc)
            return False

    def update_slot_context(
        self,
        *,
        session_id: str | None = None,
        current_context: str | None = None,
        chat_summary: str | None = None,
        tab_name: str | None = None,
        provider: str | None = None,
        model_name: str | None = None,
    ) -> bool:
        """Best-effort Agent Lights context sidecar update for normal Hermes slots."""
        backend = self._ensure_slot_status_backend()
        emit_context = getattr(backend, "emit_context", None)
        if not callable(emit_context):
            return False
        try:
            return bool(emit_context(
                session_id=session_id,
                current_context=current_context,
                chat_summary=chat_summary,
                tab_name=tab_name,
                provider=provider,
                model_name=model_name,
            ))
        except Exception as exc:
            logger.debug("Slot context sidecar update failed: %s", exc)
            return False


def build_light_cue_service_from_config(config: dict[str, Any] | None = None, *, auto_assign_slot: bool = False) -> LightCueService:
    """Build the profile-wide light cue service from Hermes config.

    Prefer the platform-neutral ``light_cues.wiz`` namespace.  For existing
    local installs, keep accepting Telegram's historical ``notification_light``
    stanza as a compatibility source, but still route it through the core WiZ
    backend rather than platform adapter code.
    """
    config = config or {}
    configured_model = None
    if isinstance(config, dict):
        model_config = config.get("model")
        if isinstance(model_config, dict):
            configured_model = model_config.get("default")
        elif isinstance(model_config, str):
            configured_model = model_config
    configured_model = configured_model.strip() if isinstance(configured_model, str) and configured_model.strip() else None
    def slot_status_backend_factory() -> SlotStatusFileBackend | None:
        return SlotStatusFileBackend.from_env(auto_assign=auto_assign_slot, model_name=configured_model)

    slot_status_backend = slot_status_backend_factory()
    menu_bar_launcher = AgentLightsMenuBarLauncher() if slot_status_backend is not None else None
    mode = LightCueMode.from_value((config.get("light_cues") or {}).get("mode")) if isinstance(config, dict) else LightCueMode.DEFAULT
    wiz_cfg: Any = None
    try:
        light_cues = config.get("light_cues") if isinstance(config, dict) else None
        if isinstance(light_cues, dict):
            wiz_cfg = light_cues.get("wiz")
    except Exception:
        wiz_cfg = None

    if wiz_cfg is None:
        try:
            platforms = config.get("platforms") if isinstance(config, dict) else None
            telegram = (platforms or {}).get("telegram") if isinstance(platforms, dict) else None
            extra = getattr(telegram, "extra", None) if telegram is not None else None
            if extra is None and isinstance(telegram, dict):
                extra = telegram.get("extra") or telegram
            if isinstance(extra, dict):
                wiz_cfg = extra.get("notification_light") or extra.get("wiz_notification_light")
        except Exception:
            wiz_cfg = None

    if wiz_cfg:
        try:
            wiz_config = WiZLightCueConfig.from_mapping(wiz_cfg)
            return LightCueService(
                backend=WiZLightCueBackend(wiz_config),
                mode=mode,
                slot_status_backend=slot_status_backend,
                slot_status_backend_factory=slot_status_backend_factory if auto_assign_slot else None,
                menu_bar_launcher=menu_bar_launcher,
            )
        except Exception as exc:
            logger.debug("Failed to build WiZ light cue backend from config: %s", exc)
    return LightCueService(
        mode=mode,
        slot_status_backend=slot_status_backend,
        slot_status_backend_factory=slot_status_backend_factory if auto_assign_slot else None,
        menu_bar_launcher=menu_bar_launcher,
    )
