"""Composition root integration tests (Task 10.1).

Loads ``tests/fixtures/config/valid.yaml``, composes a ``DomainWatcher``,
runs a single ``check_now`` against a recorded RDAP fixture, and asserts
that the expected ``DomainCheckCompleted`` event was published.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from domain_watcher import DomainCheckCompleted, DomainName
from domain_watcher.composition import compose_from_config
from domain_watcher.core.checking.value_objects import CheckOutcome
from domain_watcher.infrastructure.config.loader import load_config


def _rdap_payload() -> dict[str, Any]:
    """A minimal RDAP response with a single ``expiration`` event."""
    return {
        "objectClassName": "domain",
        "ldhName": "example.com",
        "events": [
            {"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"},
            {"eventAction": "expiration", "eventDate": "2030-01-01T00:00:00Z"},
        ],
    }


def _bootstrap_payload() -> dict[str, Any]:
    """A minimal IANA bootstrap response mapping ``com`` to a fake RDAP base."""
    return {
        "version": "1.0",
        "publication": "2026-05-09T00:00:00Z",
        "services": [
            [["com"], ["https://rdap.test/com/"]],
        ],
    }


def _mock_handler(req: httpx.Request) -> httpx.Response:
    """Route bootstrap + RDAP requests to canned fixtures."""
    url = str(req.url)
    if url.startswith("https://data.iana.org/rdap/dns.json"):
        return httpx.Response(200, content=json.dumps(_bootstrap_payload()))
    if url.startswith("https://rdap.test/com/domain/"):
        return httpx.Response(200, content=json.dumps(_rdap_payload()))
    return httpx.Response(404, text=f"unmocked: {url}")


@pytest.fixture
def env_for_valid_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TG_BOT_TOKEN", "abc123")
    monkeypatch.setenv("TG_OPS_CHAT", "-100200")
    monkeypatch.setenv("PD_TOKEN", "pd-token")
    # Push the state DB into tmp_path so tests don't pollute the cwd.
    monkeypatch.chdir(tmp_path)


async def _collect_one(it: AsyncIterator, type_: type) -> object:
    async for event in it:
        if isinstance(event, type_):
            return event
    raise AssertionError(f"no {type_.__name__} observed")


async def test_compose_from_valid_yaml_runs_one_check(
    env_for_valid_yaml: None,
) -> None:
    """End-to-end happy path: yaml → compose → check_now → event published."""
    valid_yaml = (
        Path(__file__).resolve().parents[1] / "fixtures" / "config" / "valid.yaml"
    )
    cfg = load_config(valid_yaml)

    # The valid.yaml uses sqlite:///state.db. Override to memory so the test
    # is hermetic — we keep the rest of the YAML wiring exactly as-is.
    cfg = cfg.model_copy(
        update={"runtime": cfg.runtime.model_copy(update={"state_db": "memory://"})}
    )

    transport = httpx.MockTransport(_mock_handler)
    client = httpx.AsyncClient(transport=transport)
    watcher = compose_from_config(cfg, http_client=client)
    try:
        await watcher.start()

        events: list = []

        async def _on_completed(event: DomainCheckCompleted) -> None:
            events.append(event)

        watcher.on(DomainCheckCompleted, _on_completed)

        # The valid.yaml seeds example.com under the rdap checker; the mock
        # transport answers both bootstrap and RDAP.
        result = await watcher.check_now(DomainName("example.com"))
        assert result.outcome is CheckOutcome.OK
        assert result.expires_at == datetime(2030, 1, 1, tzinfo=UTC)
        assert len(events) == 1
        assert isinstance(events[0], DomainCheckCompleted)
    finally:
        await watcher.stop()
        await client.aclose()


async def test_compose_seeds_repository_with_initial_domains(
    env_for_valid_yaml: None,
) -> None:
    """``start()`` must persist every config-declared domain into the repo."""
    valid_yaml = (
        Path(__file__).resolve().parents[1] / "fixtures" / "config" / "valid.yaml"
    )
    cfg = load_config(valid_yaml)
    cfg = cfg.model_copy(
        update={"runtime": cfg.runtime.model_copy(update={"state_db": "memory://"})}
    )
    watcher = compose_from_config(cfg)
    try:
        await watcher.start()
        names = sorted(d.name.value for d in await watcher.repo.list_all())
        assert names == ["example.com", "example.ru"]
    finally:
        await watcher.stop()


async def test_compose_unknown_checker_type_raises_config_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plugin name typos in the YAML must fail at compose, not at check time."""
    monkeypatch.chdir(tmp_path)
    yaml_text = """
version: 1
runtime:
  state_db: memory://
checkers:
  - id: weirdo
    type: not-a-real-checker
notifiers:
  - id: tg
    type: telegram
    settings: {bot_token: x, chat_id: y}
domains: []
"""
    config = tmp_path / "c.yaml"
    config.write_text(yaml_text)
    cfg = load_config(config)
    from domain_watcher.core.shared.errors import ConfigError

    with pytest.raises(ConfigError, match="unknown checker type"):
        compose_from_config(cfg)


async def test_compose_uses_mock_client_for_rdap(
    env_for_valid_yaml: None,
) -> None:
    """Sanity: the override hook actually replaces the production httpx client."""
    valid_yaml = (
        Path(__file__).resolve().parents[1] / "fixtures" / "config" / "valid.yaml"
    )
    cfg = load_config(valid_yaml)
    cfg = cfg.model_copy(
        update={"runtime": cfg.runtime.model_copy(update={"state_db": "memory://"})}
    )

    captured: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(str(req.url))
        return _mock_handler(req)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    watcher = compose_from_config(cfg, http_client=client)
    try:
        await watcher.start()
        await watcher.check_now(DomainName("example.com"))
    finally:
        await watcher.stop()
        await client.aclose()

    # Bootstrap fetched then domain-specific RDAP query.
    assert any("data.iana.org" in u for u in captured)
    assert any("rdap.test/com/domain/example.com" in u for u in captured)
