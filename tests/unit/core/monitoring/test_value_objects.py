from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain_watcher.core.checking.value_objects import CheckOutcome
from domain_watcher.core.monitoring.value_objects import (
    ChannelId,
    CheckSchedule,
    LastCheck,
)


class TestChannelId:
    def test_basic_value(self) -> None:
        c = ChannelId("tg-ops")
        assert c.value == "tg-ops"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            ChannelId("")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValueError):
            ChannelId("   ")

    def test_strips_surrounding_whitespace(self) -> None:
        assert ChannelId("  tg-ops  ").value == "tg-ops"

    def test_equality(self) -> None:
        assert ChannelId("a") == ChannelId("a")
        assert ChannelId("a") != ChannelId("b")


class TestCheckSchedule:
    def test_basic_cron(self) -> None:
        s = CheckSchedule("0 */6 * * *")
        assert s.cron == "0 */6 * * *"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            CheckSchedule("")

    def test_rejects_wrong_field_count(self) -> None:
        # 4 fields instead of 5
        with pytest.raises(ValueError, match="cron"):
            CheckSchedule("0 */6 * *")


class TestLastCheck:
    def _at(self) -> datetime:
        return datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)

    def test_ok_with_expires_at(self) -> None:
        lc = LastCheck(
            at=self._at(),
            outcome=CheckOutcome.OK,
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        )
        assert lc.outcome is CheckOutcome.OK

    def test_ok_without_expires_at_rejected(self) -> None:
        with pytest.raises(ValueError, match="OK"):
            LastCheck(at=self._at(), outcome=CheckOutcome.OK, expires_at=None)

    def test_failure_with_expires_at_rejected(self) -> None:
        with pytest.raises(ValueError, match="OK"):
            LastCheck(
                at=self._at(),
                outcome=CheckOutcome.TRANSIENT_ERROR,
                expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            )

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValueError, match="tz-aware"):
            LastCheck(
                at=datetime(2026, 5, 9, 12, 0, 0),  # naive
                outcome=CheckOutcome.OK,
                expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            )
