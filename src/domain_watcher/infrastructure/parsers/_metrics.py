"""In-process counters for the validation pipeline.

This is a deliberately tiny shim: Phase 11 mounts a real Prometheus
endpoint and the same counter names ride
``prometheus_client.Counter``. Until then we expose a hand-rolled
``Counter`` keyed by label so unit tests can assert on increments
without depending on prometheus.
"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock


class LabeledCounter:
    """Thread-safe ``{label_value -> int}`` counter for a fixed metric name."""

    __slots__ = ("_lock", "_values", "name")

    def __init__(self, name: str) -> None:
        self.name = name
        self._values: defaultdict[str, int] = defaultdict(int)
        self._lock = Lock()

    def inc(self, label_value: str, amount: int = 1) -> None:
        with self._lock:
            self._values[label_value] += amount

    def value(self, label_value: str) -> int:
        with self._lock:
            return self._values[label_value]

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._values)

    def reset(self) -> None:
        with self._lock:
            self._values.clear()


# Module-level singleton — Phase 11 swaps this for prometheus_client.Counter
# without touching call sites.
pipeline_gate5_skipped_total = LabeledCounter("domain_watcher_pipeline_gate5_skipped_total")
"""Reasons: ``no_known_good`` (data file lookup miss),
``cross_check_unavailable`` (transient transport failure)."""


__all__ = ["LabeledCounter", "pipeline_gate5_skipped_total"]
