from __future__ import annotations

import json
from pathlib import Path

from agent.light_cues import (
    LightCueEvent,
    LightCueMode,
    LightCueService,
    NullLightCueBackend,
    SlotStatusFileBackend,
    WiZLightCueBackend,
    build_light_cue_service_from_config,
    load_light_cue_mode,
    save_light_cue_mode,
)


class RecordingBackend(NullLightCueBackend):
    def __init__(self):
        self.actions = []

    def emit(self, action):
        self.actions.append(action)
        return True


def test_light_cue_event_mapping_for_all_modes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    backend = RecordingBackend()
    service = LightCueService(backend=backend)

    expected = {
        LightCueMode.DEFAULT: [
            ("working", "busy", 100, False),
            ("human_intervention", "intervention", 100, True),
            ("final_answer", "final", 100, False),
        ],
        LightCueMode.NIGHT: [
            ("working", "night_working", 10, False),
            ("human_intervention", "night_intervention", 10, True),
            ("final_answer", "final", 10, False),
        ],
        LightCueMode.DIM_DEFAULT: [
            ("working", "busy", 35, False),
            ("human_intervention", "intervention", 35, True),
            ("final_answer", "final", 35, False),
        ],
    }

    for mode, rows in expected.items():
        service.set_mode(mode)
        for event_name, cue_name, brightness, flashing in rows:
            backend.actions.clear()
            service.emit(LightCueEvent(event_name))
            assert len(backend.actions) == 1
            action = backend.actions[0]
            assert action.cue == cue_name
            assert action.brightness == brightness
            assert action.flashing is flashing


def test_no_light_mode_suppresses_cue_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    backend = RecordingBackend()
    service = LightCueService(backend=backend)

    service.set_mode(LightCueMode.NO_LIGHT)
    for event in LightCueEvent:
        assert service.emit(event) is False
    assert backend.actions == []


def test_default_prompt_waiting_cue_preserves_full_brightness_alarm_scene(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.wiz_light import WiZNotificationLightConfig

    seen = []

    def fake_setter(config, mode):
        seen.append((config, mode))
        return True

    config = WiZNotificationLightConfig.from_mapping({
        "enabled": True,
        "hosts": ["192.0.2.10"],
        "busy_mode": "scene",
        "busy_scene_id": 11,
        "busy_dimming": 100,
    })
    service = LightCueService(
        backend=WiZLightCueBackend(config, setter=fake_setter),
        mode=LightCueMode.DEFAULT,
    )

    assert service.emit(LightCueEvent.WORKING) is True

    cue_config, cue_mode = seen[-1]
    assert cue_mode == "busy"
    assert cue_config.busy_mode == "scene"
    assert cue_config.busy_scene_id == 11
    assert cue_config.busy_dimming == 100


def test_default_human_intervention_cue_uses_busy_flashing_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.wiz_light import WiZNotificationLightConfig

    seen = []

    def fake_setter(config, mode):
        seen.append((config, mode))
        return True

    config = WiZNotificationLightConfig.from_mapping({
        "enabled": True,
        "hosts": ["192.0.2.10"],
        "busy_mode": "scene",
        "busy_scene_id": 11,
        "busy_dimming": 100,
        "ready_rgb": [255, 0, 0],
    })
    service = LightCueService(
        backend=WiZLightCueBackend(config, setter=fake_setter),
        mode=LightCueMode.DEFAULT,
    )

    assert service.emit(LightCueEvent.HUMAN_INTERVENTION) is True

    cue_config, cue_mode = seen[-1]
    assert cue_mode == "busy"
    assert cue_config.busy_mode == "scene"
    assert cue_config.busy_scene_id == 11
    assert cue_config.busy_dimming == 100


def test_dim_default_human_intervention_cue_uses_dimmed_busy_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.wiz_light import WiZNotificationLightConfig

    seen = []

    def fake_setter(config, mode):
        seen.append((config, mode))
        return True

    config = WiZNotificationLightConfig.from_mapping({
        "enabled": True,
        "hosts": ["192.0.2.10"],
        "busy_mode": "scene",
        "busy_scene_id": 11,
        "busy_dimming": 100,
    })
    service = LightCueService(
        backend=WiZLightCueBackend(config, setter=fake_setter),
        mode=LightCueMode.DIM_DEFAULT,
    )

    assert service.emit(LightCueEvent.HUMAN_INTERVENTION) is True

    cue_config, cue_mode = seen[-1]
    assert cue_mode == "busy"
    assert cue_config.busy_mode == "scene"
    assert cue_config.busy_scene_id == 11
    assert cue_config.busy_dimming == 35


def test_night_prompt_waiting_cue_uses_low_brightness_blue_not_alarm_scene(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.wiz_light import WiZNotificationLightConfig

    seen = []

    def fake_setter(config, mode):
        seen.append((config, mode))
        return True

    config = WiZNotificationLightConfig.from_mapping({
        "enabled": True,
        "hosts": ["192.0.2.10"],
        "busy_mode": "scene",
        "busy_scene_id": 11,
        "busy_dimming": 100,
        "busy_rgb": [255, 255, 255],
    })
    service = LightCueService(
        backend=WiZLightCueBackend(config, setter=fake_setter),
        mode=LightCueMode.NIGHT,
    )

    assert service.emit(LightCueEvent.WORKING) is True

    cue_config, cue_mode = seen[-1]
    assert cue_mode == "busy"
    assert cue_config.busy_mode == "rgb"
    assert cue_config.busy_dimming == 10
    assert cue_config.busy_rgb == (0, 64, 255)


def test_night_human_intervention_cue_uses_low_brightness_blue_not_alarm_scene(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.wiz_light import WiZNotificationLightConfig

    seen = []

    def fake_setter(config, mode):
        seen.append((config, mode))
        return True

    config = WiZNotificationLightConfig.from_mapping({
        "enabled": True,
        "hosts": ["192.0.2.10"],
        "busy_mode": "scene",
        "busy_scene_id": 35,
        "busy_dimming": 100,
        "busy_rgb": [255, 255, 255],
    })
    service = LightCueService(
        backend=WiZLightCueBackend(config, setter=fake_setter),
        mode=LightCueMode.NIGHT,
    )

    assert service.emit(LightCueEvent.HUMAN_INTERVENTION) is True

    cue_config, cue_mode = seen[-1]
    assert cue_mode == "busy"
    assert cue_config.busy_mode == "rgb"
    assert cue_config.busy_scene_id == 35
    assert cue_config.busy_dimming == 10
    assert cue_config.busy_rgb == (0, 64, 255)


def test_night_final_reply_cue_uses_low_brightness_red(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.wiz_light import WiZNotificationLightConfig

    seen = []

    def fake_setter(config, mode):
        seen.append((config, mode))
        return True

    config = WiZNotificationLightConfig.from_mapping({
        "enabled": True,
        "hosts": ["192.0.2.10"],
        "ready_rgb": [255, 0, 0],
        "ready_dimming": 100,
    })
    service = LightCueService(
        backend=WiZLightCueBackend(config, setter=fake_setter),
        mode=LightCueMode.NIGHT,
    )

    assert service.emit(LightCueEvent.FINAL_ANSWER) is True

    cue_config, cue_mode = seen[-1]
    assert cue_mode == "ready"
    assert cue_config.ready_rgb == (255, 0, 0)
    assert cue_config.ready_dimming == 10


def test_light_cue_mode_persists_stickily_in_profile_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    save_light_cue_mode(LightCueMode.NIGHT)

    assert load_light_cue_mode() == LightCueMode.NIGHT
    state_path = Path(tmp_path) / "light_cues.json"
    assert json.loads(state_path.read_text())["mode"] == "night"


def test_load_light_cue_mode_ignores_non_object_json(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    state = tmp_path / "light_cues.json"
    state.write_text("[]", encoding="utf-8")

    assert load_light_cue_mode(LightCueMode.NIGHT) is LightCueMode.NIGHT


def test_light_cue_service_reloads_sticky_mode_before_emit(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    backend = RecordingBackend()
    service = LightCueService(backend=backend, mode=LightCueMode.DEFAULT)

    save_light_cue_mode(LightCueMode.NO_LIGHT)

    assert service.emit(LightCueEvent.WORKING) is False
    assert backend.actions == []
    assert service.mode is LightCueMode.NO_LIGHT


def test_slot_status_file_backend_writes_per_slot_status_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "2")

    service = LightCueService(backend=RecordingBackend(), slot_status_backend=SlotStatusFileBackend.from_env())

    assert service.emit(LightCueEvent.WORKING) is True

    status_path = tmp_path / "agent-lights" / "slots" / "2.json"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["slot"] == 2
    assert payload["event"] == "working"
    assert payload["state"] == "working"
    assert payload["pid"] > 0
    assert payload["updated_at"]
    assert not status_path.with_suffix(".json.tmp").exists()


def test_slot_status_file_backend_ignores_missing_or_invalid_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    assert SlotStatusFileBackend.from_env() is None

    monkeypatch.setenv("HERMES_SLOT", "5")
    assert SlotStatusFileBackend.from_env() is None

    monkeypatch.setenv("HERMES_SLOT", "nope")
    assert SlotStatusFileBackend.from_env() is None
    assert not (tmp_path / "agent-lights").exists()


def test_slot_status_file_backend_updates_even_when_physical_light_mode_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "3")
    save_light_cue_mode(LightCueMode.NO_LIGHT)
    service = LightCueService(backend=RecordingBackend(), slot_status_backend=SlotStatusFileBackend.from_env())

    assert service.emit(LightCueEvent.FINAL_ANSWER) is False

    status_path = tmp_path / "agent-lights" / "slots" / "3.json"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["state"] == "final_answer"

    service.emit(LightCueEvent.WORKING)
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["state"] == "working"


def test_config_builder_enables_slot_status_backend_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "4")

    service = build_light_cue_service_from_config({})

    assert service.emit(LightCueEvent.IDLE) is False
    payload = json.loads((tmp_path / "agent-lights" / "slots" / "4.json").read_text(encoding="utf-8"))
    assert payload["slot"] == 4
    assert payload["state"] == "idle"


def test_config_builder_reuses_telegram_wiz_notification_light(monkeypatch):
    seen = {}

    class FakeWiZConfig:
        @classmethod
        def from_mapping(cls, data):
            seen["data"] = data
            return "wiz-config"

    monkeypatch.setattr("agent.light_cues.WiZLightCueConfig", FakeWiZConfig)
    service = build_light_cue_service_from_config({
        "platforms": {
            "telegram": {
                "notification_light": {"enabled": True, "hosts": ["192.0.2.1"]}
            }
        }
    })

    assert isinstance(service, LightCueService)
    assert seen["data"] == {"enabled": True, "hosts": ["192.0.2.1"]}


def test_terminal_and_telegram_use_same_core_service_instances(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from cli import HermesCLI
    from gateway.platforms.base import BasePlatformAdapter, SendResult

    cli = HermesCLI.__new__(HermesCLI)
    cli._light_cue_service = None
    assert cli._get_light_cue_service().__class__ is LightCueService

    class StubAdapter(BasePlatformAdapter):
        async def connect(self):
            return True

        async def disconnect(self):
            return None

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            return SendResult(success=True)

        async def get_chat_info(self, chat_id):
            return {}

    adapter = StubAdapter.__new__(StubAdapter)
    adapter.config = None
    adapter.platform = None
    adapter._light_cue_service = None
    assert adapter._get_light_cue_service().__class__ is LightCueService


def test_terminal_chat_emits_working_and_final_light_cues(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    backend = RecordingBackend()
    cli._light_cue_service = LightCueService(backend=backend)
    cli.config = {}
    cli.conversation_history = []
    cli._last_turn_interrupted = False
    cli._ensure_runtime_credentials = lambda: True
    cli._resolve_turn_agent_config = lambda message: {"signature": "same", "model": None, "runtime": {}, "request_overrides": None}
    cli._active_agent_route_signature = "same"
    cli.agent = type("Agent", (), {
        "run_conversation": lambda self, **kwargs: {
            "final_response": "done",
            "messages": [{"role": "assistant", "content": "done"}],
            "completed": True,
        },
        "session_id": "s1",
        "max_iterations": 90,
    })()
    cli._init_agent = lambda **kwargs: True
    cli._reset_stream_state = lambda: None
    cli._flush_stream = lambda: None
    cli._invalidate = lambda *args, **kwargs: None
    cli._voice_tts = False
    cli._voice_mode = False
    cli.show_reasoning = False
    cli._stream_started = False
    cli._stream_box_opened = False
    cli.bell_on_complete = False
    cli.session_id = "s1"
    cli._session_db = None
    cli._voice_continuous = False
    cli.final_response_markdown = "strip"
    cli.console = type("Console", (), {"width": 80})()
    cli._scrollback_box_width = lambda *args, **kwargs: 80
    cli._pending_input = __import__("queue").Queue()
    cli._interrupt_queue = __import__("queue").Queue()

    monkeypatch.setattr("cli.ChatConsole", lambda: type("CC", (), {"print": lambda self, *a, **k: None})())
    monkeypatch.setattr("cli.Panel", lambda *a, **k: "panel")

    assert cli.chat("hello") == "done"
    assert [action.cue for action in backend.actions] == ["busy", "final"]
