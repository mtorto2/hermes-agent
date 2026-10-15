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
import zipfile
from pathlib import Path

import pytest

from agent.light_cues import SlotStatusFileBackend as CoreBackend

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_DIR = _REPO_ROOT / "apps" / "agent-lights-menu-bar"
_POOL_TOOL = _APP_DIR / "scripts" / "agent_lights_pool.py"
_LEGACY_TOOL = _APP_DIR / "scripts" / "legacy_agent_lights.py"


def _load_pool_tool():
    spec = importlib.util.spec_from_file_location("agent_lights_pool_under_test", _POOL_TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pool_tool = _load_pool_tool()


def _load_legacy_tool():
    spec = importlib.util.spec_from_file_location("legacy_agent_lights_under_test", _LEGACY_TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


legacy = _load_legacy_tool()


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
        "launchctl": '[ "${1-}" = "print" ] && exit 113; exit 0',
        "tccutil": "exit 0",
        "defaults": "exit 0",
        "hermes": 'if [ "${1-}" = "--version" ] && [ -n "${FAKE_HERMES_DIR-}" ]; then echo "Install directory: $FAKE_HERMES_DIR"; fi\nexit 0',
        # Never let a test see (or move) this machine's real apps.
        "ps": "exit 0",
        "mdfind": "exit 0",
    }
    for name, body in bodies.items():
        stub = shims / name
        stub.write_text(_STUB.format(log=log, body=body))
        stub.chmod(0o755)
    path = os.pathsep.join([str(shims), os.path.dirname(shutil.which("python3") or "/usr/bin/python3"),
                            "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    (tmp_path / "SystemApplications").mkdir()
    env = {"HOME": str(home), "PATH": path, "HERMES_HOME": str(home / ".hermes"),
           "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
           # Keep the legacy scan off this machine's real /Applications and
           # refuse any move outside this test's own folder.
           "AGENT_LIGHTS_SYSTEM_APPLICATIONS": str(tmp_path / "SystemApplications"),
           "AGENT_LIGHTS_LEGACY_CONFINE": str(tmp_path)}
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
        _mark(plugins / "agent-lights")
    (app / "Contents" / "MacOS" / "AgentLightsMenuBar").chmod(0o755)
    return app


def _mark(plugin: Path) -> None:
    """What install.sh writes into the plugin it installs."""
    (plugin / ".agent-lights-install").write_text("agent-lights plugin installed by install.sh\n2026-10-15\n")


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
    assert [c for c in _calls(box, "hermes") if " plugins " in c] == [
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
    archives = list((tmp_path / "dist").glob("agent-lights-src-*.zip"))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as archive:
        names = archive.namelist()
    assert "agent-lights/install.sh" in names
    assert "agent-lights/uninstall.sh" in names
    assert not any("/.build/" in name for name in names)


# -- an older install ---------------------------------------------------------

_PLUGIN_SRC = _APP_DIR / "plugin" / "agent-lights"
_NEW_ID = "io.github.mtorto2.agent-lights"


def _old_bundle(path: Path, bundle_id: str = "com.savant.hermes-agent-lights") -> Path:
    import plistlib

    (path / "Contents" / "MacOS").mkdir(parents=True)
    (path / "Contents" / "MacOS" / "AgentLightsMenuBar").write_bytes(b"old")
    with open(path / "Contents" / "Info.plist", "wb") as handle:
        plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleExecutable": "AgentLightsMenuBar"}, handle)
    return path


def _write_plist(path: Path, data: dict) -> Path:
    import plistlib

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        plistlib.dump(data, handle)
    return path


def _plant_old_install(home: Path, hermes_dir: Path) -> dict:
    """Every shape an older Agent Lights install has taken, plus new pieces to keep."""
    hermes = home / ".hermes"
    (hermes / "profiles" / "work").mkdir(parents=True, exist_ok=True)
    old = {
        "user_app": _old_bundle(home / "Applications" / "AgentLightsMenuBar.app"),
        "repo_app": _old_bundle(hermes_dir / "apps" / "agent-lights-menu-bar" / ".build" / "AgentLightsMenuBar.app"),
        "launch_agent": _write_plist(home / "Library" / "LaunchAgents" / "com.friend.agentlights.plist", {
            "Label": "com.friend.agentlights",
            "ProgramArguments": ["/usr/bin/open", str(home / "Applications" / "AgentLightsMenuBar.app")]}),
        "plugin": hermes / "profiles" / "work" / "plugins" / "lights",
        "root_pool": hermes / "agent-lights" / "slots",
        "profile_pool": hermes / "profiles" / "work" / "agent-lights" / "slots",
    }
    old["plugin"].mkdir(parents=True)
    (old["plugin"] / "plugin.yaml").write_text("name: lights\n")
    (old["plugin"] / "__init__.py").write_text("from agent.light_cues import LightCueService\n")
    old["root_pool"].mkdir(parents=True)
    (old["root_pool"] / "1.json").write_text("{}")
    old["profile_pool"].mkdir(parents=True)
    keep = {
        "new_app": _old_bundle(home / "Applications" / "AgentLightsMenuBar-v2.app", _NEW_ID),
        "new_pool": hermes / "agent-lights" / "v2",
        "new_plugin": hermes / "plugins" / "agent-lights",
        "new_agent": _write_plist(home / "Library" / "LaunchAgents" / f"{_NEW_ID}.plist", {
            "Label": _NEW_ID, "ProgramArguments": ["/usr/bin/open", "-g", str(home / "Applications")]}),
        "other_plugin": hermes / "plugins" / "unrelated",
        "mention_only": hermes / "plugins" / "diagnostics",
    }
    keep["new_pool"].mkdir(parents=True)
    shutil.copytree(_PLUGIN_SRC, keep["new_plugin"], ignore=shutil.ignore_patterns("__pycache__"))
    keep["other_plugin"].mkdir(parents=True)
    (keep["other_plugin"] / "__init__.py").write_text("print('hello')\n")
    keep["mention_only"].mkdir(parents=True)
    (keep["mention_only"] / "__init__.py").write_text("def check(): return True\n")
    (keep["mention_only"] / "README.md").write_text("Compatible with agent-lights.\n")
    return {"old": old, "keep": keep}


class FakeSystem:
    """Stands in for ps, launchctl, mdfind and hermes inside the legacy tool."""

    def __init__(self, monkeypatch):
        self.processes: list[tuple[str, str]] = []
        self.loaded: set[str] = set()
        self.stuck: set[str] = set()
        self.spotlight: list[str] = []
        self.calls: list[list[str]] = []
        real_run = legacy.subprocess.run

        def run(argv, *a, **k):
            argv = list(argv)
            self.calls.append(argv)
            done = lambda code=0, out="": subprocess.CompletedProcess(argv, code, out, "")
            if argv[0] == "ps":
                return done(0, "".join(f"{pid} {cmd}\n" for pid, cmd in self.processes))
            if argv[0] == "mdfind":
                return done(0, "".join(f"{p}\n" for p in self.spotlight))
            if argv[0] == "launchctl":
                label = argv[2].rsplit("/", 1)[-1]
                if argv[1] == "print":
                    return done(0 if label in self.loaded else 113)
                if argv[1] == "bootout" and label not in self.stuck:
                    self.loaded.discard(label)
                return done(0)
            if argv[0] == "/fake/hermes":
                return done(0)
            return real_run(argv, *a, **k)

        monkeypatch.setattr(legacy.subprocess, "run", run)


@pytest.fixture
def fake_system(monkeypatch):
    return FakeSystem(monkeypatch)


def _ctx(tmp_path: Path, home: Path, hermes_dir: Path | None, **kw):
    system_apps = tmp_path / "SystemApplications"
    system_apps.mkdir(exist_ok=True)
    kw.setdefault("install_profiles", {"default"})
    return legacy.Context(home=home, hermes_dir=hermes_dir, profiles=[], spotlight=kw.pop("spotlight", False),
                          new_bundle_id=_NEW_ID, system_apps=system_apps, new_plugin_dir=_PLUGIN_SRC,
                          confine=tmp_path, **kw)


def test_legacy_scan_finds_every_old_piece_and_none_of_the_new_ones(tmp_path, fake_system):
    home, hermes_dir = tmp_path / "home", tmp_path / "hermes-agent"
    planted = _plant_old_install(home, hermes_dir)

    findings = legacy.scan(_ctx(tmp_path, home, hermes_dir))

    removable = {f.path for f in findings if f.removable}
    assert removable == set(planted["old"].values())
    assert not {f.path for f in findings} & (set(planted["keep"].values()) - {planted["keep"]["mention_only"]})
    assert legacy.report(findings) == legacy.EXIT_FOUND


def test_a_plugin_that_only_mentions_agent_lights_is_reported_not_moved(tmp_path, fake_system):
    """Review round 4, finding 4."""
    home, hermes_dir = tmp_path / "home", tmp_path / "hermes-agent"
    planted = _plant_old_install(home, hermes_dir)
    ctx = _ctx(tmp_path, home, hermes_dir)

    mention = [f for f in legacy.scan(ctx) if f.path == planted["keep"]["mention_only"]]
    assert len(mention) == 1 and mention[0].removable is False

    assert legacy.remove(ctx, legacy.scan(ctx), tmp_path / "backup", "/fake/hermes") == legacy.EXIT_NONE
    assert planted["keep"]["mention_only"].exists()


def test_report_only_items_and_running_copies_do_not_block(tmp_path, fake_system):
    home = tmp_path / "home"
    mention = home / ".hermes" / "plugins" / "diagnostics"
    mention.mkdir(parents=True)
    (mention / "README.md").write_text("Compatible with agent-lights.\n")

    findings = legacy.scan(_ctx(tmp_path, home, None))
    assert [f.removable for f in findings] == [False]
    assert legacy.report(findings) == legacy.EXIT_NONE


def test_a_running_old_copy_leads_to_its_bundle_wherever_it_is(tmp_path, fake_system):
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    odd = _old_bundle(tmp_path / "Downloads" / "Lights.app")
    fake_system.processes = [("4242", str(odd / "Contents" / "MacOS" / "AgentLightsMenuBar"))]

    apps = [f for f in legacy.scan(_ctx(tmp_path, home, None)) if f.kind == "app"]
    assert [f.path for f in apps] == [odd] and apps[0].removable


def test_legacy_remove_moves_everything_into_a_new_backup_and_verifies(tmp_path, fake_system):
    home, hermes_dir = tmp_path / "home", tmp_path / "hermes-agent"
    planted = _plant_old_install(home, hermes_dir)
    fake_system.loaded.add("com.friend.agentlights")
    ctx = _ctx(tmp_path, home, hermes_dir)
    backup = tmp_path / "backup"

    assert legacy.remove(ctx, legacy.scan(ctx), backup, "/fake/hermes") == legacy.EXIT_NONE

    for path in planted["old"].values():
        assert not path.exists(), path
    for path in planted["keep"].values():
        assert path.exists(), path
    manifest = (backup / "MANIFEST.txt").read_text()
    assert all(str(path) in manifest for path in planted["old"].values())
    assert "com.friend.agentlights" not in fake_system.loaded
    assert ["/fake/hermes", "-p", "work", "plugins", "disable", "lights"] in fake_system.calls
    assert [f for f in legacy.scan(ctx) if f.removable] == []


def test_backups_are_never_reused_or_overwritten(tmp_path, fake_system):
    """Review round 4, finding 2."""
    home, hermes_dir = tmp_path / "home", tmp_path / "hermes-agent"
    planted = _plant_old_install(home, hermes_dir)
    ctx = _ctx(tmp_path, home, hermes_dir)
    reused = tmp_path / "backup"
    reused.mkdir()
    (reused / "01-app-AgentLightsMenuBar.app").write_text("earlier backup")

    assert legacy.remove(ctx, legacy.scan(ctx), reused, "/fake/hermes") == legacy.EXIT_FAILED
    assert (reused / "01-app-AgentLightsMenuBar.app").read_text() == "earlier backup"
    assert planted["old"]["user_app"].exists()

    first, second = legacy._new_backup_dir(home), legacy._new_backup_dir(home)
    assert first != second and first.is_dir() and second.is_dir()


def test_moves_outside_the_allowed_root_are_refused(tmp_path, fake_system):
    """Review round 4, finding 3: tests (and anything confined) can't move other files."""
    home, hermes_dir = tmp_path / "home", tmp_path / "hermes-agent"
    _plant_old_install(home, hermes_dir)
    elsewhere = _old_bundle(tmp_path / "elsewhere" / "AgentLightsMenuBar.app")
    fake_system.spotlight = [str(elsewhere)]
    ctx = _ctx(tmp_path, home, hermes_dir, spotlight=True)
    ctx.confine = home

    assert legacy.remove(ctx, legacy.scan(ctx), tmp_path / "backup", "/fake/hermes") == legacy.EXIT_FAILED
    assert elsewhere.exists()


def test_backed_up_apps_found_by_spotlight_are_not_active_installs(tmp_path, fake_system):
    """Review round 4, finding 5."""
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    archived = _old_bundle(home / ".hermes" / "agent-lights-legacy-backup-20261015-110338-abc123"
                           / "01-app-AgentLightsMenuBar.app")
    trashed = _old_bundle(home / ".Trash" / "AgentLightsMenuBar.app")
    fake_system.spotlight = [str(archived), str(trashed)]

    assert legacy.scan(_ctx(tmp_path, home, None, spotlight=True)) == []


def test_standalone_scan_knows_the_current_login_item(tmp_path, fake_system, monkeypatch):
    """Review round 4, finding 6: no --new-bundle-id given."""
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    _write_plist(home / "Library" / "LaunchAgents" / f"{_NEW_ID}.plist",
                 {"Label": _NEW_ID, "ProgramArguments": ["/usr/bin/open", "-g", "AgentLightsMenuBar-v2.app"]})
    monkeypatch.setattr(legacy.Path, "home", lambda: home)
    monkeypatch.setenv("AGENT_LIGHTS_SYSTEM_APPLICATIONS", str(tmp_path / "none"))

    assert legacy.main(["scan", "--no-spotlight"]) == legacy.EXIT_NONE


def test_an_existing_agent_lights_plugin_is_backed_up_unless_identical(tmp_path, fake_system):
    """Review round 4, finding 7: a same-named old plugin was deleted unsaved."""
    home = tmp_path / "home"
    plugin = home / ".hermes" / "plugins" / "agent-lights"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("from agent.light_cues import LightCueService  # improvised\n")
    ctx = _ctx(tmp_path, home, None)

    [finding] = legacy.scan(ctx)
    assert finding.removable and not finding.auto  # needs --remove-legacy
    assert legacy.remove(ctx, [finding], tmp_path / "backup", "/fake/hermes") == legacy.EXIT_NONE
    assert (tmp_path / "backup" / "01-plugin-agent-lights" / "__init__.py").exists()

    # A previous install of this plugin is backed up automatically...
    shutil.copytree(_PLUGIN_SRC, plugin, ignore=shutil.ignore_patterns("__pycache__"))
    (plugin / "protocol.py").write_text((plugin / "protocol.py").read_text() + "\n# local tweak\n")
    _mark(plugin)
    [previous] = legacy.scan(ctx)
    assert previous.auto
    assert legacy.report([previous]) == legacy.EXIT_NONE
    assert legacy.remove(ctx, [previous], tmp_path / "backup2", None, auto_only=True) == legacy.EXIT_NONE
    assert "local tweak" in (tmp_path / "backup2" / "01-plugin-agent-lights" / "protocol.py").read_text()

    # ...and an identical copy is just replaced.
    shutil.copytree(_PLUGIN_SRC, plugin, ignore=shutil.ignore_patterns("__pycache__"))
    assert legacy.scan(ctx) == []


def test_another_app_at_the_new_apps_path_is_backed_up(tmp_path, fake_system):
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    _old_bundle(home / "Applications" / "AgentLightsMenuBar-v2.app")  # old id at the new path
    ctx = _ctx(tmp_path, home, None)

    [finding] = legacy.scan(ctx)
    assert finding.removable and "new app's path" in finding.detail


def test_an_alias_and_the_app_it_points_to_are_both_set_aside(tmp_path, fake_system):
    """Review round 4, finding 8."""
    home, hermes_dir = tmp_path / "home", tmp_path / "hermes-agent"
    (home / ".hermes").mkdir(parents=True)
    real = _old_bundle(tmp_path / "builds" / "AgentLightsMenuBar.app")
    (home / "Applications").mkdir()
    alias = home / "Applications" / "AgentLightsMenuBar.app"
    alias.symlink_to(real)
    ctx = _ctx(tmp_path, home, hermes_dir)

    assert {f.path for f in legacy.scan(ctx)} == {alias, real}
    assert legacy.remove(ctx, legacy.scan(ctx), tmp_path / "backup", None) == legacy.EXIT_NONE
    assert not os.path.lexists(alias) and not real.exists()


def test_failures_win_over_an_allowed_core_patch(tmp_path, fake_system):
    """Review round 4, finding 9."""
    home, hermes_dir = tmp_path / "home", tmp_path / "hermes-agent"
    (hermes_dir / "agent").mkdir(parents=True)
    (hermes_dir / "agent" / "light_cues.py").write_text("# patch\n")
    (home / ".hermes").mkdir(parents=True)
    outside = _old_bundle(tmp_path / "outside" / "AgentLightsMenuBar.app")
    fake_system.spotlight = [str(outside)]
    ctx = _ctx(tmp_path, home, hermes_dir, spotlight=True)
    ctx.confine = home

    assert legacy.remove(ctx, legacy.scan(ctx), tmp_path / "backup", None) == legacy.EXIT_FAILED


def test_a_login_item_that_will_not_unload_or_an_old_copy_still_running_fails(tmp_path, fake_system):
    """Review round 4, finding 10."""
    home, hermes_dir = tmp_path / "home", tmp_path / "hermes-agent"
    planted = _plant_old_install(home, hermes_dir)
    fake_system.loaded.add("com.friend.agentlights")
    fake_system.stuck.add("com.friend.agentlights")
    ctx = _ctx(tmp_path, home, hermes_dir)

    assert legacy.remove(ctx, legacy.scan(ctx), tmp_path / "backup", None) == legacy.EXIT_FAILED
    assert planted["old"]["launch_agent"].exists()  # not moved while still loaded

    fake_system.stuck.clear()
    fake_system.processes = [("4242", str(tmp_path / "old" / "AgentLightsMenuBar.app/Contents/MacOS/AgentLightsMenuBar"))]
    assert legacy.remove(ctx, legacy.scan(ctx), tmp_path / "backup2", None) == legacy.EXIT_FAILED


def test_automatic_plugin_backup_only_in_profiles_this_install_writes(tmp_path, fake_system):
    """Round 5 #3."""
    home = tmp_path / "home"
    for profile_home in (home / ".hermes", home / ".hermes" / "profiles" / "work"):
        plugin = profile_home / "plugins" / "agent-lights"
        shutil.copytree(_PLUGIN_SRC, plugin, ignore=shutil.ignore_patterns("__pycache__"))
        (plugin / "protocol.py").write_text((plugin / "protocol.py").read_text() + "\n# tweak\n")
        _mark(plugin)

    def profiles_hit(targets):
        return {f.profile for f in legacy.scan(_ctx(tmp_path, home, None, install_profiles=targets))}

    assert profiles_hit({"default"}) == {"default"}
    assert profiles_hit({"default", "work"}) == {"default", "work"}
    assert profiles_hit(None) == set()  # --no-plugin


def test_files_and_links_at_the_install_paths_are_caught(tmp_path, fake_system):
    """Round 5 #4."""
    home = tmp_path / "home"
    (home / "Applications").mkdir(parents=True)
    (home / "Applications" / "AgentLightsMenuBar-v2.app").write_text("a plain file\n")
    (home / ".hermes" / "plugins").mkdir(parents=True)
    (home / ".hermes" / "plugins" / "agent-lights").write_text("a plain file\n")

    paths = {f.path for f in legacy.scan(_ctx(tmp_path, home, None)) if f.removable}
    assert paths == {home / "Applications" / "AgentLightsMenuBar-v2.app",
                     home / ".hermes" / "plugins" / "agent-lights"}


def test_comments_and_docstrings_are_not_producer_code(tmp_path, fake_system):
    """Round 5 #5."""
    home = tmp_path / "home"
    plugin = home / ".hermes" / "plugins" / "notes"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text(
        '"""Works next to AgentLightsMenuBar; from agent.light_cues import nothing."""\n'
        "# AgentLightsMenuBar, SlotStatusFileBackend, agent-lights/slots\n"
        "def register(ctx):\n    \"\"\"See agent-lights.\"\"\"\n    return None\n")

    [finding] = legacy.scan(_ctx(tmp_path, home, None))
    assert finding.removable is False

    (plugin / "__init__.py").write_text("import agent.light_cues\n")
    [finding] = legacy.scan(_ctx(tmp_path, home, None))
    assert finding.removable is True and "imports agent.light_cues" in finding.detail


def test_a_symlinked_old_plugin_is_found_and_only_the_link_moves(tmp_path, fake_system):
    """Round 5 #6."""
    home = tmp_path / "home"
    code = tmp_path / "src" / "old-lights"
    code.mkdir(parents=True)
    (code / "__init__.py").write_text("from agent.light_cues import LightCueService\n")
    link = home / ".hermes" / "plugins" / "old-lights"
    link.parent.mkdir(parents=True)
    link.symlink_to(code)
    ctx = _ctx(tmp_path, home, None)

    [finding] = legacy.scan(ctx)
    assert finding.path == link and finding.removable and str(code) in finding.detail
    assert legacy.remove(ctx, [finding], tmp_path / "backup", None) == legacy.EXIT_NONE
    assert not os.path.lexists(link) and (code / "__init__.py").exists()


def test_comment_naming_the_producer_class_neither_identifies_nor_exempts(tmp_path, fake_system):
    """Round 6 #3: a raw `class AgentLightsProducer` match made an unrelated
    plugin 'previous install' (auto-removed) and exempted an old producer."""
    home = tmp_path / "home"
    unrelated = home / ".hermes" / "plugins" / "agent-lights"
    unrelated.mkdir(parents=True)
    (unrelated / "__init__.py").write_text("# like class AgentLightsProducer, but not\ndef register(ctx): pass\n")
    old = home / ".hermes" / "profiles" / "work" / "plugins" / "lights"
    old.mkdir(parents=True)
    (old / "__init__.py").write_text("# class AgentLightsProducer\nfrom agent.light_cues import LightCueService\n")

    findings = {f.path: f for f in legacy.scan(_ctx(tmp_path, home, None))}
    assert findings[unrelated].removable and not findings[unrelated].auto  # needs --remove-legacy
    assert findings[old].removable  # still inspected and found


def test_a_string_mentioning_the_app_is_report_only(tmp_path, fake_system):
    """Round 6 #4."""
    home = tmp_path / "home"
    plugin = home / ".hermes" / "plugins" / "status"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text('def report_status():\n    return "AgentLightsMenuBar is not installed"\n')

    [finding] = legacy.scan(_ctx(tmp_path, home, None))
    assert finding.removable is False
    assert legacy.remove(_ctx(tmp_path, home, None), [finding], tmp_path / "backup", None) == legacy.EXIT_NONE
    assert plugin.exists()


def test_a_canonical_name_link_outside_install_targets_is_inspected(tmp_path, fake_system):
    """Round 6 #5."""
    home = tmp_path / "home"
    code = tmp_path / "src" / "old"
    code.mkdir(parents=True)
    (code / "__init__.py").write_text("import agent.light_cues\n")
    link = home / ".hermes" / "profiles" / "work" / "plugins" / "agent-lights"
    link.parent.mkdir(parents=True)
    link.symlink_to(code)
    (home / ".hermes" / "plugins").mkdir(parents=True)

    for targets in ({"default"}, None):  # install run, and a standalone scan
        [finding] = legacy.scan(_ctx(tmp_path, home, None, install_profiles=targets))
        assert finding.path == link and finding.removable

    # A marked current install linked there is left alone.
    _mark(code)
    assert legacy.scan(_ctx(tmp_path, home, None)) == []


def test_replace_guard_allows_only_absent_paths_and_previous_installs(tmp_path):
    app = tmp_path / "AgentLightsMenuBar-v2.app"
    check_app = lambda: legacy.replaceable(app, kind="app", new_bundle_id=_NEW_ID, new_plugin_dir=_PLUGIN_SRC)
    assert check_app() is None
    app.mkdir()
    (app / "notes.txt").write_text("x")
    assert check_app() is not None
    shutil.rmtree(app)
    _old_bundle(app, _NEW_ID)
    assert check_app() is None

    plugin = tmp_path / "agent-lights"
    check_plugin = lambda: legacy.replaceable(plugin, kind="plugin", new_bundle_id=None, new_plugin_dir=_PLUGIN_SRC)
    plugin.write_text("a file")
    assert check_plugin() is not None
    plugin.unlink()
    plugin.mkdir()
    (plugin / "__init__.py").write_text("# class AgentLightsProducer\n")
    assert check_plugin() is not None
    _mark(plugin)
    assert check_plugin() is None


def test_a_hermes_core_patch_blocks_and_is_never_touched(tmp_path, fake_system):
    home, hermes_dir = tmp_path / "home", tmp_path / "hermes-agent"
    (hermes_dir / "agent").mkdir(parents=True)
    (hermes_dir / "agent" / "light_cues.py").write_text("_AGENT_LIGHTS_POOL = 'v1'\n")
    (home / ".hermes").mkdir(parents=True)
    ctx = _ctx(tmp_path, home, hermes_dir)

    findings = legacy.scan(ctx)
    assert [f.kind for f in findings] == ["core"]
    assert legacy.report(findings) == legacy.EXIT_BLOCKING
    assert legacy.remove(ctx, findings, tmp_path / "backup", None) == legacy.EXIT_BLOCKING
    assert (hermes_dir / "agent" / "light_cues.py").exists()


@pytest.mark.macos_only
@pytest.mark.skipif(shutil.which("swift") is None, reason="needs the Swift toolchain")
def test_install_stops_on_an_old_install_then_sets_it_aside_with_remove_legacy(tmp_path):
    box = _sandbox(tmp_path)
    box["env"]["PATH"] = os.pathsep.join([box["env"]["PATH"], os.path.dirname(shutil.which("swift"))])
    hermes_dir = tmp_path / "hermes-agent"
    box["env"]["FAKE_HERMES_DIR"] = str(hermes_dir)
    planted = _plant_old_install(box["home"], hermes_dir)
    shutil.rmtree(planted["keep"]["new_app"])

    refused = _run("install.sh", box, "--no-test", "--yes")
    assert refused.returncode != 0
    assert "--remove-legacy" in refused.stderr
    assert "Building" not in refused.stdout
    assert planted["old"]["user_app"].exists()

    done = _run("install.sh", box, "--no-test", "--yes", "--remove-legacy")
    assert done.returncode == 0, done.stderr
    for path in planted["old"].values():
        assert not path.exists(), path
    assert planted["keep"]["mention_only"].exists()
    # Re-running (the update path) works without flags: only report-only
    # items remain, and the previous install is simply replaced.
    again = _run("install.sh", box, "--no-test", "--yes")
    assert again.returncode == 0, again.stderr
    backups = list((box["home"] / ".hermes").glob("agent-lights-legacy-backup-*"))
    assert len(backups) == 1 and (backups[0] / "MANIFEST.txt").exists()  # the re-run set nothing aside
    assert (box["home"] / "Applications" / "AgentLightsMenuBar-v2.app" / "Contents" / "Info.plist").exists()


@pytest.mark.macos_only
@pytest.mark.skipif(shutil.which("swift") is None, reason="needs the Swift toolchain")
def test_reinstalling_after_a_local_plugin_edit_backs_it_up_without_extra_flags(tmp_path):
    box = _sandbox(tmp_path)
    box["env"]["PATH"] = os.pathsep.join([box["env"]["PATH"], os.path.dirname(shutil.which("swift"))])
    plugin = box["home"] / ".hermes" / "plugins" / "agent-lights"
    shutil.copytree(_PLUGIN_SRC, plugin, ignore=shutil.ignore_patterns("__pycache__"))
    (plugin / "protocol.py").write_text((plugin / "protocol.py").read_text() + "\n# local tweak\n")
    _mark(plugin)

    done = _run("install.sh", box, "--no-test", "--yes")
    assert done.returncode == 0, done.stderr
    backups = list((box["home"] / ".hermes").glob("agent-lights-legacy-backup-*"))
    assert len(backups) == 1
    assert "local tweak" in next(backups[0].glob("*-plugin-agent-lights")).joinpath("protocol.py").read_text()
    assert "local tweak" not in (plugin / "protocol.py").read_text()  # replaced by the new one


@pytest.mark.macos_only
@pytest.mark.skipif(shutil.which("swift") is None, reason="needs the Swift toolchain")
def test_no_plugin_install_and_other_profiles_leave_plugins_alone(tmp_path):
    """Round 5 #3: plugins were removed from profiles the install never writes."""
    box = _sandbox(tmp_path)
    box["env"]["PATH"] = os.pathsep.join([box["env"]["PATH"], os.path.dirname(shutil.which("swift"))])
    mine = box["home"] / ".hermes" / "plugins" / "agent-lights"
    theirs = box["home"] / ".hermes" / "profiles" / "work" / "plugins" / "agent-lights"
    for plugin in (mine, theirs):
        shutil.copytree(_PLUGIN_SRC, plugin, ignore=shutil.ignore_patterns("__pycache__"))
        (plugin / "protocol.py").write_text((plugin / "protocol.py").read_text() + "\n# local tweak\n")
        _mark(plugin)

    done = _run("install.sh", box, "--no-test", "--yes", "--no-plugin")
    assert done.returncode == 0, done.stderr
    assert "local tweak" in (mine / "protocol.py").read_text()
    assert "local tweak" in (theirs / "protocol.py").read_text()

    again = _run("install.sh", box, "--no-test", "--yes")  # default profile only
    assert again.returncode == 0, again.stderr
    assert "local tweak" not in (mine / "protocol.py").read_text()
    assert "local tweak" in (theirs / "protocol.py").read_text()


@pytest.mark.macos_only
@pytest.mark.skipif(shutil.which("swift") is None, reason="needs the Swift toolchain")
def test_unrecognized_contents_at_the_new_app_path_survive_install(tmp_path):
    """Round 5 #4: a folder there without an Agent Lights executable was rm -rf'd."""
    box = _sandbox(tmp_path)
    box["env"]["PATH"] = os.pathsep.join([box["env"]["PATH"], os.path.dirname(shutil.which("swift"))])
    dest = box["home"] / "Applications" / "AgentLightsMenuBar-v2.app"
    dest.mkdir(parents=True)
    (dest / "my-notes.txt").write_text("keep me\n")

    refused = _run("install.sh", box, "--no-test", "--yes", "--no-plugin")
    assert refused.returncode != 0
    assert (dest / "my-notes.txt").read_text() == "keep me\n"

    done = _run("install.sh", box, "--no-test", "--yes", "--no-plugin", "--remove-legacy")
    assert done.returncode == 0, done.stderr
    [backup] = (box["home"] / ".hermes").glob("agent-lights-legacy-backup-*")
    assert (next(backup.glob("*-app-AgentLightsMenuBar-v2.app")) / "my-notes.txt").read_text() == "keep me\n"


@pytest.mark.macos_only
@pytest.mark.skipif(shutil.which("swift") is None, reason="needs the Swift toolchain")
def test_allow_core_producer_never_lets_an_occupied_destination_be_deleted(tmp_path):
    """Round 6 #2."""
    box = _sandbox(tmp_path)
    box["env"]["PATH"] = os.pathsep.join([box["env"]["PATH"], os.path.dirname(shutil.which("swift"))])
    hermes_dir = tmp_path / "hermes-agent"
    (hermes_dir / "agent").mkdir(parents=True)
    (hermes_dir / "agent" / "light_cues.py").write_text("_AGENT_LIGHTS_POOL = 'v2'\n")
    box["env"]["FAKE_HERMES_DIR"] = str(hermes_dir)
    dest = box["home"] / "Applications" / "AgentLightsMenuBar-v2.app"
    dest.mkdir(parents=True)
    (dest / "my-notes.txt").write_text("keep me\n")

    done = _run("install.sh", box, "--no-test", "--yes", "--no-plugin", "--allow-core-producer")

    assert done.returncode != 0
    assert (dest / "my-notes.txt").read_text() == "keep me\n"


@pytest.mark.macos_only
def test_uninstall_removes_only_what_install_put_there(tmp_path):
    box = _sandbox(tmp_path)
    app = _fake_install(box["home"])
    (app / "Contents" / "Info.plist").write_bytes(
        (app / "Contents" / "Info.plist").read_bytes().replace(_NEW_ID.encode(), b"com.someone.else"))
    foreign = box["home"] / ".hermes" / "profiles" / "work" / "plugins" / "agent-lights"
    (foreign / ".agent-lights-install").unlink()

    done = _run("uninstall.sh", box, "--profile", "work", "--yes")

    assert done.returncode == 0, done.stderr
    assert app.exists() and foreign.exists()  # not ours: left in place
    assert not (box["home"] / ".hermes" / "plugins" / "agent-lights").exists()  # ours: removed


@pytest.mark.macos_only
def test_a_foreign_app_at_the_app_path_never_names_what_uninstall_cleans_up(tmp_path):
    """Round 7 #1: the foreign app's bundle id drove bootout, plist removal,
    defaults and TCC resets."""
    box = _sandbox(tmp_path)
    app = _fake_install(box["home"])
    (app / "Contents" / "Info.plist").write_bytes(
        (app / "Contents" / "Info.plist").read_bytes().replace(_NEW_ID.encode(), b"com.someone.else"))
    agents = box["home"] / "Library" / "LaunchAgents"
    foreign_agent = _write_plist(agents / "com.someone.else.plist", {"Label": "com.someone.else"})
    own_agent = _write_plist(agents / f"{_NEW_ID}.plist", {"Label": _NEW_ID})

    done = _run("uninstall.sh", box, "--purge", "--yes")

    assert done.returncode == 0, done.stderr
    assert app.exists() and foreign_agent.exists()
    assert not own_agent.exists()
    effects = _calls(box, "launchctl") + _calls(box, "defaults") + _calls(box, "tccutil")
    assert not [call for call in effects if "com.someone.else" in call]
    assert any(f"bootout gui/{os.getuid()}/{_NEW_ID}" in call for call in effects)
    assert any(f"tccutil reset Accessibility {_NEW_ID}" in call for call in effects)


@pytest.mark.macos_only
def test_a_symlinked_install_marker_does_not_prove_ownership(tmp_path):
    """Round 7 #2: uninstall read the marker through a link."""
    box = _sandbox(tmp_path)
    _fake_install(box["home"])
    plugin = box["home"] / ".hermes" / "plugins" / "agent-lights"
    real_marker = tmp_path / "marker"
    real_marker.write_text("agent-lights plugin installed by install.sh\n")
    (plugin / ".agent-lights-install").unlink()
    (plugin / ".agent-lights-install").symlink_to(real_marker)
    assert legacy.is_marked_install(plugin) is False  # the helper's view

    done = _run("uninstall.sh", box, "--yes")

    assert done.returncode == 0, done.stderr
    assert plugin.exists()
    assert not [call for call in _calls(box, "hermes") if " plugins disable " in call and "-p default" in call]


@pytest.mark.macos_only
@pytest.mark.skipif(shutil.which("swift") is None, reason="needs the Swift toolchain")
def test_install_refuses_while_hermes_core_carries_an_old_patch(tmp_path):
    box = _sandbox(tmp_path)
    box["env"]["PATH"] = os.pathsep.join([box["env"]["PATH"], os.path.dirname(shutil.which("swift"))])
    hermes_dir = tmp_path / "hermes-agent"
    (hermes_dir / "agent").mkdir(parents=True)
    (hermes_dir / "agent" / "light_cues.py").write_text("# old patch\n")
    box["env"]["FAKE_HERMES_DIR"] = str(hermes_dir)

    done = _run("install.sh", box, "--no-test", "--yes", "--remove-legacy")

    assert done.returncode != 0
    assert "stock Hermes" in done.stderr
    assert "Building" not in done.stdout
    assert (hermes_dir / "agent" / "light_cues.py").exists()
