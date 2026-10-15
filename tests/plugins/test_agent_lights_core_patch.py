"""apps/agent-lights-menu-bar/scripts/hermes_core_patch.py against real git fixtures.

Pins Hermes review round 4, P1: removing an old Agent Lights patch must keep
every other change (staged, unstaged, committed, untracked), back everything
up first, and take index and working tree back to the stock baseline.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parents[2] / "apps" / "agent-lights-menu-bar" / "scripts" / "hermes_core_patch.py"
_spec = importlib.util.spec_from_file_location("hermes_core_patch_under_test", _TOOL)
core = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = core
_spec.loader.exec_module(core)

BASE_CLI = "\n".join(f"line {i}" for i in range(1, 41)) + "\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def _edit(path: Path, line_no: int, new: str) -> None:
    lines = path.read_text().splitlines()
    lines[line_no - 1] = new
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "hermes-agent"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "cli.py").write_text(BASE_CLI)
    (repo / "other.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "stock")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")  # the stock baseline
    return repo


def _baseline(repo):
    return core.resolve_baseline(repo, None)


def test_staged_patch_plus_unrelated_unstaged_edit_is_mixed_and_refused(repo, tmp_path):
    """The reviewer's fixture: `git checkout -- file` kept the staged patch and
    dropped the unrelated edit; the tool must refuse instead."""
    _edit(repo / "cli.py", 5, "self._emit_light_cue(LightCueEvent.WORKING)")
    _git(repo, "add", "cli.py")
    _edit(repo / "cli.py", 30, "my_own_tweak = True")
    label, base = _baseline(repo)

    reports = {r.path: r for r in core.inventory(repo, base)}
    assert reports["cli.py"].category == "mixed"

    backup = core.backup(repo, label, base, parent=tmp_path / "backups")
    assert b"LightCueEvent.WORKING" in (backup / "staged.patch").read_bytes()
    assert b"my_own_tweak" in (backup / "unstaged.patch").read_bytes()
    assert core.restore(repo, base, backup, ["cli.py"]) == 3
    assert "my_own_tweak = True" in (repo / "cli.py").read_text()
    assert "LightCueEvent" in _git(repo, "diff", "--cached")


def test_committed_staged_and_untracked_patch_comes_out_and_other_work_stays(repo, tmp_path):
    (repo / "agent").mkdir()
    (repo / "agent" / "light_cues.py").write_text("class LightCueService: ...\n")
    _edit(repo / "cli.py", 3, "from agent.light_cues import LightCueService")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add agent lights")
    _edit(repo / "cli.py", 8, "        self._get_light_cue_service().mark_slot_online()")
    _git(repo, "add", "cli.py")  # staged on top of the commit
    (repo / "apps" / "agent-lights-menu-bar").mkdir(parents=True)
    (repo / "apps" / "agent-lights-menu-bar" / "Package.swift").write_text("// swift\n")
    (repo / "other.py").write_text("x = 2  # unrelated, unstaged\n")
    (repo / "notes.txt").write_text("unrelated untracked\n")
    label, base = _baseline(repo)

    reports = {r.path: r.category for r in core.inventory(repo, base)}
    assert reports == {
        "agent/light_cues.py": "agent-lights-file",
        "apps/agent-lights-menu-bar/Package.swift": "agent-lights-file",
        "cli.py": "agent-lights-only",
        "notes.txt": "unrelated",
        "other.py": "unrelated",
    }
    backup = core.backup(repo, label, base, parent=tmp_path / "backups")
    assert core.restore(repo, base, backup, ["cli.py", "agent/light_cues.py", "apps/agent-lights-menu-bar"]) == 0

    # Index and working tree are stock for the patched paths...
    assert (repo / "cli.py").read_text() == BASE_CLI
    assert _git(repo, "show", ":cli.py") == BASE_CLI
    assert not (repo / "agent" / "light_cues.py").exists()
    assert "agent/light_cues.py" not in _git(repo, "ls-files")
    # ...the untracked Agent Lights folder is in the backup, not deleted...
    assert (backup / "removed" / "apps" / "agent-lights-menu-bar" / "Package.swift").exists()
    # ...and everything unrelated is untouched.
    assert (repo / "other.py").read_text() == "x = 2  # unrelated, unstaged\n"
    assert (repo / "notes.txt").exists()
    # The backup can rebuild the old state: refs and patches are there.
    refs = _git(repo, "for-each-ref", "--format=%(refname)", "refs/agent-lights-backup")
    assert "/head" in refs and "/worktree" in refs
    assert b"light_cues" in (backup / "committed.patch").read_bytes()


def test_restore_refuses_a_stale_backup_and_unrelated_paths(repo, tmp_path):
    _edit(repo / "cli.py", 5, "self._emit_light_cue(LightCueEvent.WORKING)")
    label, base = _baseline(repo)
    backup = core.backup(repo, label, base, parent=tmp_path / "backups")
    (repo / "other.py").write_text("x = 3\n")  # changed after the backup

    assert core.restore(repo, base, backup, ["cli.py"]) == 2
    assert "LightCueEvent" in (repo / "cli.py").read_text()

    fresh = core.backup(repo, label, base, parent=tmp_path / "backups")
    assert fresh != backup  # never reuses a backup folder
    assert core.restore(repo, base, fresh, ["other.py"]) == 3
    assert core.restore(repo, base, fresh, ["cli.py"]) == 0
    assert (repo / "other.py").read_text() == "x = 3\n"


def test_cli_restore_requires_explicit_confirmation(repo, tmp_path):
    _edit(repo / "cli.py", 5, "self._emit_light_cue(LightCueEvent.WORKING)")
    label, base = _baseline(repo)
    backup = core.backup(repo, label, base, parent=tmp_path / "backups")

    assert core.main(["restore", "--hermes-dir", str(repo), "--backup", str(backup), "cli.py"]) == 2
    assert "LightCueEvent" in (repo / "cli.py").read_text()
