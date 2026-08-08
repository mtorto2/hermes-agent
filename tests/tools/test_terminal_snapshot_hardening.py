"""Regression coverage for terminal spill and snapshot hardening."""

import os
import shlex
import sys

import pytest

from tools.environments.base import _BoundedOutputCollector


class TestBoundedOutputSpillHardening:
    def test_spill_transforms_a_secret_split_across_stream_chunks_before_write(self, tmp_path):
        secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
        spill = tmp_path / "terminal-output.log"
        collector = _BoundedOutputCollector(
            8,
            spill_path=spill,
            spill_transform=lambda text: text.replace(secret, "[REDACTED]"),
        )

        collector.append("prefix " + secret[:16])
        collector.append(secret[16:] + " suffix\n")

        assert collector.close_spill() == str(spill)
        stored = spill.read_text(encoding="utf-8")
        assert secret not in stored
        assert "[REDACTED]" in stored

    def test_suffix_truncation_materializes_a_recovery_spill(self, tmp_path):
        spill = tmp_path / "terminal-output.log"
        collector = _BoundedOutputCollector(
            100,
            spill_path=spill,
            spill_transform=lambda text: text,
        )
        collector.append("item " * 19)

        rendered = collector.render(suffix="\n[Command timed out]")

        assert "[OUTPUT TRUNCATED" in rendered
        assert collector.close_spill() == str(spill)
        assert spill.read_text(encoding="utf-8") == "item " * 19

    def test_spill_cap_applies_before_the_initial_backlog_write(self, tmp_path, monkeypatch):
        spill = tmp_path / "terminal-output.log"
        monkeypatch.setattr(_BoundedOutputCollector, "_SPILL_CAP_CHARS", 80)
        collector = _BoundedOutputCollector(
            8,
            spill_path=spill,
            spill_transform=lambda text: text,
        )
        collector.append("row " * 40)

        assert collector.close_spill() == str(spill)
        assert collector.spill_capped
        assert len(spill.read_text(encoding="utf-8")) <= 80


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell function override")
def test_snapshot_update_bypasses_a_successful_shell_mktemp_override(tmp_path):
    """A persisted user shell function must not redirect Hermes artifacts."""
    from tools.environments.local import LocalEnvironment

    outside_target = tmp_path / "outside-target"
    outside_target.write_text("unchanged", encoding="utf-8")
    outside_link = tmp_path / "outside-link"
    try:
        os.symlink(outside_target, outside_link)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    env = LocalEnvironment(cwd=str(tmp_path), timeout=30)
    env.init_session()
    assert env._snapshot_ready
    try:
        update = env.execute(
            "mktemp() { printf '%s\\n' "
            f"{shlex.quote(str(outside_link))}; }}; "
            "export SNAPSHOT_TRUSTED_MKTEMP=present; printf UPDATE_OK"
        )
        assert update["returncode"] == 0, update
        assert "UPDATE_OK" in update["output"]
        assert outside_target.read_text(encoding="utf-8") == "unchanged"

        persisted = env.execute(
            'test "${SNAPSHOT_TRUSTED_MKTEMP-}" = present && printf PERSISTED'
        )
        assert persisted["returncode"] == 0, persisted
        assert "PERSISTED" in persisted["output"]
    finally:
        env.cleanup()
