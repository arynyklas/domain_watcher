"""LiteLLM-backed ``RuleSuggester``.

Brokers ~100 LLM providers behind a single ``model: <provider>/<model>``
config string (``ollama/gemma3``, ``openai/gpt-4o-mini``, ``anthropic/...``).
``temperature=0`` and JSON-object response format are non-overridable —
the safety pipeline downstream depends on JSON-only responses and any
configurable knob here would be footgun-shaped.

Failures map to ``SuggestionError`` with a ``transient`` flag the caller
uses to decide whether to retry now or back off:

- Timeouts, connection errors, 5xx → transient.
- AuthenticationError, malformed JSON, invalid regex → permanent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import litellm
import litellm.exceptions

from domain_watcher.core.parsing.value_objects import (
    DateFormat,
    ParseRule,
    RegexPattern,
)
from domain_watcher.core.shared.errors import SuggestionError
from domain_watcher.infrastructure.parsers._prompts import build_messages

if TYPE_CHECKING:
    from collections.abc import Mapping

    from domain_watcher.core.shared.value_objects import DomainName


_DATE_FORMAT_ALIASES: Mapping[str, DateFormat] = {
    "iso8601": DateFormat.ISO_8601,
    "iso-8601": DateFormat.ISO_8601,
    "iso_8601": DateFormat.ISO_8601,
    "rfc3339": DateFormat.RFC_3339,
    "rfc-3339": DateFormat.RFC_3339,
    "rfc_3339": DateFormat.RFC_3339,
    "yyyy-mm-dd": DateFormat.YYYY_MM_DD,
    "yyyymmdd": DateFormat.YYYY_MM_DD,
    "dd-mmm-yyyy": DateFormat.DD_MMM_YYYY,
    "epoch": DateFormat.EPOCH_SECONDS,
    "epoch_seconds": DateFormat.EPOCH_SECONDS,
    "custom": DateFormat.CUSTOM,
}


def _coerce_date_format(value: object) -> DateFormat:
    if not isinstance(value, str):
        raise SuggestionError(
            f"date_format must be a string, got {type(value).__name__}"
        )
    normalized = value.strip().lower()
    if normalized in _DATE_FORMAT_ALIASES:
        return _DATE_FORMAT_ALIASES[normalized]
    raise SuggestionError(f"unknown date_format {value!r}")


def _extract_content(response: Any) -> str:  # noqa: ANN401 — litellm response is opaque
    """Unpack ``litellm.acompletion`` → assistant text content.

    LiteLLM returns an OpenAI-shaped response (object or dict). We probe
    the standard locations and fail loudly otherwise.
    """
    try:
        choices = response.choices  # type: ignore[union-attr]
    except AttributeError:
        if isinstance(response, dict):
            choices = response.get("choices", [])
        else:
            raise SuggestionError(  # noqa: B904 — caller doesn't need the cause
                f"unexpected litellm response shape: {type(response).__name__}"
            )
    if not choices:
        raise SuggestionError("litellm response has no choices")
    first = choices[0]
    msg = getattr(first, "message", None)
    if msg is None and isinstance(first, dict):
        msg = first.get("message")
    if msg is None:
        raise SuggestionError("litellm choice missing message")
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if not isinstance(content, str):
        raise SuggestionError(
            f"litellm message.content not a string: {type(content).__name__}"
        )
    return content


def _parse_rule_payload(content: str, *, tld: str) -> ParseRule:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SuggestionError(f"litellm returned malformed JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SuggestionError(
            f"litellm payload is not a JSON object: {type(payload).__name__}"
        )
    expires_regex = payload.get("expires_regex")
    if not isinstance(expires_regex, str) or not expires_regex.strip():
        raise SuggestionError("litellm payload missing or invalid expires_regex")
    date_format = _coerce_date_format(payload.get("date_format"))
    timezone = payload.get("timezone", "UTC")
    if not isinstance(timezone, str) or not timezone.strip():
        timezone = "UTC"
    strptime_format = payload.get("strptime_format")
    if strptime_format is not None and not isinstance(strptime_format, str):
        raise SuggestionError(
            "litellm strptime_format must be string or null, "
            f"got {type(strptime_format).__name__}"
        )
    if date_format is DateFormat.CUSTOM and not strptime_format:
        raise SuggestionError("litellm: date_format=custom but strptime_format missing")
    if date_format is not DateFormat.CUSTOM:
        # ParseRule.__post_init__ refuses strptime_format unless CUSTOM.
        strptime_format = None

    try:
        pattern = RegexPattern(expires_regex)
    except ValueError as exc:
        raise SuggestionError(f"litellm produced invalid regex: {exc}") from exc
    try:
        return ParseRule(
            tld=tld,
            expires_regex=pattern,
            date_format=date_format,
            timezone=timezone,
            strptime_format=strptime_format,
        )
    except ValueError as exc:
        raise SuggestionError(f"litellm produced invalid ParseRule: {exc}") from exc


@dataclass(slots=True)
class LiteLLMRuleSuggester:
    """``RuleSuggester`` backed by ``litellm.acompletion``."""

    id: ClassVar[str] = "litellm"

    model: str
    api_base: str | None = None
    api_key: str | None = None
    timeout: float = 30.0

    async def suggest(self, raw_whois: str, domain: DomainName) -> ParseRule:
        messages = build_messages(
            raw_whois=raw_whois,
            tld=domain.tld,
            domain=domain.value,
        )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "timeout": self.timeout,
            "response_format": {"type": "json_object"},
        }
        if self.api_base is not None:
            kwargs["api_base"] = self.api_base
        if self.api_key is not None:
            kwargs["api_key"] = self.api_key
        try:
            response = await litellm.acompletion(**kwargs)
        except litellm.exceptions.AuthenticationError as exc:
            raise SuggestionError(
                f"litellm auth failure: {exc}", permanent=True
            ) from exc
        except litellm.exceptions.Timeout as exc:
            raise SuggestionError(f"litellm timeout: {exc}", transient=True) from exc
        except (
            litellm.exceptions.APIConnectionError,
            litellm.exceptions.ServiceUnavailableError,
            litellm.exceptions.InternalServerError,
            litellm.exceptions.BadGatewayError,
            litellm.exceptions.RateLimitError,
        ) as exc:
            raise SuggestionError(
                f"litellm transport failure: {exc}", transient=True
            ) from exc
        except litellm.exceptions.APIError as exc:
            raise SuggestionError(f"litellm api error: {exc}", transient=True) from exc

        content = _extract_content(response)
        return _parse_rule_payload(content, tld=domain.tld)


__all__ = ["LiteLLMRuleSuggester"]
