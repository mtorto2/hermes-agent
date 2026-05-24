"""Tests for Telegram light cue mode controls."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

    mod = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN = "Markdown"
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ChatType.PRIVATE = "private"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from gateway.config import PlatformConfig
from gateway.platforms.telegram import TelegramAdapter
from agent.light_cues import LightCueMode, load_light_cue_mode


class Button:
    def __init__(self, text, callback_data=None):
        self.text = text
        self.callback_data = callback_data


class Markup:
    def __init__(self, rows):
        self.inline_keyboard = rows


def _make_adapter(extra=None):
    config = PlatformConfig(enabled=True, token="test-token", extra=extra or {})
    adapter = TelegramAdapter(config)
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


@pytest.mark.asyncio
async def test_light_cue_menu_renders_all_modes_with_short_callbacks(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _make_adapter()
    mock_msg = MagicMock()
    mock_msg.message_id = 4242
    adapter._bot.send_message = AsyncMock(return_value=mock_msg)

    with patch("gateway.platforms.telegram.InlineKeyboardButton", Button), patch(
        "gateway.platforms.telegram.InlineKeyboardMarkup", Markup
    ):
        result = await adapter.send_light_cue_menu(chat_id="12345")

    assert result.success is True
    kwargs = adapter._bot.send_message.call_args[1]
    assert kwargs["chat_id"] == 12345
    assert "Light cue mode" in kwargs["text"]
    rows = kwargs["reply_markup"].inline_keyboard
    buttons = [button for row in rows for button in row]
    assert [button.callback_data for button in buttons] == [
        "lc:default",
        "lc:night",
        "lc:no-light",
    ]
    assert all(len(button.callback_data.encode("utf-8")) <= 64 for button in buttons)


@pytest.mark.asyncio
async def test_light_command_rejects_unauthorized_user_before_sending_menu(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _make_adapter()
    adapter._bot.username = "HermesBot"
    adapter.send_light_cue_menu = AsyncMock()

    class _DenyRunner:
        async def _handle_message(self, event):
            return None

        def _is_user_authorized(self, source):
            return False

    adapter._message_handler = _DenyRunner()._handle_message
    adapter._should_process_message = lambda msg, is_command=False: True
    adapter._ensure_forum_commands = AsyncMock()

    msg = MagicMock()
    msg.text = "/light"
    msg.message_id = 101
    msg.message_thread_id = None
    msg.is_topic_message = False
    msg.date = None
    msg.reply_to_message = None
    msg.chat.id = 12345
    msg.chat.type = "private"
    msg.chat.title = None
    msg.chat.full_name = "Private Chat"
    msg.chat.is_forum = False
    msg.from_user.id = "999"
    msg.from_user.full_name = "Mallory"
    update = MagicMock()
    update.update_id = 202
    update.effective_message = msg
    context = MagicMock()

    await adapter._handle_command(update, context)

    adapter.send_light_cue_menu.assert_not_called()


@pytest.mark.asyncio
async def test_light_cue_callback_updates_and_persists_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _make_adapter()

    query = AsyncMock()
    query.data = "lc:night"
    query.message = MagicMock()
    query.message.chat_id = 12345
    query.message.chat.type = "private"
    query.from_user = MagicMock()
    query.from_user.id = "777"
    query.from_user.first_name = "Tester"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    context = MagicMock()

    with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
        await adapter._handle_callback_query(update, context)

    assert adapter._get_light_cue_service().mode is LightCueMode.NIGHT
    assert load_light_cue_mode() is LightCueMode.NIGHT
    query.answer.assert_called_once()
    assert "night" in query.answer.call_args[1]["text"].lower()
    query.edit_message_text.assert_called_once()


@pytest.mark.asyncio
async def test_light_cue_callback_rejects_unauthorized_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _make_adapter()

    class _DenyRunner:
        async def _handle_message(self, event):
            return None

        def _is_user_authorized(self, source):
            return False

    adapter._message_handler = _DenyRunner()._handle_message

    query = AsyncMock()
    query.data = "lc:no-light"
    query.message = MagicMock()
    query.message.chat_id = 12345
    query.message.chat.type = "private"
    query.from_user = MagicMock()
    query.from_user.id = "999"
    query.from_user.first_name = "Mallory"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    context = MagicMock()

    await adapter._handle_callback_query(update, context)

    assert load_light_cue_mode() is LightCueMode.DEFAULT
    query.answer.assert_called_once()
    assert "not authorized" in query.answer.call_args[1]["text"].lower()
    query.edit_message_text.assert_not_called()
