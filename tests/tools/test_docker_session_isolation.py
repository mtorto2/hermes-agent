"""Per-session docker container isolation (docker + container_persistent: false).

Two user-reported bugs on the docker terminal backend (desktop app, sandboxed
cybersecurity profile):

1. **Stale workspace mount leak** — a NEW chat's container carried the
   PREVIOUS session's workspace bind-mounted rw at /workspace, because the
   mount source was the process-global TERMINAL_CWD env var (written by the
   workspace picker, outliving the session that set it) and because every
   session collapsed onto one shared "default" container.

2. **Broken startup cd (exit 126)** — every command tried to
   ``cd /Users/<user>/...`` (a host path recorded as the session cwd by the
   desktop/TUI gateway) inside the container where it doesn't exist.

These tests pin the fix:

* ``container_persistent: false`` + docker ⇒ each session task_id is its own
  container key (fresh container per session); subagents share the parent's
  container via ``register_container_alias``.
* ``container_persistent: true`` (or any other backend) ⇒ legacy shared
  "default" container, unchanged.
* Mount resolution (``_resolve_task_host_cwd``) refuses process-global cwd
  sources under isolation; only the session's own attached workspace mounts.
* ``_resolve_command_cwd`` discards recorded host-path cwds on container
  backends instead of prefixing commands with an un-cd-able host path.
"""

import os

import pytest

from tools import terminal_tool


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Isolate override/alias/cwd-record state and pin docker isolation env."""
    before_overrides = dict(terminal_tool._task_env_overrides)
    terminal_tool._task_env_overrides.clear()
    with terminal_tool._container_alias_lock:
        before_aliases = dict(terminal_tool._container_aliases)
        terminal_tool._container_aliases.clear()
    with terminal_tool._session_cwd_lock:
        before_cwd = dict(terminal_tool._session_cwd)
        terminal_tool._session_cwd.clear()
    # The config→env bridge is one-shot; mark it done so tests control env vars.
    monkeypatch.setattr(terminal_tool, "_terminal_config_bridge_attempted", True)
    yield
    terminal_tool._task_env_overrides.clear()
    terminal_tool._task_env_overrides.update(before_overrides)
    with terminal_tool._container_alias_lock:
        terminal_tool._container_aliases.clear()
        terminal_tool._container_aliases.update(before_aliases)
    with terminal_tool._session_cwd_lock:
        terminal_tool._session_cwd.clear()
        terminal_tool._session_cwd.update(before_cwd)


def _enable_isolation(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setenv("TERMINAL_CONTAINER_PERSISTENT", "false")


def _disable_isolation(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setenv("TERMINAL_CONTAINER_PERSISTENT", "true")


class TestSessionIsolationKeying:
    def test_persistent_true_keeps_shared_default(self, monkeypatch):
        _disable_isolation(monkeypatch)
        assert terminal_tool._resolve_container_task_id("tui:sess-1") == "default"

    def test_persistent_false_keys_by_session(self, monkeypatch):
        _enable_isolation(monkeypatch)
        assert terminal_tool._resolve_container_task_id("tui:sess-1") == "tui:sess-1"

    def test_two_sessions_get_distinct_keys(self, monkeypatch):
        """The reported bug: session B must not land in session A's container."""
        _enable_isolation(monkeypatch)
        a = terminal_tool._resolve_container_task_id("tui:sess-a")
        b = terminal_tool._resolve_container_task_id("tui:sess-b")
        assert a != b

    def test_none_task_id_still_default_under_isolation(self, monkeypatch):
        _enable_isolation(monkeypatch)
        assert terminal_tool._resolve_container_task_id(None) == "default"

    def test_local_backend_unaffected(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_ENV", "local")
        monkeypatch.setenv("TERMINAL_CONTAINER_PERSISTENT", "false")
        assert terminal_tool._resolve_container_task_id("tui:sess-1") == "default"

    def test_rl_override_isolation_still_wins(self, monkeypatch):
        """Image/env_type overrides keep their own key in BOTH modes."""
        _enable_isolation(monkeypatch)
        terminal_tool.register_task_env_overrides(
            "bench-env", {"docker_image": "custom:latest"}
        )
        try:
            assert terminal_tool._resolve_container_task_id("bench-env") == "bench-env"
        finally:
            terminal_tool.clear_task_env_overrides("bench-env")

    def test_subagent_alias_resolves_to_parent(self, monkeypatch):
        """delegate_task children share the PARENT session's container."""
        _enable_isolation(monkeypatch)
        terminal_tool.register_container_alias("subagent-1", "tui:sess-a")
        assert terminal_tool._resolve_container_task_id("subagent-1") == "tui:sess-a"

    def test_subagent_alias_without_parent_falls_to_default(self, monkeypatch):
        _enable_isolation(monkeypatch)
        terminal_tool.register_container_alias("subagent-2", None)
        assert terminal_tool._resolve_container_task_id("subagent-2") == "default"

    def test_nested_alias_chain_resolves(self, monkeypatch):
        """Orchestrator child spawning its own worker: chain to the root session."""
        _enable_isolation(monkeypatch)
        terminal_tool.register_container_alias("child", "tui:sess-a")
        terminal_tool.register_container_alias("grandchild", "child")
        assert terminal_tool._resolve_container_task_id("grandchild") == "tui:sess-a"

    def test_alias_cycle_does_not_hang(self, monkeypatch):
        _enable_isolation(monkeypatch)
        terminal_tool.register_container_alias("x", "y")
        terminal_tool.register_container_alias("y", "x")
        # Any terminating answer is fine; the invariant is no infinite loop.
        assert terminal_tool._resolve_container_task_id("x") in {"x", "y"}


class TestSessionScopedMountResolution:
    """_resolve_task_host_cwd: the single owner of the cwd→/workspace mount policy."""

    def _config(self, host_cwd="/Users/prev/dev/oldrepo", mount=True):
        return {
            "env_type": "docker",
            "docker_mount_cwd_to_workspace": mount,
            "host_cwd": host_cwd,
        }

    def test_shared_mode_keeps_validated_legacy_host_cwd(self, monkeypatch, tmp_path):
        _disable_isolation(monkeypatch)
        cfg = self._config(host_cwd=str(tmp_path))
        assert (
            terminal_tool._resolve_task_host_cwd(cfg, "tui:sess-1")
            == str(tmp_path)
        )

    def test_shared_mode_rejects_missing_configured_mount_root(self, monkeypatch):
        _disable_isolation(monkeypatch)
        cfg = self._config(host_cwd="/Users/prev/dev/missing")
        assert terminal_tool._resolve_task_host_cwd(cfg, "tui:sess-1") is None

    def test_isolation_refuses_process_global_mount(self, monkeypatch, tmp_path):
        """The reported leak: a fresh session with NO attached workspace must
        not inherit the process-global TERMINAL_CWD-derived mount."""
        _enable_isolation(monkeypatch)
        cfg = self._config(host_cwd=str(tmp_path))
        assert terminal_tool._resolve_task_host_cwd(cfg, "tui:sess-new") is None

    def test_isolation_refuses_process_tagged_override(self, monkeypatch, tmp_path):
        """A cwd override tagged cwd_source='process' (gateway env-var fallback)
        is a launch artifact, not a session workspace — never a mount source."""
        _enable_isolation(monkeypatch)
        terminal_tool.register_task_env_overrides(
            "tui:sess-new", {"cwd": str(tmp_path), "cwd_source": "process"}
        )
        cfg = self._config(host_cwd=str(tmp_path))
        assert terminal_tool._resolve_task_host_cwd(cfg, "tui:sess-new") is None

    def test_isolation_mounts_session_attached_workspace(self, monkeypatch, tmp_path):
        """A workspace the user attached to THIS session does mount."""
        _enable_isolation(monkeypatch)
        ws = tmp_path / "attached"
        ws.mkdir()
        terminal_tool.register_task_env_overrides(
            "tui:sess-new", {"cwd": str(ws), "cwd_source": "session"}
        )
        cfg = self._config(host_cwd="/Users/prev/dev/oldrepo")
        assert terminal_tool._resolve_task_host_cwd(cfg, "tui:sess-new") == str(ws)

    def test_isolation_rejects_nonexistent_session_dir(self, monkeypatch, tmp_path):
        _enable_isolation(monkeypatch)
        terminal_tool.register_task_env_overrides(
            "tui:sess-new",
            {"cwd": str(tmp_path / "gone"), "cwd_source": "session"},
        )
        cfg = self._config()
        assert terminal_tool._resolve_task_host_cwd(cfg, "tui:sess-new") is None

    def test_isolation_rejects_in_container_path_as_mount(self, monkeypatch):
        _enable_isolation(monkeypatch)
        terminal_tool.register_task_env_overrides(
            "tui:sess-new", {"cwd": "/workspace", "cwd_source": "session"}
        )
        cfg = self._config()
        assert terminal_tool._resolve_task_host_cwd(cfg, "tui:sess-new") is None

    def test_mount_flag_off_means_no_mount(self, monkeypatch, tmp_path):
        _enable_isolation(monkeypatch)
        ws = tmp_path / "attached"
        ws.mkdir()
        terminal_tool.register_task_env_overrides(
            "tui:sess-new", {"cwd": str(ws), "cwd_source": "session"}
        )
        cfg = self._config(mount=False)
        assert terminal_tool._resolve_task_host_cwd(cfg, "tui:sess-new") is None

    def test_default_task_keeps_validated_legacy_behavior_under_isolation(self, monkeypatch, tmp_path):
        """The single-session CLI parent keeps its validated configured mount."""
        _enable_isolation(monkeypatch)
        cfg = self._config(host_cwd=str(tmp_path))
        assert (
            terminal_tool._resolve_task_host_cwd(cfg, None)
            == str(tmp_path)
        )

    def test_non_docker_backend_never_mounts(self, monkeypatch):
        _disable_isolation(monkeypatch)
        cfg = self._config()
        cfg["env_type"] = "modal"
        assert terminal_tool._resolve_task_host_cwd(cfg, "t") is None


class TestRecordedHostCwdDiscardedOnContainers:
    """_resolve_command_cwd must not cd to a recorded HOST path in a sandbox.

    The reported exit-126 bug: the desktop gateway records the host launch
    dir as the session cwd; every subsequent command then ran
    ``cd /Users/<user>/dev/<repo> && <cmd>`` inside the container.
    """

    def test_host_record_discarded_for_docker(self):
        terminal_tool.record_session_cwd("sess-1", "/Users/me/dev/repo")
        cwd = terminal_tool._resolve_command_cwd(
            workdir=None, default_cwd="/workspace",
            session_key="sess-1", env_type="docker",
        )
        assert cwd == "/workspace"

    def test_exact_host_root_record_discarded_for_docker(self):
        terminal_tool.record_session_cwd("sess-1", "/home")
        cwd = terminal_tool._resolve_command_cwd(
            workdir=None, default_cwd="/root",
            session_key="sess-1", env_type="docker",
        )
        assert cwd == "/root"

    def test_exact_host_root_explicit_workdir_discarded_for_docker(self):
        cwd = terminal_tool._resolve_command_cwd(
            workdir="/Users", default_cwd="/root",
            session_key="sess-1", env_type="docker",
        )
        assert cwd == "/root"

    def test_container_record_honored_for_docker(self):
        """A legitimate in-container cd is the session's state — keep it."""
        terminal_tool.record_session_cwd("sess-1", "/workspace/subdir")
        cwd = terminal_tool._resolve_command_cwd(
            workdir=None, default_cwd="/workspace",
            session_key="sess-1", env_type="docker",
        )
        assert cwd == "/workspace/subdir"

    def test_host_record_kept_for_local_backend(self):
        terminal_tool.record_session_cwd("sess-1", "/home/me/project")
        cwd = terminal_tool._resolve_command_cwd(
            workdir=None, default_cwd="/anything",
            session_key="sess-1", env_type="local",
        )
        assert cwd == "/home/me/project"

    def test_explicit_workdir_still_wins(self):
        terminal_tool.record_session_cwd("sess-1", "/workspace/a")
        cwd = terminal_tool._resolve_command_cwd(
            workdir="/workspace/b", default_cwd="/workspace",
            session_key="sess-1", env_type="docker",
        )
        assert cwd == "/workspace/b"

    def test_explicit_host_workdir_remaps_to_mounted_workspace(self):
        cwd = terminal_tool._resolve_command_cwd(
            workdir="/Users/me/dev/repo", default_cwd="/root",
            session_key="sess-1", env_type="docker",
            host_cwd="/Users/me/dev/repo",
        )
        assert cwd == "/workspace"

    def test_explicit_relative_workdir_maps_beneath_mounted_workspace(self):
        cwd = terminal_tool._resolve_command_cwd(
            workdir="src/", default_cwd="/root",
            session_key="sess-1", env_type="docker",
            host_cwd="/Users/me/dev/repo",
        )
        assert cwd == "/workspace/src"

    def test_explicit_unrelated_host_workdir_falls_back_even_with_a_mount(self):
        cwd = terminal_tool._resolve_command_cwd(
            workdir="/Users/me/private", default_cwd="/root",
            session_key="sess-1", env_type="docker",
            host_cwd="/Users/me/dev/repo",
        )
        assert cwd == "/root"

    def test_explicit_dot_segment_host_escape_falls_back_even_with_a_mount(self):
        cwd = terminal_tool._resolve_command_cwd(
            workdir="/Users/me/dev/repo/../private", default_cwd="/root",
            session_key="sess-1", env_type="docker",
            host_cwd="/Users/me/dev/repo",
        )
        assert cwd == "/root"

    def test_explicit_host_workdir_falls_back_without_a_mount(self):
        cwd = terminal_tool._resolve_command_cwd(
            workdir="/Users/me/dev/repo", default_cwd="/root",
            session_key="sess-1", env_type="docker", host_cwd=None,
        )
        assert cwd == "/root"

    def test_plugin_container_workdirs_fall_back_without_a_mount(self):
        """Plugin containers use the same host/relative CWD boundary as Docker."""
        from agent import terminal_env_registry as registry
        from agent.terminal_env_provider import TerminalEnvironmentProvider

        class _PluginContainer(TerminalEnvironmentProvider):
            is_container = True

            @property
            def name(self) -> str:
                return "cwd_testbox"

            def is_available(self) -> bool:
                return True

            def create_environment(
                self, *, cwd, timeout, task_id="default", image=None,
                container_config=None, **_kwargs,
            ):
                return None

        provider = _PluginContainer()
        previous = registry.snapshot_registration(provider.name)
        registry.register_provider(provider)
        try:
            for workdir in ("/Users/me/dev/repo", "src/"):
                cwd = terminal_tool._resolve_command_cwd(
                    workdir=workdir, default_cwd="/root",
                    session_key="sess-1", env_type=provider.name, host_cwd=None,
                )
                assert cwd == "/root"
        finally:
            registry.restore_registration(provider.name, provider, previous)

    def test_no_env_type_keeps_previous_behavior(self):
        """Callers that don't pass env_type (legacy sites) are unchanged."""
        terminal_tool.record_session_cwd("sess-1", "/home/me/project")
        cwd = terminal_tool._resolve_command_cwd(
            workdir=None, default_cwd="/fallback", session_key="sess-1",
        )
        assert cwd == "/home/me/project"


class TestSessionScopedContainerLifecycle:
    def test_session_scoped_env_counts_persistent_for_turn_teardown(self, monkeypatch):
        """Session-scoped containers survive between turns (torn down at
        session close, not per-turn)."""
        _enable_isolation(monkeypatch)

        class _FakeEnv:
            _session_scoped = True
            _persistent = False

        monkeypatch.setitem(
            terminal_tool._active_environments, "tui:sess-1", _FakeEnv()
        )
        try:
            assert terminal_tool.is_persistent_env("tui:sess-1") is True
        finally:
            terminal_tool._active_environments.pop("tui:sess-1", None)

    def test_create_environment_marks_session_scoped(self, monkeypatch):
        """_create_environment disables cross-process persist for session
        containers and stamps the marker the lifecycle paths read."""
        _enable_isolation(monkeypatch)
        captured = {}

        class _FakeDockerEnv:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(terminal_tool, "_DockerEnvironment", _FakeDockerEnv)
        monkeypatch.setattr(terminal_tool, "_maybe_reap_docker_orphans", lambda cc: None)

        env = terminal_tool._create_environment(
            env_type="docker", image="img:1", cwd="/workspace", timeout=60,
            container_config={"docker_persist_across_processes": True},
            task_id="tui:sess-1",
        )
        assert captured["persist_across_processes"] is False
        assert getattr(env, "_session_scoped") is True

    def test_create_environment_default_task_not_session_scoped(self, monkeypatch):
        _enable_isolation(monkeypatch)
        captured = {}

        class _FakeDockerEnv:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(terminal_tool, "_DockerEnvironment", _FakeDockerEnv)
        monkeypatch.setattr(terminal_tool, "_maybe_reap_docker_orphans", lambda cc: None)

        env = terminal_tool._create_environment(
            env_type="docker", image="img:1", cwd="/workspace", timeout=60,
            container_config={"docker_persist_across_processes": True},
            task_id="default",
        )
        assert captured["persist_across_processes"] is True
        assert getattr(env, "_session_scoped", False) is False


class TestEnvironmentCwdComposition:
    """``_resolve_environment_cwd``: one owner for the environment-creation cwd.

    Blue routes every environment cwd through ``_sanitize_environment_cwd`` so
    a host/relative path recorded by ACP/desktop can never reach
    ``docker run -w``. Green additionally remaps such a path to ``/workspace``
    when THIS session's workspace is actually bind-mounted there, instead of
    dropping the user to the image default. Both must hold.
    """

    def _cfg(self, env_type="docker", default_cwd="/workspace"):
        return {"env_type": env_type, "cwd": default_cwd}

    def test_host_cwd_remaps_to_workspace_when_session_workspace_is_mounted(self):
        cwd = terminal_tool._resolve_environment_cwd(
            "/Users/me/dev/repo", self._cfg(default_cwd="/root"),
            host_cwd="/Users/me/dev/repo",
        )
        assert cwd == "/workspace", "a mounted session workspace lands at /workspace"

    def test_relative_cwd_maps_beneath_mounted_workspace(self):
        cwd = terminal_tool._resolve_environment_cwd(
            "src/", self._cfg(default_cwd="/root"), host_cwd="/Users/me/dev/repo",
        )
        assert cwd == "/workspace/src"

    def test_mounted_host_descendant_maps_beneath_workspace(self):
        cwd = terminal_tool._resolve_environment_cwd(
            "/Users/me/dev/repo/src", self._cfg(default_cwd="/root"),
            host_cwd="/Users/me/dev/repo",
        )
        assert cwd == "/workspace/src"

    def test_unrelated_host_cwd_falls_back_even_when_workspace_is_mounted(self):
        cwd = terminal_tool._resolve_environment_cwd(
            "/Users/me/private", self._cfg(default_cwd="/root"),
            host_cwd="/Users/me/dev/repo",
        )
        assert cwd == "/root"

    def test_posix_rooted_cwd_does_not_map_under_windows_mount(self):
        cwd = terminal_tool._resolve_environment_cwd(
            "/Users/me/private", self._cfg(default_cwd="/root"),
            host_cwd=r"C:\repo",
        )
        assert cwd == "/root"

    def test_escaping_relative_cwd_falls_back_even_when_workspace_is_mounted(self):
        cwd = terminal_tool._resolve_environment_cwd(
            "../private", self._cfg(default_cwd="/root"),
            host_cwd="/Users/me/dev/repo",
        )
        assert cwd == "/root"

    def test_dot_segment_host_escape_falls_back_even_when_workspace_is_mounted(self):
        cwd = terminal_tool._resolve_environment_cwd(
            "/Users/me/dev/repo/../private", self._cfg(default_cwd="/root"),
            host_cwd="/Users/me/dev/repo",
        )
        assert cwd == "/root"

    def test_dot_segment_host_descendant_maps_to_normalized_workspace_child(self):
        cwd = terminal_tool._resolve_environment_cwd(
            "/Users/me/dev/repo/src/../child", self._cfg(default_cwd="/root"),
            host_cwd="/Users/me/dev/repo",
        )
        assert cwd == "/workspace/child"

    def test_dot_segment_relative_child_maps_to_normalized_workspace_child(self):
        cwd = terminal_tool._resolve_environment_cwd(
            "src/../child", self._cfg(default_cwd="/root"),
            host_cwd="/Users/me/dev/repo",
        )
        assert cwd == "/workspace/child"

    def test_host_cwd_falls_back_to_config_default_without_a_mount(self):
        cwd = terminal_tool._resolve_environment_cwd(
            "/Users/me/dev/repo", self._cfg(default_cwd="/root"), host_cwd=None,
        )
        assert cwd == "/root", "no mount means the host path must be discarded"

    def test_in_container_cwd_passes_through_untouched(self):
        for host_cwd in (None, "/Users/me/dev/repo"):
            assert terminal_tool._resolve_environment_cwd(
                "/workspace/subdir", self._cfg(), host_cwd=host_cwd,
            ) == "/workspace/subdir"
            assert terminal_tool._resolve_environment_cwd(
                "/root", self._cfg(), host_cwd=host_cwd,
            ) == "/root"

    def test_local_backend_keeps_the_host_path(self):
        cwd = terminal_tool._resolve_environment_cwd(
            "/Users/me/dev/repo", self._cfg(env_type="local", default_cwd="/tmp"),
            host_cwd=None,
        )
        assert cwd == "/Users/me/dev/repo"

    def test_empty_cwd_is_left_to_the_caller_default(self):
        assert terminal_tool._resolve_environment_cwd(
            "", self._cfg(), host_cwd="/Users/me/dev/repo",
        ) == ""
