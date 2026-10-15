"""agent-lights plugin (apps/agent-lights-menu-bar/plugin/agent-lights).

The plugin lets stock Hermes produce Agent Lights slot files. These tests pin
its hook-to-state mapping, and that its copy of the v2 slot protocol
interoperates with this fork's core producer in one shared pool.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.light_cues import LightCueEvent as CoreEvent
from agent.light_cues import SlotStatusFileBackend as CoreBackend

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = _REPO_ROOT / "apps" / "agent-lights-menu-bar" / "plugin" / "agent-lights"
_has_fcntl = importlib.util.find_spec("fcntl") is not None


def _load_plugin():
    name = "agent_lights_plugin_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, _PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(_PLUGIN_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


plugin = _load_plugin()
protocol = sys.modules["agent_lights_plugin_under_test.protocol"]
Event = protocol.LightCueEvent


class RecordingBackend:
    def __init__(self):
        self.events: list[str] = []
        self.contexts: list[dict] = []
        self.model_name = None

    def emit_event(self, event):
        self.events.append(event.value)
        return True

    def emit_context(self, **kwargs):
        self.contexts.append(kwargs)
        return True


class RecordingLauncher:
    def __init__(self):
        self.calls = 0

    def ensure_running(self):
        self.calls += 1
        return True


@pytest.fixture
def producer(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    backend = RecordingBackend()
    claims = []

    def factory():
        claims.append(1)
        return backend

    made = plugin.AgentLightsProducer(backend_factory=factory, launcher=RecordingLauncher())
    made.backend = backend
    made.claims = claims
    return made


def _turn(producer, *, session_id="s1", platform="cli", response="done", **end):
    producer.on_pre_llm_call(session_id=session_id, platform=platform, model="m", user_message="hi")
    producer.on_post_llm_call(session_id=session_id, platform=platform, assistant_response=response)
    producer.on_session_end(session_id=session_id, platform=platform, **{"completed": True, **end})


@pytest.mark.parametrize("platform", ["cli", "tui"])
def test_completed_turn_goes_working_then_final_answer(producer, platform):
    _turn(producer, platform=platform)

    assert producer.backend.events == ["working", "final_answer"]


def test_failed_turn_is_error_and_interrupted_turn_is_idle(producer):
    _turn(producer, failed=True)
    _turn(producer, interrupted=True)

    assert producer.backend.events == ["working", "error", "working", "idle"]


def test_turn_without_a_response_ends_idle(producer):
    _turn(producer, response="")

    assert producer.backend.events == ["working", "idle"]


@pytest.mark.parametrize("platform", ["telegram", "cron", "desktop", "", None])
def test_non_interactive_platforms_never_claim_a_slot(producer, platform):
    _turn(producer, platform=platform)
    producer.on_human_input_request(request_id="r1")

    assert producer.claims == []
    assert producer.backend.events == []


def test_pre_llm_call_returns_none_so_nothing_is_injected(producer):
    assert producer.on_pre_llm_call(session_id="s1", platform="cli", user_message="hi") is None


def test_subagent_turns_do_not_touch_the_parent_dot(producer):
    producer.on_pre_llm_call(session_id="parent", platform="cli", user_message="go")
    producer.on_subagent_start(parent_session_id="parent", child_session_id="child")
    _turn(producer, session_id="child")
    _turn(producer, session_id="child", failed=True)

    assert producer.backend.events == ["working"]


def test_human_input_turns_yellow_until_every_request_resolves(producer):
    producer.on_pre_llm_call(session_id="s1", platform="cli", user_message="hi")
    producer.on_human_input_request(request_id="a", kind="approval")
    producer.on_human_input_request(request_id="b", kind="sudo")
    producer.on_human_input_resolved(request_id="a", outcome="approved")
    producer.on_human_input_resolved(request_id="b", outcome="approved")
    producer.on_human_input_resolved(request_id="b", outcome="approved")

    assert producer.backend.events == ["working", "human_intervention", "human_intervention", "working"]


def test_human_input_outside_a_session_turn_is_ignored(producer):
    producer.on_human_input_request(request_id="a")

    assert producer.claims == []


def test_fallback_hooks_cover_clarify_and_human_approvals(producer):
    producer.on_pre_llm_call(session_id="s1", platform="cli", user_message="hi")
    producer.on_pre_tool_call(tool_name="terminal", tool_call_id="t0")
    producer.on_pre_tool_call(tool_name="clarify", tool_call_id="t1")
    producer.on_post_tool_call(tool_name="clarify", tool_call_id="t1")
    producer.on_pre_approval_request(surface="smart")
    producer.on_pre_approval_request(surface="cli")
    producer.on_post_approval_response(surface="cli", choice="once")

    assert producer.backend.events == [
        "working", "human_intervention", "working", "human_intervention", "working",
    ]


def test_kanban_worker_reports_its_own_task_only(producer, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    producer.on_pre_llm_call(session_id="w", platform="kanban", user_message="work")
    producer.on_kanban_task_blocked(task_id="t_2")
    producer.on_kanban_task_blocked(task_id="t_1")
    producer.on_kanban_task_completed(task_id="t_1")

    assert producer.backend.events == ["working", "human_intervention", "final_answer"]


def test_claim_retries_on_later_events_and_launches_once(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    backend = RecordingBackend()
    results = [None, backend]
    launcher = RecordingLauncher()
    made = plugin.AgentLightsProducer(backend_factory=lambda: results.pop(0), launcher=launcher)

    made.on_pre_llm_call(session_id="s", platform="cli", user_message="a")  # pool full
    made.on_session_end(session_id="s", platform="cli", completed=True)  # claims now
    made.on_pre_llm_call(session_id="s", platform="cli", user_message="b")
    for thread in threading.enumerate():
        if thread.name == "agent-lights-launch":
            thread.join(timeout=5)

    assert backend.events == ["idle", "working"]
    assert launcher.calls == 1


def test_context_preview_is_one_short_line(producer):
    producer.on_pre_llm_call(session_id="s1", platform="cli", model="gpt-x", user_message="line one\n" + "x" * 400)
    producer.on_pre_llm_call(session_id="s1", platform="cli", model="gpt-x", user_message=[{"type": "image"}])

    first, second = producer.backend.contexts
    assert first["current_context"].startswith("Working on: line one x")
    assert "\n" not in first["current_context"]
    assert len(first["current_context"]) <= len("Working on: ") + 160
    assert first["model_name"] == "gpt-x"
    assert second["current_context"] == "Working on multimodal input"
    assert producer.backend.model_name == "gpt-x"


def test_hook_table_prefers_human_input_hooks_when_available(producer):
    every = {
        "pre_llm_call", "post_llm_call", "on_session_end", "subagent_start", "kanban_task_blocked",
        "kanban_task_completed", "pre_tool_call", "post_tool_call", "pre_approval_request",
        "post_approval_response", "on_human_input_request", "on_human_input_resolved",
    }
    with_human_input = plugin.hook_table(producer, every)
    without = plugin.hook_table(producer, every - {"on_human_input_request", "on_human_input_resolved"})

    assert {"on_human_input_request", "on_human_input_resolved"} <= set(with_human_input)
    assert "pre_tool_call" not in with_human_input
    assert {"pre_tool_call", "post_tool_call", "pre_approval_request", "post_approval_response"} <= set(without)
    # Never registers a hook the running Hermes does not know.
    assert set(plugin.hook_table(producer, {"pre_llm_call"})) == {"pre_llm_call"}


def test_registered_hooks_are_all_known_to_this_hermes(producer):
    from hermes_cli.plugins import VALID_HOOKS

    assert set(plugin.hook_table(producer, VALID_HOOKS)) <= VALID_HOOKS
    assert {"pre_llm_call", "post_llm_call", "on_session_end"} <= set(plugin.hook_table(producer, VALID_HOOKS))


def test_register_stands_down_where_core_already_produces(monkeypatch):
    ctx = SimpleNamespace(hooks=[], register_hook=lambda name, cb: ctx.hooks.append(name))
    monkeypatch.setattr(plugin.sys, "platform", "darwin")

    # This fork ships agent.light_cues, so the plugin must not double-claim.
    assert plugin.fork_producer_present() is True
    plugin.register(ctx)
    assert ctx.hooks == []

    monkeypatch.setattr(plugin, "fork_producer_present", lambda: False)
    plugin.register(ctx)
    assert "pre_llm_call" in ctx.hooks


def test_register_is_inert_off_macos(monkeypatch):
    ctx = SimpleNamespace(hooks=[], register_hook=lambda name, cb: ctx.hooks.append(name))
    monkeypatch.setattr(plugin.sys, "platform", "linux")
    monkeypatch.setattr(plugin, "fork_producer_present", lambda: False)

    plugin.register(ctx)

    assert ctx.hooks == []


# -- real files -------------------------------------------------------------


def test_real_backend_writes_the_session_and_kanban_pools(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    made = plugin.AgentLightsProducer(launcher=RecordingLauncher())

    _turn(made)

    payload = json.loads((tmp_path / "agent-lights" / "v2" / "slots" / "1.json").read_text())
    assert payload["state"] == "final_answer"
    assert payload["pid"] == os.getpid()
    assert payload["source"] == "hermes"
    assert payload["model_name"] == "m"
    assert (tmp_path / "agent-lights" / "v2" / "slots" / "1.lock").exists()
    context = json.loads((tmp_path / "agent-lights" / "v2" / "context" / "1.json").read_text())
    assert context["current_context"] == "Working on: hi"

    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_9")
    worker = plugin.AgentLightsProducer(launcher=RecordingLauncher())
    worker.on_pre_llm_call(session_id="w", platform="kanban", user_message="go")
    agent_payload = json.loads((tmp_path / "agent-lights" / "v2" / "agents" / "1.json").read_text())
    assert agent_payload["state"] == "working"
    assert agent_payload["kanban_task_id"] == "t_9"


def test_plugin_and_core_payloads_carry_the_same_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path / "a"))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    core = CoreBackend.from_env(auto_assign=True, model_name="m")
    core.emit_event(CoreEvent.WORKING)
    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path / "b"))
    mine = protocol.SlotStatusFileBackend.from_env(auto_assign=True, model_name="m")
    mine.emit_event(Event.WORKING)

    core_payload = json.loads((tmp_path / "a" / "agent-lights" / "v2" / "slots" / "1.json").read_text())
    plugin_payload = json.loads((tmp_path / "b" / "agent-lights" / "v2" / "slots" / "1.json").read_text())
    assert set(core_payload) == set(plugin_payload)
    core.clear_if_owned()
    mine.clear_if_owned()


def test_each_producer_honors_the_others_live_claims_and_reaps_dead_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    slots = tmp_path / "agent-lights" / "v2" / "slots"
    slots.mkdir(parents=True)
    dead = {"pid": 999_999, "process_start_time": 1.0, "state": "working"}
    (slots / "1.json").write_text(json.dumps(dead))
    (slots / "1.lock").write_text(json.dumps(dead))
    live_other = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    try:
        import psutil

        live = {"pid": live_other.pid, "process_start_time": psutil.Process(live_other.pid).create_time()}
        (slots / "2.json").write_text(json.dumps({**live, "state": "working"}))
        (slots / "2.lock").write_text(json.dumps(live))

        for backend_cls in (protocol.SlotStatusFileBackend, CoreBackend):
            # A live session of the other producer is not claimable; a dead one is.
            assert backend_cls._payload_is_live(json.loads((slots / "2.lock").read_text())) is True
            assert backend_cls._payload_is_live(dead) is False

        claimed = protocol.SlotStatusFileBackend.from_env(auto_assign=True)
        assert claimed.slot == 1
        assert not (slots / "1.json").exists()  # dead owner's status reaped at claim
        assert json.loads((slots / "1.lock").read_text())["pid"] == os.getpid()
        assert CoreBackend._payload_is_live(json.loads((slots / "1.lock").read_text())) is True
        claimed.clear_if_owned()
    finally:
        live_other.stdin.close()
        live_other.wait(timeout=10)


_MIXED_CLAIM_CHILD = """
import importlib.util, sys, time
from pathlib import Path
go, which, plugin_protocol = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
while not go.exists():
    time.sleep(0.001)
if which == "core":
    from agent.light_cues import SlotStatusFileBackend
else:
    spec = importlib.util.spec_from_file_location("agent_lights_protocol", plugin_protocol)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    SlotStatusFileBackend = module.SlotStatusFileBackend
backend = None
# The plugin waits only briefly for the claim lock and retries on its next
# lifecycle event; model that with a bounded retry loop.
for _ in range(200):
    backend = SlotStatusFileBackend.from_env(auto_assign=True)
    if backend is not None:
        break
    time.sleep(0.01)
print(backend.slot if backend else "none", flush=True)
sys.stdin.read()
"""


@pytest.mark.skipif(not _has_fcntl, reason="slot-claim serialization needs fcntl.flock")
@pytest.mark.parametrize("round_", range(3))
def test_core_and_plugin_claimants_racing_never_share_a_slot(tmp_path, round_):
    home = tmp_path / "home"
    slots = home / "agent-lights" / "v2" / "slots"
    slots.mkdir(parents=True)
    for slot in range(1, 7):
        (slots / f"{slot}.lock").write_text(json.dumps({"pid": 999_999, "process_start_time": 1.0}))
    go = tmp_path / "go"
    env = {
        **os.environ,
        "HERMES_AGENT_LIGHTS_HOME": str(home),
        "HERMES_AGENT_LIGHTS_AUTO_LAUNCH": "0",
        "PYTHONPATH": str(_REPO_ROOT),
    }
    env.pop("HERMES_SLOT", None)
    env.pop("HERMES_KANBAN_TASK", None)
    children = [
        subprocess.Popen(
            [sys.executable, "-c", _MIXED_CLAIM_CHILD, str(go), "core" if i % 2 else "plugin",
             str(_PLUGIN_DIR / "protocol.py")],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        for i in range(8)
    ]
    try:
        go.touch()
        claimed = [child.stdout.readline().strip() for child in children]
    finally:
        for child in children:
            child.communicate(input="", timeout=60)

    taken = [value for value in claimed if value != "none"]
    assert sorted(taken) == ["1", "2", "3", "4", "5", "6"]
    assert claimed.count("none") == 2


# -- launcher ---------------------------------------------------------------


def _install_fake_app(home: Path) -> Path:
    app = home / "Applications" / "AgentLightsMenuBar-v2.app"
    exe = app / "Contents" / "MacOS" / "AgentLightsMenuBar"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"app")
    return app


def test_launcher_opens_the_installed_app_when_none_is_running(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path / "pool"))
    monkeypatch.delenv("HERMES_AGENT_LIGHTS_AUTO_LAUNCH", raising=False)
    monkeypatch.setattr(protocol.Path, "home", lambda: tmp_path)
    app = _install_fake_app(tmp_path)
    launcher = protocol.InstalledAppLauncher()
    seen = {"running": False}
    opened = []

    def fake_run(argv, **kwargs):
        opened.append(argv)
        seen["running"] = True
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(protocol.subprocess, "run", fake_run)
    monkeypatch.setattr(launcher, "any_app_process", lambda: seen["running"])

    assert launcher.ensure_running() is True
    assert opened == [["/usr/bin/open", "-g", str(app)]]


def test_launcher_leaves_a_running_app_alone_and_skips_when_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path / "pool"))
    monkeypatch.delenv("HERMES_AGENT_LIGHTS_AUTO_LAUNCH", raising=False)
    monkeypatch.setattr(protocol.Path, "home", lambda: tmp_path)
    opened = []
    monkeypatch.setattr(protocol.subprocess, "run", lambda argv, **kw: opened.append(argv))
    launcher = protocol.InstalledAppLauncher()
    monkeypatch.setattr(launcher, "candidate_app_paths", lambda: [tmp_path / "Applications" / "AgentLightsMenuBar-v2.app"])

    assert launcher.ensure_running() is False  # not installed

    _install_fake_app(tmp_path)
    monkeypatch.setattr(launcher, "any_app_process", lambda: True)
    assert launcher.ensure_running() is True  # some app already running
    assert opened == []
