"""Best-effort local WiZ bulb notification light control.

WiZ bulbs expose a simple LAN UDP API on port 38899.  This module keeps the
integration intentionally small and optional: if it is not enabled in config it
is inert, and failures are logged but never allowed to break gateway delivery.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import random
import socket
import subprocess
import time
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_WIZ_PORT = 38899


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _coerce_int(value: Any, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _split_csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [str(v).strip() for v in parsed if str(v).strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


@dataclass(frozen=True)
class WiZNotificationLightConfig:
    enabled: bool = False
    hosts: tuple[str, ...] = ()
    port: int = _WIZ_PORT
    allowed_chat_ids: tuple[str, ...] = ()
    default_kelvin: int = 5500
    default_dimming: int = 100
    ready_rgb: tuple[int, int, int] = (255, 0, 0)
    ready_dimming: int = 100
    busy_mode: str = "default"
    busy_scene_id: int | None = None
    busy_rgb: tuple[int, int, int] = (255, 128, 0)
    busy_kelvin: int = 2700
    busy_dimming: int = 100
    discover_timeout: float = 0.8

    @classmethod
    def from_mapping(cls, data: Any) -> "WiZNotificationLightConfig":
        if not isinstance(data, dict):
            data = {}

        env_enabled = os.getenv("HERMES_WIZ_LIGHT_ENABLED")
        enabled = _coerce_bool(env_enabled, _coerce_bool(data.get("enabled"), False))

        hosts = _split_csv(os.getenv("HERMES_WIZ_LIGHT_HOSTS") or data.get("hosts") or data.get("host"))
        allowed_chat_ids = _split_csv(
            os.getenv("HERMES_WIZ_LIGHT_ALLOWED_CHAT_IDS")
            or data.get("allowed_chat_ids")
            or data.get("allowed_chats")
        )

        ready_rgb_value = data.get("ready_rgb") or os.getenv("HERMES_WIZ_LIGHT_READY_RGB")
        ready_rgb = (255, 0, 0)
        parts = _split_csv(ready_rgb_value)
        if len(parts) == 3:
            ready_rgb = (
                _coerce_int(parts[0], 255, minimum=0, maximum=255),
                _coerce_int(parts[1], 0, minimum=0, maximum=255),
                _coerce_int(parts[2], 0, minimum=0, maximum=255),
            )

        busy_rgb_value = os.getenv("HERMES_WIZ_LIGHT_BUSY_RGB") or data.get("busy_rgb")
        busy_rgb = (255, 128, 0)
        parts = _split_csv(busy_rgb_value)
        if len(parts) == 3:
            busy_rgb = (
                _coerce_int(parts[0], 255, minimum=0, maximum=255),
                _coerce_int(parts[1], 128, minimum=0, maximum=255),
                _coerce_int(parts[2], 0, minimum=0, maximum=255),
            )

        busy_scene_value = os.getenv("HERMES_WIZ_LIGHT_BUSY_SCENE_ID") or data.get("busy_scene_id")
        busy_scene_id = (
            _coerce_int(
                busy_scene_value,
                0,
                minimum=1,
                maximum=255,
            )
            if busy_scene_value is not None
            else None
        )
        busy_mode = str(os.getenv("HERMES_WIZ_LIGHT_BUSY_MODE") or data.get("busy_mode") or "").strip().lower()
        if busy_mode in {"color", "colour"}:
            busy_mode = "rgb"
        elif busy_mode in {"temp", "kelvin"}:
            busy_mode = "temperature"
        elif busy_mode not in {"scene", "rgb", "temperature", "default"}:
            busy_mode = "scene" if busy_scene_id is not None else "default"

        return cls(
            enabled=enabled,
            hosts=tuple(hosts),
            port=_coerce_int(os.getenv("HERMES_WIZ_LIGHT_PORT") or data.get("port"), _WIZ_PORT, minimum=1, maximum=65535),
            allowed_chat_ids=tuple(allowed_chat_ids),
            default_kelvin=_coerce_int(
                os.getenv("HERMES_WIZ_LIGHT_DEFAULT_KELVIN") or data.get("default_kelvin"),
                5500,
                minimum=2200,
                maximum=6500,
            ),
            default_dimming=_coerce_int(data.get("default_dimming"), 100, minimum=1, maximum=100),
            ready_rgb=ready_rgb,
            ready_dimming=_coerce_int(data.get("ready_dimming"), 100, minimum=1, maximum=100),
            busy_mode=busy_mode,
            busy_scene_id=busy_scene_id,
            busy_rgb=busy_rgb,
            busy_kelvin=_coerce_int(
                os.getenv("HERMES_WIZ_LIGHT_BUSY_KELVIN") or data.get("busy_kelvin"),
                2700,
                minimum=2200,
                maximum=6500,
            ),
            busy_dimming=_coerce_int(data.get("busy_dimming"), 100, minimum=1, maximum=100),
            discover_timeout=float(data.get("discover_timeout", 0.8) or 0.8),
        )

    def applies_to_chat(self, chat_id: Any) -> bool:
        if not self.enabled:
            return False
        if not self.allowed_chat_ids:
            return True
        return str(chat_id) in {str(v) for v in self.allowed_chat_ids}


def _interface_broadcast_addresses() -> set[str]:
    broadcasts = {"255.255.255.255"}
    try:
        output = subprocess.check_output(["ifconfig"], text=True, stderr=subprocess.DEVNULL, timeout=1.0)
    except Exception:
        return broadcasts

    import re

    broadcasts.update(re.findall(r"broadcast (\d+\.\d+\.\d+\.\d+)", output))
    return broadcasts


def discover_wiz_bulbs(*, port: int = _WIZ_PORT, timeout: float = 0.8) -> list[str]:
    """Return IPs that answer WiZ getPilot on the local network."""
    message = b'{"method":"getPilot"}'
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
        sock.settimeout(0.2)
        for broadcast in sorted(_interface_broadcast_addresses()):
            try:
                sock.sendto(message, (broadcast, port))
            except OSError:
                continue

        found: set[str] = set()
        deadline = time.time() + max(0.05, timeout)
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                payload = json.loads(data.decode("utf-8", "replace"))
            except Exception:
                continue
            if payload.get("method") == "getPilot" and ("result" in payload or "params" in payload):
                found.add(addr[0])
        return sorted(found)
    finally:
        sock.close()


def _send_set_pilot(host: str, params: dict[str, Any], *, port: int = _WIZ_PORT, timeout: float = 0.4) -> bool:
    payload = {
        "id": random.randint(1, 9999),
        "method": "setPilot",
        "params": params,
    }
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(data, (host, port))
        try:
            response, _ = sock.recvfrom(4096)
        except socket.timeout:
            return True  # Fire-and-forget is acceptable for notification light.
        try:
            parsed = json.loads(response.decode("utf-8", "replace"))
        except Exception:
            return True
        return bool(parsed.get("result", {}).get("success", True))
    except OSError as exc:
        logger.debug("WiZ notification light send failed for %s:%s: %s", host, port, exc)
        return False
    finally:
        sock.close()


def set_wiz_notification_light(config: WiZNotificationLightConfig, mode: str) -> bool:
    """Set configured WiZ bulb(s) to ``busy`` animation, ``default`` daylight, or ``ready`` red."""
    if not config.enabled:
        return False

    hosts = list(config.hosts) or discover_wiz_bulbs(port=config.port, timeout=config.discover_timeout)
    if not hosts:
        logger.debug("WiZ notification light enabled but no bulbs were found")
        return False

    if mode == "ready":
        r, g, b = config.ready_rgb
        params = {
            "state": True,
            "r": r,
            "g": g,
            "b": b,
            "dimming": config.ready_dimming,
        }
    elif mode == "busy":
        if config.busy_mode == "scene" and config.busy_scene_id is not None:
            params = {
                "state": True,
                "sceneId": config.busy_scene_id,
                "dimming": config.busy_dimming,
            }
        elif config.busy_mode == "rgb":
            r, g, b = config.busy_rgb
            params = {
                "state": True,
                "r": r,
                "g": g,
                "b": b,
                "dimming": config.busy_dimming,
            }
        elif config.busy_mode in {"temperature", "default"}:
            params = {
                "state": True,
                "temp": config.busy_kelvin if config.busy_mode == "temperature" else config.default_kelvin,
                "dimming": config.busy_dimming,
            }
        else:
            raise ValueError(f"unknown WiZ notification light busy mode: {config.busy_mode}")
    elif mode == "default":
        params = {
            "state": True,
            "temp": config.default_kelvin,
            "dimming": config.default_dimming,
        }
    else:
        raise ValueError(f"unknown WiZ notification light mode: {mode}")

    ok = False
    for host in hosts:
        ok = _send_set_pilot(host, params, port=config.port) or ok
    return ok
