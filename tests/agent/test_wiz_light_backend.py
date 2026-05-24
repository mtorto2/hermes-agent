from __future__ import annotations

from agent.light_cues import LightCueEvent, LightCueMode, LightCueService, build_light_cue_service_from_config


def test_config_builder_prefers_core_light_cues_wiz_config(monkeypatch):
    seen = {}

    class FakeBackend:
        def __init__(self, config):
            seen["config"] = config

        def emit(self, action):
            return True

    monkeypatch.setattr("agent.light_cues.WiZLightCueBackend", FakeBackend)

    service = build_light_cue_service_from_config(
        {
            "light_cues": {
                "mode": "dim-default",
                "wiz": {
                    "enabled": True,
                    "devices": {
                        "strip": {"host": "192.0.2.10", "role": "primary"},
                        "a19": {"host": "192.0.2.11", "role": "ambient"},
                    },
                },
            },
            "platforms": {
                "telegram": {
                    "notification_light": {"enabled": True, "hosts": ["192.0.2.99"]}
                }
            },
        }
    )

    assert isinstance(service, LightCueService)
    assert service.mode is LightCueMode.DIM_DEFAULT
    assert [device.name for device in seen["config"].devices] == ["strip", "a19"]
    assert [device.host for device in seen["config"].devices] == ["192.0.2.10", "192.0.2.11"]


def test_wiz_light_backend_sends_role_specific_working_cue(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from agent.wiz_light import WiZLightCueConfig
    from agent.light_cues import WiZLightCueBackend

    sent = []

    def fake_send(host, params, *, port=38899, timeout=0.4):
        sent.append((host, params, port))
        return True

    monkeypatch.setattr("agent.wiz_light._send_set_pilot", fake_send)
    config = WiZLightCueConfig.from_mapping(
        {
            "enabled": True,
            "devices": {
                "strip": {"host": "192.0.2.10", "role": "primary"},
                "a19": {"host": "192.0.2.11", "role": "ambient"},
            },
        }
    )
    service = LightCueService(backend=WiZLightCueBackend(config), mode=LightCueMode.DEFAULT)

    assert service.emit(LightCueEvent.WORKING) is True

    assert sent == [
        ("192.0.2.10", {"state": True, "sceneId": 23, "dimming": 35, "speed": 80}, 38899),
        ("192.0.2.11", {"state": True, "r": 0, "g": 64, "b": 255, "dimming": 10}, 38899),
    ]


def test_wiz_light_backend_restores_snapshot_on_idle(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from agent.wiz_light import WiZLightCueConfig
    from agent.light_cues import WiZLightCueBackend

    requests = []
    sent = []

    def fake_request(host, method, params=None, *, port=38899, timeout=0.4, retries=1):
        requests.append((host, method))
        return {"method": method, "result": {"state": True, "r": 9, "g": 8, "b": 7, "dimming": 44}}

    def fake_send(host, params, *, port=38899, timeout=0.4):
        sent.append((host, params, port))
        return True

    monkeypatch.setattr("agent.wiz_light.wiz_request", fake_request)
    monkeypatch.setattr("agent.wiz_light._send_set_pilot", fake_send)
    config = WiZLightCueConfig.from_mapping({"enabled": True, "hosts": ["192.0.2.10"], "restore_previous_state": True})
    service = LightCueService(backend=WiZLightCueBackend(config), mode=LightCueMode.DEFAULT)

    assert service.emit(LightCueEvent.WORKING) is True
    assert service.emit(LightCueEvent.IDLE) is True

    assert requests == [("192.0.2.10", "getPilot")]
    assert sent[-1] == ("192.0.2.10", {"state": True, "r": 9, "g": 8, "b": 7, "dimming": 44}, 38899)


def test_wiz_light_backend_degrades_to_false_when_all_devices_unreachable(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from agent.wiz_light import WiZLightCueConfig
    from agent.light_cues import WiZLightCueBackend

    monkeypatch.setattr("agent.wiz_light._send_set_pilot", lambda *a, **k: False)
    config = WiZLightCueConfig.from_mapping({"enabled": True, "hosts": ["192.0.2.10", "192.0.2.11"]})
    service = LightCueService(backend=WiZLightCueBackend(config), mode=LightCueMode.DEFAULT)

    assert service.emit(LightCueEvent.FINAL_ANSWER) is False
