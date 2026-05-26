from __future__ import annotations

import json
from pathlib import Path

from agent.light_cues import (
    AgentLightsMenuBarLauncher,
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
    assert payload["process_started_at"]
    assert payload["source"] == "hermes"
    assert payload["updated_at"]
    assert not status_path.with_suffix(".json.tmp").exists()


def test_slot_status_file_backend_marks_kanban_workers(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "2")
    monkeypatch.setenv("HERMES_MODEL", "openai-codex/gpt-5.5")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_ring1234")
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "voice-clipboard")
    monkeypatch.setenv("HERMES_KANBAN_TASK_TITLE", "Investigate menu rings")
    monkeypatch.setenv("HERMES_PROFILE", "matt-codex")

    service = LightCueService(backend=RecordingBackend(), slot_status_backend=SlotStatusFileBackend.from_env())

    assert service.emit(LightCueEvent.WORKING) is True
    payload = json.loads((tmp_path / "agent-lights" / "agents" / "2.json").read_text(encoding="utf-8"))
    assert payload["source"] == "kanban_worker"
    assert payload["model_name"] == "openai-codex/gpt-5.5"
    assert payload["kanban_task_id"] == "t_ring1234"
    assert payload["kanban_board"] == "voice-clipboard"
    assert payload["kanban_task_title"] == "Investigate menu rings"
    assert payload["profile"] == "matt-codex"
    assert not (tmp_path / "agent-lights" / "slots" / "2.json").exists()


def test_kanban_worker_auto_assignment_uses_separate_agent_capacity_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_agent_pool")
    slots = tmp_path / "agent-lights" / "slots"
    slots.mkdir(parents=True)
    for slot in range(1, 5):
        (slots / f"{slot}.json").write_text(
            json.dumps({"slot": slot, "pid": 100 + slot, "state": "idle", "source": "hermes"}),
            encoding="utf-8",
        )
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: 100 < pid < 105))

    backend = SlotStatusFileBackend.from_env(auto_assign=True)

    assert backend is not None
    assert backend.slot == 1
    assert backend._directory == tmp_path / "agent-lights" / "agents"
    assert (tmp_path / "agent-lights" / "agents" / "1.lock").exists()


def test_slot_status_file_backend_uses_config_model_when_env_model_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "1")
    monkeypatch.delenv("HERMES_MODEL", raising=False)
    monkeypatch.delenv("HERMES_INFERENCE_MODEL", raising=False)

    service = build_light_cue_service_from_config({"model": "anthropic/claude-sonnet-4.6", "light_cues": {"mode": "no-light"}})

    assert service.emit(LightCueEvent.WORKING) is False
    payload = json.loads((tmp_path / "agent-lights" / "slots" / "1.json").read_text(encoding="utf-8"))
    assert payload["model_name"] == "anthropic/claude-sonnet-4.6"


def test_slot_status_backend_clears_owned_slot_and_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    backend = SlotStatusFileBackend(slot=1)
    assert backend.emit_event(LightCueEvent.WORKING) is True
    lock_path = tmp_path / "agent-lights" / "slots" / "1.lock"
    lock_path.write_text((tmp_path / "agent-lights" / "slots" / "1.json").read_text(encoding="utf-8"), encoding="utf-8")

    assert backend.clear_if_owned() is True

    assert not (tmp_path / "agent-lights" / "slots" / "1.json").exists()
    assert not lock_path.exists()


def test_slot_status_backend_does_not_clear_unowned_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    backend = SlotStatusFileBackend(slot=1)
    assert backend.emit_event(LightCueEvent.WORKING) is True
    path = tmp_path / "agent-lights" / "slots" / "1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["pid"] = 999999
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert backend.clear_if_owned() is False
    assert path.exists()


def test_mark_slot_online_writes_idle_status_without_physical_light_action(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "1")
    backend = RecordingBackend()
    service = LightCueService(backend=backend, slot_status_backend=SlotStatusFileBackend.from_env())

    assert service.mark_slot_online() is True

    assert backend.actions == []
    payload = json.loads((tmp_path / "agent-lights" / "slots" / "1.json").read_text(encoding="utf-8"))
    assert payload["slot"] == 1
    assert payload["event"] == "idle"
    assert payload["state"] == "idle"
    assert payload["pid"] > 0
    assert payload["process_started_at"]


class RecordingMenuBarLauncher:
    def __init__(self):
        self.calls = 0

    def ensure_running(self) -> bool:
        self.calls += 1
        return True


def test_mark_slot_online_attempts_menu_bar_launch_before_idle_status(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "1")
    launcher = RecordingMenuBarLauncher()
    service = LightCueService(
        backend=RecordingBackend(),
        slot_status_backend=SlotStatusFileBackend.from_env(),
        menu_bar_launcher=launcher,
    )

    assert service.mark_slot_online() is True

    assert launcher.calls == 1
    assert (tmp_path / "agent-lights" / "slots" / "1.json").exists()


def test_agent_lights_menu_bar_launcher_prepares_app_bundle_from_existing_debug_binary(tmp_path):
    binary_path = tmp_path / "apps" / "agent-lights-menu-bar" / ".build" / "debug" / "AgentLightsMenuBar"
    binary_path.parent.mkdir(parents=True)
    binary_path.write_bytes(b"debug-binary")
    launcher = AgentLightsMenuBarLauncher(repo_root=tmp_path)

    app_path = launcher._ensure_app_bundle()

    assert app_path is not None
    assert app_path == tmp_path / "apps" / "agent-lights-menu-bar" / ".build" / "AgentLightsMenuBar.app"
    executable_path = app_path / "Contents" / "MacOS" / "AgentLightsMenuBar"
    assert executable_path.read_bytes() == b"debug-binary"
    plist = (app_path / "Contents" / "Info.plist").read_text(encoding="utf-8")
    assert "LSUIElement" in plist
    assert "NSAppleEventsUsageDescription" in plist
    assert "Terminal" in plist


def test_agent_lights_menu_bar_launcher_refreshes_existing_bundle_from_debug_binary(tmp_path):
    binary_path = tmp_path / "apps" / "agent-lights-menu-bar" / ".build" / "debug" / "AgentLightsMenuBar"
    binary_path.parent.mkdir(parents=True)
    binary_path.write_bytes(b"new-binary")
    executable_path = (
        tmp_path
        / "apps"
        / "agent-lights-menu-bar"
        / ".build"
        / "AgentLightsMenuBar.app"
        / "Contents"
        / "MacOS"
        / "AgentLightsMenuBar"
    )
    executable_path.parent.mkdir(parents=True)
    executable_path.write_bytes(b"old-binary")
    launcher = AgentLightsMenuBarLauncher(repo_root=tmp_path)

    assert launcher._ensure_app_bundle() is not None

    assert executable_path.read_bytes() == b"new-binary"


def test_agent_lights_menu_bar_launcher_returns_none_without_built_binary(tmp_path):
    launcher = AgentLightsMenuBarLauncher(repo_root=tmp_path)

    assert launcher._ensure_app_bundle() is None


def test_slot_status_file_backend_ignores_missing_or_invalid_slot_without_auto_assign(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    assert SlotStatusFileBackend.from_env() is None

    monkeypatch.setenv("HERMES_SLOT", "5")
    assert SlotStatusFileBackend.from_env() is None

    monkeypatch.setenv("HERMES_SLOT", "nope")
    assert SlotStatusFileBackend.from_env() is None
    assert not (tmp_path / "agent-lights").exists()


def test_slot_status_file_backend_auto_assigns_first_available_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)

    backend = SlotStatusFileBackend.from_env(auto_assign=True)

    assert backend is not None
    assert backend.slot == 1
    assert (tmp_path / "agent-lights" / "slots" / "1.lock").exists()


def test_slot_status_file_backend_auto_assign_skips_live_lock_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    slots = tmp_path / "agent-lights" / "slots"
    slots.mkdir(parents=True)
    (slots / "1.lock").write_text(json.dumps({"pid": 111, "process_started_at": "started-111"}), encoding="utf-8")
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: pid == 111))
    monkeypatch.setattr(SlotStatusFileBackend, "_process_started_at", staticmethod(lambda pid: "started-111" if pid == 111 else "current"))

    backend = SlotStatusFileBackend.from_env(auto_assign=True)

    assert backend is not None
    assert backend.slot == 2


def test_slot_status_file_backend_auto_assign_removes_dead_lock_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    slots = tmp_path / "agent-lights" / "slots"
    slots.mkdir(parents=True)
    (slots / "1.lock").write_text(json.dumps({"pid": 111, "process_started_at": "started-111"}), encoding="utf-8")
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: False))

    backend = SlotStatusFileBackend.from_env(auto_assign=True)

    assert backend is not None
    assert backend.slot == 1


def test_slot_status_file_backend_auto_assign_reclaims_dead_recent_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    slots = tmp_path / "agent-lights" / "slots"
    slots.mkdir(parents=True)
    (slots / "1.json").write_text(
        json.dumps({"slot": 1, "pid": 111, "state": "final_answer", "source": "kanban_worker"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: False))

    backend = SlotStatusFileBackend.from_env(auto_assign=True)

    assert backend is not None
    assert backend.slot == 1


def test_slot_status_file_backend_auto_assigns_invalid_slot_to_available_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "6")
    slots = tmp_path / "agent-lights" / "slots"
    slots.mkdir(parents=True)
    (slots / "1.json").write_text(json.dumps({"slot": 1, "pid": 111, "state": "idle"}), encoding="utf-8")
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: pid == 111))

    backend = SlotStatusFileBackend.from_env(auto_assign=True)

    assert backend is not None
    assert backend.slot == 2


def test_slot_status_file_backend_auto_assign_caps_at_four_live_slots(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    slots = tmp_path / "agent-lights" / "slots"
    slots.mkdir(parents=True)
    for slot in range(1, 5):
        (slots / f"{slot}.json").write_text(json.dumps({"slot": slot, "pid": 100 + slot, "state": "idle"}), encoding="utf-8")
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: True))

    assert SlotStatusFileBackend.from_env(auto_assign=True) is None


def test_slot_status_file_backend_auto_assign_reuses_current_process_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    slots = tmp_path / "agent-lights" / "slots"
    slots.mkdir(parents=True)
    (slots / "3.json").write_text(json.dumps({"slot": 3, "pid": __import__("os").getpid(), "state": "idle"}), encoding="utf-8")

    backend = SlotStatusFileBackend.from_env(auto_assign=True)

    assert backend is not None
    assert backend.slot == 3


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

    service = build_light_cue_service_from_config({"model": {"default": "gpt-5.5", "provider": "openai-codex"}})

    assert service.emit(LightCueEvent.IDLE) is False
    payload = json.loads((tmp_path / "agent-lights" / "slots" / "4.json").read_text(encoding="utf-8"))
    assert payload["slot"] == 4
    assert payload["state"] == "idle"
    assert payload["model_name"] == "gpt-5.5"


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
