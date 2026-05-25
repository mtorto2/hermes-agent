"""Shared light cue service for Hermes notification/busy-light surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Any, Protocol

from agent.wiz_light import WiZLightCueBackend, WiZLightCueConfig

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_STATE_FILE = "light_cues.json"


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


class NullLightCueBackend:
    def emit(self, action: LightCueAction) -> bool:
        return False


@dataclass(frozen=True)
class SlotStatusFileBackend:
    """Write per-agent slot lifecycle status for local renderers.

    This is intentionally separate from physical light mode. Matt may disable
    LEDs while still wanting a menu bar surface to know which Hermes lane needs
    attention.
    """

    slot: int
    directory: Path | None = None

    @classmethod
    def from_env(cls, *, auto_assign: bool = False, directory: Path | None = None) -> "SlotStatusFileBackend | None":
        raw_slot = os.environ.get("HERMES_SLOT", "").strip()
        try:
            slot = int(raw_slot)
        except ValueError:
            slot = 0
        if slot in {1, 2, 3, 4}:
            return cls(slot=slot, directory=directory)
        if not auto_assign:
            return None
        slot = cls._claim_available_slot(directory or (Path(get_hermes_home()) / "agent-lights" / "slots"))
        return cls(slot=slot, directory=directory) if slot is not None else None

    @classmethod
    def _claim_available_slot(cls, directory: Path) -> int | None:
        """Atomically claim the current process's slot or the first free slot."""
        directory.mkdir(parents=True, exist_ok=True)
        current_pid = os.getpid()
        for slot in range(1, 5):
            if cls._slot_pid_matches(directory / f"{slot}.json", current_pid) or cls._slot_pid_matches(
                cls._lock_path(directory, slot), current_pid
            ):
                return slot
        for slot in range(1, 5):
            path = directory / f"{slot}.json"
            lock_path = cls._lock_path(directory, slot)
            if not cls._slot_is_available(path, lock_path=lock_path):
                continue
            if cls._try_claim_lock(lock_path):
                return slot
        return None

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
    def _process_payload(cls) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "process_started_at": cls._process_started_at(os.getpid()),
        }

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
        try:
            return (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime) > 120
        except Exception:
            return True

    @classmethod
    def _payload_is_live(cls, payload: dict[str, Any] | None) -> bool:
        if not payload:
            return False
        pid = cls._payload_pid(payload)
        return pid is not None and cls._pid_is_running(pid) and cls._payload_process_matches(payload)

    @classmethod
    def _payload_process_matches(cls, payload: dict[str, Any]) -> bool:
        pid = cls._payload_pid(payload)
        if pid is None:
            return False
        expected_started_at = payload.get("process_started_at")
        if not isinstance(expected_started_at, str) or not expected_started_at:
            return True
        return cls._process_started_at(pid) == expected_started_at

    @staticmethod
    def _process_started_at(pid: int) -> str | None:
        try:
            completed = subprocess.run(
                ["/bin/ps", "-o", "lstart=", "-p", str(pid)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1,
            )
        except Exception:
            return None
        started_at = completed.stdout.strip()
        return started_at or None

    @staticmethod
    def _pid_is_running(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return False

    @property
    def _directory(self) -> Path:
        return self.directory or (Path(get_hermes_home()) / "agent-lights" / "slots")

    def emit_event(self, event: LightCueEvent) -> bool:
        path = self._directory / f"{self.slot}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "slot": self.slot,
            "event": event.value,
            "state": event.value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **self._process_payload(),
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return True


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
    ):
        self.backend = backend or NullLightCueBackend()
        self.mode = LightCueMode.from_value(mode) if mode is not None else load_light_cue_mode()
        self.slot_status_backend = slot_status_backend

    def set_mode(self, mode: LightCueMode | str, *, persist: bool = True) -> LightCueMode:
        self.mode = LightCueMode.from_value(mode)
        if persist:
            save_light_cue_mode(self.mode)
        return self.mode

    def mark_slot_online(self) -> bool:
        """Write an idle slot status without touching physical light backends."""
        if self.slot_status_backend is None:
            return False
        try:
            return bool(self.slot_status_backend.emit_event(LightCueEvent.IDLE))
        except Exception as exc:
            logger.debug("Slot status backend failed during startup idle mark: %s", exc)
            return False

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
        if self.slot_status_backend is not None:
            try:
                self.slot_status_backend.emit_event(event)
            except Exception as exc:
                logger.debug("Slot status backend failed: %s", exc)
        self.mode = load_light_cue_mode(self.mode)
        action = self.action_for(event)
        if action is None:
            return False
        try:
            return bool(self.backend.emit(action))
        except Exception as exc:
            logger.debug("Light cue backend failed: %s", exc)
            return False


def build_light_cue_service_from_config(config: dict[str, Any] | None = None, *, auto_assign_slot: bool = False) -> LightCueService:
    """Build the profile-wide light cue service from Hermes config.

    Prefer the platform-neutral ``light_cues.wiz`` namespace.  For existing
    local installs, keep accepting Telegram's historical ``notification_light``
    stanza as a compatibility source, but still route it through the core WiZ
    backend rather than platform adapter code.
    """
    config = config or {}
    slot_status_backend = SlotStatusFileBackend.from_env(auto_assign=auto_assign_slot)
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
            return LightCueService(backend=WiZLightCueBackend(wiz_config), mode=mode, slot_status_backend=slot_status_backend)
        except Exception as exc:
            logger.debug("Failed to build WiZ light cue backend from config: %s", exc)
    return LightCueService(mode=mode, slot_status_backend=slot_status_backend)
