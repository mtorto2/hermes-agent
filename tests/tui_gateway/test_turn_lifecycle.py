"""Cross-path admission and settlement contracts; no provider or host service."""
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tui_gateway import server


@pytest.fixture
def env(monkeypatch, tmp_path):
    session = dict(agent=SimpleNamespace(), session_key="lifecycle", history=[],
                   history_lock=threading.Lock(), history_version=0, running=False,
                   attached_images=[], transport=None, cwd=str(tmp_path))
    events, workers, starts = [], [], []

    class Thread:
        def __init__(self, target, args=(), kwargs=None, **kw):
            self.run = lambda: target(*args, **(kwargs or {}))
        def start(self):
            workers.append(self.run)
        def is_alive(self):
            return True

    monkeypatch.setitem(server._sessions, "lifecycle", session)
    monkeypatch.setattr(server.threading, "Thread", Thread)
    monkeypatch.setattr(server, "_load_cfg", lambda: {})
    monkeypatch.setattr(server, "_ensure_active_session_slot", lambda *a: None)
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *a: False)
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda s: True)
    monkeypatch.setattr(server, "_persist_branch_seed", lambda s: None)
    monkeypatch.setattr(server, "_start_agent_build", lambda *a: None)
    monkeypatch.setattr(server, "_wait_agent_for_prompt", lambda *a: None)
    monkeypatch.setattr(server, "_wait_agent", lambda *a, **kw: None)
    monkeypatch.setattr(server, "_load_busy_input_mode", lambda: "queue")
    monkeypatch.setattr(server, "_reconcile_session_cwd_from_terminal", lambda s: None)
    monkeypatch.setattr(server, "_session_info", lambda a, s: {"running": s["running"]})
    monkeypatch.setattr(server, "_emit", lambda event, sid, payload=None: events.append((event, payload)))
    monkeypatch.setattr(server, "_run_prompt_submit", lambda r, sid, s, text, **kw: starts.append(text))
    submit = lambda text: server._methods["prompt.submit"](text, {"session_id": "lifecycle", "text": text})
    return session, events, workers, starts, submit


def test_submit_idle_observation_is_atomic_claim(env):
    session, events, workers, starts, submit = env
    other = []
    lock = threading.Lock()

    class RaceLock:
        armed = True
        def __enter__(self):
            lock.acquire()
        def __exit__(self, *exc):
            lock.release()
            if self.armed:
                self.armed = False
                other.append(submit("B"))

    session["history_lock"] = RaceLock()
    first = submit("A")
    for run in list(workers):
        run()
    assert first["result"]["status"] == "streaming"
    assert other[0]["result"]["status"] == "queued"
    assert starts == ["A"]
    assert session["queued_prompt"]["text"] == "B"


def test_loop_defers_without_consuming_due_tick(env, monkeypatch):
    import hermes_cli.loops as loops
    session, events, workers, starts, submit = env
    mgr = Mock()
    mgr.is_due.return_value = True
    mgr.fire_tick.return_value = "loop prompt"
    monkeypatch.setattr(loops, "LoopManager", lambda **kw: mgr)
    monkeypatch.setattr(loops, "goal_blocks_loop_tick", lambda key: False)
    session["_turn_settling"] = True
    server._maybe_fire_tui_loop_tick("lifecycle", session)
    assert starts == []
    mgr.fire_tick.assert_not_called()
    session.pop("_turn_settling")
    server._maybe_fire_tui_loop_tick("lifecycle", session)
    assert starts == ["loop prompt"]


def test_auto_continue_retries_after_barrier(env, monkeypatch):
    session, events, workers, starts, submit = env
    monkeypatch.setattr(server, "read_turn_marker", lambda *a: dict(started_at=time.time(), attempts=0, prompt="recover"))
    monkeypatch.setattr(server, "_auto_continue_config", lambda: (True, 600, 3))
    session["_turn_settling"] = True
    server._maybe_schedule_auto_continue("lifecycle", session, "lifecycle")
    workers.pop(0)()
    assert starts == []
    # Settlement must dispatch the deferred continuation without another resume.
    session["running"] = True
    server._settle_turn("r", "lifecycle", session)
    for run in list(workers):
        run()
    assert len(starts) == 1
    assert "recover" in starts[0]


def test_compute_error_holds_barrier_until_idle_publication(env, monkeypatch):
    session, events, workers, starts, submit = env
    session["running"] = True
    def emit(event, sid, payload=None):
        assert session["history_lock"].acquire(blocking=False)
        session["history_lock"].release()
        events.append((event, payload))
        if event == "message.complete":
            assert submit("successor")["result"]["status"] == "queued"
            assert starts == []
        if event == "session.info":
            assert session.get("_turn_settling") is True
            assert starts == []
    monkeypatch.setattr(server, "_emit", emit)
    server._on_compute_host_turn_done("r", "lifecycle", session, {"type": "turn.error", "message": "host died"})
    assert [e for e, _ in events if e in {"message.complete", "session.info"}] == ["message.complete", "session.info"]
    assert starts == ["successor"]


def test_build_timeout_drains_accepted_queue_after_late_readiness(env, monkeypatch):
    session, events, workers, starts, submit = env
    session["agent"] = None
    waits = []
    def wait(*args):
        waits.append(True)
        if len(waits) == 1:
            assert submit("queued")["result"]["status"] == "queued"
            return {"error": {"message": "build timed out"}}
        session["agent"] = SimpleNamespace()
        return None
    monkeypatch.setattr(server, "_wait_agent_for_prompt", wait)
    assert submit("first")["result"]["status"] == "streaming"
    while workers:
        workers.pop(0)()
    assert starts == ["queued"]
    assert len(waits) == 2
    assert any(e == "message.complete" and "build timed out" in p["text"] for e, p in events)
    assert sum(e == "session.info" and p["running"] is False for e, p in events) == 1


@pytest.mark.parametrize("failure", [False, OSError("storage failed")])
def test_failed_storage_claim_releases_and_drains(env, monkeypatch, failure):
    session, events, workers, starts, submit = env
    def persist(s):
        assert submit("queued")["result"]["status"] == "queued"
        # Storage recovers for the already accepted successor.
        monkeypatch.setattr(server, "_ensure_session_db_row", lambda s: True)
        if isinstance(failure, Exception):
            raise failure
        return failure
    monkeypatch.setattr(server, "_ensure_session_db_row", persist)
    response = submit("unsaved")
    assert "error" in response
    assert starts == ["queued"]
    assert not any(e == "message.complete" for e, _ in events)


def test_storage_unavailable_does_not_leave_phantom_worker(env, monkeypatch):
    session, events, workers, starts, submit = env
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda s: False)
    assert submit("unsaved")["error"]["code"] == 5072
    assert session["running"] is False
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda s: True)
    assert submit("retry")["result"]["status"] == "streaming"
    workers.pop(0)()
    assert starts == ["retry"]


# Exercise the real dispatcher through admission, stopping before its worker runs.
_REAL_RUN_PROMPT = server._run_prompt_submit


def test_loop_emits_one_start_from_real_dispatcher(env, monkeypatch):
    import hermes_cli.loops as loops
    session, events, workers, starts, submit = env
    mgr = Mock()
    mgr.is_due.return_value = True
    mgr.fire_tick.return_value = "loop prompt"
    monkeypatch.setattr(loops, "LoopManager", lambda **kw: mgr)
    monkeypatch.setattr(loops, "goal_blocks_loop_tick", lambda key: False)
    monkeypatch.setattr(server, "_run_prompt_submit", _REAL_RUN_PROMPT)
    server._maybe_fire_tui_loop_tick("lifecycle", session)
    assert sum(e == "message.start" for e, p in events) == 1


def test_cancelled_lazy_queue_settles_without_start(env, monkeypatch):
    session, events, workers, starts, submit = env
    session["agent"] = None
    session["queued_prompt"] = {"text": "queued", "transport": None}
    def wait(*args):
        session["_turn_cancel_requested"] = True
    monkeypatch.setattr(server, "_wait_agent_for_prompt", wait)
    server._drain_queued_prompt("r", "lifecycle", session)
    workers.pop(0)()
    assert starts == []
    assert session["running"] is False
    assert any(e == "session.info" and p["running"] is False for e, p in events)


@pytest.mark.parametrize("blocked", ["_turn_settling", "_closing"])
def test_compute_worker_uses_shared_admission(env, monkeypatch, blocked):
    from tui_gateway.compute_host import ComputeHost
    session, events, workers, starts, submit = env
    session[blocked] = True
    host = ComputeHost.__new__(ComputeHost)
    frames = []
    host._ensure_server_session = lambda *args: session
    host._bump_progress = lambda: None
    def emit(frame):
        assert session["history_lock"].acquire(blocking=False)
        session["history_lock"].release()
        frames.append(frame)
    host.emit = emit
    host._run_real_turn({"sid": "lifecycle", "request_id": "r", "text": "host prompt"})
    assert starts == []
    assert not any(f["type"] == "turn.started" for f in frames)
    assert session[blocked] is True
    assert session["running"] is False


def test_compute_worker_dispatch_error_settles_once(env, monkeypatch):
    from tui_gateway.compute_host import ComputeHost
    session, events, workers, starts, submit = env
    host = ComputeHost.__new__(ComputeHost)
    frames = []
    host._ensure_server_session = lambda *args: session
    host._bump_progress = lambda: None
    host.emit = frames.append
    monkeypatch.setattr(server, "_run_prompt_submit", Mock(side_effect=RuntimeError("dispatch broke")))
    host._run_real_turn({"sid": "lifecycle", "request_id": "r", "text": "host prompt"})
    assert [e for e, p in events] == ["message.complete", "session.info"]
    assert session["running"] is False
    assert frames[-1]["message_complete_emitted"] is True
    assert frames[-1]["session_info_emitted"] is True


def test_storage_failure_still_publishes_idle_when_metadata_unavailable(env, monkeypatch):
    session, events, workers, starts, submit = env
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda s: False)
    monkeypatch.setattr(server, "_session_info", Mock(side_effect=OSError("metadata unavailable")))
    assert submit("unsaved")["error"]["code"] == 5072
    assert session["running"] is False
    assert not session.get("_turn_settling")
    assert events[-1] == ("session.info", {"running": False})


@pytest.mark.parametrize("kind", ["exec", "send"])
def test_slash_loop_preserves_idle_command_contract(env, monkeypatch, kind):
    import hermes_cli.loops as loops
    session, events, workers, starts, submit = env
    mgr = Mock()
    mgr.is_due.return_value = True
    mgr.fire_tick.return_value = "/example"
    mgr.complete_tick.return_value = {}
    monkeypatch.setattr(loops, "LoopManager", lambda **kw: mgr)
    monkeypatch.setattr(loops, "goal_blocks_loop_tick", lambda key: False)
    observed = []
    def command(*args):
        observed.append(session["running"])
        return {"result": {"type": kind, "message": "resolved prompt"}}
    monkeypatch.setitem(server._methods, "command.dispatch", command)
    server._maybe_fire_tui_loop_tick("lifecycle", session)
    assert observed == [False]
    assert starts == (["resolved prompt"] if kind == "send" else [])


_REAL_ENSURE_ROW = server._ensure_session_db_row


def test_queued_first_message_persists_after_failed_admission(env, monkeypatch, tmp_path):
    from hermes_state import SessionDB
    session, events, workers, starts, submit = env
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_resolve_model", lambda: "test-model")
    attempts = []
    def ensure(s):
        attempts.append(True)
        if len(attempts) == 1:
            assert submit("queued")["result"]["status"] == "queued"
            return False
        return _REAL_ENSURE_ROW(s)
    monkeypatch.setattr(server, "_ensure_session_db_row", ensure)
    def run(*args, **kwargs):
        assert db.get_session("lifecycle") is not None
        starts.append(args[3])
    monkeypatch.setattr(server, "_run_prompt_submit", run)
    try:
        assert submit("unsaved")["error"]["code"] == 5072
        assert starts == ["queued"]
        assert len(attempts) == 2
    finally:
        db.close()


def test_accepted_queue_reports_persistent_storage_failure(env, monkeypatch):
    session, events, workers, starts, submit = env
    def fail_first(s):
        assert submit("queued")["result"]["status"] == "queued"
        monkeypatch.setattr(server, "_ensure_session_db_row", lambda s: False)
        return False
    monkeypatch.setattr(server, "_ensure_session_db_row", fail_first)
    assert submit("unsaved")["error"]["code"] == 5072
    assert starts == []
    assert session["running"] is False
    assert session["inflight_turn"]["user"] == "queued"
    assert sum(e == "message.complete" for e, p in events) == 1


def test_parent_relay_withholds_worker_terminal_and_owns_queue(env, monkeypatch, tmp_path):
    from tui_gateway.host_supervisor import HostSupervisor
    session, events, workers, starts, submit = env
    supervisor = HostSupervisor(registry_path=tmp_path / 'host.json', autostart=False,
                                rpc_sink=server._relay_compute_host_rpc)
    frames = []
    monkeypatch.setattr(supervisor, 'start', lambda: None)
    monkeypatch.setattr(supervisor, '_send_frame', frames.append)
    monkeypatch.setattr(server, '_get_compute_host_supervisor', lambda *a: supervisor)
    monkeypatch.setattr(server, '_session_uses_compute_host', lambda *a: True)
    monkeypatch.setattr(server, '_load_busy_input_mode', lambda: 'interrupt')
    monkeypatch.setattr(server, 'write_json', lambda m: events.append((m['params']['type'], m['params'].get('payload'))) or True)
    assert submit('first')['result']['status'] == 'streaming'
    first = frames[0]
    def relay(kind, payload):
        supervisor._handle_host_frame({'type': 'rpc', 'message': {'method': 'event', 'params': {
            'session_id': 'lifecycle', 'type': kind, 'payload': payload,
            'compute_turn_id': first['request_id']}}})
    relay('message.complete', {'text': 'done'})
    relay('session.info', {'running': False})
    assert events == []
    assert submit('user wins')['result']['status'] == 'queued'
    assert workers == []  # no interrupt thread in the old terminal window
    assert session.get('_queued_prompt_generation', 0) == 0
    supervisor._handle_host_frame({'type': 'turn.end', 'request_id': first['request_id'],
                                  'session_info_emitted': True, 'followup': 'goal'})
    assert [e for e, p in events] == ['message.complete', 'session.info']
    assert events[-1][1]['pending_turn'] is True
    assert [f['text'] for f in frames] == ['first', 'user wins']
    assert frames[1]['request_id'] != first['request_id']
    assert session['running'] is True
    # A stale completion must not settle the running successor.
    supervisor._handle_host_frame({'type': 'turn.end', 'request_id': first['request_id']})
    assert len(events) == 2
    assert session['running'] is True


def test_stop_then_queue_lazy_ready_resets_only_old_cancellation(env, monkeypatch):
    session, events, workers, starts, submit = env
    session['running'] = True
    session['_turn_claimed'] = True
    session['agent'] = None
    server._interrupt_session_turn('lifecycle', session)
    assert session['_turn_cancel_requested'] is True
    assert submit('after Stop')['result']['status'] == 'queued'
    server._settle_turn('old', 'lifecycle', session)
    while workers:
        workers.pop(0)()
    assert starts == ['after Stop']


@pytest.mark.parametrize("parent_generation", [0, 8])
def test_worker_parent_admission_overrides_local_cancellation_and_autonomy(env, monkeypatch, parent_generation):
    from tui_gateway.compute_host import ComputeHost
    session, events, workers, starts, submit = env
    host = ComputeHost.__new__(ComputeHost)
    host._ensure_server_session = lambda *a: session
    host._bump_progress = lambda: None
    frames = []
    host.emit = frames.append
    session['_queued_prompt_generation'] = 8  # worker Stop is not parent's queue authority
    def run(rid, sid, s, text, **kw):
        starts.append(text)
        server._settle_turn(rid, sid, s, drain=False)
        with s['history_lock']:
            assert not server._claim_turn_locked(s, autonomous=True)
    monkeypatch.setattr(server, '_run_prompt_submit', run)
    host._run_real_turn({'sid': 'lifecycle', 'request_id': 'parent-accepted',
                         'text': 'user', 'queued_prompt_generation': parent_generation})
    assert starts == ['user']
    assert frames[-1]['type'] == 'turn.end'


def test_real_worker_goal_is_a_parent_proposal_not_a_second_turn(env, monkeypatch):
    from tui_gateway.compute_host import ComputeHost, _HostTransport
    session, events, workers, starts, submit = env
    frames = []

    class InlineThread:
        def __init__(self, target, args=(), kwargs=None, **kw):
            self.target = lambda: target(*args, **(kwargs or {}))
        def start(self):
            self.target()
        def join(self, *a):
            pass
        def is_alive(self):
            return False

    def conversation(text, **kwargs):
        starts.append(text)
        return {'final_response': 'done', 'messages': [
            {'role': 'user', 'content': text}, {'role': 'assistant', 'content': 'done'}]}

    session['agent'] = SimpleNamespace(session_id='lifecycle', run_conversation=conversation,
                                       clear_interrupt=lambda: None)
    transport = _HostTransport(frames.append)
    session['transport'] = transport
    monkeypatch.setattr(server.threading, 'Thread', InlineThread)
    monkeypatch.setattr(server, '_run_prompt_submit', _REAL_RUN_PROMPT)
    monkeypatch.setattr(server, '_wire_callbacks', lambda *a: None)
    monkeypatch.setattr(server, '_sync_agent_model_with_config', lambda *a: None)
    monkeypatch.setattr(server, '_tts_stream_begin', lambda: None)
    monkeypatch.setattr(server, '_get_usage', lambda *a: {})
    monkeypatch.setattr(server, '_plan_goal_compression_recovery', lambda *a, **kw: ('goal successor', None))
    monkeypatch.setattr(server, '_emit', lambda kind, sid, payload=None: transport.write({
        'method': 'event', 'params': {'type': kind, 'session_id': sid, 'payload': payload}}))
    host = ComputeHost.__new__(ComputeHost)
    host._ensure_server_session = lambda *a: session
    host._bump_progress = lambda: None
    host.emit = frames.append
    host._run_real_turn({'sid': 'lifecycle', 'request_id': 'parent-turn', 'text': 'user'})
    assert starts == ['user']
    assert frames[-1]['type'] == 'turn.end'
    assert frames[-1]['followup'] == 'goal successor'
    relayed = [f['message']['params'] for f in frames if f['type'] == 'rpc']
    assert all(p['compute_turn_id'] == 'parent-turn' for p in relayed)
    assert [p['type'] for p in relayed if p['type'] in {'message.start', 'message.complete', 'session.info'}] == [
        'message.start', 'message.complete', 'session.info']


def test_parent_stop_cancels_before_transport_and_targets_only_old_turn(env, monkeypatch):
    session, events, workers, starts, submit = env
    session.update(running=True, _turn_claimed=True, _compute_turn_id='old', _compute_host_active=True)
    monkeypatch.setattr(server, '_session_uses_compute_host', lambda *a: True)
    calls = []
    def interrupt(sid, **kwargs):
        assert session['history_lock'].acquire(blocking=False)
        session['history_lock'].release()
        calls.append(kwargs)
        assert session['_turn_cancel_requested'] is True
        assert submit('after Stop')['result']['status'] == 'queued'
    monkeypatch.setattr(server, '_get_compute_host_supervisor', lambda: SimpleNamespace(interrupt=interrupt))
    server._interrupt_session_turn('lifecycle', session)
    assert calls == [{'request_id': None, 'target_turn_id': 'old'}]
    assert session['queued_prompt']['text'] == 'after Stop'


def test_worker_ignores_delayed_interrupt_for_previous_turn(env, monkeypatch):
    from tui_gateway.compute_host import ComputeHost
    session, events, workers, starts, submit = env
    interrupt = Mock()
    session.update(running=True, _turn_claimed=True, _compute_turn_id='successor',
                   agent=SimpleNamespace(interrupt=interrupt))
    host = ComputeHost.__new__(ComputeHost)
    host._sessions = {}
    frames = []
    host.emit = frames.append
    host._handle_interrupt({'sid': 'lifecycle', 'target_turn_id': 'old'})
    interrupt.assert_not_called()
    assert not session.get('_turn_cancel_requested')
    host._handle_interrupt({'sid': 'lifecycle', 'target_turn_id': 'successor'})
    interrupt.assert_called_once()
    assert session['_turn_cancel_requested'] is True


def test_stop_during_worker_construction_cancels_claim_before_conversation(env, monkeypatch):
    from tui_gateway.compute_host import ComputeHost
    session, events, workers, starts, submit = env
    host = ComputeHost.__new__(ComputeHost)
    host._sessions = {}
    host._turn_futures_lock = threading.Lock()
    host._pending_real_turns = {'r': {'sid': 'lifecycle', 'cancelled': False}}
    frames = []
    host.emit = frames.append
    host._bump_progress = lambda: None
    def construct(*args):
        server._sessions.pop('lifecycle')  # agent/session construction hasn't published yet
        host._handle_interrupt({'sid': 'lifecycle', 'target_turn_id': 'r'})
        server._sessions['lifecycle'] = session
        return session
    host._ensure_server_session = construct
    host._run_real_turn({'sid': 'lifecycle', 'request_id': 'r', 'text': 'cancelled'})
    assert starts == []
    assert frames[-1]['type'] in {'turn.error', 'turn.end'}


def test_audit_first_worker_initialization_idle_is_not_terminal(env, monkeypatch, tmp_path):
    from tui_gateway.host_supervisor import HostSupervisor
    session, events, workers, starts, submit = env
    supervisor = HostSupervisor(registry_path=tmp_path / "host.json", autostart=False,
                                rpc_sink=server._relay_compute_host_rpc)
    frames = []
    monkeypatch.setattr(supervisor, "start", lambda: None)
    monkeypatch.setattr(supervisor, "_send_frame", frames.append)
    monkeypatch.setattr(server, "_get_compute_host_supervisor", lambda *a: supervisor)
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *a: True)
    monkeypatch.setattr(server, "_load_busy_input_mode", lambda: "interrupt")
    monkeypatch.setattr(server, "write_json", lambda m: events.append(m) or True)
    assert submit("first")["result"]["status"] == "streaming"
    supervisor._handle_host_frame({"type": "rpc", "message": {"method": "event", "params": {
        "type": "session.info", "session_id": "lifecycle", "payload": {"running": False}}}})
    assert not session.get("_turn_settling")
    assert events == []  # initialization must not tell Desktop the active turn is idle
    submit("correction")
    assert workers  # configured interrupt path, not terminal-window queuing


@pytest.mark.parametrize("during_publish", [True, False])
@pytest.mark.parametrize("work", ["queued_prompt", "_compute_followup"])
def test_audit_stop_reconciles_advertised_pending(env, monkeypatch, during_publish, work):
    session, events, workers, starts, submit = env
    session["running"] = True
    session[work] = {"text": "cancel me", "transport": None} if work == "queued_prompt" else "goal"
    def emit(kind, sid, payload=None):
        assert session["history_lock"].acquire(blocking=False)
        session["history_lock"].release()
        events.append((kind, dict(payload or {})))
        if during_publish and kind == "session.info" and payload.get("pending_turn"):
            server._interrupt_session_turn(sid, session)
    monkeypatch.setattr(server, "_emit", emit)
    server._emit_terminal_turn_error("lifecycle", session, "done")
    server._settle_turn("old", "lifecycle", session, drain=False)
    if not during_publish:
        server._interrupt_session_turn("lifecycle", session)
    assert starts == []
    assert [e for e, p in events if e == "message.complete"] == ["message.complete"]
    idle = [p for e, p in events if e == "session.info"]
    assert idle[0]["pending_turn"] is True
    assert idle[-1].get("pending_turn") is False
    assert idle[-1]["running"] is False
    assert len(idle) == 2


@pytest.mark.parametrize("dispatch_fails", [False, True])
@pytest.mark.parametrize("durable", [False, True])
@pytest.mark.parametrize("acceptance_failure", [None, "receipt", "commit"])
def test_audit_worker_notification_exports_without_autonomous_admission(
    env, monkeypatch, tmp_path, dispatch_fails, durable, acceptance_failure,
):
    from tui_gateway.compute_host import ComputeHost, _HostTransport
    session, events, workers, starts, submit = env
    frames = []
    import queue
    from tools.process_registry import process_registry
    monkeypatch.setattr(process_registry, "completion_queue", queue.Queue())
    notification = {"type": "completion", "session_id": "audit-process",
                    "session_key": "lifecycle", "exit_code": 0, "output": "finished"}
    from tools import async_delegation
    if durable:
        notification.update(type="async_delegation", delegation_id="audit-notification",
                            status="completed", result={"final_response": "finished"})
        async_delegation._persist_dispatch({
            **notification, "dispatched_at": time.time(), "origin_ui_session_id": "lifecycle"})
        async_delegation._persist_completion(notification, notification["result"])
    process_registry.completion_queue.put(notification)

    class InlineThread:
        def __init__(self, target, args=(), kwargs=None, **kw):
            self.target = lambda: target(*args, **(kwargs or {}))
        def start(self):
            self.target()
        def join(self, *a):
            pass
        def is_alive(self):
            return False

    def conversation(text, **kwargs):
        starts.append(text)
        return {'final_response': 'done', 'messages': [
            {'role': 'user', 'content': text}, {'role': 'assistant', 'content': 'done'}]}

    session['agent'] = SimpleNamespace(session_id='lifecycle', run_conversation=conversation,
                                       clear_interrupt=lambda: None)
    transport = _HostTransport(frames.append)
    session['transport'] = transport
    monkeypatch.setattr(server.threading, 'Thread', InlineThread)
    monkeypatch.setattr(server, '_run_prompt_submit', _REAL_RUN_PROMPT)
    monkeypatch.setattr(server, '_wire_callbacks', lambda *a: None)
    monkeypatch.setattr(server, '_sync_agent_model_with_config', lambda *a: None)
    monkeypatch.setattr(server, '_tts_stream_begin', lambda: None)
    monkeypatch.setattr(server, '_get_usage', lambda *a: {})
    monkeypatch.setattr(server, '_plan_goal_compression_recovery', lambda *a, **kw: (None, None))
    monkeypatch.setattr(server, '_emit', lambda kind, sid, payload=None: transport.write({
        'method': 'event', 'params': {'type': kind, 'session_id': sid, 'payload': payload}}))
    host = ComputeHost.__new__(ComputeHost)
    host._ensure_server_session = lambda *a: session
    host._bump_progress = lambda: None
    host.emit = frames.append
    host._run_real_turn({'sid': 'lifecycle', 'request_id': 'parent-turn', 'text': 'user'})
    assert starts == ['user']
    assert frames[-1]['type'] == 'turn.end'

    proposals = [f["message"]["params"]["payload"] for f in frames
                 if f["type"] == "rpc" and f["message"]["params"]["type"] == "compute.notification"]
    assert len(proposals) == 1
    assert proposals[0]["event"] == notification
    assert proposals[0]["text"]
    assert proposals[0]["id"]

    # Separate process-local session maps while exercising the actual supervisor
    # callback/transport seam. Parent already owns a queued user.
    from tui_gateway.host_supervisor import HostSupervisor
    worker_session = session
    parent = dict(agent=SimpleNamespace(), session_key="lifecycle", history=[],
                  history_version=0, history_lock=threading.Lock(), running=True,
                  attached_images=[], cwd=str(tmp_path), transport=None,
                  _compute_turn_id="old", _turn_claimed=True,
                  queued_prompt={"text": "parent user", "transport": None})
    monkeypatch.setitem(server._sessions, "lifecycle", parent)
    outgoing = []
    supervisor = HostSupervisor(registry_path=tmp_path / "host.json", autostart=False,
                                rpc_sink=server._relay_compute_host_rpc)
    monkeypatch.setattr(supervisor, "start", lambda: None)
    def send_frame(frame):
        assert parent["history_lock"].acquire(blocking=False)
        parent["history_lock"].release()
        if dispatch_fails and frame.get("notification_id") and not outgoing[-1].get("failed"):
            outgoing.append({"failed": True})
            raise OSError("injected pipe failure")
        outgoing.append(frame)
    monkeypatch.setattr(supervisor, "_send_frame", send_frame)
    monkeypatch.setattr(server, "_get_compute_host_supervisor", lambda *a: supervisor)
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *a: True)
    monkeypatch.setattr(server, "_emit", lambda *a: None)
    proposal_frame = next(f for f in frames if f["type"] == "rpc"
                          and f["message"]["params"]["type"] == "compute.notification")
    supervisor._handle_host_frame(proposal_frame)
    assert outgoing == []
    parent["_compute_terminal"] = {"text": "done"}
    server._on_compute_host_turn_done("old", "lifecycle", parent, {"request_id": "old"})
    assert outgoing[0]["text"] == "parent user"
    supervisor._handle_host_frame({"type": "turn.end", "request_id": outgoing[0]["request_id"]})
    if dispatch_fails:
        assert outgoing[-1] == {"failed": True}
        supervisor._handle_host_frame(proposal_frame)  # worker retries unacknowledged proposal
    admitted = outgoing[-1]
    assert admitted["notification_id"] == proposals[0]["id"]
    assert admitted["text"] == proposals[0]["text"]
    # Parent dispatch acceptance alone is not delivery: worker still owns it.
    supervisor._handle_host_frame({"type": "rpc", "message": {"method": "event", "params": {
        "type": "compute.notification.accepted", "session_id": "lifecycle",
        "payload": {"id": proposals[0]["id"], "turn_id": "not-admitted"}}}})
    assert proposals[0]["id"] in parent["_compute_notification_proposals"]
    if durable:
        record = async_delegation.get_durable_delegation("audit-notification")
        assert record["delivery_state"] == "pending"
        assert record["delivery_attempts"] == 0
    monkeypatch.setitem(server._sessions, "lifecycle", worker_session)
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *a: False)
    receipt_attempts = []
    def worker_emit(kind, sid, payload=None):
        assert worker_session["history_lock"].acquire(blocking=False)
        worker_session["history_lock"].release()
        if kind == "compute.notification.accepted":
            receipt_attempts.append(True)
            if acceptance_failure == "receipt" and len(receipt_attempts) == 1:
                raise OSError("injected receipt write failure")
        return transport.write({
            "method": "event", "params": {"type": kind, "session_id": sid, "payload": payload}})
    monkeypatch.setattr(server, "_emit", worker_emit)
    commit = server._commit_compute_notification
    commit_attempts = []
    def commit_with_failure(*args):
        commit_attempts.append(True)
        if acceptance_failure == "commit" and len(commit_attempts) == 1:
            raise OSError("injected delivery commit failure")
        return commit(*args)
    monkeypatch.setattr(server, "_commit_compute_notification", commit_with_failure)
    before = len(frames)
    host._run_real_turn(admitted)
    assert frames[-1]["type"] == "turn.end"
    if acceptance_failure:
        server._export_compute_notifications("lifecycle", worker_session)
    assert starts == ["user", proposals[0]["text"]]
    acknowledgements = [f for f in frames[before:] if f["type"] == "rpc"
                        and f["message"]["params"]["type"] == "compute.notification.accepted"]
    assert len(acknowledgements) == 1
    if durable:
        record = async_delegation.get_durable_delegation("audit-notification")
        assert record["delivery_state"] == "delivered"
        assert record["delivery_attempts"] == 1
    monkeypatch.setitem(server._sessions, "lifecycle", parent)
    monkeypatch.setattr(server, "_emit", lambda *a: None)
    for frame in frames[before:]:
        supervisor._handle_host_frame(frame)
    count = len(outgoing)
    supervisor._handle_host_frame(proposal_frame)
    assert len(outgoing) == count

    # Lost confirmations are retried, then retire the worker's receipt without
    # admitting or injecting another notification.
    receipt_confirmation = next(f for f in outgoing if f.get("type") == "notification.received")
    monkeypatch.setitem(server._sessions, "lifecycle", worker_session)
    host.handle_frame({**receipt_confirmation, "turn_id": "stale"})
    assert worker_session["_compute_notification_receipts"]
    host.handle_frame(receipt_confirmation)
    assert not worker_session["_compute_notification_receipts"]


def test_audit_worker_poller_exports_between_turns_and_preserves_foreign_owner(env, monkeypatch):
    import queue
    from tools.process_registry import process_registry
    session, events, workers, starts, submit = env
    session["_parent_owned_turns"] = True
    monkeypatch.setattr(process_registry, "completion_queue", queue.Queue())
    owned = {"type": "completion", "session_id": "ours", "session_key": "lifecycle",
             "exit_code": 0, "output": "done"}
    foreign = {**owned, "session_id": "foreign", "session_key": "someone-else"}
    process_registry.completion_queue.put(owned)
    process_registry.completion_queue.put(foreign)
    monkeypatch.setattr(server, "_maybe_fire_tui_loop_tick", lambda *a: None)
    monkeypatch.setattr(server, "_retry_tui_light_slot_registration", lambda *a: None)
    monkeypatch.setattr(server, "_collect_kanban_notifications", lambda *a: [])
    class OnePoll:
        stopped = False
        def is_set(self):
            return self.stopped
        def wait(self, timeout):
            self.stopped = True
    server._notification_poller_loop(OnePoll(), "lifecycle", session)
    proposals = [p for e, p in events if e == "compute.notification"]
    assert len(proposals) == 2  # loop + shutdown retry, same proposal identity
    assert proposals[0] == proposals[1]
    assert proposals[0]["event"] == owned
    assert process_registry.completion_queue.get_nowait() == foreign
    assert starts == []
    assert not session["running"]
