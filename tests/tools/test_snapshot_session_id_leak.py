"""Cross-session HERMES_SESSION_ID leak via the shared bash snapshot.

Regression coverage for the bug where a single long-lived backend serves many
sessions through ONE ``_active_environments["default"]`` LocalEnvironment (the
messaging gateway, TUI, and desktop/web dashboard all collapse the terminal to
"default"). That environment persists a bash *session snapshot* file and
``source``s it before every command. ``export -p`` dumped the FIRST session's
``HERMES_SESSION_ID`` into the snapshot, so every LATER session ``source``d that
stale value and its ``echo $HERMES_SESSION_ID`` reported a FOREIGN session's id
— overriding the correct per-command Popen env injected by
``_inject_session_context_env``.

The fix strips the per-session bridged vars (HERMES_SESSION_* / UI /
CRON_AUTO_DELIVER_) from the snapshot at both dump sites in
``tools/environments/base.py``; they are re-injected fresh on every command.
"""

import os
import re
import sys

import pytest

from tools.environments.base import (
    _SNAPSHOT_EXCLUDED_ENV_REGEX,
    _export_dump_excluding_session_vars,
)


# ---------------------------------------------------------------------------
# Unit: the exclusion regex matches exactly the bridged vars, nothing else.
# ---------------------------------------------------------------------------

def test_regex_matches_bridged_session_vars():
    rx = re.compile(_SNAPSHOT_EXCLUDED_ENV_REGEX)
    # Every var the gateway bridges must be excluded.
    from gateway.session_context import _VAR_MAP

    for name in _VAR_MAP:
        line = f'declare -x {name}="whatever"'
        assert rx.search(line), f"{name} should be excluded from the snapshot"


def test_export_snippet_shape():
    tmp_ref = '"${__hermes_snapshot_tmp_test}"'
    snippet = _export_dump_excluding_session_vars(tmp_ref)
    assert "export -p" in snippet
    # Unset-by-name (not line-grep): multi-line declare values must not leave
    # continuation lines in the snapshot (issue #71296).
    assert "unset" in snippet
    assert "${!HERMES_SESSION_*}" in snippet
    assert "${!HERMES_CRON_AUTO_DELIVER_*}" in snippet
    assert "HERMES_CRON_SESSION" in snippet
    assert "HERMES_UI_SESSION_ID" in snippet
    assert "grep -vE" not in snippet
    # The redirection must be attached to a brace group wrapping the dump,
    # not to a pipeline segment, so the full unset-and-dump sequence goes to
    # the same mktemp-allocated file that the caller will atomically publish.
    assert snippet.lstrip().startswith("{ ")
    assert "|| true; }" in snippet
    assert snippet.rstrip().endswith(f"> {tmp_ref}")


# ---------------------------------------------------------------------------
# Integration: real LocalEnvironment, two sessions, no cross-contamination.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX bash snapshot path")
def test_shared_snapshot_no_cross_session_leak(tmp_path):
    import threading

    from agent import secret_scope
    from gateway.session_context import _VAR_MAP, _UNSET, set_session_vars
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.environments.local import LocalEnvironment

    env = LocalEnvironment(cwd=str(tmp_path), timeout=30)
    env.init_session()
    secret_scope.set_multiplex_active(True)
    try:
        def run_as(sid, home, cron_session):
            out = {}

            def worker():
                for v in _VAR_MAP.values():
                    v.set(_UNSET)
                home_token = set_hermes_home_override(home)
                try:
                    set_session_vars(
                        session_key="k" + sid,
                        session_id=sid,
                        source="desktop",
                        cron_session=cron_session,
                    )
                    out["r"] = env.execute(
                        'printf "[%s][%s][%s]" "$HERMES_SESSION_ID" '
                        '"${HERMES_CRON_SESSION-unset}" "$HERMES_HOME"'
                    )
                finally:
                    reset_hermes_home_override(home_token)

            t = threading.Thread(target=worker)
            t.start()
            t.join()
            return out["r"].get("output", "")

        profile_a = str(tmp_path / "profile-a")
        profile_b = str(tmp_path / "profile-b")
        out_a = run_as("SIDAAA", profile_a, "1")
        out_b = run_as("SIDBBB", profile_b, "")

        assert "SIDAAA" in out_a, f"session A saw {out_a!r}"
        # The core assertion: B must see its OWN id, not A's leaked via snapshot.
        assert "SIDBBB" in out_b, f"session B saw {out_b!r}"
        assert "SIDAAA" not in out_b, f"session B leaked A's id: {out_b!r}"
        assert "[1]" in out_a, f"cron session A saw {out_a!r}"
        assert "[1]" not in out_b, f"non-cron B leaked cron identity: {out_b!r}"
        assert profile_a in out_a, f"profile A saw {out_a!r}"
        assert profile_b in out_b, f"profile B leaked A home: {out_b!r}"
        assert profile_a not in out_b, f"profile B leaked A home: {out_b!r}"

        # And the snapshot file must not carry the session id at all.
        snap = env._snapshot_path
        if os.path.exists(snap):
            with open(snap) as f:
                assert "HERMES_SESSION_ID" not in f.read()
    finally:
        secret_scope.set_multiplex_active(False)
        env.cleanup()
