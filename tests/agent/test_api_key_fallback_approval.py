"""Fail-closed billing gate tests for credential-pool API-key fallback."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent import credential_pool as credential_pool_mod
from agent.agent_runtime_helpers import (
    _approve_api_key_fallback,
    recover_with_credential_pool,
)
from agent.credential_pool import AUTH_TYPE_API_KEY, AUTH_TYPE_OAUTH
from agent.error_classifier import FailoverReason
from hermes_cli import runtime_provider as runtime_provider
from hermes_cli.auth import AuthError


def test_api_key_fallback_approval_policy_is_provider_scoped(monkeypatch):
    monkeypatch.setattr(
        credential_pool_mod,
        "_load_config_safe",
        lambda: {"credential_pool_api_key_fallback_approval": {"anthropic": True}},
    )

    assert credential_pool_mod.api_key_fallback_requires_approval("anthropic") is True
    assert credential_pool_mod.api_key_fallback_requires_approval("openai") is False


def test_api_key_fallback_policy_defaults_off(monkeypatch):
    monkeypatch.setattr(credential_pool_mod, "_load_config_safe", lambda: {})

    assert credential_pool_mod.api_key_fallback_requires_approval("anthropic") is False


def test_denied_api_key_rotation_never_swaps_credential(monkeypatch):
    api_entry = SimpleNamespace(id="api", auth_type=AUTH_TYPE_API_KEY)
    pool = SimpleNamespace(
        provider="anthropic",
        mark_exhausted_and_rotate=Mock(return_value=api_entry),
    )
    agent = SimpleNamespace(
        provider="anthropic",
        api_key="oauth-token",
        _credential_pool=pool,
        _swap_credential=Mock(),
    )
    monkeypatch.setattr(
        "agent.agent_runtime_helpers.api_key_fallback_requires_approval",
        lambda _provider: True,
    )
    monkeypatch.setattr(
        "tools.approval.request_elicitation_consent", lambda **_kwargs: "decline"
    )

    recovered, _ = recover_with_credential_pool(
        agent,
        status_code=402,
        has_retried_429=False,
        classified_reason=FailoverReason.billing,
    )

    assert recovered is False
    agent._swap_credential.assert_not_called()


def test_approved_api_key_rotation_swaps_only_that_request(monkeypatch):
    api_entry = SimpleNamespace(id="api", auth_type=AUTH_TYPE_API_KEY)
    pool = SimpleNamespace(
        provider="anthropic",
        mark_exhausted_and_rotate=Mock(return_value=api_entry),
    )
    agent = SimpleNamespace(
        provider="anthropic",
        api_key="oauth-token",
        _credential_pool=pool,
        _swap_credential=Mock(),
    )
    monkeypatch.setattr(
        "agent.agent_runtime_helpers.api_key_fallback_requires_approval",
        lambda _provider: True,
    )
    monkeypatch.setattr(
        "tools.approval.request_elicitation_consent", lambda **_kwargs: "accept"
    )

    recovered, _ = recover_with_credential_pool(
        agent,
        status_code=402,
        has_retried_429=False,
        classified_reason=FailoverReason.billing,
    )

    assert recovered is True
    agent._swap_credential.assert_called_once_with(api_entry)


def test_oauth_rotation_never_needs_api_key_approval(monkeypatch):
    agent = SimpleNamespace(provider="anthropic")
    oauth_entry = SimpleNamespace(id="oauth", auth_type=AUTH_TYPE_OAUTH)
    monkeypatch.setattr(
        "agent.agent_runtime_helpers.api_key_fallback_requires_approval",
        lambda _provider: True,
    )

    assert _approve_api_key_fallback(agent, oauth_entry, status_code=429) is True


def test_runtime_blocks_direct_api_key_selection_when_policy_enabled(monkeypatch):
    api_entry = SimpleNamespace(
        auth_type=AUTH_TYPE_API_KEY,
        runtime_api_key="test-api-key",
        access_token="test-api-key",
        runtime_base_url="https://api.anthropic.com",
        base_url="https://api.anthropic.com",
    )
    pool = SimpleNamespace(
        has_credentials=lambda: True,
        select=lambda: api_entry,
    )
    monkeypatch.setattr(runtime_provider, "resolve_provider", lambda *_args, **_kwargs: "anthropic")
    monkeypatch.setattr(runtime_provider, "load_pool", lambda _provider: pool)
    monkeypatch.setattr(runtime_provider, "credential_pool_matches_provider", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runtime_provider, "api_key_fallback_requires_approval", lambda _provider: True)
    monkeypatch.setattr(runtime_provider, "_get_model_config", lambda: {"provider": "anthropic"})

    with pytest.raises(AuthError, match="API-key fallback is blocked"):
        runtime_provider.resolve_runtime_provider(requested="anthropic")
