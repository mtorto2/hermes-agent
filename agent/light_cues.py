"""Shared light cue service for Hermes notification/busy-light surfaces."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import json
import logging
from pathlib import Path
from typing import Any, Protocol

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_STATE_FILE = "light_cues.json"


class LightCueMode(str, Enum):
    DEFAULT = "default"
    NIGHT = "night"
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
            # Preserve compatibility with the removed dim-default option by
            # folding old persisted/menu values back to day/default mode.
            "dim": cls.DEFAULT,
            "dim-default": cls.DEFAULT,
            "dimmed": cls.DEFAULT,
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

    The v1 light surface reuses Telegram's existing WiZ notification-light
    configuration so terminal and Telegram cues drive the same backend without
    introducing a second config namespace.
    """
    config = config or {}
    notification_cfg: Any = None
    try:
        platforms = config.get("platforms") if isinstance(config, dict) else None
        telegram = (platforms or {}).get("telegram") if isinstance(platforms, dict) else None
        extra = getattr(telegram, "extra", None) if telegram is not None else None
        if extra is None and isinstance(telegram, dict):
            extra = telegram.get("extra") or telegram
        if isinstance(extra, dict):
            notification_cfg = extra.get("notification_light") or extra.get("wiz_notification_light")
    except Exception:
        notification_cfg = None

    if notification_cfg:
        try:
            from gateway.wiz_light import WiZNotificationLightConfig

            wiz_config = WiZNotificationLightConfig.from_mapping(notification_cfg)
            return LightCueService(backend=WiZLightCueBackend(wiz_config))
        except Exception as exc:
            logger.debug("Failed to build WiZ light cue backend from config: %s", exc)
    return LightCueService()


class WiZLightCueBackend:
    """Adapter from abstract light cue actions to the existing WiZ LAN backend."""

    def __init__(self, config, setter=None):
        self.config = config
        self._setter = setter

    def emit(self, action: LightCueAction) -> bool:
        from gateway.wiz_light import WiZNotificationLightConfig, set_wiz_notification_light

        setter = self._setter or set_wiz_notification_light
        config = self.config
        if not isinstance(config, WiZNotificationLightConfig):
            return False
        dimming = max(1, min(100, int(action.brightness)))
        if action.cue == "night_working":
            # Night mode must never use the configured alarm/busy scene: WiZ
            # scenes can ignore dimming and produce a harsh full-power white
            # flash. Force a low-brightness blue RGB waiting glow instead.
            config = replace(
                config,
                busy_mode="rgb",
                busy_rgb=(0, 64, 255),
                busy_dimming=min(dimming, 10),
            )
            mode = "busy"
        elif action.cue == "night_intervention":
            # Human-intervention in night mode should be visible but gentle:
            # avoid the alarm scene and use the same low-blue waiting surface.
            config = replace(
                config,
                busy_mode="rgb",
                busy_rgb=(0, 64, 255),
                busy_dimming=min(dimming, 10),
            )
            mode = "busy"
        elif action.cue == "busy":
            # Day/default mode intentionally preserves the configured busy
            # scene (Matt's alarm preset) at full brightness.
            config = replace(config, busy_dimming=dimming)
            mode = "busy"
        elif action.cue == "intervention":
            # Waiting on a human answer should remain attention-grabbing, not
            # collapse to the same red ready cue as a final answer. Reuse the
            # configured busy/flashing surface, honoring the active dimming.
            config = replace(config, busy_dimming=dimming)
            mode = "busy"
        elif action.cue in {"final", "error"}:
            config = replace(config, ready_dimming=dimming)
            mode = "ready"
        else:
            config = replace(config, default_dimming=dimming)
            mode = "default"
        return setter(config, mode)
