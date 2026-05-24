from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return home


def _event(text: str) -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="123",
            user_id="u1",
            user_name="Matt",
        ),
        message_id="m1",
    )


@pytest.mark.asyncio
async def test_kanban_boards_status_audio_gateway_adds_voice_media(kanban_home, monkeypatch, tmp_path):
    runner = GatewayRunner.__new__(GatewayRunner)
    audio_path = tmp_path / "report.ogg"
    audio_path.write_bytes(b"ogg")
    calls: list[str] = []

    monkeypatch.setattr("tools.tts_tool.check_tts_requirements", lambda: True)

    def fake_tts_tool(*, text: str):
        calls.append(text)
        return json.dumps({"file_path": str(audio_path)})

    monkeypatch.setattr("tools.tts_tool.text_to_speech_tool", fake_tts_tool)

    output = await runner._handle_kanban_command(_event("/kanban boards status audio"))

    assert calls
    assert "Kanban morning report." in calls[0]
    assert "[[audio_as_voice]]" in output
    assert f"MEDIA:{audio_path}" in output
    assert "Kanban morning report." in output


@pytest.mark.asyncio
async def test_kanban_board_status_audio_gateway_adds_voice_media(kanban_home, monkeypatch, tmp_path):
    runner = GatewayRunner.__new__(GatewayRunner)
    audio_path = tmp_path / "report.ogg"
    audio_path.write_bytes(b"ogg")
    calls: list[str] = []

    monkeypatch.setattr("tools.tts_tool.check_tts_requirements", lambda: True)

    def fake_tts_tool(*, text: str):
        calls.append(text)
        return json.dumps({"file_path": str(audio_path)})

    monkeypatch.setattr("tools.tts_tool.text_to_speech_tool", fake_tts_tool)

    output = await runner._handle_kanban_command(_event("/kanban board status audio"))

    assert calls
    assert "Kanban morning report." in calls[0]
    assert "[[audio_as_voice]]" in output
    assert f"MEDIA:{audio_path}" in output
    assert "Kanban morning report." in output


@pytest.mark.asyncio
async def test_kanban_boards_status_audio_gateway_detects_options_before_audio(kanban_home, monkeypatch, tmp_path):
    runner = GatewayRunner.__new__(GatewayRunner)
    audio_path = tmp_path / "report.ogg"
    audio_path.write_bytes(b"ogg")
    calls: list[str] = []

    monkeypatch.setattr("tools.tts_tool.check_tts_requirements", lambda: True)

    def fake_tts_tool(*, text: str):
        calls.append(text)
        return json.dumps({"file_path": str(audio_path)})

    monkeypatch.setattr("tools.tts_tool.text_to_speech_tool", fake_tts_tool)

    output = await runner._handle_kanban_command(_event("/kanban boards status --limit 1 audio"))

    assert calls
    assert "Kanban morning report." in calls[0]
    assert f"MEDIA:{audio_path}" in output


@pytest.mark.asyncio
async def test_kanban_boards_status_audio_gateway_detects_global_board_option(kanban_home, monkeypatch, tmp_path):
    runner = GatewayRunner.__new__(GatewayRunner)
    audio_path = tmp_path / "report.ogg"
    audio_path.write_bytes(b"ogg")
    calls: list[str] = []

    monkeypatch.setattr("tools.tts_tool.check_tts_requirements", lambda: True)

    def fake_tts_tool(*, text: str):
        calls.append(text)
        return json.dumps({"file_path": str(audio_path)})

    monkeypatch.setattr("tools.tts_tool.text_to_speech_tool", fake_tts_tool)

    output = await runner._handle_kanban_command(_event("/kanban --board default board status audio"))

    assert calls
    assert "Kanban morning report." in calls[0]
    assert f"MEDIA:{audio_path}" in output


@pytest.mark.asyncio
async def test_kanban_boards_status_audio_gateway_does_not_tts_json(kanban_home, monkeypatch):
    runner = GatewayRunner.__new__(GatewayRunner)
    calls: list[str] = []

    monkeypatch.setattr("tools.tts_tool.check_tts_requirements", lambda: True)

    def fake_tts_tool(*, text: str):
        calls.append(text)
        return json.dumps({"file_path": "/tmp/report.ogg"})

    monkeypatch.setattr("tools.tts_tool.text_to_speech_tool", fake_tts_tool)

    output = await runner._handle_kanban_command(_event("/kanban boards status audio --json"))

    assert '"overall"' in output
    assert calls == []
    assert "MEDIA:" not in output


@pytest.mark.asyncio
async def test_kanban_boards_status_audio_gateway_preserves_media_after_truncation(kanban_home, monkeypatch, tmp_path):
    runner = GatewayRunner.__new__(GatewayRunner)
    audio_path = tmp_path / "report.ogg"
    audio_path.write_bytes(b"ogg")

    monkeypatch.setattr("tools.tts_tool.check_tts_requirements", lambda: True)
    monkeypatch.setattr(
        "tools.tts_tool.text_to_speech_tool",
        lambda *, text: json.dumps({"file_path": str(audio_path)}),
    )

    for idx in range(90):
        with kb.connect(board="default") as conn:
            kb.create_task(conn, title=f"very long task title {idx} " + ("x" * 80), initial_status="blocked")

    output = await runner._handle_kanban_command(_event("/kanban boards status --limit 90 audio"))

    assert len(output) <= 3800
    assert "[[audio_as_voice]]" in output
    assert output.rstrip().endswith(f"MEDIA:{audio_path}")


@pytest.mark.asyncio
async def test_kanban_boards_status_audio_gateway_keeps_text_when_tts_unavailable(kanban_home, monkeypatch):
    runner = GatewayRunner.__new__(GatewayRunner)
    monkeypatch.setattr("tools.tts_tool.check_tts_requirements", lambda: False)

    output = await runner._handle_kanban_command(_event("/kanban boards status audio"))

    assert "Kanban morning report." in output
    assert "MEDIA:" not in output
    assert "[[audio_as_voice]]" not in output
