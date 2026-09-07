"""Global emergency stop (`hermes pause` / `hermes resume`) — agent/estop.py.

The ESTOP sentinel is a resumable pause for NEW work only: cron dispatch,
kanban dispatch, and new gateway turns are halted while it is engaged; work
already in flight is never touched. Removing the sentinel (`hermes resume`)
restores normal operation with no restart.

Ported from: gastownhall/gastown estop.go (MIT); related prior art: #26778
(/panic — kill/exit semantics, deliberately different) and #44617
(interrupt in-flight cron — deliberately NOT done here).
"""

from __future__ import annotations

import argparse
import json
import logging

import pytest

from agent import estop


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Point HERMES_HOME at a temp dir and reset estop module log state."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    estop._reset_log_state_for_tests()
    return tmp_path


# ── sentinel create / remove ────────────────────────────────────────────────


def test_engage_creates_sentinel_and_is_engaged(hermes_home):
    assert estop.is_engaged() is False
    estop.engage()
    assert (hermes_home / "ESTOP").exists()
    assert estop.is_engaged() is True


def test_disengage_removes_sentinel(hermes_home):
    estop.engage()
    assert estop.disengage() is True
    assert not (hermes_home / "ESTOP").exists()
    assert estop.is_engaged() is False
    # Disengaging when not engaged is a no-op that reports False.
    assert estop.disengage() is False


def test_reason_and_timestamp_stored(hermes_home):
    estop.engage(reason="runaway cron fan-out")
    state = estop.get_state()
    assert state is not None
    assert state["reason"] == "runaway cron fan-out"
    assert state["engaged_at"]  # ISO timestamp string

    raw = json.loads((hermes_home / "ESTOP").read_text(encoding="utf-8"))
    assert raw["reason"] == "runaway cron fan-out"


def test_get_state_none_when_disengaged(hermes_home):
    assert estop.get_state() is None


def test_corrupt_sentinel_still_engages(hermes_home):
    """A hand-touched/corrupt ESTOP file must still pause (fail safe)."""
    (hermes_home / "ESTOP").write_text("not json", encoding="utf-8")
    assert estop.is_engaged() is True
    state = estop.get_state()
    assert state is not None
    assert state.get("reason") is None


# ── paused notice for new gateway turns ─────────────────────────────────────


def test_paused_reply_none_when_disengaged(hermes_home):
    assert estop.paused_reply() is None


def test_paused_reply_surfaces_reason_and_resume_hint(hermes_home):
    estop.engage(reason="deploy window")
    notice = estop.paused_reply()
    assert notice is not None
    assert "paused" in notice.lower()
    assert "deploy window" in notice
    assert "hermes resume" in notice


def test_paused_reply_without_reason(hermes_home):
    estop.engage()
    notice = estop.paused_reply()
    assert notice is not None
    assert "paused" in notice.lower()
    assert "hermes resume" in notice


# ── check_paused: cheap gate + log-once ─────────────────────────────────────


def test_check_paused_logs_once_per_engagement(hermes_home, caplog):
    logger = logging.getLogger("test.estop.component")
    estop.engage()
    with caplog.at_level(logging.INFO, logger=logger.name):
        assert estop.check_paused("cron", logger) is True
        assert estop.check_paused("cron", logger) is True
        assert estop.check_paused("cron", logger) is True
    paused_logs = [r for r in caplog.records if "paused" in r.getMessage().lower()]
    assert len(paused_logs) == 1

    # Resume then re-engage → logs once more (transition-based, not forever).
    caplog.clear()
    estop.disengage()
    with caplog.at_level(logging.INFO, logger=logger.name):
        assert estop.check_paused("cron", logger) is False
        estop.engage()
        assert estop.check_paused("cron", logger) is True
        assert estop.check_paused("cron", logger) is True
    paused_logs = [r for r in caplog.records if "paused" in r.getMessage().lower()]
    assert len(paused_logs) == 1


# ── cron scheduler integration ──────────────────────────────────────────────


def test_cron_tick_skips_dispatch_when_engaged(hermes_home, monkeypatch):
    from cron import scheduler

    calls = []

    def _fake_get_due_jobs():
        calls.append(1)
        return []

    monkeypatch.setattr(scheduler, "get_due_jobs", _fake_get_due_jobs)

    estop.engage(reason="test")
    assert scheduler.tick(verbose=False) == 0
    assert calls == [], "engaged ESTOP must skip the due-job scan entirely"


def test_cron_tick_resumes_after_disengage(hermes_home, monkeypatch):
    from cron import scheduler

    calls = []

    def _fake_get_due_jobs():
        calls.append(1)
        return []

    monkeypatch.setattr(scheduler, "get_due_jobs", _fake_get_due_jobs)

    estop.engage()
    scheduler.tick(verbose=False)
    assert calls == []

    estop.disengage()
    scheduler.tick(verbose=False)
    assert calls == [1], "resume must restore normal cron dispatch"


# ── kanban dispatcher integration ───────────────────────────────────────────


def test_kanban_dispatch_blocked_when_engaged(hermes_home):
    from gateway.kanban_watchers import _kanban_dispatch_allowed

    assert _kanban_dispatch_allowed() is True
    estop.engage(reason="test")
    assert _kanban_dispatch_allowed() is False
    estop.disengage()
    assert _kanban_dispatch_allowed() is True


def test_kanban_dispatch_uses_the_assignee_profiles_estop(hermes_home):
    """A shared Kanban board must obey the selected worker profile's pause."""
    from gateway.kanban_watchers import _kanban_dispatch_allowed

    worker_home = hermes_home / "profiles" / "worker"
    worker_home.mkdir(parents=True)
    estop.engage(reason="worker maintenance", home=worker_home)

    assert _kanban_dispatch_allowed("default") is True
    assert _kanban_dispatch_allowed("worker") is False


# ── gateway turn-start integration ──────────────────────────────────────────


class _FakeSource:
    platform = None
    chat_id = "c1"
    user_id = "u1"
    user_name = "user"
    chat_type = "dm"
    profile = None


class _FakeEvent:
    internal = False
    text = "hello"

    def __init__(self):
        self.source = _FakeSource()


@pytest.mark.asyncio
async def test_gateway_new_turn_gets_paused_reply(hermes_home):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._is_user_authorized = lambda source: True  # bare-instance stub
    estop.engage(reason="maintenance")
    reply = await runner._handle_message(_FakeEvent())
    assert reply is not None
    assert "paused" in reply.lower()
    assert "maintenance" in reply


@pytest.mark.asyncio
async def test_gateway_internal_events_bypass_estop(hermes_home):
    """Internal events (in-flight work completions) must NOT be paused."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    estop.engage()
    event = _FakeEvent()
    event.internal = True
    # An internal event proceeds past the estop gate; the bare runner then
    # blows up further down the pipeline on missing attributes — that error
    # (anything but a paused reply) proves the gate let it through.
    try:
        reply = await runner._handle_message(event)
    except Exception:
        return
    assert reply is None or "paused" not in (reply or "").lower()


# ── CLI: hermes pause / hermes resume ───────────────────────────────────────


def test_cli_pause_engages_with_reason(hermes_home, capsys):
    from hermes_cli.subcommands.pause import cmd_pause

    rc = cmd_pause(argparse.Namespace(reason="ops incident"))
    assert rc == 0
    assert estop.is_engaged() is True
    assert estop.get_state()["reason"] == "ops incident"
    assert "paused" in capsys.readouterr().out.lower()


def test_cli_pause_idempotent(hermes_home, capsys):
    from hermes_cli.subcommands.pause import cmd_pause

    assert cmd_pause(argparse.Namespace(reason=None)) == 0
    assert cmd_pause(argparse.Namespace(reason=None)) == 0
    assert estop.is_engaged() is True


def test_cli_resume_disengages(hermes_home, capsys):
    from hermes_cli.subcommands.pause import cmd_pause, cmd_resume

    cmd_pause(argparse.Namespace(reason=None))
    rc = cmd_resume(argparse.Namespace())
    assert rc == 0
    assert estop.is_engaged() is False
    assert "resumed" in capsys.readouterr().out.lower()


def test_cli_resume_when_not_paused(hermes_home, capsys):
    from hermes_cli.subcommands.pause import cmd_resume

    rc = cmd_resume(argparse.Namespace())
    assert rc == 0
    assert "not paused" in capsys.readouterr().out.lower()


def test_builtin_subcommands_include_pause_resume():
    from hermes_cli.main import _BUILTIN_SUBCOMMANDS

    assert "pause" in _BUILTIN_SUBCOMMANDS
    assert "resume" in _BUILTIN_SUBCOMMANDS


# ── hermes status surfacing ─────────────────────────────────────────────────


def test_status_line_when_paused(hermes_home):
    from hermes_cli.status import _estop_status_line

    assert _estop_status_line() is None
    estop.engage(reason="ops")
    line = _estop_status_line()
    assert line is not None
    assert "paused" in line.lower()
    assert "ops" in line
    estop.disengage()
    assert _estop_status_line() is None


# ── post-merge audit fixes (#81148 follow-up) ───────────────────────────────


def test_is_engaged_fails_safe_on_stat_error(hermes_home, monkeypatch):
    """A stat failure must report ENGAGED (fail safe) — the pause has to
    hold even when HERMES_HOME is misbehaving, matching the module's
    corrupt-sentinel doctrine."""
    class _BoomPath:
        def exists(self):
            raise OSError("permission denied")

    monkeypatch.setattr(estop, "sentinel_path", lambda: _BoomPath())
    assert estop.is_engaged() is True


class _FakeCmdEvent(_FakeEvent):
    text = "/status"

    def get_command(self):
        return "status"

    def get_command_args(self):
        return ""


@pytest.mark.asyncio
async def test_gateway_slash_commands_bypass_estop(hermes_home):
    """Recognized slash commands must pass the estop gate — /pause off is
    the in-band resume path for messaging-only users, and /status, /help
    and friends must keep working while paused."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._is_user_authorized = lambda source: True
    estop.engage(reason="maintenance")
    # The command proceeds past the estop gate; the bare runner then blows
    # up further down on missing attributes — anything but the paused
    # notice proves the gate let it through.
    try:
        reply = await runner._handle_message(_FakeCmdEvent())
    except Exception:
        return
    assert reply is None or "hermes is paused" not in (reply or "").lower()


class _FakePauseEvent(_FakeEvent):
    def __init__(self, args=""):
        super().__init__()
        self._args = args
        self.text = f"/pause {args}".strip()

    def get_command(self):
        return "pause"

    def get_command_args(self):
        return self._args


@pytest.mark.asyncio
async def test_gateway_pause_command_engages_and_resumes(hermes_home):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)

    reply = await runner._handle_pause_command(_FakePauseEvent("deploy window"))
    assert "paused" in reply.lower()
    assert estop.is_engaged() is True
    assert estop.get_state()["reason"] == "deploy window"

    # Re-issuing without args reports already-paused instead of clobbering.
    reply = await runner._handle_pause_command(_FakePauseEvent(""))
    assert "already paused" in reply.lower()

    reply = await runner._handle_pause_command(_FakePauseEvent("off"))
    assert "resumed" in reply.lower()
    assert estop.is_engaged() is False

    reply = await runner._handle_pause_command(_FakePauseEvent("off"))
    assert "wasn't paused" in reply.lower()


def test_pause_command_registered_for_gateway():
    from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS, resolve_command

    cmd = resolve_command("pause")
    assert cmd is not None and cmd.name == "pause"
    assert "pause" in GATEWAY_KNOWN_COMMANDS
    # Must be dispatchable while an agent is running (in-band emergency stop).
    assert cmd.busy_policy == "dispatch"


def test_profile_gateway_honors_canonical_root_estop(tmp_path, monkeypatch):
    """fleet-analyst-class: HERMES_HOME is a profile dir; pause lives at root.

    A process launched with HERMES_HOME=~/.hermes/profiles/fleet-analyst must
    still treat ~/.hermes/ESTOP as engaged. Otherwise `hermes pause` is not
    a global emergency stop (t_7b65ff88).
    """
    root = tmp_path / "hermes-root"
    profile = root / "profiles" / "fleet-analyst"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    estop._reset_log_state_for_tests()

    assert estop.is_engaged() is False
    (root / "ESTOP").write_text("{\"reason\": \"thundering herd\"}\n", encoding="utf-8")
    assert estop.is_engaged() is True
    assert estop.paused_reply() is not None
    assert "paused" in estop.paused_reply().lower()
    # Profile-local engage still works and is independent.
    estop.engage(reason="local")
    assert (profile / "ESTOP").exists()
    assert estop.is_engaged() is True
    (root / "ESTOP").unlink()
    assert estop.is_engaged() is True  # still held by profile sentinel
    estop.disengage()
    assert estop.is_engaged() is False


# ── Blue/Green composition: per-profile pause + fleet-root sentinel ─────────


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A fleet root as HERMES_HOME with two profile homes beneath it."""
    root = tmp_path / "hermes-root"
    worker = root / "profiles" / "worker"
    analyst = root / "profiles" / "analyst"
    worker.mkdir(parents=True)
    analyst.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    estop._reset_log_state_for_tests()
    return root, worker, analyst


def test_fleet_root_sentinel_binds_every_profile_home(fleet):
    """Green's fleet-wide stop must bind Blue's home-parameterized checks."""
    root, worker, analyst = fleet

    assert estop.is_engaged(worker) is False
    assert estop.is_engaged(analyst) is False

    estop.engage(reason="fleet freeze")  # operator seat writes <root>/ESTOP
    assert (root / "ESTOP").exists()

    assert estop.is_engaged(worker) is True
    assert estop.is_engaged(analyst) is True
    assert estop.get_state(worker) == {
        "reason": "fleet freeze",
        "engaged_at": estop.get_state(worker)["engaged_at"],
    }
    assert estop.get_state(worker)["reason"] == "fleet freeze"


def test_profile_pause_does_not_bind_a_sibling_profile(fleet):
    """Blue's per-profile pause stays scoped when no fleet sentinel is set."""
    root, worker, analyst = fleet

    estop.engage(reason="worker maintenance", home=worker)
    assert (worker / "ESTOP").exists()
    assert not (root / "ESTOP").exists()

    assert estop.is_engaged(worker) is True
    assert estop.is_engaged(analyst) is False
    assert estop.get_state(analyst) is None
    assert estop.get_state(worker)["reason"] == "worker maintenance"


def test_profile_resume_never_lifts_the_fleet_root_pause(fleet):
    """A profile operator cannot clear a fleet-wide emergency stop."""
    root, worker, analyst = fleet

    estop.engage(reason="fleet freeze")
    estop.engage(reason="worker maintenance", home=worker)
    assert estop.is_engaged(worker) is True
    assert estop.is_engaged(analyst) is True

    assert estop.disengage(home=worker) is True
    assert not (worker / "ESTOP").exists()
    assert (root / "ESTOP").exists()
    assert estop.is_engaged(worker) is True
    assert estop.is_engaged(analyst) is True

    assert estop.disengage() is True
    assert not (root / "ESTOP").exists()
    assert estop.is_engaged(worker) is False
    assert estop.is_engaged(analyst) is False


def test_is_engaged_with_home_fails_safe_on_stat_error(fleet):
    """An unreadable profile sentinel holds the pause (fail closed)."""
    _root, worker, _analyst = fleet

    class _BoomPath:
        def exists(self):
            raise OSError("permission denied")

    original = estop.sentinel_path
    estop.sentinel_path = lambda home=None: (
        _BoomPath() if home is not None else original()
    )
    try:
        assert estop.is_engaged(worker) is True
    finally:
        estop.sentinel_path = original


def test_check_paused_with_home_honors_the_fleet_sentinel(fleet, caplog):
    """Dispatch loops scoped to a profile still stop on the fleet pause."""
    root, worker, _analyst = fleet
    logger = logging.getLogger("test.estop.composition")

    assert estop.check_paused("worker-dispatch", logger, home=worker) is False

    estop.engage(reason="fleet freeze")
    with caplog.at_level(logging.INFO, logger="test.estop.composition"):
        assert estop.check_paused("worker-dispatch", logger, home=worker) is True
    assert "fleet freeze" in caplog.text
