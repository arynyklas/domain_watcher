from __future__ import annotations

import pytest

from domain_watcher.core.checking.policies import RetryPolicy
from domain_watcher.core.shared.value_objects import Duration


def test_retry_policy_default_max_attempts() -> None:
    p = RetryPolicy()
    assert p.max_attempts == 3


def test_delay_grows_geometrically() -> None:
    p = RetryPolicy(
        max_attempts=4,
        base_delay=Duration.from_seconds(1),
        factor=5.0,
    )
    assert p.delay_for(1) == Duration.from_seconds(1)
    assert p.delay_for(2) == Duration.from_seconds(5)
    assert p.delay_for(3) == Duration.from_seconds(25)


def test_delay_for_zero_or_below_rejected() -> None:
    p = RetryPolicy()
    with pytest.raises(ValueError):
        p.delay_for(0)
    with pytest.raises(ValueError):
        p.delay_for(-1)


def test_delay_for_at_or_above_max_attempts_rejected() -> None:
    p = RetryPolicy(max_attempts=3)
    # Last attempt's delay still computable (3 < 3 is false → raise).
    # Caller asks for delay BEFORE attempt N; only attempts 1..max-1 are
    # eligible because the last attempt has nothing to delay before.
    with pytest.raises(ValueError):
        p.delay_for(3)


def test_delay_is_non_negative() -> None:
    p = RetryPolicy(base_delay=Duration.from_seconds(2), factor=1.5)
    for attempt in range(1, p.max_attempts):
        assert p.delay_for(attempt).seconds >= 0


def test_factor_one_keeps_constant_delay() -> None:
    p = RetryPolicy(max_attempts=4, base_delay=Duration.from_seconds(7), factor=1.0)
    assert p.delay_for(1) == Duration.from_seconds(7)
    assert p.delay_for(2) == Duration.from_seconds(7)
    assert p.delay_for(3) == Duration.from_seconds(7)
