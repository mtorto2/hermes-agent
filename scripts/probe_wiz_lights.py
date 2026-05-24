#!/usr/bin/env python3
"""Discover, dump, and safely test WiZ LAN light devices for Hermes cues.

Examples:
  python scripts/probe_wiz_lights.py --discover
  python scripts/probe_wiz_lights.py --hosts 192.168.1.50,192.168.1.51 --dump
  python scripts/probe_wiz_lights.py --hosts 192.168.1.50 --test-cue
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.wiz_light import (  # noqa: E402
    WIZ_PORT,
    _send_set_pilot,
    discover_wiz_devices,
    dump_wiz_device,
    wiz_request,
)


def _split_hosts(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _pilot_params(response):
    if not isinstance(response, dict):
        return None
    result = response.get("result") or response.get("params")
    if not isinstance(result, dict):
        return None
    keys = ("state", "sceneId", "speed", "dimming", "r", "g", "b", "w", "c", "temp")
    return {key: result[key] for key in keys if key in result}


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe local WiZ lights over UDP 38899.")
    parser.add_argument("--hosts", help="Comma-separated device IPs. If omitted with --discover, discovered IPs are used.")
    parser.add_argument("--port", type=int, default=WIZ_PORT)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--discover", action="store_true", help="Broadcast getPilot and list devices that reply.")
    parser.add_argument("--dump", action="store_true", help="Dump getPilot/getSystemConfig/getModelConfig JSON for each host.")
    parser.add_argument("--test-cue", action="store_true", help="Send a safe low-brightness blue cue, then restore prior getPilot state.")
    parser.add_argument("--hold", type=float, default=1.5, help="Seconds to hold --test-cue before restore.")
    args = parser.parse_args()

    hosts = _split_hosts(args.hosts)
    if args.discover or not hosts:
        discovered = discover_wiz_devices(port=args.port, timeout=args.timeout)
        print(json.dumps({"discovered": discovered}, indent=2, sort_keys=True))
        if not hosts:
            hosts = discovered

    if not hosts:
        print("No WiZ hosts provided or discovered. Pass --hosts 192.168.x.y or retry --discover on the WiZ LAN.", file=sys.stderr)
        return 2

    if args.dump or not args.test_cue:
        dumps = [dump_wiz_device(host, port=args.port, timeout=args.timeout) for host in hosts]
        print(json.dumps({"devices": dumps}, indent=2, sort_keys=True))

    if args.test_cue:
        snapshots = {}
        for host in hosts:
            snapshots[host] = wiz_request(host, "getPilot", port=args.port, timeout=args.timeout, retries=2)
            ok = _send_set_pilot(host, {"state": True, "dimming": 10, "r": 0, "g": 80, "b": 255}, port=args.port, timeout=args.timeout)
            print(json.dumps({"host": host, "test_cue_sent": ok}, sort_keys=True))
        time.sleep(max(0.1, args.hold))
        for host in hosts:
            params = _pilot_params(snapshots.get(host))
            if not params:
                print(json.dumps({"host": host, "restore": False, "reason": "no snapshot"}, sort_keys=True))
                continue
            ok = _send_set_pilot(host, params, port=args.port, timeout=args.timeout)
            print(json.dumps({"host": host, "restore": ok}, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
