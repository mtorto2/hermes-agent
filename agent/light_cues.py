"""Shared light cue service for Hermes notification/busy-light surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import logging
from pathlib import Path
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


class NullLightCueBackend:
    def emit(self, action: LightCueAction) -> bool:
        return False


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
    def __init__(self, backend: LightCueBackend | None = None, *, mode: LightCueMode | str | None = None):
        self.backend = backend or NullLightCueBackend()
        self.mode = LightCueMode.from_value(mode) if mode is not None else load_light_cue_mode()

    def set_mode(self, mode: LightCueMode | str, *, persist: bool = True) -> LightCueMode:
        self.mode = LightCueMode.from_value(mode)
        if persist:
            save_light_cue_mode(self.mode)
        return self.mode

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
        self.mode = load_light_cue_mode(self.mode)
        action = self.action_for(event)
        if action is None:
            return False
        try:
            return bool(self.backend.emit(action))
        except Exception as exc:
            logger.debug("Light cue backend failed: %s", exc)
            return False


def build_light_cue_service_from_config(config: dict[str, Any] | None = None) -> LightCueService:
    """Build the profile-wide light cue service from Hermes config.

    Prefer the platform-neutral ``light_cues.wiz`` namespace.  For existing
    local installs, keep accepting Telegram's historical ``notification_light``
    stanza as a compatibility source, but still route it through the core WiZ
    backend rather than platform adapter code.
    """
    config = config or {}
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
            return LightCueService(backend=WiZLightCueBackend(wiz_config), mode=mode)
        except Exception as exc:
            logger.debug("Failed to build WiZ light cue backend from config: %s", exc)
    return LightCueService(mode=mode)
