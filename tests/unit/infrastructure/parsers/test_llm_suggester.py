"""``LiteLLMRuleSuggester`` — mocks ``litellm.acompletion`` end-to-end."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import litellm.exceptions
import pytest

from domain_watcher.core.parsing.value_objects import DateFormat
from domain_watcher.core.shared.errors import SuggestionError
from domain_watcher.core.shared.value_objects import DomainName
from domain_watcher.infrastructure.parsers.llm_suggester import LiteLLMRuleSuggester


def _make_response(content: str) -> dict[str, Any]:
    """OpenAI-shaped response that matches what litellm.acompletion returns."""
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _good_payload() -> str:
    return json.dumps(
        {
            "expires_regex": r"Registry Expiry Date:\s+(\S+)",
            "date_format": "iso8601",
            "timezone": "UTC",
        }
    )


@pytest.fixture
def patched_acompletion():
    """Patch ``litellm.acompletion`` for the duration of the test."""
    with patch("litellm.acompletion", new=AsyncMock()) as mock:
        yield mock


async def test_happy_path_returns_parse_rule(patched_acompletion: AsyncMock) -> None:
    patched_acompletion.return_value = _make_response(_good_payload())
    s = LiteLLMRuleSuggester(model="ollama/gemma3", api_base="http://localhost:11434")
    rule = await s.suggest(
        "Registry Expiry Date: 2030-01-01\n", DomainName("example.com")
    )
    assert rule.tld == "com"
    assert rule.date_format is DateFormat.ISO_8601
    assert rule.expires_regex.compiled.search("Registry Expiry Date: 2030-01-01")


async def test_call_kwargs_round_trip(patched_acompletion: AsyncMock) -> None:
    patched_acompletion.return_value = _make_response(_good_payload())
    s = LiteLLMRuleSuggester(
        model="openai/gpt-4o-mini",
        api_base="https://example.test/v1",
        api_key="sk-secret",
        timeout=15.0,
    )
    await s.suggest("anything", DomainName("example.com"))
    kwargs = patched_acompletion.call_args.kwargs
    assert kwargs["model"] == "openai/gpt-4o-mini"
    assert kwargs["api_base"] == "https://example.test/v1"
    assert kwargs["api_key"] == "sk-secret"
    assert kwargs["timeout"] == 15.0
    assert kwargs["temperature"] == 0
    assert kwargs["response_format"] == {"type": "json_object"}
    assert isinstance(kwargs["messages"], list)
    assert kwargs["messages"][0]["role"] == "system"


async def test_no_api_key_omits_kwarg(patched_acompletion: AsyncMock) -> None:
    patched_acompletion.return_value = _make_response(_good_payload())
    s = LiteLLMRuleSuggester(model="ollama/gemma3", api_base="http://localhost:11434")
    await s.suggest("x", DomainName("example.com"))
    kwargs = patched_acompletion.call_args.kwargs
    assert "api_key" not in kwargs


async def test_malformed_json_raises_suggestion_error(
    patched_acompletion: AsyncMock,
) -> None:
    patched_acompletion.return_value = _make_response("not actually json")
    s = LiteLLMRuleSuggester(model="ollama/gemma3")
    with pytest.raises(SuggestionError, match="malformed JSON"):
        await s.suggest("x", DomainName("example.com"))


async def test_invalid_regex_raises_suggestion_error(
    patched_acompletion: AsyncMock,
) -> None:
    payload = json.dumps(
        {"expires_regex": "[unclosed", "date_format": "iso8601", "timezone": "UTC"}
    )
    patched_acompletion.return_value = _make_response(payload)
    s = LiteLLMRuleSuggester(model="ollama/gemma3")
    with pytest.raises(SuggestionError, match="invalid regex"):
        await s.suggest("x", DomainName("example.com"))


async def test_regex_without_capture_group_raises(
    patched_acompletion: AsyncMock,
) -> None:
    payload = json.dumps(
        {
            "expires_regex": "Registry Expiry Date: \\S+",  # no group
            "date_format": "iso8601",
            "timezone": "UTC",
        }
    )
    patched_acompletion.return_value = _make_response(payload)
    s = LiteLLMRuleSuggester(model="ollama/gemma3")
    with pytest.raises(SuggestionError, match="ParseRule"):
        await s.suggest("x", DomainName("example.com"))


async def test_unknown_date_format_raises(patched_acompletion: AsyncMock) -> None:
    payload = json.dumps(
        {
            "expires_regex": r"Foo:\s+(\S+)",
            "date_format": "klingon-stardate",
            "timezone": "UTC",
        }
    )
    patched_acompletion.return_value = _make_response(payload)
    s = LiteLLMRuleSuggester(model="ollama/gemma3")
    with pytest.raises(SuggestionError, match="unknown date_format"):
        await s.suggest("x", DomainName("example.com"))


async def test_custom_date_format_requires_strptime(
    patched_acompletion: AsyncMock,
) -> None:
    payload = json.dumps(
        {
            "expires_regex": r"Foo:\s+(\S+)",
            "date_format": "custom",
            "timezone": "UTC",
            # no strptime_format
        }
    )
    patched_acompletion.return_value = _make_response(payload)
    s = LiteLLMRuleSuggester(model="ollama/gemma3")
    with pytest.raises(SuggestionError, match="strptime_format"):
        await s.suggest("x", DomainName("example.com"))


async def test_timeout_raises_transient(patched_acompletion: AsyncMock) -> None:
    patched_acompletion.side_effect = litellm.exceptions.Timeout(
        message="boom", model="ollama/gemma3", llm_provider="ollama"
    )
    s = LiteLLMRuleSuggester(model="ollama/gemma3")
    with pytest.raises(SuggestionError) as exc_info:
        await s.suggest("x", DomainName("example.com"))
    assert exc_info.value.transient is True


async def test_connection_error_raises_transient(
    patched_acompletion: AsyncMock,
) -> None:
    patched_acompletion.side_effect = litellm.exceptions.APIConnectionError(
        message="conn refused", llm_provider="ollama", model="ollama/gemma3"
    )
    s = LiteLLMRuleSuggester(model="ollama/gemma3")
    with pytest.raises(SuggestionError) as exc_info:
        await s.suggest("x", DomainName("example.com"))
    assert exc_info.value.transient is True


async def test_auth_error_raises_permanent(patched_acompletion: AsyncMock) -> None:
    patched_acompletion.side_effect = litellm.exceptions.AuthenticationError(
        message="bad token", llm_provider="openai", model="openai/gpt-4o-mini"
    )
    s = LiteLLMRuleSuggester(model="openai/gpt-4o-mini")
    with pytest.raises(SuggestionError) as exc_info:
        await s.suggest("x", DomainName("example.com"))
    assert exc_info.value.permanent is True


async def test_id_class_var() -> None:
    assert LiteLLMRuleSuggester.id == "litellm"
