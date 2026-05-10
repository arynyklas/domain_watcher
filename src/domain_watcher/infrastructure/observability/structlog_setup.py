"""Structlog configuration: JSON in prod, console in dev, with secret scrubbing.

Call :func:`configure` once at application startup. Subsequent calls are
idempotent — re-binding processors lets hot reload swap log_format without
restarting.

The :func:`scrub_secrets` processor is the security boundary: it MUST stay
on the chain in production. Disabling it via ``runtime.scrub_secrets:
false`` emits a startup warning and is intended only for local
development with synthetic secrets.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse, urlunparse

import structlog

_REDACTED = "***"

_SECRET_KEYS = frozenset(
    {
        "bot_token",
        "password",
        "api_key",
        "smtp_password",
        "secret",
        "token",
        "authorization",
    }
)
"""Case-insensitive key matches scrubbed to ``"***"``."""

_URL_KEYS = frozenset(
    {
        "webhook_url",
        "api_base",
        "url",
    }
)
"""Keys whose values are URLs — scrubbed to ``scheme://host``."""


def _is_secret_key(key: str) -> bool:
    return key.lower() in _SECRET_KEYS


def _is_url_key(key: str) -> bool:
    return key.lower() in _URL_KEYS


def _strip_url(value: str) -> str:
    """Return ``scheme://host`` only — drop userinfo, path, query, fragment."""

    try:
        parsed = urlparse(value)
    except ValueError:
        return _REDACTED
    if not parsed.scheme or not parsed.hostname:
        return _REDACTED
    # ``urlparse`` keeps username/password in ``netloc``; rebuild from
    # hostname (and port) to drop them.
    netloc = parsed.hostname
    if parsed.port:
        netloc = f"{parsed.hostname}:{parsed.port}"
    return urlunparse((parsed.scheme, netloc, "", "", "", ""))


def _scrub(value: Any) -> Any:  # noqa: ANN401 — log values are arbitrary
    """Recurse into mappings/sequences and apply the same scrubber."""

    if isinstance(value, dict):
        return {k: _scrub_kv(k, v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(_scrub(v) for v in value)
    return value


def _scrub_kv(key: str, value: Any) -> Any:  # noqa: ANN401 — log values are arbitrary
    if not isinstance(key, str):
        return _scrub(value)
    if _is_secret_key(key):
        return _REDACTED
    if _is_url_key(key) and isinstance(value, str):
        return _strip_url(value)
    return _scrub(value)


def scrub_secrets(
    _logger: object, _name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor — redact known-secret keys and URLs.

    Operates on the top-level event_dict and recurses into nested
    mappings / sequences. The processor is pure (no I/O) and runs on
    every log call, so it MUST be cheap.
    """

    return {k: _scrub_kv(k, v) for k, v in event_dict.items()}


def configure(*, json_format: bool = True, scrub: bool = True) -> None:
    """(Re)configure the structlog and stdlib logging chains.

    Idempotent — re-runs replace previous processor configuration. The
    ``scrub`` flag controls the secret scrubber; turning it off in
    production should emit a warning at the call site.
    """

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if scrub:
        shared_processors.append(scrub_secrets)

    if json_format:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


__all__ = ["configure", "scrub_secrets"]
