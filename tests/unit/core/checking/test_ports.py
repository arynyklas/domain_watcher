from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from domain_watcher.core.checking.ports import ExpirationChecker
from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult

if TYPE_CHECKING:
    from domain_watcher.core.shared.value_objects import DomainName


class _FakeChecker:
    id = "fake"

    async def check(self, domain: DomainName) -> CheckResult:
        return CheckResult(
            domain=domain,
            outcome=CheckOutcome.OK,
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            source=self.id,
        )


def test_runtime_checkable_protocol() -> None:
    assert isinstance(_FakeChecker(), ExpirationChecker)
