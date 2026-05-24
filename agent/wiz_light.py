"""Core WiZ LAN backend for Hermes light cues.

WiZ devices expose a small local UDP JSON API on port 38899.  This module is
intentionally dependency-free and best-effort: unreachable lights are logged and
reported as a failed cue, but callers should never let that break Hermes message
handling.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import logging
import os
import random
import socket
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

WIZ_PORT = 38899


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


def _coerce_float(value: Any, default: float, *, minimum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
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


def _rgb(value: Any, default: tuple[int, int, int]) -> tuple[int, int, int]:
    parts = _split_csv(value)
    if len(parts) != 3:
        return default
    return (
        _coerce_int(parts[0], default[0], minimum=0, maximum=255),
        _coerce_int(parts[1], default[1], minimum=0, maximum=255),
        _coerce_int(parts[2], default[2], minimum=0, maximum=255),
    )


@dataclass(frozen=True)
class WiZCueDevice:
    name: str
    host: str
    role: str = "primary"

    @classmethod
    def from_mapping(cls, name: str, data: Any) -> "WiZCueDevice | None":
        if isinstance(data, str):
            host = data.strip()
            role = "primary"
        elif isinstance(data, dict):
            host = str(data.get("host") or data.get("ip") or "").strip()
            role = str(data.get("role") or "primary").strip().lower() or "primary"
        else:
            return None
        if not host:
            return None
        if role not in {"primary", "ambient"}:
            role = "primary"
        return cls(name=str(name), host=host, role=role)


@dataclass(frozen=True)
class WiZLightCueConfig:
    enabled: bool = False
    devices: tuple[WiZCueDevice, ...] = ()
    port: int = WIZ_PORT
    allowed_chat_ids: tuple[str, ...] = ()
    restore_previous_state: bool = True
    discover_timeout: float = 0.8
    send_timeout: float = 0.4
    default_kelvin: int = 5500
    default_dimming: int = 100
    ready_rgb: tuple[int, int, int] = (0, 255, 128)
    ready_dimming: int = 100
    error_rgb: tuple[int, int, int] = (255, 0, 0)
    busy_scene_id: int = 23
    busy_speed: int = 80
    busy_rgb: tuple[int, int, int] = (0, 64, 255)
    busy_dimming: int = 35
    ambient_busy_dimming: int = 10
    intervention_rgb: tuple[int, int, int] = (255, 96, 0)
    intervention_dimming: int = 60

    @classmethod
    def from_mapping(cls, data: Any) -> "WiZLightCueConfig":
        if not isinstance(data, dict):
            data = {}
        env_hosts = os.getenv("HERMES_WIZ_LIGHT_HOSTS")
        devices = _parse_devices(data.get("devices"), env_hosts or data.get("hosts") or data.get("host"))
        enabled = _coerce_bool(os.getenv("HERMES_WIZ_LIGHT_ENABLED"), _coerce_bool(data.get("enabled"), False))
        allowed_chat_ids = _split_csv(
            os.getenv("HERMES_WIZ_LIGHT_ALLOWED_CHAT_IDS")
            or data.get("allowed_chat_ids")
            or data.get("allowed_chats")
        )
        return cls(
            enabled=enabled,
            devices=tuple(devices),
            port=_coerce_int(os.getenv("HERMES_WIZ_LIGHT_PORT") or data.get("port"), WIZ_PORT, minimum=1, maximum=65535),
            allowed_chat_ids=tuple(allowed_chat_ids),
            restore_previous_state=_coerce_bool(data.get("restore_previous_state"), True),
            discover_timeout=_coerce_float(data.get("discover_timeout"), 0.8, minimum=0.05),
            send_timeout=_coerce_float(data.get("send_timeout"), 0.4, minimum=0.05),
            default_kelvin=_coerce_int(os.getenv("HERMES_WIZ_LIGHT_DEFAULT_KELVIN") or data.get("default_kelvin"), 5500, minimum=2200, maximum=6500),
            default_dimming=_coerce_int(data.get("default_dimming"), 100, minimum=1, maximum=100),
            ready_rgb=_rgb(data.get("ready_rgb") or os.getenv("HERMES_WIZ_LIGHT_READY_RGB"), (0, 255, 128)),
            ready_dimming=_coerce_int(data.get("ready_dimming"), 100, minimum=1, maximum=100),
            error_rgb=_rgb(data.get("error_rgb"), (255, 0, 0)),
            busy_scene_id=_coerce_int(data.get("busy_scene_id"), 23, minimum=1, maximum=255),
            busy_speed=_coerce_int(data.get("busy_speed"), 80, minimum=10, maximum=200),
            busy_rgb=_rgb(data.get("busy_rgb") or os.getenv("HERMES_WIZ_LIGHT_BUSY_RGB"), (0, 64, 255)),
            busy_dimming=_coerce_int(data.get("busy_dimming"), 35, minimum=1, maximum=100),
            ambient_busy_dimming=_coerce_int(data.get("ambient_busy_dimming"), 10, minimum=1, maximum=100),
            intervention_rgb=_rgb(data.get("intervention_rgb"), (255, 96, 0)),
            intervention_dimming=_coerce_int(data.get("intervention_dimming"), 60, minimum=1, maximum=100),
        )

    def applies_to_chat(self, chat_id: Any) -> bool:
        if not self.enabled:
            return False
        if not self.allowed_chat_ids:
            return True
        return str(chat_id) in {str(v) for v in self.allowed_chat_ids}


def _parse_devices(devices_value: Any, hosts_value: Any) -> list[WiZCueDevice]:
    devices: list[WiZCueDevice] = []
    if isinstance(devices_value, dict):
        for name, value in devices_value.items():
            device = WiZCueDevice.from_mapping(str(name), value)
            if device is not None:
                devices.append(device)
    elif isinstance(devices_value, list):
        for idx, value in enumerate(devices_value, start=1):
            name = value.get("name", f"wiz-{idx}") if isinstance(value, dict) else f"wiz-{idx}"
            device = WiZCueDevice.from_mapping(str(name), value)
            if device is not None:
                devices.append(device)
    if not devices:
        for idx, host in enumerate(_split_csv(hosts_value), start=1):
            devices.append(WiZCueDevice(name=f"wiz-{idx}", host=host, role="primary" if idx == 1 else "ambient"))
    return devices


def interface_broadcast_addresses() -> set[str]:
    broadcasts = {"255.255.255.255"}
    try:
        output = subprocess.check_output(["ifconfig"], text=True, stderr=subprocess.DEVNULL, timeout=1.0)
    except Exception:
        return broadcasts

    import re

    broadcasts.update(re.findall(r"broadcast (\d+\.\d+\.\d+\.\d+)", output))
    return broadcasts


def wiz_request(
    host: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    port: int = WIZ_PORT,
    timeout: float = 0.4,
    retries: int = 1,
) -> dict[str, Any] | None:
    payload = {"id": random.randint(1, 9999), "method": method}
    if params is not None:
        payload["params"] = params
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    attempts = max(1, retries)
    for attempt in range(attempts):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
            sock.sendto(data, (host, port))
            response, _ = sock.recvfrom(8192)
            parsed = json.loads(response.decode("utf-8", "replace"))
            if isinstance(parsed, dict):
                return parsed
        except (OSError, ValueError, socket.timeout) as exc:
            if attempt == attempts - 1:
                logger.debug("WiZ request failed for %s:%s %s: %s", host, port, method, exc)
        finally:
            sock.close()
    return None


def _send_set_pilot(host: str, params: dict[str, Any], *, port: int = WIZ_PORT, timeout: float = 0.4) -> bool:
    payload = {"id": random.randint(1, 9999), "method": "setPilot", "params": params}
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(data, (host, port))
        try:
            response, _ = sock.recvfrom(8192)
        except socket.timeout:
            # WiZ UDP can drop responses even when the light accepts the command.
            return True
        try:
            parsed = json.loads(response.decode("utf-8", "replace"))
        except Exception:
            return True
        return bool(parsed.get("result", {}).get("success", True))
    except OSError as exc:
        logger.debug("WiZ setPilot failed for %s:%s: %s", host, port, exc)
        return False
    finally:
        sock.close()


def discover_wiz_devices(*, port: int = WIZ_PORT, timeout: float = 0.8) -> list[str]:
    message = b'{"method":"getPilot","params":{}}'
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
        sock.settimeout(0.2)
        for broadcast in sorted(interface_broadcast_addresses()):
            try:
                sock.sendto(message, (broadcast, port))
            except OSError:
                continue

        found: set[str] = set()
        deadline = time.time() + max(0.05, timeout)
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(8192)
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


def dump_wiz_device(host: str, *, port: int = WIZ_PORT, timeout: float = 1.0) -> dict[str, Any]:
    return {
        "host": host,
        "getPilot": wiz_request(host, "getPilot", port=port, timeout=timeout, retries=2),
        "getSystemConfig": wiz_request(host, "getSystemConfig", port=port, timeout=timeout, retries=2),
        "getModelConfig": wiz_request(host, "getModelConfig", port=port, timeout=timeout, retries=2),
    }


def _pilot_result(response: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    result = response.get("result") or response.get("params")
    return result if isinstance(result, dict) else None


def _snapshot_params(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    data = _pilot_result(snapshot)
    if not data:
        return None
    keys = ("state", "sceneId", "speed", "dimming", "r", "g", "b", "w", "c", "temp")
    return {key: data[key] for key in keys if key in data}


class WiZLightCueBackend:
    """Hermes light-cue backend for one or more WiZ LAN devices."""

    def __init__(self, config: WiZLightCueConfig, setter: Any | None = None):
        self.config = config
        self._setter = setter
        self._snapshots: dict[str, dict[str, Any]] = {}

    def emit(self, action: Any) -> bool:
        if self._setter is not None or not isinstance(self.config, WiZLightCueConfig):
            return self._emit_legacy_notification_action(action)

        config = self.config
        if not config.enabled:
            return False
        devices = list(config.devices)
        if not devices:
            hosts = discover_wiz_devices(port=config.port, timeout=config.discover_timeout)
            devices = [WiZCueDevice(name=f"wiz-{idx}", host=host, role="primary" if idx == 1 else "ambient") for idx, host in enumerate(hosts, start=1)]
        if not devices:
            logger.debug("WiZ light cues enabled but no devices are configured or discovered")
            return False

        if getattr(action, "cue", "") == "idle" and config.restore_previous_state:
            return self._restore(devices)

        ok = False
        for device in devices:
            try:
                if config.restore_previous_state and device.host not in self._snapshots:
                    snapshot = wiz_request(device.host, "getPilot", port=config.port, timeout=config.send_timeout, retries=1)
                    if snapshot is not None:
                        self._snapshots[device.host] = snapshot
                params = _params_for_action(config, device.role, action)
                ok = _send_set_pilot(device.host, params, port=config.port, timeout=config.send_timeout) or ok
            except Exception as exc:
                logger.debug("WiZ light cue failed for %s (%s): %s", device.name, device.host, exc)
        return ok

    def _emit_legacy_notification_action(self, action: Any) -> bool:
        """Compatibility adapter for the historical gateway.wiz_light config."""
        try:
            from gateway.wiz_light import set_wiz_notification_light
        except Exception:
            return False

        setter = self._setter or set_wiz_notification_light
        config = self.config
        dimming = max(1, min(100, int(getattr(action, "brightness", 100))))
        cue = str(getattr(action, "cue", ""))
        if cue == "night_working":
            config = replace(config, busy_mode="rgb", busy_rgb=(0, 64, 255), busy_dimming=min(dimming, 10))
            mode = "busy"
        elif cue == "night_intervention":
            config = replace(config, busy_mode="rgb", busy_rgb=(0, 64, 255), busy_dimming=min(dimming, 10))
            mode = "busy"
        elif cue == "busy":
            config = replace(config, busy_dimming=dimming)
            mode = "busy"
        elif cue == "intervention":
            config = replace(config, busy_dimming=dimming)
            mode = "busy"
        elif cue in {"final", "error"}:
            config = replace(config, ready_dimming=dimming)
            mode = "ready"
        else:
            config = replace(config, default_dimming=dimming)
            mode = "default"
        return bool(setter(config, mode))

    def _restore(self, devices: list[WiZCueDevice]) -> bool:
        ok = False
        for device in devices:
            params = _snapshot_params(self._snapshots.get(device.host))
            if not params:
                continue
            try:
                ok = _send_set_pilot(device.host, params, port=self.config.port, timeout=self.config.send_timeout) or ok
            except Exception as exc:
                logger.debug("WiZ restore failed for %s (%s): %s", device.name, device.host, exc)
        return ok


def _scale(value: int, brightness: int) -> int:
    return max(1, min(100, round(value * max(1, min(100, int(brightness))) / 100)))


def _params_for_action(config: WiZLightCueConfig, role: str, action: Any) -> dict[str, Any]:
    cue = str(getattr(action, "cue", ""))
    brightness = _coerce_int(getattr(action, "brightness", 100), 100, minimum=1, maximum=100)
    night = cue.startswith("night_")

    if cue in {"busy", "night_working"}:
        if role == "ambient" or night:
            r, g, b = config.busy_rgb
            dimming = 10 if night else config.ambient_busy_dimming
            return {"state": True, "r": r, "g": g, "b": b, "dimming": _scale(dimming, brightness)}
        return {"state": True, "sceneId": config.busy_scene_id, "dimming": _scale(config.busy_dimming, brightness), "speed": config.busy_speed}

    if cue in {"intervention", "night_intervention"}:
        if night:
            r, g, b = config.busy_rgb
            return {"state": True, "r": r, "g": g, "b": b, "dimming": _scale(10, brightness)}
        r, g, b = config.intervention_rgb
        dimming = config.intervention_dimming if role == "primary" else min(config.intervention_dimming, 35)
        return {"state": True, "r": r, "g": g, "b": b, "dimming": _scale(dimming, brightness)}

    if cue == "error":
        r, g, b = config.error_rgb
        return {"state": True, "r": r, "g": g, "b": b, "dimming": _scale(45 if role == "primary" else 25, brightness)}

    if cue == "final":
        r, g, b = config.ready_rgb
        return {"state": True, "r": r, "g": g, "b": b, "dimming": _scale(config.ready_dimming, brightness)}

    return {"state": True, "temp": config.default_kelvin, "dimming": _scale(config.default_dimming, brightness)}
