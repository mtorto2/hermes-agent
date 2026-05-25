"""Personality-aware TTS voice selection helpers.

This module keeps the voice-library behavior deliberately config-driven:

    tts:
      default_voice: eric
      personality_voices:
        tate: tate
      voice_library:
        eric:
          provider: elevenlabs
          elevenlabs:
            voice_id: cjVigY5qzO86Huf0OWal
            model_id: eleven_multilingual_v2
        tate:
          provider: elevenlabs
          elevenlabs:
            voice_id: 6xeJMztbegcLnAVl0Eke
            model_id: eleven_multilingual_v2

When a personality changes, CLI/gateway handlers call these helpers to copy the
selected voice profile into the active ``tts.provider`` and provider-specific
config used by ``tools.tts_tool``.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml


_NEUTRAL_PERSONALITIES = {"", "none", "default", "neutral"}


def _normalise_name(name: Optional[str]) -> str:
    return str(name or "").strip().lower()


def _section(config: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = config.get(key)
    if isinstance(value, dict):
        return value
    value = {}
    config[key] = value
    return value


def resolve_voice_alias(config: Dict[str, Any], personality_name: Optional[str]) -> Optional[str]:
    """Resolve a personality name to a configured TTS voice alias.

    Unmapped personalities fall back to ``tts.default_voice``. Returning ``None``
    means no voice-library config exists, so callers should leave TTS unchanged.
    """
    if not isinstance(config, dict):
        return None
    tts = config.get("tts")
    if not isinstance(tts, dict):
        return None

    library = tts.get("voice_library")
    if not isinstance(library, dict) or not library:
        return None

    default_alias = tts.get("default_voice") or tts.get("default_voice_profile")
    mappings = tts.get("personality_voices")
    if not isinstance(mappings, dict):
        mappings = {}

    personality = _normalise_name(personality_name)
    alias = default_alias if personality in _NEUTRAL_PERSONALITIES else mappings.get(personality, default_alias)
    if alias is None:
        return None

    alias = str(alias).strip()
    if alias in library and isinstance(library.get(alias), dict):
        return alias
    return None


def apply_personality_voice_to_config(
    config: Dict[str, Any],
    personality_name: Optional[str],
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Apply the voice profile for *personality_name* to active TTS config.

    Returns ``(changed, alias, provider)``. If ``alias`` is ``None``, no mapping
    was configured and the config is left untouched.
    """
    alias = resolve_voice_alias(config, personality_name)
    if alias is None:
        return False, None, None

    tts = _section(config, "tts")
    library = tts.get("voice_library") or {}
    profile = deepcopy(library.get(alias) or {})
    provider = str(profile.get("provider") or tts.get("provider") or "edge").strip().lower()
    if not provider:
        provider = "edge"

    changed = False
    if tts.get("provider") != provider:
        tts["provider"] = provider
        changed = True
    if tts.get("active_voice") != alias:
        tts["active_voice"] = alias
        changed = True

    provider_profile = profile.get(provider)
    if isinstance(provider_profile, dict):
        provider_config = _section(tts, provider)
        for key, value in provider_profile.items():
            if provider_config.get(key) != value:
                provider_config[key] = value
                changed = True

    return changed, alias, provider


def save_personality_voice(personality_name: Optional[str]) -> Tuple[bool, Optional[str], Optional[str]]:
    """Load config.yaml, apply a personality voice, and atomically save it."""
    from hermes_cli.config import get_config_path
    from utils import atomic_yaml_write

    config_path = Path(get_config_path())
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    except Exception:
        config = {}
    if not isinstance(config, dict):
        config = {}

    changed, alias, provider = apply_personality_voice_to_config(config, personality_name)
    if changed:
        atomic_yaml_write(config_path, config, sort_keys=False)
    return changed, alias, provider
