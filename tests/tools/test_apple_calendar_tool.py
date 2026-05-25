import json
import subprocess


def test_apple_calendar_lists_events_with_calendar_and_day_filters(monkeypatch):
    from tools import apple_calendar_tool

    captured = {}

    def fake_run(cmd, *, check, capture_output, text, timeout):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='[{"title":"Gig","calendar":"Gigs"}]',
            stderr="",
        )

    monkeypatch.setattr(apple_calendar_tool, "_ensure_bridge_script", lambda: "/tmp/apple_calendar.swift")
    monkeypatch.setattr(apple_calendar_tool.subprocess, "run", fake_run)

    result = json.loads(
        apple_calendar_tool.apple_calendar(
            action="list_events",
            calendar="Gigs",
            days=30,
        )
    )

    assert result == [{"title": "Gig", "calendar": "Gigs"}]
    assert captured["cmd"] == [
        "/usr/bin/swift",
        "/tmp/apple_calendar.swift",
        "events",
        "--days",
        "30",
        "--calendar",
        "Gigs",
    ]


def test_apple_calendar_refuses_create_without_confirmation(monkeypatch):
    from tools import apple_calendar_tool

    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess should not run without confirmation")

    monkeypatch.setattr(apple_calendar_tool.subprocess, "run", fail_run)

    result = json.loads(
        apple_calendar_tool.apple_calendar(
            action="create_event",
            calendar="Home",
            title=".hermes was here.",
            start="2026-05-26T10:00:00-05:00",
            end="2026-05-26T11:00:00-05:00",
            location="Baton Rouge General Mid-City, Baton Rouge, Louisiana",
        )
    )

    assert result["success"] is False
    assert "requires confirmed=true" in result["error"]


def test_apple_calendar_delete_requires_event_identifier_and_confirmation(monkeypatch):
    from tools import apple_calendar_tool

    result = json.loads(apple_calendar_tool.apple_calendar(action="delete_event", confirmed=True))

    assert result["success"] is False
    assert "event_identifier" in result["error"]


def test_apple_calendar_create_builds_safe_confirmed_command(monkeypatch):
    from tools import apple_calendar_tool

    captured = {}

    def fake_run(cmd, *, check, capture_output, text, timeout):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout='{"status":"created"}', stderr="")

    monkeypatch.setattr(apple_calendar_tool, "_ensure_bridge_script", lambda: "/tmp/apple_calendar.swift")
    monkeypatch.setattr(apple_calendar_tool.subprocess, "run", fake_run)

    result = json.loads(
        apple_calendar_tool.apple_calendar(
            action="create_event",
            confirmed=True,
            calendar="Home",
            title=".hermes was here.",
            start="2026-05-26T10:00:00-05:00",
            end="2026-05-26T11:00:00-05:00",
            location="Baton Rouge General Mid-City, Baton Rouge, Louisiana",
            notes="created by Hermes",
        )
    )

    assert result == {"status": "created"}
    assert captured["cmd"] == [
        "/usr/bin/swift",
        "/tmp/apple_calendar.swift",
        "create",
        "--calendar",
        "Home",
        "--title",
        ".hermes was here.",
        "--start",
        "2026-05-26T10:00:00-05:00",
        "--end",
        "2026-05-26T11:00:00-05:00",
        "--location",
        "Baton Rouge General Mid-City, Baton Rouge, Louisiana",
        "--notes",
        "created by Hermes",
    ]
