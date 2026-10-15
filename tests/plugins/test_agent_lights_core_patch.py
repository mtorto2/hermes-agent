"""apps/agent-lights-menu-bar/scripts/hermes_core_patch.py against real git fixtures.

Hermes review rounds 4 and 5: removing an old Agent Lights patch must keep
every other change (staged, unstaged, committed, untracked, even when an
unrelated staged edit is hidden by the working tree), only remove what is
provably Agent Lights *and* was approved by digest, and be fully recoverable.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
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


def _base(repo):
    return core.resolve_baseline(repo, None)


def _categories(repo, base):
    return {r.path: r.category for r in core.inventory(repo, base)}


def _backup_and_restore(repo, tmp_path, paths):
    label, base = _base(repo)
    backup = core.backup(repo, label, base, parent=tmp_path / "backups")
    digest, _ = core.review_digest(repo, base, paths)
    return backup, core.restore(repo, base, backup, paths, digest)


def test_adjacent_agent_lights_and_unrelated_edits_are_mixed_and_refused(repo, tmp_path):
    """Round 5 #1: one Agent Lights line made the whole hunk 'Agent Lights'."""
    _edit(repo / "cli.py", 5, "self._emit_light_cue(LightCueEvent.WORKING)")
    _edit(repo / "cli.py", 6, "my_own_tweak = True")  # same -U0 hunk
    _, base = _base(repo)

    assert _categories(repo, base)["cli.py"] == "mixed"
    _, status = _backup_and_restore(repo, tmp_path, ["cli.py"])
    assert status == 3
    assert "my_own_tweak = True" in (repo / "cli.py").read_text()


def test_a_new_file_that_only_mentions_agent_lights_is_review_and_stays(repo, tmp_path):
    """Round 5 #1: a notes file mentioning agent-lights was moved out."""
    (repo / "notes.md").write_text("Remember to try agent-lights someday.\n")
    _, base = _base(repo)

    assert _categories(repo, base)["notes.md"] == "review"
    _, status = _backup_and_restore(repo, tmp_path, ["notes.md"])
    assert status == 3
    assert (repo / "notes.md").exists()


def test_an_unrelated_staged_edit_hidden_by_the_working_tree_blocks_restore(repo, tmp_path):
    """Round 5 #2: staged 'line 30 -> tweak', working tree back to 'line 30',
    plus an Agent Lights edit: the combined diff showed only Agent Lights."""
    _edit(repo / "cli.py", 30, "staged_tweak = 1")
    _git(repo, "add", "cli.py")
    _edit(repo / "cli.py", 30, "line 30")
    _edit(repo / "cli.py", 5, "self._emit_light_cue(LightCueEvent.WORKING)")
    _, base = _base(repo)

    assert _categories(repo, base)["cli.py"] == "mixed"
    _, status = _backup_and_restore(repo, tmp_path, ["cli.py"])
    assert status == 3
    assert "staged_tweak = 1" in _git(repo, "show", ":cli.py")


def test_staged_patch_plus_unrelated_unstaged_edit_is_mixed_and_refused(repo, tmp_path):
    """Round 4 #1, the original fixture."""
    _edit(repo / "cli.py", 5, "self._emit_light_cue(LightCueEvent.WORKING)")
    _git(repo, "add", "cli.py")
    _edit(repo / "cli.py", 30, "my_own_tweak = True")
    label, base = _base(repo)

    assert _categories(repo, base)["cli.py"] == "mixed"
    backup, status = _backup_and_restore(repo, tmp_path, ["cli.py"])
    assert status == 3
    assert b"LightCueEvent.WORKING" in (backup / "staged.patch").read_bytes()
    assert b"my_own_tweak" in (backup / "unstaged.patch").read_bytes()
    assert "my_own_tweak = True" in (repo / "cli.py").read_text()


def test_a_pure_patch_comes_out_of_index_and_working_tree_and_other_work_stays(repo, tmp_path):
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
    _, base = _base(repo)

    assert _categories(repo, base) == {
        "agent/light_cues.py": "agent-lights-file",
        "apps/agent-lights-menu-bar/Package.swift": "agent-lights-file",
        "cli.py": "agent-lights-only",
        "notes.txt": "unrelated",
        "other.py": "unrelated",
    }
    backup, status = _backup_and_restore(repo, tmp_path,
                                         ["cli.py", "agent/light_cues.py", "apps/agent-lights-menu-bar"])
    assert status == 0
    assert (repo / "cli.py").read_text() == BASE_CLI
    assert _git(repo, "show", ":cli.py") == BASE_CLI
    assert "agent/light_cues.py" not in _git(repo, "ls-files")
    assert (backup / "removed" / "apps" / "agent-lights-menu-bar" / "Package.swift").exists()
    assert (repo / "other.py").read_text() == "x = 2  # unrelated, unstaged\n"
    assert (repo / "notes.txt").exists()


def test_restore_needs_the_digest_of_exactly_what_was_reviewed(repo, tmp_path):
    _edit(repo / "cli.py", 5, "self._emit_light_cue(LightCueEvent.WORKING)")
    label, base = _base(repo)
    digest, text = core.review_digest(repo, base, ["cli.py"])
    assert "LightCueEvent.WORKING" in text
    _edit(repo / "cli.py", 6, "self._emit_light_cue(LightCueEvent.IDLE)")  # changed after review
    backup = core.backup(repo, label, base, parent=tmp_path / "backups")

    assert core.restore(repo, base, backup, ["cli.py"], digest) == 2
    assert "LightCueEvent.IDLE" in (repo / "cli.py").read_text()
    assert core.main(["restore", "--hermes-dir", str(repo), "--backup", str(backup), "--yes", "cli.py"]) == 2


def test_restore_refuses_a_stale_backup(repo, tmp_path):
    _edit(repo / "cli.py", 5, "self._emit_light_cue(LightCueEvent.WORKING)")
    label, base = _base(repo)
    backup = core.backup(repo, label, base, parent=tmp_path / "backups")
    (repo / "other.py").write_text("x = 3\n")
    digest, _ = core.review_digest(repo, base, ["cli.py"])

    assert core.restore(repo, base, backup, ["cli.py"], digest) == 2
    fresh = core.backup(repo, label, base, parent=tmp_path / "backups")
    assert fresh != backup  # never reuses a backup folder


def _messy_state(repo):
    """Committed, staged, unstaged, untracked and binary changes together."""
    (repo / "agent").mkdir()
    (repo / "agent" / "light_cues.py").write_text("class LightCueService: ...\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "patch")
    _edit(repo / "cli.py", 3, "self._emit_light_cue(LightCueEvent.WORKING)")
    _git(repo, "add", "cli.py")
    (repo / "other.py").write_text("x = 9\n")
    (repo / "blob.bin").write_bytes(bytes(range(256)))
    (repo / "notes.md").write_text("mine\n")


def test_recover_puts_the_checkout_back_exactly(repo, tmp_path):
    """Round 5 #7: a full recovery round trip, verified by fingerprint."""
    _messy_state(repo)
    label, base = _base(repo)
    before = core.state_fingerprint(repo)
    backup = core.backup(repo, label, base, parent=tmp_path / "backups")
    digest, _ = core.review_digest(repo, base, ["agent/light_cues.py", "cli.py"])
    assert core.restore(repo, base, backup, ["agent/light_cues.py", "cli.py"], digest) == 0
    assert core.state_fingerprint(repo) != before

    assert core.main(["recover", "--backup", str(backup), "--yes"]) == 0
    assert core.state_fingerprint(repo) == before


def test_the_readme_commands_also_recover_exactly(repo, tmp_path):
    """The by-hand commands in the backup README use absolute paths and work."""
    _messy_state(repo)
    label, base = _base(repo)
    before = core.state_fingerprint(repo)
    backup = core.backup(repo, label, base, parent=tmp_path / "backups")
    digest, _ = core.review_digest(repo, base, ["cli.py"])
    assert core.restore(repo, base, backup, ["cli.py"], digest) == 0

    readme = (backup / "README.txt").read_text()
    by_hand = readme.split("Or by hand (skip a patch file that is empty):\n", 1)[1].split("\n\n", 1)[0]
    for command in by_hand.strip().splitlines():
        subprocess.run(["/bin/bash", "-c", command], check=True, cwd=tmp_path)
    assert core.state_fingerprint(repo) == before
    assert re.search(r"recover --backup \S+ --yes", readme)


def _outside(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("must not change\n")
    return outside


def test_recover_replaces_a_symlink_instead_of_writing_through_it(repo, tmp_path):
    """Round 6 #1: on Python 3.9 tar extraction followed the link and wrote
    outside the checkout, and the fingerprint read through the link."""
    (repo / "notes.txt").write_text("my note\n")
    label, base = _base(repo)
    before = core.state_fingerprint(repo)
    backup = core.backup(repo, label, base, parent=tmp_path / "backups")
    outside = _outside(tmp_path)
    (repo / "notes.txt").unlink()
    (repo / "notes.txt").symlink_to(outside)
    assert core.state_fingerprint(repo) != before  # a link is not the same state

    assert core.main(["recover", "--backup", str(backup), "--yes"]) == 0
    assert outside.read_text() == "must not change\n"
    assert not (repo / "notes.txt").is_symlink()
    assert (repo / "notes.txt").read_text() == "my note\n"
    assert core.state_fingerprint(repo) == before


def test_recover_refuses_before_changing_anything_when_a_parent_is_a_link(repo, tmp_path):
    (repo / "docs").mkdir()
    (repo / "docs" / "note.txt").write_text("my note\n")
    label, base = _base(repo)
    backup = core.backup(repo, label, base, parent=tmp_path / "backups")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(repo / "docs")
    (repo / "docs").symlink_to(elsewhere)
    (repo / "cli.py").write_text("changed after backup\n")
    state = core.state_fingerprint(repo)

    assert core.main(["recover", "--backup", str(backup), "--yes"]) == 2
    assert core.state_fingerprint(repo) == state  # nothing was reset or written
    assert list(elsewhere.iterdir()) == []


@pytest.mark.macos_only
@pytest.mark.skipif(not Path("/usr/bin/python3").exists(), reason="needs the system python3")
def test_recover_symlink_case_on_the_system_python(repo, tmp_path):
    """The same regression on the Command Line Tools' python3 (3.9 here)."""
    (repo / "notes.txt").write_text("my note\n")
    backup = subprocess.run(["/usr/bin/python3", "-I", str(_TOOL), "backup", "--hermes-dir", str(repo)],
                            capture_output=True, text=True, check=True, env={"HOME": str(tmp_path)}).stdout.strip()
    outside = _outside(tmp_path)
    (repo / "notes.txt").unlink()
    (repo / "notes.txt").symlink_to(outside)

    done = subprocess.run(["/usr/bin/python3", "-I", str(_TOOL), "recover", "--backup", backup, "--yes"],
                          capture_output=True, text=True, env={"HOME": str(tmp_path)})
    assert done.returncode == 0, done.stderr
    assert outside.read_text() == "must not change\n"
    assert (repo / "notes.txt").read_text() == "my note\n" and not (repo / "notes.txt").is_symlink()


def test_the_readme_tar_command_replaces_links_too(repo, tmp_path):
    (repo / "notes.txt").write_text("my note\n")
    label, base = _base(repo)
    backup = core.backup(repo, label, base, parent=tmp_path / "backups")
    outside = _outside(tmp_path)
    (repo / "notes.txt").unlink()
    (repo / "notes.txt").symlink_to(outside)

    tar_line = next(line for line in (backup / "README.txt").read_text().splitlines() if "tar -x" in line)
    subprocess.run(["/bin/bash", "-c", tar_line.split("#")[0]], check=True)
    assert outside.read_text() == "must not change\n"
    assert (repo / "notes.txt").read_text() == "my note\n"
