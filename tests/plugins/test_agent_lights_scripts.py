"""Agent Lights distribution scripts (apps/agent-lights-menu-bar).

Runs the real install/uninstall/package scripts and the pool tool against a
throwaway HOME, with process, launchd, and Hermes commands replaced by stubs
that only record their calls, so nothing touches the machine's real app,
login items, or Hermes config.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from agent.light_cues import SlotStatusFileBackend as CoreBackend

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_DIR = _REPO_ROOT / "apps" / "agent-lights-menu-bar"
_POOL_TOOL = _APP_DIR / "scripts" / "agent_lights_pool.py"


def _load_pool_tool():
    spec = importlib.util.spec_from_file_location("agent_lights_pool_under_test", _POOL_TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pool_tool = _load_pool_tool()


# -- pool tool --------------------------------------------------------------

_HOLD_SLOT_CHILD = """
import sys
from agent.light_cues import LightCueEvent, SlotStatusFileBackend
backend = SlotStatusFileBackend.from_env(auto_assign=True)
backend.emit_event(LightCueEvent.WORKING)
print(backend.slot, flush=True)
sys.stdin.read()
"""


def _spawn_slot_owner(home: Path) -> subprocess.Popen:
    env = {**os.environ, "HERMES_AGENT_LIGHTS_HOME": str(home), "PYTHONPATH": str(_REPO_ROOT)}
    env.pop("HERMES_SLOT", None)
    env.pop("HERMES_KANBAN_TASK", None)
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLD_SLOT_CHILD], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "1"
    return child


def test_purge_refuses_while_a_session_owns_a_slot_and_keeps_its_files(tmp_path, monkeypatch):
    """Hermes review P1: purging under a live session let two sessions share slot 1."""
    pool = tmp_path / "agent-lights" / "v2"
    owner = _spawn_slot_owner(tmp_path)
    try:
        before = sorted(p.name for p in (pool / "slots").iterdir())

        assert pool_tool.main(["check", str(pool)]) == pool_tool.EXIT_LIVE
        assert pool_tool.main(["purge", str(pool)]) == pool_tool.EXIT_LIVE
        assert sorted(p.name for p in (pool / "slots").iterdir()) == before

        # A second session still gets its own slot: ownership is intact.
        monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_SLOT", raising=False)
        monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
        second = CoreBackend.from_env(auto_assign=True)
        assert second.slot == 2
        second.clear_if_owned()
    finally:
        owner.stdin.close()
        owner.wait(timeout=10)


def test_purge_clears_a_dead_pool_but_keeps_the_claim_locks(tmp_path, monkeypatch):
    pool = tmp_path / "agent-lights" / "v2"
    owner = _spawn_slot_owner(tmp_path)
    owner.stdin.close()
    owner.wait(timeout=10)  # exits without cleanup, like a closed Terminal tab
    (pool / "agents").mkdir()
    (pool / "agents" / ".claim.lock").write_text("")
    (pool / "agents" / "3.json").write_text(json.dumps({"pid": 999_999, "state": "working"}))
    (pool / "context").mkdir(exist_ok=True)
    (pool / "context" / "1.json").write_text("{}")
    (pool / "app.json").write_text(json.dumps({"pid": 999_999, "pool": "v2"}))
    (pool / ".claim.lock").write_text("")
    slots_lock_inode = (pool / "slots" / ".claim.lock").stat().st_ino

    assert pool_tool.main(["purge", str(pool)]) == 0

    remaining = sorted(str(p.relative_to(pool)) for p in pool.rglob("*"))
    assert remaining == [".claim.lock", "agents", "agents/.claim.lock", "app.lock", "slots", "slots/.claim.lock"]
    assert (pool / "slots" / ".claim.lock").stat().st_ino == slots_lock_inode
    monkeypatch.setenv("HERMES_AGENT_LIGHTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SLOT", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    fresh = CoreBackend.from_env(auto_assign=True)
    assert fresh.slot == 1
    fresh.clear_if_owned()


def test_purge_waits_out_a_claim_in_progress_then_gives_up(tmp_path):
    pool = tmp_path / "agent-lights" / "v2"
    (pool / "slots").mkdir(parents=True)
    (pool / "slots" / "4.json").write_text(json.dumps({"pid": 999_999, "state": "idle"}))
    lock = os.open(pool / "slots" / ".claim.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert pool_tool.purge(pool, lock_timeout_s=0.2) == pool_tool.EXIT_BUSY
        assert (pool / "slots" / "4.json").exists()
    finally:
        os.close(lock)


_FIRST_WORKER_CHILD = """
import os
from agent.light_cues import SlotStatusFileBackend
backend = SlotStatusFileBackend.from_env(auto_assign=True)
print(backend.slot if backend else "none", flush=True)
os._exit(0)  # keep whatever it claimed, like a worker that is still running
"""


def test_a_first_kanban_worker_cannot_claim_between_the_scan_and_the_clear(tmp_path, monkeypatch):
    """Hermes review round 2, P1: with no agents/.claim.lock yet, purge locked
    nothing for that pool, so a first worker claimed slot 1 after the scan and
    purge then erased its claim."""
    pool = tmp_path / "agent-lights" / "v2"
    (pool / "slots").mkdir(parents=True)
    (pool / "slots" / ".claim.lock").write_text("")
    env = {**os.environ, "HERMES_AGENT_LIGHTS_HOME": str(tmp_path), "PYTHONPATH": str(_REPO_ROOT),
           "HERMES_KANBAN_TASK": "t_1"}
    env.pop("HERMES_SLOT", None)
    claimed_during_purge = []
    real_live_owners = pool_tool.live_owners

    def scan_then_race(scanned_pool):
        owners = real_live_owners(scanned_pool)
        # A worker starts right after the scan, before the clear.
        child = subprocess.run([sys.executable, "-c", _FIRST_WORKER_CHILD], env=env,
                               capture_output=True, text=True, timeout=60)
        claimed_during_purge.append(child.stdout.strip())
        return owners

    monkeypatch.setattr(pool_tool, "live_owners", scan_then_race)

    assert pool_tool.purge(pool) == 0
    assert claimed_during_purge == ["none"]  # blocked by purge, retries later
    # Afterwards a worker claims normally in the cleared pool.
    after = subprocess.run([sys.executable, "-c", _FIRST_WORKER_CHILD], env=env,
                           capture_output=True, text=True, timeout=60)
    assert after.stdout.strip() == "1"
    assert (pool / "agents" / "1.lock").exists()


def _hold_app_lock(pool: Path) -> int:
    """What the app does at startup, before it writes its heartbeat."""
    fd = os.open(pool / "app.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def test_purge_refuses_while_an_app_holds_its_instance_lock_before_its_heartbeat(tmp_path):
    """Hermes review round 2, P2: purge deleted a held app.lock, so a second
    app could lock a fresh file while the first still held the old one."""
    pool = tmp_path / "agent-lights" / "v2"
    (pool / "slots").mkdir(parents=True)
    (pool / "context").mkdir()
    (pool / "context" / "1.json").write_text("{}")
    fd = _hold_app_lock(pool)
    try:
        inode = (pool / "app.lock").stat().st_ino
        assert not (pool / "app.json").exists()

        assert pool_tool.main(["check", str(pool)]) == pool_tool.EXIT_LIVE
        assert pool_tool.main(["purge", str(pool)]) == pool_tool.EXIT_LIVE

        assert (pool / "app.lock").stat().st_ino == inode
        assert (pool / "context" / "1.json").exists()
        # The single-instance guarantee holds: a second app cannot lock it.
        with pytest.raises(BlockingIOError):
            os.close(_hold_app_lock(pool))
    finally:
        os.close(fd)


def test_purge_keeps_the_app_lock_file_so_a_later_app_locks_the_same_file(tmp_path):
    pool = tmp_path / "agent-lights" / "v2"
    pool.mkdir(parents=True)
    os.close(os.open(pool / "app.lock", os.O_RDWR | os.O_CREAT, 0o600))
    inode = (pool / "app.lock").stat().st_ino

    assert pool_tool.purge(pool) == 0

    assert (pool / "app.lock").stat().st_ino == inode


def test_check_and_purge_of_a_missing_pool_succeed(tmp_path):
    assert pool_tool.main(["check", str(tmp_path / "nope")]) == 0
    assert pool_tool.main(["purge", str(tmp_path / "nope")]) == 0


# -- scripts with stubs ----------------------------------------------------

_STUB = """#!/bin/bash
printf '%s' "$(basename "$0")" >> "{log}"
for arg in "$@"; do printf ' %s' "$arg" >> "{log}"; done
printf ' [HERMES_HOME=%s]\\n' "${{HERMES_HOME-unset}}" >> "{log}"
{body}
"""


def _sandbox(tmp_path: Path, *, app_running: bool = False) -> dict:
    home = tmp_path / "home"
    (home / ".hermes" / "profiles" / "work").mkdir(parents=True)
    # A sticky non-default profile: HERMES_HOME=<root> alone would follow it.
    (home / ".hermes" / "active_profile").write_text("work\n")
    shims = tmp_path / "shims"
    shims.mkdir()
    log = tmp_path / "calls.log"
    bodies = {
        "pgrep": "exit 0" if app_running else "exit 1",
        "pkill": "exit 0",
        "open": "exit 0",
        "launchctl": "exit 0",
        "tccutil": "exit 0",
        "defaults": "exit 0",
        "hermes": "exit 0",
    }
    for name, body in bodies.items():
        stub = shims / name
        stub.write_text(_STUB.format(log=log, body=body))
        stub.chmod(0o755)
    path = os.pathsep.join([str(shims), os.path.dirname(shutil.which("python3") or "/usr/bin/python3"),
                            "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    env = {"HOME": str(home), "PATH": path, "HERMES_HOME": str(home / ".hermes"),
           "TMPDIR": os.environ.get("TMPDIR", "/tmp")}
    return {"home": home, "env": env, "log": log}


def _calls(box: dict, command: str) -> list[str]:
    if not box["log"].exists():
        return []
    return [line for line in box["log"].read_text().splitlines() if line.split(" ", 1)[0] == command]


def _fake_install(home: Path) -> Path:
    app = home / "Applications" / "AgentLightsMenuBar-v2.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "AgentLightsMenuBar").write_bytes(b"app")
    shutil.copy(_APP_DIR / "Resources" / "Info.plist", app / "Contents" / "Info.plist")
    for plugins in (home / ".hermes" / "plugins", home / ".hermes" / "profiles" / "work" / "plugins"):
        shutil.copytree(_APP_DIR / "plugin" / "agent-lights", plugins / "agent-lights")
    return app


def _run(script: str, box: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(_APP_DIR / script), *args], env=box["env"],
        capture_output=True, text=True, timeout=600,
    )


@pytest.mark.macos_only
def test_uninstall_aborts_before_removing_anything_when_the_app_will_not_quit(tmp_path):
    box = _sandbox(tmp_path, app_running=True)
    app = _fake_install(box["home"])

    done = _run("uninstall.sh", box, "--yes")

    assert done.returncode != 0
    assert "did not quit" in done.stderr
    assert app.exists()
    assert (box["home"] / ".hermes" / "plugins" / "agent-lights").exists()
    assert _calls(box, "hermes") == []


@pytest.mark.macos_only
def test_uninstall_purge_refuses_up_front_while_a_session_owns_a_slot(tmp_path):
    box = _sandbox(tmp_path)
    app = _fake_install(box["home"])
    owner = _spawn_slot_owner(box["home"] / ".hermes")
    try:
        done = _run("uninstall.sh", box, "--purge", "--yes")

        assert done.returncode != 0
        assert "nothing was removed" in done.stderr
        assert app.exists()
        assert (box["home"] / ".hermes" / "agent-lights" / "v2" / "slots" / "1.json").exists()
    finally:
        owner.stdin.close()
        owner.wait(timeout=10)


@pytest.mark.macos_only
def test_uninstall_purge_quits_a_running_app_then_clears_the_pool(tmp_path):
    """The app's own heartbeat must not block --purge: uninstall quits it first."""
    box = _sandbox(tmp_path)
    _fake_install(box["home"])
    pool = box["home"] / ".hermes" / "agent-lights" / "v2"
    # An orphaned stand-in "app" (reaped by launchd when killed, so it never
    # lingers as a zombie of this test).
    app_pid = int(subprocess.run(["/bin/sh", "-c", "sleep 300 >/dev/null 2>&1 & echo $!"],
                                 capture_output=True, text=True, check=True).stdout)
    try:
        (pool / "slots").mkdir(parents=True)
        (pool / "app.json").write_text(json.dumps({"pid": app_pid, "pool": "v2"}))
        # Stand-ins for quitting the app: pkill ends the heartbeat's process.
        (box["home"].parent / "shims" / "pkill").write_text(f"#!/bin/bash\nkill {app_pid}\nexit 0\n")
        (box["home"].parent / "shims" / "pgrep").write_text(f"#!/bin/bash\nkill -0 {app_pid} 2>/dev/null\n")

        done = _run("uninstall.sh", box, "--purge", "--yes")
    finally:
        try:
            os.kill(app_pid, 9)
        except ProcessLookupError:
            pass

    assert done.returncode == 0, done.stderr
    assert not (pool / "app.json").exists()


@pytest.mark.macos_only
def test_uninstall_pins_each_profile_explicitly(tmp_path):
    """Hermes review P2: with a sticky profile, HERMES_HOME=<root> disabled the wrong one."""
    box = _sandbox(tmp_path)
    _fake_install(box["home"])

    done = _run("uninstall.sh", box, "--profile", "work", "--yes")

    assert done.returncode == 0, done.stderr
    assert _calls(box, "hermes") == [
        "hermes -p default plugins disable agent-lights [HERMES_HOME=unset]",
        "hermes -p work plugins disable agent-lights [HERMES_HOME=unset]",
    ]
    assert not (box["home"] / ".hermes" / "plugins" / "agent-lights").exists()
    assert not (box["home"] / ".hermes" / "profiles" / "work" / "plugins" / "agent-lights").exists()


@pytest.mark.macos_only
@pytest.mark.skipif(shutil.which("swift") is None, reason="needs the Swift toolchain")
def test_install_refuses_a_dangling_legacy_alias_before_building(tmp_path):
    """Hermes review P2: an alias to a not-yet-installed v2 bundle passed the checks."""
    box = _sandbox(tmp_path)
    apps = box["home"] / "Applications"
    apps.mkdir()
    (apps / "AgentLightsMenuBar.app").symlink_to(apps / "AgentLightsMenuBar-v2.app")
    box["env"]["PATH"] = os.pathsep.join([box["env"]["PATH"], os.path.dirname(shutil.which("swift"))])

    done = _run("install.sh", box, "--no-plugin", "--no-test", "--yes")

    assert done.returncode != 0
    assert "resolves into" in done.stderr
    assert "Building" not in done.stdout
    assert not (apps / "AgentLightsMenuBar-v2.app").exists()


@pytest.mark.macos_only
@pytest.mark.skipif(shutil.which("swift") is None, reason="needs the Swift toolchain")
def test_install_pins_each_profile_explicitly_and_installs_a_valid_bundle(tmp_path):
    box = _sandbox(tmp_path)
    box["env"]["PATH"] = os.pathsep.join([box["env"]["PATH"], os.path.dirname(shutil.which("swift"))])

    done = _run("install.sh", box, "--profile", "work", "--no-test", "--yes")

    assert done.returncode == 0, done.stderr
    assert _calls(box, "hermes") == [
        "hermes -p default plugins enable agent-lights [HERMES_HOME=unset]",
        "hermes -p work plugins enable agent-lights [HERMES_HOME=unset]",
    ]
    assert (box["home"] / ".hermes" / "plugins" / "agent-lights" / "plugin.yaml").exists()
    assert (box["home"] / ".hermes" / "profiles" / "work" / "plugins" / "agent-lights" / "plugin.yaml").exists()
    app = box["home"] / "Applications" / "AgentLightsMenuBar-v2.app"
    subprocess.run(["codesign", "--verify", "--strict", str(app)], check=True)
    assert _calls(box, "open")[-1].startswith(f"open -g {app}")


def test_package_source_honors_a_relative_output_directory(tmp_path):
    """Hermes review P2: a relative output dir resolved against the repo root."""
    done = subprocess.run(
        ["/bin/bash", str(_APP_DIR / "scripts" / "package-source.sh"), "dist"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )

    assert done.returncode == 0, done.stderr
    tarballs = list((tmp_path / "dist").glob("agent-lights-src-*.tar.gz"))
    assert len(tarballs) == 1
    with tarfile.open(tarballs[0]) as archive:
        names = archive.getnames()
    assert "agent-lights/install.sh" in names
    assert "agent-lights/uninstall.sh" in names
    assert not any("/.build/" in name for name in names)
