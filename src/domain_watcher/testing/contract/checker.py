"""Checker conformance suite (ADR 0004 §4.1).

A checker plugin author subclasses :class:`CheckerContractTest`,
implements :meth:`make_ok`, :meth:`make_transient`, and
:meth:`make_permanent`, and pytest runs the conformance methods against
each variant.
"""

from __future__ import annotations

from typing import Any

import pytest

from domain_watcher.core.checking.value_objects import CheckOutcome
from domain_watcher.core.shared.value_objects import DomainName

_DEMO_DOMAIN = DomainName("contract-example.com")


class CheckerContractTest:
    """Base class for checker conformance — name does not start with ``Test``."""

    def make_ok(self) -> Any:
        raise NotImplementedError(
            "Override make_ok() to return a checker that returns CheckResult.OK."
        )

    def make_transient(self) -> Any:
        raise NotImplementedError(
            "Override make_transient() — checker that fails with TRANSIENT_ERROR."
        )

    def make_permanent(self) -> Any:
        raise NotImplementedError(
            "Override make_permanent() — checker that fails with PERMANENT_ERROR."
        )

    def domain(self) -> DomainName:
        """Subclasses MAY override to use a transport-friendly fixture domain."""

        return _DEMO_DOMAIN

    @pytest.mark.asyncio
    async def test_id_is_classvar_string(self) -> None:
        checker = self.make_ok()
        assert isinstance(type(checker).id, str)
        assert type(checker).id != ""

    @pytest.mark.asyncio
    async def test_ok_returns_expires_at(self) -> None:
        """OK outcome MUST carry a tz-aware ``expires_at``."""

        checker = self.make_ok()
        result = await checker.check(self.domain())
        assert result.outcome == CheckOutcome.OK
        assert result.expires_at is not None
        assert result.expires_at.tzinfo is not None
        assert result.error is None

    @pytest.mark.asyncio
    async def test_transient_failure_classifies_correctly(self) -> None:
        checker = self.make_transient()
        result = await checker.check(self.domain())
        assert result.outcome == CheckOutcome.TRANSIENT_ERROR
        assert result.expires_at is None
        assert result.error is not None and result.error != ""

    @pytest.mark.asyncio
    async def test_permanent_failure_classifies_correctly(self) -> None:
        checker = self.make_permanent()
        result = await checker.check(self.domain())
        assert result.outcome == CheckOutcome.PERMANENT_ERROR
        assert result.expires_at is None
        assert result.error is not None and result.error != ""


__all__ = ["CheckerContractTest"]
