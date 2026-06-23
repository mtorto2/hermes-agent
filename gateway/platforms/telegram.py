"""Compatibility import for the Telegram platform adapter.

The implementation now lives in ``plugins.platforms.telegram.adapter``; keep
this module so Matt-local tests and any existing imports of
``gateway.platforms.telegram`` continue to work during the upstream migration.
"""

from plugins.platforms.telegram.adapter import *  # noqa: F401,F403
