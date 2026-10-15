from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    monkeypatch.setenv("HERMES_SESSION_ID", "20260529_123456_abcd")

    service = LightCueService(backend=RecordingBackend(), slot_status_backend=SlotStatusFileBackend.from_env())

    assert service.emit(LightCueEvent.WORKING) is True
    status_path = tmp_path / "agent-lights" / "v2" / "slots" / "2.json"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["slot"] == 2
    assert payload["event"] == "working"
    assert payload["state"] == "working"
    assert payload["pid"] > 0
    assert isinstance(payload["process_start_time"], float)
    assert payload["source"] == "hermes"
    assert payload["session_id"] == "20260529_123456_abcd"
    assert payload["updated_at"]
    assert not status_path.with_suffix(".json.tmp").exists()


def test_light_cue_service_writes_per_slot_context_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "2")

    service = LightCueService(backend=RecordingBackend(), slot_status_backend=SlotStatusFileBackend.from_env())

    assert service.update_slot_context(
        session_id="20260529_123456_abcd",
        current_context="Implement Agent Lights tab context.",
        chat_summary="Wiring the Swift menu submenu.",
        provider="openai-codex",
        model_name="gpt-5.5",
    ) is True
    context_path = tmp_path / "agent-lights" / "v2" / "context" / "2.json"
    payload = json.loads(context_path.read_text(encoding="utf-8"))
    assert payload["slot"] == 2
    assert payload["source"] == "hermes"
    assert payload["session_id"] == "20260529_123456_abcd"
    assert payload["provider"] == "openai-codex"
    assert payload["model_name"] == "gpt-5.5"
    assert payload["current_context"] == "Implement Agent Lights tab context."
    assert payload["chat_summary"] == "Wiring the Swift menu submenu."
    assert payload["updated_at"]
    assert not context_path.with_suffix(".json.tmp").exists()


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
    payload = json.loads((tmp_path / "agent-lights" / "v2" / "agents" / "2.json").read_text(encoding="utf-8"))
    assert payload["source"] == "kanban_worker"
    assert payload["model_name"] == "openai-codex/gpt-5.5"
    assert payload["kanban_task_id"] == "t_ring1234"
    assert payload["kanban_board"] == "voice-clipboard"
    assert payload["kanban_task_title"] == "Investigate menu rings"
    assert payload["profile"] == "matt-codex"
    assert not (tmp_path / "agent-lights" / "v2" / "slots" / "2.json").exists()


def test_kanban_worker_auto_assignment_uses_separate_agent_capacity_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_agent_pool")
    slots = tmp_path / "agent-lights" / "v2" / "slots"
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
    assert backend._directory == tmp_path / "agent-lights" / "v2" / "agents"
    assert (tmp_path / "agent-lights" / "v2" / "agents" / "1.lock").exists()


def test_slot_status_file_backend_uses_config_model_when_env_model_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "1")
    monkeypatch.delenv("HERMES_MODEL", raising=False)
    monkeypatch.delenv("HERMES_INFERENCE_MODEL", raising=False)

    service = build_light_cue_service_from_config({"model": "anthropic/claude-sonnet-4.6", "light_cues": {"mode": "no-light"}})

    assert service.emit(LightCueEvent.WORKING) is False
    payload = json.loads((tmp_path / "agent-lights" / "v2" / "slots" / "1.json").read_text(encoding="utf-8"))
    assert payload["model_name"] == "anthropic/claude-sonnet-4.6"


def test_slot_status_backend_clears_owned_slot_and_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    backend = SlotStatusFileBackend(slot=1)
    assert backend.emit_event(LightCueEvent.WORKING) is True
    lock_path = tmp_path / "agent-lights" / "v2" / "slots" / "1.lock"
    lock_path.write_text((tmp_path / "agent-lights" / "v2" / "slots" / "1.json").read_text(encoding="utf-8"), encoding="utf-8")

    assert backend.clear_if_owned() is True

    assert not (tmp_path / "agent-lights" / "v2" / "slots" / "1.json").exists()
    assert not lock_path.exists()


def test_kanban_worker_clear_preserves_terminal_slot_status_but_releases_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_done1234")
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "hermes-agent")
    agents = tmp_path / "agent-lights" / "v2" / "agents"
    backend = SlotStatusFileBackend(slot=1, directory=agents)
    assert backend.emit_event(LightCueEvent.FINAL_ANSWER) is True
    slot_path = agents / "1.json"
    lock_path = agents / "1.lock"
    lock_path.write_text(slot_path.read_text(encoding="utf-8"), encoding="utf-8")

    assert backend.clear_if_owned() is True

    payload = json.loads(slot_path.read_text(encoding="utf-8"))
    assert payload["source"] == "kanban_worker"
    assert payload["state"] == "final_answer"
    assert payload["kanban_task_id"] == "t_done1234"
    assert not lock_path.exists()


def test_slot_status_backend_does_not_clear_unowned_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    backend = SlotStatusFileBackend(slot=1)
    assert backend.emit_event(LightCueEvent.WORKING) is True
    path = tmp_path / "agent-lights" / "v2" / "slots" / "1.json"
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
    payload = json.loads((tmp_path / "agent-lights" / "v2" / "slots" / "1.json").read_text(encoding="utf-8"))
    assert payload["slot"] == 1
    assert payload["event"] == "idle"
    assert payload["state"] == "idle"
    assert payload["pid"] > 0
    assert isinstance(payload["process_start_time"], float)


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
    assert (tmp_path / "agent-lights" / "v2" / "slots" / "1.json").exists()


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _install_info_plist(repo_root: Path) -> None:
    """Copy the real checked-in Info.plist into a temp repo root."""
    target = repo_root / AgentLightsMenuBarLauncher.info_plist_relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_REPO_ROOT / AgentLightsMenuBarLauncher.info_plist_relative_path, target)


def test_agent_lights_menu_bar_launcher_prepares_app_bundle_from_existing_debug_binary(tmp_path):
    binary_path = tmp_path / "apps" / "agent-lights-menu-bar" / ".build" / "debug" / "AgentLightsMenuBar"
    binary_path.parent.mkdir(parents=True)
    binary_path.write_bytes(b"debug-binary")
    _install_info_plist(tmp_path)
    launcher = AgentLightsMenuBarLauncher(repo_root=tmp_path)

    app_path = launcher._ensure_app_bundle()

    assert app_path is not None
    assert app_path == tmp_path / "apps" / "agent-lights-menu-bar" / ".build" / "AgentLightsMenuBar-v2.app"
    executable_path = app_path / "Contents" / "MacOS" / "AgentLightsMenuBar"
    assert executable_path.read_bytes() == b"debug-binary"
    # The bundle carries the checked-in plist unchanged, and that plist names
    # the executable the launcher installs.
    plist_bytes = (app_path / "Contents" / "Info.plist").read_bytes()
    assert plist_bytes == (tmp_path / AgentLightsMenuBarLauncher.info_plist_relative_path).read_bytes()
    plist = plistlib.loads(plist_bytes)
    assert plist["CFBundleExecutable"] == AgentLightsMenuBarLauncher.executable_name
    assert plist["LSUIElement"] is True
    assert plist["NSAppleEventsUsageDescription"]


def test_agent_lights_menu_bar_launcher_skips_without_info_plist(tmp_path):
    binary_path = tmp_path / "apps" / "agent-lights-menu-bar" / ".build" / "debug" / "AgentLightsMenuBar"
    binary_path.parent.mkdir(parents=True)
    binary_path.write_bytes(b"debug-binary")
    launcher = AgentLightsMenuBarLauncher(repo_root=tmp_path)

    assert launcher._ensure_app_bundle() is None


def test_agent_lights_menu_bar_launcher_ad_hoc_signs_the_refreshed_bundle(tmp_path, monkeypatch):
    binary_path = tmp_path / "apps" / "agent-lights-menu-bar" / ".build" / "debug" / "AgentLightsMenuBar"
    binary_path.parent.mkdir(parents=True)
    binary_path.write_bytes(b"debug-binary")
    calls = []

    def record_run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agent.light_cues.subprocess.run", record_run)
    _install_info_plist(tmp_path)
    launcher = AgentLightsMenuBarLauncher(repo_root=tmp_path)

    app_path = launcher._ensure_app_bundle()

    assert app_path is not None
    assert calls == [(
        ["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(app_path)],
        {"check": False, "stdout": -3, "stderr": -3, "timeout": 3},
    )]


def test_agent_lights_menu_bar_launcher_refreshes_existing_bundle_from_debug_binary(tmp_path):
    binary_path = tmp_path / "apps" / "agent-lights-menu-bar" / ".build" / "debug" / "AgentLightsMenuBar"
    binary_path.parent.mkdir(parents=True)
    binary_path.write_bytes(b"new-binary")
    executable_path = (
        tmp_path
        / "apps"
        / "agent-lights-menu-bar"
        / ".build"
        / "AgentLightsMenuBar-v2.app"
        / "Contents"
        / "MacOS"
        / "AgentLightsMenuBar"
    )
    executable_path.parent.mkdir(parents=True)
    executable_path.write_bytes(b"old-binary")
    _install_info_plist(tmp_path)
    launcher = AgentLightsMenuBarLauncher(repo_root=tmp_path)

    assert launcher._ensure_app_bundle() is not None

    assert executable_path.read_bytes() == b"new-binary"


def test_agent_lights_menu_bar_launcher_uses_arch_specific_swiftpm_binary_after_clean_build(tmp_path):
    binary_path = (
        tmp_path
        / "apps"
        / "agent-lights-menu-bar"
        / ".build"
        / "arm64-apple-macosx"
        / "debug"
        / "AgentLightsMenuBar"
    )
    binary_path.parent.mkdir(parents=True)
    binary_path.write_bytes(b"arch-specific-binary")
    _install_info_plist(tmp_path)
    launcher = AgentLightsMenuBarLauncher(repo_root=tmp_path)

    app_path = launcher._ensure_app_bundle()

    assert app_path is not None
    executable_path = app_path / "Contents" / "MacOS" / "AgentLightsMenuBar"
    assert executable_path.read_bytes() == b"arch-specific-binary"


def test_agent_lights_menu_bar_launcher_prefers_newer_swiftpm_binary_candidate(tmp_path):
    legacy_binary = tmp_path / "apps" / "agent-lights-menu-bar" / ".build" / "debug" / "AgentLightsMenuBar"
    arch_binary = (
        tmp_path
        / "apps"
        / "agent-lights-menu-bar"
        / ".build"
        / "arm64-apple-macosx"
        / "debug"
        / "AgentLightsMenuBar"
    )
    legacy_binary.parent.mkdir(parents=True)
    arch_binary.parent.mkdir(parents=True)
    legacy_binary.write_bytes(b"old-legacy-binary")
    arch_binary.write_bytes(b"new-arch-binary")
    _install_info_plist(tmp_path)
    launcher = AgentLightsMenuBarLauncher(repo_root=tmp_path)

    app_path = launcher._ensure_app_bundle()

    assert app_path is not None
    assert (app_path / "Contents" / "MacOS" / "AgentLightsMenuBar").read_bytes() == b"new-arch-binary"


def test_agent_lights_menu_bar_launcher_returns_none_without_built_binary(tmp_path):
    _install_info_plist(tmp_path)
    launcher = AgentLightsMenuBarLauncher(repo_root=tmp_path)

    assert launcher._ensure_app_bundle() is None


def test_slot_status_file_backend_ignores_missing_or_invalid_slot_without_auto_assign(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    assert SlotStatusFileBackend.from_env() is None

    monkeypatch.setenv("HERMES_SLOT", "7")
    assert SlotStatusFileBackend.from_env() is None

    monkeypatch.setenv("HERMES_SLOT", "nope")
    assert SlotStatusFileBackend.from_env() is None
    assert not (tmp_path / "agent-lights").exists()


def test_slot_status_file_backend_accepts_sixth_normal_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "6")

    backend = SlotStatusFileBackend.from_env()

    assert backend is not None
    assert backend.slot == 6
    assert backend.emit_event(LightCueEvent.IDLE) is True
    assert (tmp_path / "agent-lights" / "v2" / "slots" / "6.json").exists()


def test_slot_status_file_backend_auto_assigns_first_available_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)

    backend = SlotStatusFileBackend.from_env(auto_assign=True)

    assert backend is not None
    assert backend.slot == 1
    assert (tmp_path / "agent-lights" / "v2" / "slots" / "1.lock").exists()


def test_slot_status_file_backend_profile_home_writes_to_shared_root(tmp_path, monkeypatch):
    profile_home = tmp_path / "profiles" / "business"
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    monkeypatch.setenv("HERMES_PROFILE", "business")
    monkeypatch.delenv("HERMES_SLOT", raising=False)

    backend = SlotStatusFileBackend.from_env(auto_assign=True)

    assert backend is not None
    assert backend.slot == 1
    assert (tmp_path / "agent-lights" / "v2" / "slots" / "1.lock").exists()
    assert not (profile_home / "agent-lights" / "v2" / "slots" / "1.lock").exists()
    assert backend.emit_event(LightCueEvent.IDLE) is True
    payload = json.loads((tmp_path / "agent-lights" / "v2" / "slots" / "1.json").read_text(encoding="utf-8"))
    assert payload["profile"] == "business"
    assert payload["state"] == "idle"


def test_slot_status_file_backend_auto_assign_skips_live_lock_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    slots = tmp_path / "agent-lights" / "v2" / "slots"
    slots.mkdir(parents=True)
    (slots / "1.lock").write_text(json.dumps({"pid": 111, "process_start_time": 1111.25}), encoding="utf-8")
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: pid == 111))
    monkeypatch.setattr(SlotStatusFileBackend, "_process_start_time", staticmethod(lambda pid: 1111.25 if pid == 111 else 2222.0))

    backend = SlotStatusFileBackend.from_env(auto_assign=True)

    assert backend is not None
    assert backend.slot == 2


def test_slot_status_file_backend_auto_assign_removes_dead_lock_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    slots = tmp_path / "agent-lights" / "v2" / "slots"
    slots.mkdir(parents=True)
    (slots / "1.lock").write_text(json.dumps({"pid": 111, "process_start_time": 1111.25}), encoding="utf-8")
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: False))

    backend = SlotStatusFileBackend.from_env(auto_assign=True)

    assert backend is not None
    assert backend.slot == 1


def test_slot_status_file_backend_auto_assign_reclaims_dead_recent_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    slots = tmp_path / "agent-lights" / "v2" / "slots"
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
    monkeypatch.setenv("HERMES_SLOT", "7")
    slots = tmp_path / "agent-lights" / "v2" / "slots"
    slots.mkdir(parents=True)
    (slots / "1.json").write_text(json.dumps({"slot": 1, "pid": 111, "state": "idle"}), encoding="utf-8")
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: pid == 111))

    backend = SlotStatusFileBackend.from_env(auto_assign=True)

    assert backend is not None
    assert backend.slot == 2


def test_slot_status_file_backend_auto_assign_caps_at_six_live_slots(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    slots = tmp_path / "agent-lights" / "v2" / "slots"
    slots.mkdir(parents=True)
    for slot in range(1, 7):
        (slots / f"{slot}.json").write_text(json.dumps({"slot": slot, "pid": 100 + slot, "state": "idle"}), encoding="utf-8")
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: True))

    assert SlotStatusFileBackend.from_env(auto_assign=True) is None


def test_kanban_worker_auto_assign_uses_eight_agent_slots(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_slot5")
    agents = tmp_path / "agent-lights" / "v2" / "agents"
    agents.mkdir(parents=True)
    for slot in range(1, 5):
        (agents / f"{slot}.json").write_text(
            json.dumps({"slot": slot, "pid": 100 + slot, "state": "working", "source": "kanban_worker"}),
            encoding="utf-8",
        )
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: 100 < pid < 105))

    backend = SlotStatusFileBackend.from_env(auto_assign=True)

    assert backend is not None
    assert backend.slot == 5
    assert (agents / "5.lock").exists()


def test_light_cue_service_retries_slot_claim_when_capacity_frees(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    slots = tmp_path / "agent-lights" / "v2" / "slots"
    slots.mkdir(parents=True)
    for slot in range(1, 7):
        (slots / f"{slot}.json").write_text(json.dumps({"slot": slot, "pid": 100 + slot, "state": "idle"}), encoding="utf-8")
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: True))

    service = build_light_cue_service_from_config({"model": {"default": "gpt-5.5"}}, auto_assign_slot=True)
    assert service.slot_status_backend is None

    (slots / "3.json").unlink()

    service.emit(LightCueEvent.IDLE)

    payload = json.loads((slots / "3.json").read_text(encoding="utf-8"))
    assert payload["slot"] == 3
    assert payload["state"] == "idle"
    assert payload["model_name"] == "gpt-5.5"


def test_light_cue_service_retries_unregistered_slot_without_waiting_for_lifecycle_event(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    slots = tmp_path / "agent-lights" / "v2" / "slots"
    slots.mkdir(parents=True)
    for slot in range(1, 7):
        (slots / f"{slot}.json").write_text(
            json.dumps({"slot": slot, "pid": 100 + slot, "state": "idle"}),
            encoding="utf-8",
        )
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: True))

    service = build_light_cue_service_from_config(
        {"model": {"default": "gpt-5.5"}}, auto_assign_slot=True
    )
    assert service.slot_status_backend is None

    (slots / "3.json").unlink()

    assert service.retry_slot_registration() is True
    payload = json.loads((slots / "3.json").read_text(encoding="utf-8"))
    assert payload["slot"] == 3
    assert payload["state"] == "idle"


@pytest.mark.parametrize(
    "event",
    [
        LightCueEvent.WORKING,
        LightCueEvent.HUMAN_INTERVENTION,
        LightCueEvent.FINAL_ANSWER,
        LightCueEvent.ERROR,
    ],
)
def test_light_cue_service_slot_retry_restores_latest_unregistered_lifecycle_state(tmp_path, monkeypatch, event):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    slots = tmp_path / "agent-lights" / "v2" / "slots"
    slots.mkdir(parents=True)
    for slot in range(1, 7):
        (slots / f"{slot}.json").write_text(
            json.dumps({"slot": slot, "pid": 100 + slot, "state": "idle"}),
            encoding="utf-8",
        )
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: True))

    service = build_light_cue_service_from_config(
        {"model": {"default": "gpt-5.5"}}, auto_assign_slot=True
    )
    assert service.slot_status_backend is None
    assert service.emit(event) is False

    (slots / "3.json").unlink()

    assert service.retry_slot_registration() is True
    payload = json.loads((slots / "3.json").read_text(encoding="utf-8"))
    assert payload["state"] == event.value


def test_light_cue_service_slot_retry_serializes_with_lifecycle_emit():
    retry_entered_backend = threading.Event()
    release_retry = threading.Event()
    recorded_events = []

    class BlockingSlotBackend:
        def emit_event(self, event):
            if event is LightCueEvent.IDLE:
                retry_entered_backend.set()
                assert release_retry.wait(timeout=1)
            recorded_events.append(event)
            return True

    class NoopLauncher:
        def ensure_running(self):
            return True

    backend = BlockingSlotBackend()
    service = LightCueService(
        slot_status_backend_factory=lambda: backend,
        menu_bar_launcher=NoopLauncher(),
    )

    retry_thread = threading.Thread(target=service.retry_slot_registration)
    retry_thread.start()
    assert retry_entered_backend.wait(timeout=1)

    lifecycle_thread = threading.Thread(target=lambda: service.emit(LightCueEvent.FINAL_ANSWER))
    lifecycle_thread.start()
    lifecycle_thread.join(timeout=0.1)
    assert lifecycle_thread.is_alive(), "lifecycle emission must wait for the retry registration write"

    release_retry.set()
    retry_thread.join(timeout=1)
    lifecycle_thread.join(timeout=1)
    assert not retry_thread.is_alive()
    assert not lifecycle_thread.is_alive()
    assert recorded_events == [LightCueEvent.IDLE, LightCueEvent.FINAL_ANSWER]


def test_light_cue_service_slot_retry_does_not_overwrite_registered_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "1")
    service = build_light_cue_service_from_config(
        {"model": {"default": "gpt-5.5"}}, auto_assign_slot=True
    )
    assert service.emit(LightCueEvent.FINAL_ANSWER) is False

    assert service.retry_slot_registration() is False
    payload = json.loads((tmp_path / "agent-lights" / "v2" / "slots" / "1.json").read_text(encoding="utf-8"))
    assert payload["state"] == "final_answer"


def test_slot_status_file_backend_auto_assign_reuses_current_process_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    slots = tmp_path / "agent-lights" / "v2" / "slots"
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

    status_path = tmp_path / "agent-lights" / "v2" / "slots" / "3.json"
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
    payload = json.loads((tmp_path / "agent-lights" / "v2" / "slots" / "4.json").read_text(encoding="utf-8"))
    assert payload["slot"] == 4
    assert payload["state"] == "idle"
    assert payload["model_name"] == "gpt-5.5"


def test_slot_status_file_backend_derives_profile_metadata_from_profile_home(tmp_path, monkeypatch):
    profile_home = tmp_path / "profiles" / "personal"
    profile_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    monkeypatch.delenv("HERMES_PROFILE", raising=False)

    backend = SlotStatusFileBackend(slot=1, directory=tmp_path / "agent-lights" / "v2" / "slots")
    assert backend.emit_event(LightCueEvent.IDLE) is True

    payload = json.loads((tmp_path / "agent-lights" / "v2" / "slots" / "1.json").read_text(encoding="utf-8"))
    assert payload["profile"] == "personal"


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


def test_slot_status_liveness_uses_psutil(monkeypatch):
    """PID probes remain signal-free on Windows and report psutil's answer."""
    import psutil

    seen: list[int] = []
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: seen.append(pid) or True)

    assert SlotStatusFileBackend._pid_is_running(4242) is True
    assert seen == [4242]


_CLAIM_CHILD = """
import os, sys, time
from pathlib import Path
go = Path(sys.argv[1])
while not go.exists():
    time.sleep(0.001)
from agent.light_cues import SlotStatusFileBackend
backend = SlotStatusFileBackend.from_env(auto_assign=True)
print(backend.slot if backend else "none", flush=True)
sys.stdin.read()
"""


_has_fcntl = importlib.util.find_spec("fcntl") is not None


@pytest.mark.skipif(not _has_fcntl, reason="slot-claim serialization needs fcntl.flock")
@pytest.mark.parametrize("round_", range(5))
def test_concurrent_claims_with_stale_locks_never_share_a_slot(tmp_path, round_):
    """Real processes racing over stale locks must each get a distinct slot."""
    import os
    import subprocess
    import sys

    home = tmp_path / "home"
    slots = home / "agent-lights" / "v2" / "slots"
    slots.mkdir(parents=True)
    # Dead owners left locks behind in every slot: every claimant judges each
    # lock stale at the same moment, which is what made two of them share one.
    for slot in range(1, 7):
        (slots / f"{slot}.lock").write_text(json.dumps({"pid": 999_999, "process_start_time": 1.0}), encoding="utf-8")
    go = tmp_path / "go"
    env = {
        **os.environ,
        "HERMES_AGENT_LIGHTS_HOME": str(home),
        "HERMES_AGENT_LIGHTS_AUTO_LAUNCH": "0",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
    }
    env.pop("HERMES_SLOT", None)
    env.pop("HERMES_KANBAN_TASK", None)
    children = [
        subprocess.Popen(
            [sys.executable, "-c", _CLAIM_CHILD, str(go)],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        for _ in range(8)
    ]
    try:
        go.touch()
        claimed = [child.stdout.readline().strip() for child in children]
    finally:
        for child in children:
            child.communicate(input="", timeout=30)

    slots_taken = [value for value in claimed if value != "none"]
    assert sorted(slots_taken) == ["1", "2", "3", "4", "5", "6"]
    assert claimed.count("none") == 2


def test_payload_process_matches_rejects_recycled_pid(monkeypatch):
    monkeypatch.setattr(SlotStatusFileBackend, "_process_start_time", staticmethod(lambda pid: 5000.0))

    assert SlotStatusFileBackend._payload_process_matches({"pid": 42, "process_start_time": 5000.4}) is True
    # Same PID, different process: the original started well before this one.
    assert SlotStatusFileBackend._payload_process_matches({"pid": 42, "process_start_time": 4000.0}) is False


def test_payload_process_matches_rejects_exited_process(monkeypatch):
    monkeypatch.setattr(SlotStatusFileBackend, "_process_start_time", staticmethod(lambda pid: None))

    assert SlotStatusFileBackend._payload_process_matches({"pid": 42, "process_start_time": 5000.0}) is False


def test_payload_process_matches_trusts_legacy_lstart_payloads_on_pid(monkeypatch):
    """Pre-upgrade sessions wrote a locale-dependent ``ps lstart`` string.

    A reader in another locale or timezone renders a different string for the
    same process, so the legacy text must not be able to declare a live
    session dead.
    """
    monkeypatch.setattr(SlotStatusFileBackend, "_process_start_time", staticmethod(lambda pid: 5000.0))

    assert SlotStatusFileBackend._payload_process_matches({"pid": 42, "process_started_at": "Mi. 14 Okt. 16:07:19 2026"}) is True


def test_process_start_time_matches_a_real_process_and_is_locale_independent(monkeypatch):
    import os

    first = SlotStatusFileBackend._process_start_time(os.getpid())
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("TZ", "Asia/Tokyo")

    assert isinstance(first, float)
    assert SlotStatusFileBackend._process_start_time(os.getpid()) == first
    assert SlotStatusFileBackend._payload_process_matches({"pid": os.getpid(), "process_start_time": first}) is True


@pytest.mark.skipif(not _has_fcntl, reason="slot-claim serialization needs fcntl.flock")
def test_claim_gives_up_instead_of_hanging_when_mutex_holder_is_wedged(tmp_path, monkeypatch):
    """A stopped claimant (e.g. Ctrl-Z mid-startup) must not hang new sessions."""
    import fcntl
    import os
    import time

    slots = tmp_path / "agent-lights" / "v2" / "slots"
    slots.mkdir(parents=True)
    monkeypatch.setattr(SlotStatusFileBackend, "_CLAIM_MUTEX_TIMEOUT_S", 0.2)
    # flock conflicts across separate open file descriptions, even in-process.
    fd = os.open(slots / ".claim.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        started = time.monotonic()
        assert SlotStatusFileBackend._claim_available_slot(slots) is None
        assert time.monotonic() - started < 2.0
        assert not (slots / "1.lock").exists()
    finally:
        os.close(fd)

    assert SlotStatusFileBackend._claim_available_slot(slots) == 1


def _live_owner_payload(pid: int, start: float, **extra) -> str:
    return json.dumps({"pid": pid, "process_start_time": start, **extra})


def test_explicit_slot_does_not_take_over_a_live_sessions_slot(tmp_path, monkeypatch):
    """HERMES_SLOT is a preference: a live owner keeps its slot."""
    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "1")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    slots = tmp_path / "agent-lights" / "v2" / "slots"
    slots.mkdir(parents=True)
    (slots / "1.json").write_text(_live_owner_payload(111, 1111.0, slot=1, state="working", source="hermes"), encoding="utf-8")
    (slots / "1.lock").write_text(_live_owner_payload(111, 1111.0), encoding="utf-8")
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: True))
    monkeypatch.setattr(
        SlotStatusFileBackend, "_process_start_time",
        staticmethod(lambda pid: 1111.0 if pid == 111 else 2222.0),
    )

    # Without auto-assignment there is nowhere else to go.
    assert SlotStatusFileBackend.from_env(auto_assign=False) is None
    # With it, the session falls back to the next free slot.
    backend = SlotStatusFileBackend.from_env(auto_assign=True)
    assert backend is not None and backend.slot == 2
    assert json.loads((slots / "1.lock").read_text(encoding="utf-8"))["pid"] == 111
    assert json.loads((slots / "1.json").read_text(encoding="utf-8"))["pid"] == 111


def test_explicit_slot_is_claimed_with_a_lock_when_free(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SLOT", "3")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)

    backend = SlotStatusFileBackend.from_env(auto_assign=False)

    assert backend is not None and backend.slot == 3
    lock = json.loads((tmp_path / "agent-lights" / "v2" / "slots" / "3.lock").read_text(encoding="utf-8"))
    assert lock["pid"] == os.getpid()


def test_claim_reaps_dead_owners_status_file_under_the_mutex(tmp_path, monkeypatch):
    """The dead owner's JSON must not outlive the claim.

    If it did, the menu app could prune it at the moment this session's first
    emit replaced it, deleting the new owner's file.
    """
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "1.json").write_text(_live_owner_payload(111, 1111.0, slot=1, state="idle", source="hermes"), encoding="utf-8")
    (slots / "1.lock").write_text(_live_owner_payload(111, 1111.0), encoding="utf-8")
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: pid == os.getpid()))

    assert SlotStatusFileBackend._claim_available_slot(slots) == 1

    assert not (slots / "1.json").exists()
    assert json.loads((slots / "1.lock").read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_claim_rolls_back_when_the_dead_owners_status_file_cannot_be_reaped(tmp_path, monkeypatch):
    """A claim that leaves the dead owner's JSON behind is not safe to use.

    The pruner would judge that stale file and could delete this session's
    first status write that replaces it. The claim must be released instead.
    """
    import errno

    slots = tmp_path / "slots"
    slots.mkdir()
    for slot in (1, 2):
        (slots / f"{slot}.json").write_text(
            _live_owner_payload(100 + slot, 1000.0, slot=slot, state="idle", source="hermes"), encoding="utf-8"
        )
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: pid == os.getpid()))
    real_unlink = Path.unlink

    def flaky_unlink(self, *args, **kwargs):
        if self.name == "1.json":
            raise OSError(errno.EIO, "injected I/O error")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    # Slot 1 cannot be cleaned: its claim is released and slot 2 is used.
    assert SlotStatusFileBackend._claim_available_slot(slots) == 2
    assert not (slots / "1.lock").exists()
    assert (slots / "1.json").exists()
    assert not (slots / "2.json").exists()

    # With no slot it can safely clean, the claim fails (and is retried later).
    real_unlink(slots / "2.lock")
    assert SlotStatusFileBackend._claim_available_slot(slots, preferred=1, fallback_to_any=False) is None
    assert not (slots / "1.lock").exists()


def test_retry_after_failed_cleanup_and_failed_rollback_still_reaps_before_adopting(tmp_path, monkeypatch):
    """Both unlinks fail: the leftover own lock must not be adopted blindly.

    The retry sees a lock naming this process, but the dead owner's JSON is
    still there. It must reap it first, and only then return the slot.
    """
    import errno

    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "1.json").write_text(_live_owner_payload(101, 1000.0, slot=1, state="idle", source="hermes"), encoding="utf-8")
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: pid == os.getpid()))
    monkeypatch.setattr(SlotStatusFileBackend, "_valid_slots", classmethod(lambda cls, directory: range(1, 2)))
    real_unlink = Path.unlink
    failing = {"1.json", "1.lock"}

    def flaky_unlink(self, *args, **kwargs):
        if self.name in failing:
            raise OSError(errno.EIO, "injected I/O error")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    assert SlotStatusFileBackend._claim_available_slot(slots) is None
    assert (slots / "1.lock").exists()  # rollback failed too
    # Cleanup still failing: the retry must not adopt the slot.
    assert SlotStatusFileBackend._claim_available_slot(slots) is None
    assert (slots / "1.json").exists()

    failing.clear()
    # Once cleanup works, the retry reaps the stale file and adopts the slot.
    assert SlotStatusFileBackend._claim_available_slot(slots) == 1
    assert not (slots / "1.json").exists()
    assert json.loads((slots / "1.lock").read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_leftover_own_lock_is_released_when_another_live_process_owns_the_status_file(tmp_path, monkeypatch):
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "1.json").write_text(_live_owner_payload(222, 2222.0, slot=1, state="working", source="hermes"), encoding="utf-8")
    (slots / "1.lock").write_text(_live_owner_payload(os.getpid(), 0.0), encoding="utf-8")
    monkeypatch.setattr(SlotStatusFileBackend, "_pid_is_running", staticmethod(lambda pid: pid in {222, os.getpid()}))
    monkeypatch.setattr(
        SlotStatusFileBackend, "_process_start_time",
        staticmethod(lambda pid: 2222.0 if pid == 222 else 0.0),
    )

    assert SlotStatusFileBackend._claim_available_slot(slots) == 2
    assert not (slots / "1.lock").exists()
    assert json.loads((slots / "1.json").read_text(encoding="utf-8"))["pid"] == 222


class _FakeProc:
    def __init__(self, name, create_time):
        self.info = {"name": name, "create_time": create_time}
        self.terminated = False

    def terminate(self):
        self.terminated = True


def _launcher_with_binary(tmp_path):
    binary = tmp_path / "repo" / "apps" / "agent-lights-menu-bar" / ".build" / "debug" / "AgentLightsMenuBar"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"v2-binary")
    _install_info_plist(tmp_path / "repo")
    return AgentLightsMenuBarLauncher(repo_root=tmp_path / "repo")


def test_launcher_trusts_a_live_v2_heartbeat_and_launches_nothing(tmp_path, monkeypatch):
    import psutil

    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_AGENT_LIGHTS_AUTO_LAUNCH", raising=False)
    root = tmp_path / "agent-lights" / "v2"
    root.mkdir(parents=True)
    me = os.getpid()
    (root / "app.json").write_text(json.dumps({"pid": me, "process_start_time": psutil.Process(me).create_time(), "pool": "v2"}), encoding="utf-8")
    calls = []
    monkeypatch.setattr("agent.light_cues.subprocess.run", lambda *a, **k: calls.append(a) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: calls.append("scan") or [])

    assert _launcher_with_binary(tmp_path).ensure_running() is True
    assert calls == []


def _record_launch_commands(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "agent.light_cues.subprocess.run",
        lambda arguments, **kwargs: commands.append(arguments) or SimpleNamespace(returncode=0),
    )
    return commands


def test_launcher_never_terminates_a_running_app_without_heartbeat(tmp_path, monkeypatch):
    """Pre-v2, still starting, or failed heartbeat: all are left running."""
    import time as _time

    import psutil

    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_AGENT_LIGHTS_AUTO_LAUNCH", raising=False)
    old = _FakeProc("AgentLightsMenuBar", _time.time() - 3600)
    young = _FakeProc("AgentLightsMenuBar", _time.time() - 1)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: [old, young])
    commands = _record_launch_commands(monkeypatch)
    launcher = _launcher_with_binary(tmp_path)
    bundled = launcher.repo_root / launcher.bundle_relative_path / "Contents" / "MacOS" / "AgentLightsMenuBar"

    assert launcher.ensure_running() is False

    assert old.terminated is False and young.terminated is False
    assert commands == []  # no codesign, no open
    assert not bundled.exists()  # the running app's bundle is never rewritten


def test_launcher_opens_the_app_only_when_none_is_running_and_waits_for_it(tmp_path, monkeypatch):
    import psutil

    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_AGENT_LIGHTS_AUTO_LAUNCH", raising=False)
    commands = _record_launch_commands(monkeypatch)
    opened = lambda: any(c[:2] == ["/usr/bin/open", "-g"] for c in commands)
    # The app becomes visible a few polls after `open` returns.
    polls = {"after_open": 0}

    def process_iter(*args, **kwargs):
        if opened():
            polls["after_open"] += 1
            if polls["after_open"] >= 3:
                return [_FakeProc("AgentLightsMenuBar", 0.0)]
        return [_FakeProc("Finder", 0.0)]

    monkeypatch.setattr(psutil, "process_iter", process_iter)

    assert _launcher_with_binary(tmp_path).ensure_running() is True
    assert [c for c in commands if c[0] == "/usr/bin/open"] == [["/usr/bin/open", "-g", commands[-1][2]]]
    assert commands[-1][2].endswith("AgentLightsMenuBar-v2.app")
    assert polls["after_open"] >= 3


def test_launcher_reports_an_opened_app_that_never_appears(tmp_path, monkeypatch, caplog):
    import logging

    import psutil

    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_AGENT_LIGHTS_AUTO_LAUNCH", raising=False)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: [])
    _record_launch_commands(monkeypatch)
    launcher = _launcher_with_binary(tmp_path)
    launcher.launch_ready_timeout_s = 0.2

    with caplog.at_level(logging.INFO, logger="agent.light_cues"):
        assert launcher.ensure_running() is False
    assert "did not appear" in caplog.text


def test_launcher_never_writes_the_pre_v2_bundle(tmp_path, monkeypatch):
    """Pre-v2 launchers own .build/AgentLightsMenuBar.app; v2 uses its own."""
    launcher = _launcher_with_binary(tmp_path)
    legacy_executable = (
        launcher.repo_root / "apps" / "agent-lights-menu-bar" / ".build"
        / "AgentLightsMenuBar.app" / "Contents" / "MacOS" / "AgentLightsMenuBar"
    )
    legacy_executable.parent.mkdir(parents=True)
    legacy_executable.write_bytes(b"legacy-binary")
    monkeypatch.setattr("agent.light_cues.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0))

    app_path = launcher._ensure_app_bundle()

    assert app_path.name == "AgentLightsMenuBar-v2.app"
    assert legacy_executable.read_bytes() == b"legacy-binary"


def test_launcher_rechecks_under_the_lock_before_touching_the_bundle(tmp_path, monkeypatch):
    """A heartbeat published after the first check must stop the launch."""
    import psutil

    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_AGENT_LIGHTS_AUTO_LAUNCH", raising=False)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: [])
    commands = _record_launch_commands(monkeypatch)
    launcher = _launcher_with_binary(tmp_path)
    answers = iter([False, True])
    monkeypatch.setattr(launcher, "_is_running", lambda: next(answers))

    assert launcher.ensure_running() is True
    assert commands == []


def test_launcher_skips_when_another_launch_holds_the_lock(tmp_path, monkeypatch):
    import fcntl

    import psutil

    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_AGENT_LIGHTS_AUTO_LAUNCH", raising=False)
    monkeypatch.setattr(SlotStatusFileBackend, "_CLAIM_MUTEX_TIMEOUT_S", 0.1)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: [])
    commands = _record_launch_commands(monkeypatch)
    root = tmp_path / "agent-lights" / "v2"
    root.mkdir(parents=True)
    fd = os.open(root / ".claim.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert _launcher_with_binary(tmp_path).ensure_running() is False
    finally:
        os.close(fd)
    assert commands == []


def test_launcher_assumes_an_app_is_running_when_processes_cannot_be_listed(tmp_path, monkeypatch):
    import psutil

    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_AGENT_LIGHTS_AUTO_LAUNCH", raising=False)

    def broken(*args, **kwargs):
        raise psutil.Error("no process table")

    monkeypatch.setattr(psutil, "process_iter", broken)
    commands = _record_launch_commands(monkeypatch)

    assert _launcher_with_binary(tmp_path).ensure_running() is False
    assert commands == []


@pytest.mark.parametrize(
    "heartbeat",
    [
        {"pool": "v2"},  # no pid
        {"pid": "self", "pool": "v2"},  # no start time: no legacy form for app heartbeats
        {"pid": "self", "process_start_time": "self"},  # no pool
        {"pid": "self", "process_start_time": "self", "pool": "v1"},
    ],
)
def test_app_heartbeat_is_validated_strictly(tmp_path, monkeypatch, heartbeat):
    import psutil

    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    me = os.getpid()
    resolved = {
        key: (me if value == "self" and key == "pid" else psutil.Process(me).create_time() if value == "self" else value)
        for key, value in heartbeat.items()
    }
    root = tmp_path / "agent-lights" / "v2"
    root.mkdir(parents=True)
    (root / "app.json").write_text(json.dumps(resolved), encoding="utf-8")

    assert AgentLightsMenuBarLauncher(repo_root=tmp_path)._is_running() is False


def test_current_producers_never_touch_the_legacy_pool(tmp_path, monkeypatch):
    """Pre-v2 producers write agent-lights/slots; v2 must stay out of it."""
    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    legacy = tmp_path / "agent-lights" / "slots"
    legacy.mkdir(parents=True)
    (legacy / "1.lock").write_text(json.dumps({"pid": 999_999, "process_started_at": "x"}), encoding="utf-8")
    before = sorted(p.name for p in legacy.iterdir())

    backend = SlotStatusFileBackend.from_env(auto_assign=True)
    assert backend is not None and backend.slot == 1
    assert backend.emit_event(LightCueEvent.WORKING) is True

    assert backend.directory == tmp_path / "agent-lights" / "v2" / "slots"
    assert sorted(p.name for p in legacy.iterdir()) == before


def test_launcher_bundle_name_matches_the_name_the_app_requires():
    """The app (AppBundleIdentity.bundleName in SlotStatus.swift) exits unless
    it runs from this bundle name; launcher and app must agree on it."""
    swift = (
        Path(__file__).resolve().parents[2]
        / "apps" / "agent-lights-menu-bar" / "Sources" / "AgentLightsCore" / "SlotStatus.swift"
    ).read_text(encoding="utf-8")

    assert AgentLightsMenuBarLauncher.bundle_relative_path.name == "AgentLightsMenuBar-v2.app"
    assert 'public static let bundleName = "AgentLightsMenuBar-v2.app"' in swift


@pytest.mark.parametrize("link_level", ["Contents", "Contents/MacOS", "Contents/MacOS/AgentLightsMenuBar"])
def test_launcher_refuses_legacy_bundle_internal_symlinks_into_v2(tmp_path, monkeypatch, link_level):
    """Distinct bundle roots, but the legacy executable path reaches v2."""
    launcher = _launcher_with_binary(tmp_path)
    build = launcher.repo_root / "apps" / "agent-lights-menu-bar" / ".build"
    v2_exe = build / "AgentLightsMenuBar-v2.app" / "Contents" / "MacOS" / "AgentLightsMenuBar"
    v2_exe.parent.mkdir(parents=True)
    v2_exe.write_bytes(b"current")
    link = build / "AgentLightsMenuBar.app" / link_level
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(build / "AgentLightsMenuBar-v2.app" / link_level)
    monkeypatch.setattr("agent.light_cues.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0))

    assert "resolves into" in launcher._bundle_alias_problem(build / "AgentLightsMenuBar-v2.app")
    assert launcher._ensure_app_bundle() is None
    assert v2_exe.read_bytes() == b"current"


@pytest.mark.parametrize("alias", ["v2_to_legacy", "legacy_to_v2"])
def test_launcher_refuses_to_package_into_an_aliased_bundle(tmp_path, monkeypatch, alias):
    launcher = _launcher_with_binary(tmp_path)
    build = launcher.repo_root / "apps" / "agent-lights-menu-bar" / ".build"
    v2 = build / "AgentLightsMenuBar-v2.app"
    legacy = build / "AgentLightsMenuBar.app"
    real, link = (legacy, v2) if alias == "v2_to_legacy" else (v2, legacy)
    (real / "Contents" / "MacOS").mkdir(parents=True)
    (real / "Contents" / "MacOS" / "AgentLightsMenuBar").write_bytes(b"untouched")
    link.symlink_to(real)
    monkeypatch.setattr("agent.light_cues.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0))

    assert launcher._ensure_app_bundle() is None
    assert (real / "Contents" / "MacOS" / "AgentLightsMenuBar").read_bytes() == b"untouched"


def test_launcher_replaces_the_executable_instead_of_writing_through_a_hard_link(tmp_path, monkeypatch):
    launcher = _launcher_with_binary(tmp_path)
    build = launcher.repo_root / "apps" / "agent-lights-menu-bar" / ".build"
    v2_exe = build / "AgentLightsMenuBar-v2.app" / "Contents" / "MacOS" / "AgentLightsMenuBar"
    other = tmp_path / "elsewhere"
    v2_exe.parent.mkdir(parents=True)
    other.write_bytes(b"other-bundle-binary")
    os.link(other, v2_exe)
    monkeypatch.setattr("agent.light_cues.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0))

    assert launcher._ensure_app_bundle() is not None
    assert v2_exe.read_bytes() == b"v2-binary"
    assert other.read_bytes() == b"other-bundle-binary"
    assert not list(v2_exe.parent.glob(".*.tmp"))
