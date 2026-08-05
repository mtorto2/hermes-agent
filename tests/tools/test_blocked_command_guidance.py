"""Tests for blocked-command recovery guidance (parser-limit + backgrounding)."""

import json
import re

import pytest

from tools.approval import _hardline_block_result, _PARSER_LIMIT_DESCRIPTION, _MALFORMED_EXEC_DESCRIPTION
from tools.terminal_tool import _foreground_background_guidance


class TestParserLimitRecovery:
    def test_parser_limit_block_saves_payload_and_names_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        cmd = "python3 -c '" + "x = 1; " * 900 + "'"
        r = _hardline_block_result(_PARSER_LIMIT_DESCRIPTION, cmd)
        assert r["approved"] is False
        assert "RECOVERY" in r["message"]
        assert "blocked-scripts" in r["message"]
        import re as _re
        m = _re.search(r"saved to (\S+\.sh)", r["message"])
        assert m, r["message"]
        from pathlib import Path
        saved = Path(m.group(1))
        assert saved.exists()
        body = saved.read_text()
        assert cmd in body
        assert body.startswith("#!/bin/bash")
        assert "cannot be run through the agent" in r["message"]

    def test_save_failure_requires_manual_execution_outside_hermes(self, monkeypatch):
        import tools.approval as ap
        monkeypatch.setattr(ap, "_save_blocked_payload", lambda c: None)
        r = _hardline_block_result(_PARSER_LIMIT_DESCRIPTION, "python3 -c 'x'")
        assert "outside Hermes" in r["message"]
        assert "write_file" not in r["message"]

    def test_no_command_requires_manual_execution_outside_hermes(self):
        r = _hardline_block_result(_PARSER_LIMIT_DESCRIPTION)
        assert "RECOVERY" in r["message"]
        assert "outside Hermes" in r["message"]

    def test_malformed_exec_block_has_recovery_recipe(self):
        r = _hardline_block_result(_MALFORMED_EXEC_DESCRIPTION)
        assert "RECOVERY" in r["message"]

    def test_real_hardline_blocks_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        r = _hardline_block_result("recursive delete of root filesystem", "rm -rf --no-preserve-root /")
        assert "RECOVERY" not in r["message"]
        assert "unconditional blocklist" in r["message"]
        # And nothing was saved for a genuine hardline block.
        assert not (tmp_path / ".hermes" / "cache" / "blocked-scripts").exists()

    def test_saved_parser_payload_stays_blocked_even_when_forced(self, tmp_path, monkeypatch):
        """A parser-limit recovery file must not turn into a hardline bypass."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        payload = "printf 'safe parser payload\\n'"
        result = _hardline_block_result(_PARSER_LIMIT_DESCRIPTION, payload)
        saved_match = re.search(r"saved to (\S+\.sh)", result["message"])
        assert saved_match, result["message"]

        from tools.terminal_tool import terminal_tool

        rerun = json.loads(
            terminal_tool(
                command=f"bash {saved_match.group(1)}",
                force=True,
                task_id="blocked-parser-payload-regression",
            )
        )

        assert rerun["status"] == "blocked"
        assert rerun["exit_code"] == 1

    def test_saved_parser_payload_symlink_stays_blocked(self, tmp_path, monkeypatch):
        """Replacing the recovery file with a symlink cannot escape its block."""
        from pathlib import Path

        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        result = _hardline_block_result(_PARSER_LIMIT_DESCRIPTION, "printf 'safe'\n")
        saved_match = re.search(r"saved to (\S+\.sh)", result["message"])
        assert saved_match, result["message"]
        saved = Path(saved_match.group(1))
        replacement = tmp_path / "safe-replacement.sh"
        replacement.write_text("printf 'safe replacement\\n'\n", encoding="utf-8")
        saved.unlink()
        saved.symlink_to(replacement)

        from tools.terminal_tool import terminal_tool

        rerun = json.loads(
            terminal_tool(
                command=f"bash {saved}",
                force=True,
                task_id="blocked-parser-payload-symlink-regression",
            )
        )

        assert rerun.get("status") == "blocked"
        assert rerun.get("exit_code") == 1

    def test_old_saved_payloads_cleaned(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        import os
        d = tmp_path / ".hermes" / "cache" / "blocked-scripts"
        d.mkdir(parents=True)
        stale = d / "blocked-1-dead.sh"
        stale.write_text("old")
        os.utime(stale, (1, 1))
        _hardline_block_result(_PARSER_LIMIT_DESCRIPTION, "python3 -c 'y'")
        assert not stale.exists()


class TestBackgroundGuidanceRecipes:
    def test_ampersand_block_names_exact_call_shape(self):
        msg = _foreground_background_guidance("python3 server.py &")
        assert msg is not None
        assert "WITHOUT the '&'" in msg
        assert "background=true" in msg

    def test_nohup_block_names_exact_call_shape(self):
        msg = _foreground_background_guidance("nohup ./worker.sh > /dev/null 2>&1")
        assert msg is not None
        assert "WITHOUT the wrapper" in msg
        assert "notify_on_complete=true" in msg

    def test_plain_command_unaffected(self):
        assert _foreground_background_guidance("echo hello") is None

    def test_quoted_ampersand_not_flagged(self):
        assert _foreground_background_guidance('git commit -m "a & b"') is None
