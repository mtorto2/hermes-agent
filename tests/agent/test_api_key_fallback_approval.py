"""Fail-closed billing gate tests for credential-pool API-key fallback."""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

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


@pytest.mark.parametrize("auth_type", [AUTH_TYPE_API_KEY, AUTH_TYPE_OAUTH])
@pytest.mark.parametrize("policy_enabled", [False, True])
def test_first_429_without_consumed_fallback_keeps_same_credential_retry(
    monkeypatch, auth_type, policy_enabled
):
    entry = SimpleNamespace(id="ordinary", auth_type=auth_type,
                            runtime_api_key="ordinary-token")
    pool = SimpleNamespace(provider="openai", entries=lambda: [entry],
                           mark_exhausted_and_rotate=Mock())
    agent = SimpleNamespace(provider="openai", api_key=entry.runtime_api_key,
                            _credential_pool_entry_id=entry.id, _credential_pool=pool)
    monkeypatch.setattr("agent.agent_runtime_helpers.api_key_fallback_requires_approval",
                        lambda _provider: policy_enabled)

    assert recover_with_credential_pool(
        agent, status_code=429, has_retried_429=False
    ) == (False, True)
    pool.mark_exhausted_and_rotate.assert_not_called()


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


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("outcome", ["success", "exception", "cancel_before_request"])
@pytest.mark.parametrize("auth_type", [AUTH_TYPE_API_KEY, AUTH_TYPE_OAUTH])
def test_fallback_consent_covers_one_inference_attempt(
    monkeypatch, outcome, auth_type, streaming, native
):
    """Even middleware retrying the request must see the restored safe route."""
    from run_agent import AIAgent

    provider = "anthropic" if native else "openai"
    with patch("run_agent.OpenAI"), patch("run_agent.get_tool_definitions", return_value=[]), \
         patch("agent.anthropic_adapter.build_anthropic_client"):
        agent = AIAgent(api_key="safe-token", provider=provider,
                        api_mode="anthropic_messages" if native else "chat_completions",
                        model="test-model", base_url="https://example.invalid/v1",
                        quiet_mode=True, skip_context_files=True, skip_memory=True)
    agent._disable_streaming = not streaming
    monkeypatch.setattr(agent, "_has_stream_consumers", lambda: streaming)
    agent._credential_pool_entry_id = "safe"
    client_attr = "_anthropic_client" if native else "client"
    original_client = getattr(agent, client_attr)
    original_kwargs = dict(agent._client_kwargs)
    next_client = MagicMock()
    monkeypatch.setattr("agent.anthropic_adapter.build_anthropic_client", lambda *a, **kw: next_client)
    monkeypatch.setattr(agent, "_build_primary_client_for_active_provider", lambda **kw: next_client)
    monkeypatch.setattr(agent, "_try_refresh_env_client_credentials", lambda: None)
    entry = SimpleNamespace(id="next", auth_type=auth_type, runtime_api_key="next-token",
                            runtime_base_url="https://example.invalid/v1")
    agent._credential_pool = SimpleNamespace(provider=provider,
        mark_exhausted_and_rotate=Mock(return_value=entry))
    consent = Mock(return_value="accept")
    monkeypatch.setattr("tools.approval.request_elicitation_consent", consent)
    monkeypatch.setattr("agent.agent_runtime_helpers.api_key_fallback_requires_approval", lambda p: True)
    response = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="done", tool_calls=None, reasoning=None),
        finish_reason="stop")], usage=None, model="test-model")
    if native:
        response = SimpleNamespace(
            id="test-message", type="message", role="assistant", model="test-model",
            content=[SimpleNamespace(type="text", text="done")], stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )
    attempts = []

    def inference(kwargs, **options):
        attempts.append((agent.api_key, getattr(agent, client_attr), agent._credential_pool_entry_id))
        if outcome == "exception" and len(attempts) == 1:
            raise RuntimeError("attempt failed")
        return response

    monkeypatch.setattr(agent, "_interruptible_api_call", inference)
    monkeypatch.setattr(agent, "_interruptible_streaming_api_call", inference)

    def middleware(kwargs, execute, **metadata):
        recovered, _ = recover_with_credential_pool(agent, status_code=402,
            has_retried_429=False, classified_reason=FailoverReason.billing)
        assert recovered
        if outcome == "cancel_before_request":
            return response
        try:
            execute(kwargs)
        except RuntimeError as exc:
            assert str(exc) == "attempt failed"
        return execute(kwargs)

    monkeypatch.setattr("hermes_cli.middleware.run_llm_execution_middleware", middleware)
    result = agent.run_conversation("hello")
    assert result["final_response"] == "done"
    expected = ("safe-token", original_client, "safe") if auth_type == AUTH_TYPE_API_KEY else (
        "next-token", next_client, "next")
    if outcome == "cancel_before_request":
        assert attempts == []
    else:
        assert attempts[0] == ("next-token", next_client, "next")
        assert attempts[1] == expected
    if auth_type == AUTH_TYPE_API_KEY:
        assert agent._client_kwargs == original_kwargs
        assert agent.api_key == "safe-token"
        assert consent.call_count == 1
    else:
        assert agent.api_key == "next-token"
        consent.assert_not_called()

    # The next user turn must also start from the safe runtime without consent.
    monkeypatch.setattr("hermes_cli.middleware.run_llm_execution_middleware",
                        lambda kwargs, execute, **metadata: execute(kwargs))
    agent.run_conversation("next user turn")
    assert attempts[-1] == expected
    if auth_type == AUTH_TYPE_API_KEY:
        original_client.close.assert_not_called()
        assert agent.api_key == "safe-token"
        if native:
            assert agent._anthropic_api_key == "safe-token"
        assert consent.call_count == 1


@pytest.fixture
def physical_fallback(monkeypatch, request):
    """Real helper/rotation paths; only SDK clients and consent are mocked."""
    from run_agent import AIAgent

    native = getattr(request, "param", "openai") == "anthropic"
    provider = "anthropic" if native else "openai"
    with patch("run_agent.OpenAI"), patch("run_agent.get_tool_definitions", return_value=[]), \
         patch("agent.anthropic_adapter.build_anthropic_client"):
        agent = AIAgent(api_key="safe-token", provider=provider,
                        api_mode="anthropic_messages" if native else "chat_completions", model="test-model",
                        base_url="https://example.invalid/v1", quiet_mode=True,
                        skip_context_files=True, skip_memory=True)
    agent._credential_pool_entry_id = "safe"
    client_attr = "_anthropic_client" if native else "client"
    safe_client = getattr(agent, client_attr)
    fallback_client = MagicMock()
    monkeypatch.setattr(agent, "_build_primary_client_for_active_provider", lambda **kw: fallback_client)
    monkeypatch.setattr("agent.anthropic_adapter.build_anthropic_client", lambda *a, **kw: fallback_client)
    monkeypatch.setattr(agent, "_try_refresh_env_client_credentials", lambda: None)
    monkeypatch.setattr("agent.agent_runtime_helpers.api_key_fallback_requires_approval", lambda p: True)
    consent = Mock(return_value="accept")
    monkeypatch.setattr("tools.approval.request_elicitation_consent", consent)
    entry = SimpleNamespace(id="fallback-B", auth_type=AUTH_TYPE_API_KEY,
                            runtime_api_key="fallback-token-B",
                            runtime_base_url=agent.base_url)
    pool = SimpleNamespace(provider=provider, entries=lambda: [entry],
                           mark_exhausted_and_rotate=Mock(return_value=entry))
    agent._credential_pool = pool
    assert recover_with_credential_pool(agent, status_code=402, has_retried_429=False)[0]
    pool.mark_exhausted_and_rotate.reset_mock()
    pool.mark_exhausted_and_rotate.return_value = None
    attempts = []
    outcomes = []

    def make_client(**kwargs):
        key, credential_id, shared = agent.api_key, agent._credential_pool_entry_id, getattr(agent, client_attr)
        client = MagicMock()

        def create(**request):
            attempts.append((key, credential_id, shared))
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            if native:
                message = SimpleNamespace(id="native-message", type="message", role="assistant",
                    model="test-model", content=[SimpleNamespace(type="text", text="done")],
                    stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1, output_tokens=1))
                if request.get("stream"):
                    stream = MagicMock()
                    stream.__iter__.return_value = iter([SimpleNamespace(type="message_stop")])
                    stream.get_final_message.return_value = message
                    manager = MagicMock()
                    manager.__enter__.return_value = stream
                    return manager
                return message
            if request.get("stream"):
                delta = SimpleNamespace(content="done", tool_calls=None, reasoning=None,
                                        reasoning_content=None)
                return iter([SimpleNamespace(choices=[SimpleNamespace(index=0, delta=delta,
                    finish_reason="stop")], model="test-model", usage=None)])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content="done", tool_calls=None), finish_reason="stop")], usage=None)

        client.chat.completions.create.side_effect = create
        client.messages.create.side_effect = create
        client.messages.stream.side_effect = lambda **kw: create(**kw, stream=True)
        return client

    monkeypatch.setattr(agent, "_create_request_openai_client", make_client)
    monkeypatch.setattr(agent, "_create_request_anthropic_client", make_client)
    return agent, pool, attempts, outcomes, safe_client, fallback_client, consent


def _physical_call(agent, path):
    if path == "stream":
        return agent._interruptible_streaming_api_call({})
    agent._disable_streaming = True
    if path == "direct":
        agent.platform = "cron"
    return agent._interruptible_api_call({})


@pytest.mark.parametrize("physical_fallback", ["openai", "anthropic"], indirect=True)
@pytest.mark.parametrize("safe_outcome", ["success", "billing"])
def test_physical_stream_retry_restores_safe_credential(physical_fallback, safe_outcome):
    import httpx
    from openai import APIStatusError

    agent, pool, attempts, outcomes, safe, fallback, consent = physical_fallback
    billing = APIStatusError("billing", response=httpx.Response(402,
        request=httpx.Request("POST", "https://example.invalid")), body=None)
    outcomes.extend([httpx.RemoteProtocolError("connection dropped"),
                     billing if safe_outcome == "billing" else None])
    if safe_outcome == "billing":
        with pytest.raises(APIStatusError):
            _physical_call(agent, "stream")
        recover_with_credential_pool(agent, status_code=402, has_retried_429=False)
        assert pool.mark_exhausted_and_rotate.call_args.kwargs["credential_id"] == "safe"
        assert pool.mark_exhausted_and_rotate.call_args.kwargs["api_key_hint"] == "safe-token"
    else:
        _physical_call(agent, "stream")
    assert attempts == [("fallback-token-B", "fallback-B", fallback),
                        ("safe-token", "safe", safe)]
    safe.close.assert_not_called()
    assert consent.call_count == 1


@pytest.mark.parametrize("path", ["stream", "worker", "direct"])
@pytest.mark.parametrize("status", [402, 429])
@pytest.mark.parametrize("has_retried_429", [False, True])
def test_physical_failure_recovery_consumes_exact_fallback_identity(
    physical_fallback, path, status, has_retried_429
):
    import httpx
    from openai import APIStatusError
    from agent.agent_runtime_helpers import restore_api_key_fallback

    agent, pool, attempts, outcomes, safe, fallback, consent = physical_fallback
    outcomes.append(APIStatusError("billing/rate limit", response=httpx.Response(status,
        request=httpx.Request("POST", "https://example.invalid")), body=None))
    with pytest.raises(APIStatusError):
        _physical_call(agent, path)
    # Match the defensive outer cleanup before credential recovery.
    restore_api_key_fallback(agent)
    assert attempts == [("fallback-token-B", "fallback-B", fallback)]
    assert (agent.api_key, agent._credential_pool_entry_id) == ("safe-token", "safe")
    recover_with_credential_pool(agent, status_code=status, has_retried_429=has_retried_429)
    pool.mark_exhausted_and_rotate.assert_called_once()
    kwargs = pool.mark_exhausted_and_rotate.call_args.kwargs
    assert (kwargs["credential_id"], kwargs["api_key_hint"]) == ("fallback-B", "fallback-token-B")
    assert kwargs["status_code"] == status
    assert "_api_key_fallback_failed_identity" not in agent.__dict__
    # Consumption is one-shot, even before another physical attempt.
    recover_with_credential_pool(agent, status_code=402, has_retried_429=False)
    kwargs = pool.mark_exhausted_and_rotate.call_args.kwargs
    assert (kwargs["credential_id"], kwargs["api_key_hint"]) == ("safe", "safe-token")
    safe.close.assert_not_called()


@pytest.mark.parametrize("path", ["stream", "worker", "direct"])
@pytest.mark.parametrize("fails", [False, True])
def test_physical_attempt_consumes_grant_before_next_request(physical_fallback, path, fails):
    import httpx

    agent, pool, attempts, outcomes, safe, fallback, consent = physical_fallback
    outcomes.extend([httpx.RemoteProtocolError("dropped") if fails else None, None])
    if fails and path != "stream":
        with pytest.raises(httpx.RemoteProtocolError):
            _physical_call(agent, path)
    else:
        _physical_call(agent, path)
    if not (fails and path == "stream"):
        _physical_call(agent, path)
    assert attempts == [("fallback-token-B", "fallback-B", fallback),
                        ("safe-token", "safe", safe)]
    assert getattr(agent, "_anthropic_client" if agent.api_mode == "anthropic_messages" else "client") is safe
    safe.close.assert_not_called()
    assert consent.call_count == 1


@pytest.mark.parametrize("physical_fallback", ["anthropic"], indirect=True)
@pytest.mark.parametrize("path", ["stream", "worker"])
@pytest.mark.parametrize("fails", [False, True])
def test_native_physical_attempt_consumes_grant(physical_fallback, path, fails):
    test_physical_attempt_consumes_grant_before_next_request(physical_fallback, path, fails)


@pytest.mark.parametrize("physical_fallback", ["anthropic"], indirect=True)
@pytest.mark.parametrize("path", ["stream", "worker"])
@pytest.mark.parametrize("status", [402, 429])
@pytest.mark.parametrize("has_retried_429", [False, True])
def test_native_physical_failure_recovery(physical_fallback, path, status, has_retried_429):
    test_physical_failure_recovery_consumes_exact_fallback_identity(
        physical_fallback, path, status, has_retried_429
    )
