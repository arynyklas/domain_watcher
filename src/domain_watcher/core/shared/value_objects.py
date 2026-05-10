"""Cross-context value objects: ``DomainName`` and ``Duration``.

Pure layer — stdlib only.

``DomainName.tld`` / ``.registrable`` use a hand-listed double-TLD set in v1
(``co.uk``, ``com.br``, ``co.jp``, ``org.uk``, ``gov.uk``). A full Public
Suffix List integration is deferred to Phase 11; the helper that consumes
PSL must live in ``infrastructure/`` to keep ``core/`` dep-free.
"""

# TODO(phase 11): replace the hand-listed double-TLD set with a PSL-backed
# helper (``infrastructure/`` only — ``core/`` stays pure).

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
"""RFC 1035 label: letters, digits, hyphen; cannot start/end with hyphen.

Matches ASCII / punycode labels post-IDN normalization. Empty labels are
rejected separately so we can produce a clearer error message.
"""

# RFC 1035 length limits.
_DOMAIN_MAX_OCTETS = 253
_LABEL_MAX_OCTETS = 63

# ``DomainName`` parsing helpers — minimum label counts for ``tld`` /
# ``registrable`` to do anything meaningful.
_MIN_LABELS_WITH_TLD = 2
_MIN_LABELS_WITH_REGISTRABLE_AND_DOUBLE_TLD = 3

_DOUBLE_TLDS: frozenset[str] = frozenset(
    {
        "co.uk",
        "com.br",
        "co.jp",
        "org.uk",
        "gov.uk",
    }
)


def _to_punycode(value: str) -> str:
    """IDN → ASCII via IDNA. Raises ``ValueError`` on invalid input."""
    try:
        return value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"invalid IDN: {value!r}: {exc}") from exc


@dataclass(frozen=True, slots=True, order=False)
class DomainName:
    """RFC 1035 normalized FQDN — lowercase, no trailing dot, IDN→punycode."""

    value: str

    def __post_init__(self) -> None:
        raw = self.value
        if not isinstance(raw, str):
            raise TypeError(f"DomainName value must be str, got {type(raw).__name__}")
        if not raw:
            raise ValueError("DomainName cannot be empty")

        s = raw.strip().rstrip(".")
        if not s:
            raise ValueError("DomainName cannot be empty after stripping trailing dot")

        # IDN normalization first (per-label or whole). encode("idna") handles
        # the per-label split internally.
        try:
            s.encode("ascii")
        except UnicodeEncodeError:
            s = _to_punycode(s)

        s = s.lower()

        # Total length cap: 253 octets (RFC 1035 §2.3.4).
        if len(s) > _DOMAIN_MAX_OCTETS:
            raise ValueError(
                f"DomainName length {len(s)} exceeds {_DOMAIN_MAX_OCTETS} octets"
            )

        labels = s.split(".")
        for label in labels:
            if not label:
                raise ValueError(f"DomainName has empty label in {s!r}")
            if len(label) > _LABEL_MAX_OCTETS:
                raise ValueError(f"DomainName label too long in {s!r}: {label!r}")
            if not _LABEL_RE.match(label):
                raise ValueError(f"DomainName has invalid label in {s!r}: {label!r}")

        # Replace value via object.__setattr__ since we're frozen.
        object.__setattr__(self, "value", s)

    @property
    def tld(self) -> str:
        """Effective TLD. Recognizes a small hand-listed set of double TLDs."""
        labels = self.value.split(".")
        if len(labels) >= _MIN_LABELS_WITH_TLD:
            tail = ".".join(labels[-2:])
            if tail in _DOUBLE_TLDS:
                return tail
        return labels[-1]

    @property
    def registrable(self) -> DomainName:
        """eTLD+1. Hand-listed double-TLD aware (see module docstring)."""
        labels = self.value.split(".")
        if len(labels) >= _MIN_LABELS_WITH_REGISTRABLE_AND_DOUBLE_TLD:
            tail = ".".join(labels[-2:])
            if tail in _DOUBLE_TLDS:
                return DomainName(".".join(labels[-3:]))
        if len(labels) >= _MIN_LABELS_WITH_TLD:
            return DomainName(".".join(labels[-2:]))
        return DomainName(self.value)

    def __str__(self) -> str:
        return self.value


_DURATION_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


@dataclass(frozen=True, slots=True, order=True)
class Duration:
    """Non-negative duration in whole seconds."""

    seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.seconds, int) or isinstance(self.seconds, bool):
            raise TypeError(
                f"Duration.seconds must be int, got {type(self.seconds).__name__}"
            )
        if self.seconds < 0:
            raise ValueError(f"Duration cannot be negative: {self.seconds}")

    @classmethod
    def from_seconds(cls, n: int) -> Duration:
        return cls(seconds=n)

    @classmethod
    def minutes(cls, n: int) -> Duration:
        return cls(seconds=n * 60)

    @classmethod
    def hours(cls, n: int) -> Duration:
        return cls(seconds=n * 3600)

    @classmethod
    def days(cls, n: int) -> Duration:
        return cls(seconds=n * 86400)

    def as_timedelta(self) -> timedelta:
        """Return this duration as a stdlib ``timedelta``.

        Useful when comparing against the difference of two ``datetime`` values
        without dragging Duration arithmetic into core comparisons.
        """
        return timedelta(seconds=self.seconds)

    @classmethod
    def parse(cls, s: str) -> Duration:
        """Parse "30d", "12h", "5m", "60s". Whitespace stripped."""
        if not isinstance(s, str):
            raise TypeError(f"Duration.parse expects str, got {type(s).__name__}")
        v = s.strip()
        if not v:
            raise ValueError("Duration.parse: empty string")
        m = _DURATION_RE.match(v)
        if m is None:
            raise ValueError(
                f"Duration.parse: invalid syntax {s!r} (expected NN[smhd])"
            )
        n = int(m.group(1))
        unit = m.group(2)
        return cls(seconds=n * _UNIT_SECONDS[unit])

    def __str__(self) -> str:
        # Choose the largest exact unit for display.
        for suf, mult in (("d", 86400), ("h", 3600), ("m", 60)):
            if self.seconds % mult == 0 and self.seconds >= mult:
                return f"{self.seconds // mult}{suf}"
        return f"{self.seconds}s"
