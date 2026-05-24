import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType, ProcessingOutcome
from gateway.session import SessionSource
from gateway.wiz_light import WiZNotificationLightConfig, set_wiz_notification_light


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    telegram_mod = MagicMock()
    telegram_mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    telegram_mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    telegram_mod.constants.ChatType.GROUP = "group"
    telegram_mod.constants.ChatType.SUPERGROUP = "supergroup"
    telegram_mod.constants.ChatType.CHANNEL = "channel"
    telegram_mod.constants.ChatType.PRIVATE = "private"
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, telegram_mod)


_ensure_telegram_mock()

from gateway.platforms.telegram import TelegramAdapter  # noqa: E402


def _event(chat_id="8609641275"):
    return MessageEvent(
        text="hi",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id=chat_id,
            chat_type="dm",
            user_id="123",
        ),
        message_type=MessageType.TEXT,
        message_id="42",
    )


@pytest.mark.asyncio
async def test_telegram_notification_light_starts_busy_immediately_when_text_is_received(monkeypatch):
    calls = []
    enqueued = []

    def fake_set(config, mode):
        calls.append(mode)
        return True

    monkeypatch.setattr("gateway.platforms.telegram.set_wiz_notification_light", fake_set)
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="fake-token",
            extra={
                "notification_light": {
                    "enabled": True,
                    "hosts": ["192.168.7.170"],
                    "allowed_chat_ids": ["8609641275"],
                }
            },
        )
    )
    event = _event()
    adapter._should_process_message = MagicMock(return_value=True)
    adapter._ensure_forum_commands = AsyncMock()
    adapter._build_message_event = MagicMock(return_value=event)
    adapter._clean_bot_trigger_text = MagicMock(side_effect=lambda text: text)
    adapter._apply_telegram_group_observe_attribution = MagicMock(side_effect=lambda e: e)
    adapter._enqueue_text_event = MagicMock(side_effect=lambda e: enqueued.append(e))

    msg = SimpleNamespace(text="reset me")
    update = SimpleNamespace(message=msg, effective_message=msg, update_id=99)

    await adapter._handle_text_message(update, None)

    assert calls == ["busy"]
    assert enqueued == [event]


@pytest.mark.asyncio
async def test_telegram_notification_light_does_not_change_on_processing_start(monkeypatch):
    calls = []

    def fake_set(config, mode):
        calls.append((config, mode))
        return True

    monkeypatch.setattr("gateway.platforms.telegram.set_wiz_notification_light", fake_set)
    monkeypatch.setenv("TELEGRAM_REACTIONS", "false")
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="fake-token",
            extra={
                "notification_light": {
                    "enabled": True,
                    "hosts": ["192.168.7.170"],
                    "allowed_chat_ids": ["8609641275"],
                }
            },
        )
    )
    adapter._set_reaction = AsyncMock()

    await adapter.on_processing_start(_event())

    assert calls == []
    adapter._set_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_notification_light_sets_ready_only_for_success(monkeypatch):
    calls = []

    def fake_set(config, mode):
        calls.append(mode)
        return True

    monkeypatch.setattr("gateway.platforms.telegram.set_wiz_notification_light", fake_set)
    monkeypatch.setenv("TELEGRAM_REACTIONS", "false")
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="fake-token",
            extra={"notification_light": {"enabled": True, "hosts": ["192.168.7.170"]}},
        )
    )

    await adapter.on_processing_complete(_event(), ProcessingOutcome.SUCCESS)
    await adapter.on_processing_complete(_event(), ProcessingOutcome.FAILURE)
    await adapter.on_processing_complete(_event(), ProcessingOutcome.CANCELLED)

    assert calls == ["ready", "default", "default"]


def test_telegram_notification_light_ignores_unlisted_chat():
    config = WiZNotificationLightConfig.from_mapping(
        {"enabled": True, "hosts": ["192.168.7.170"], "allowed_chat_ids": ["8609641275"]}
    )

    assert config.applies_to_chat("8609641275")
    assert not config.applies_to_chat("other")


def test_telegram_notification_light_accepts_json_string_config_values():
    config = WiZNotificationLightConfig.from_mapping(
        {
            "enabled": True,
            "hosts": '["192.168.7.170"]',
            "ready_rgb": "[255,0,0]",
            "busy_mode": "scene",
            "busy_scene_id": "35",
            "busy_rgb": "[0,64,255]",
        }
    )

    assert config.hosts == ("192.168.7.170",)
    assert config.ready_rgb == (255, 0, 0)
    assert config.busy_mode == "scene"
    assert config.busy_scene_id == 35
    assert config.busy_rgb == (0, 64, 255)


def test_wiz_notification_light_busy_mode_can_use_rgb_or_temperature(monkeypatch):
    sent = []

    def fake_send(host, params, *, port=38899, timeout=0.4):
        sent.append((host, params, port))
        return True

    monkeypatch.setattr("gateway.wiz_light._send_set_pilot", fake_send)

    rgb_config = WiZNotificationLightConfig.from_mapping(
        {
            "enabled": True,
            "hosts": ["192.168.7.170"],
            "busy_mode": "rgb",
            "busy_rgb": [0, 64, 255],
            "busy_dimming": 75,
        }
    )
    temp_config = WiZNotificationLightConfig.from_mapping(
        {
            "enabled": True,
            "hosts": ["192.168.7.170"],
            "busy_mode": "temperature",
            "busy_kelvin": 3000,
            "busy_dimming": 60,
        }
    )

    assert set_wiz_notification_light(rgb_config, "busy")
    assert set_wiz_notification_light(temp_config, "busy")

    assert sent == [
        ("192.168.7.170", {"state": True, "r": 0, "g": 64, "b": 255, "dimming": 75}, 38899),
        ("192.168.7.170", {"state": True, "temp": 3000, "dimming": 60}, 38899),
    ]


def test_wiz_notification_light_payloads_are_best_effort(monkeypatch):
    sent = []

    def fake_send(host, params, *, port=38899, timeout=0.4):
        sent.append((host, params, port))
        return True

    monkeypatch.setattr("gateway.wiz_light._send_set_pilot", fake_send)
    config = WiZNotificationLightConfig.from_mapping(
        {
            "enabled": True,
            "hosts": ["192.168.7.170"],
            "default_kelvin": 5500,
            "default_dimming": 80,
            "ready_rgb": [255, 0, 0],
            "busy_scene_id": 11,
            "busy_dimming": 100,
        }
    )

    assert set_wiz_notification_light(config, "default")
    assert set_wiz_notification_light(config, "busy")
    assert set_wiz_notification_light(config, "ready")

    assert sent == [
        ("192.168.7.170", {"state": True, "temp": 5500, "dimming": 80}, 38899),
        ("192.168.7.170", {"state": True, "sceneId": 11, "dimming": 100}, 38899),
        ("192.168.7.170", {"state": True, "r": 255, "g": 0, "b": 0, "dimming": 100}, 38899),
    ]
