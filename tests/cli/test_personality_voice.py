"""Tests for personality-aware TTS voice selection."""

from copy import deepcopy

from hermes_cli.personality_voice import apply_personality_voice_to_config


BASE_CONFIG = {
    "tts": {
        "provider": "elevenlabs",
        "default_voice": "eric",
        "personality_voices": {"tate": "tate"},
        "voice_library": {
            "eric": {
                "provider": "elevenlabs",
                "elevenlabs": {
                    "voice_id": "eric-id",
                    "model_id": "eleven_multilingual_v2",
                },
            },
            "tate": {
                "provider": "elevenlabs",
                "elevenlabs": {
                    "voice_id": "tate-id",
                    "model_id": "eleven_multilingual_v2",
                },
            },
        },
        "elevenlabs": {
            "voice_id": "eric-id",
            "model_id": "eleven_multilingual_v2",
        },
    }
}


def test_tate_personality_selects_tate_voice():
    config = deepcopy(BASE_CONFIG)

    changed, alias, provider = apply_personality_voice_to_config(config, "tate")

    assert changed is True
    assert alias == "tate"
    assert provider == "elevenlabs"
    assert config["tts"]["active_voice"] == "tate"
    assert config["tts"]["elevenlabs"]["voice_id"] == "tate-id"


def test_none_personality_selects_default_voice():
    config = deepcopy(BASE_CONFIG)
    apply_personality_voice_to_config(config, "tate")

    changed, alias, provider = apply_personality_voice_to_config(config, "none")

    assert changed is True
    assert alias == "eric"
    assert provider == "elevenlabs"
    assert config["tts"]["active_voice"] == "eric"
    assert config["tts"]["elevenlabs"]["voice_id"] == "eric-id"


def test_unmapped_personality_falls_back_to_default_voice():
    config = deepcopy(BASE_CONFIG)
    apply_personality_voice_to_config(config, "tate")

    changed, alias, provider = apply_personality_voice_to_config(config, "creative")

    assert changed is True
    assert alias == "eric"
    assert provider == "elevenlabs"
    assert config["tts"]["active_voice"] == "eric"
    assert config["tts"]["elevenlabs"]["voice_id"] == "eric-id"


def test_missing_voice_library_is_noop():
    config = {"tts": {"provider": "elevenlabs"}}

    changed, alias, provider = apply_personality_voice_to_config(config, "tate")

    assert changed is False
    assert alias is None
    assert provider is None
    assert config == {"tts": {"provider": "elevenlabs"}}
