from __future__ import annotations

import pytest

from domain_watcher.core.shared.value_objects import DomainName, Duration


class TestDomainName:
    def test_lowercases_input(self) -> None:
        assert DomainName("Example.COM").value == "example.com"

    def test_strips_trailing_dot(self) -> None:
        assert DomainName("example.com.").value == "example.com"

    def test_normalizes_idn_to_punycode(self) -> None:
        # президент.рф is a real IDN; punycode form is xn--d1abbgf6aiiy.xn--p1ai
        assert DomainName("президент.рф").value == "xn--d1abbgf6aiiy.xn--p1ai"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            DomainName("")

    def test_rejects_overlong(self) -> None:
        # 254 chars > 253 max
        too_long = ("a" * 50 + ".") * 5 + "b" * 4  # 254
        with pytest.raises(ValueError, match="length"):
            DomainName(too_long)

    def test_rejects_label_with_leading_hyphen(self) -> None:
        with pytest.raises(ValueError, match="label"):
            DomainName("-bad.example.com")

    def test_rejects_label_with_trailing_hyphen(self) -> None:
        with pytest.raises(ValueError, match="label"):
            DomainName("bad-.example.com")

    def test_rejects_overlong_label(self) -> None:
        with pytest.raises(ValueError, match="label"):
            DomainName("a" * 64 + ".example.com")

    def test_rejects_empty_label(self) -> None:
        with pytest.raises(ValueError, match="label"):
            DomainName("foo..bar")

    def test_tld_simple(self) -> None:
        assert DomainName("example.com").tld == "com"

    def test_tld_double_co_uk(self) -> None:
        assert DomainName("foo.co.uk").tld == "co.uk"

    def test_tld_single_label_returns_label(self) -> None:
        # eTLD-only doesn't really happen, but tld is just last label here
        assert DomainName("localhost").tld == "localhost"

    def test_registrable_simple(self) -> None:
        assert DomainName("sub.example.com").registrable.value == "example.com"

    def test_registrable_double_tld(self) -> None:
        assert DomainName("sub.foo.co.uk").registrable.value == "foo.co.uk"

    def test_registrable_already_registrable(self) -> None:
        assert DomainName("example.com").registrable.value == "example.com"

    def test_equality_uses_value(self) -> None:
        assert DomainName("Example.com") == DomainName("example.com.")

    def test_frozen(self) -> None:
        d = DomainName("example.com")
        with pytest.raises((AttributeError, Exception)):
            setattr(d, "value", "other.com")  # noqa: B010


class TestDuration:
    def test_days_factory(self) -> None:
        assert Duration.days(7).seconds == 604800

    def test_hours_factory(self) -> None:
        assert Duration.hours(2).seconds == 7200

    def test_minutes_factory(self) -> None:
        assert Duration.minutes(5).seconds == 300

    def test_seconds_factory(self) -> None:
        assert Duration.from_seconds(42).seconds == 42

    def test_parse_seconds(self) -> None:
        assert Duration.parse("30s") == Duration.from_seconds(30)

    def test_parse_minutes(self) -> None:
        assert Duration.parse("5m") == Duration.minutes(5)

    def test_parse_hours(self) -> None:
        assert Duration.parse("12h") == Duration.hours(12)

    def test_parse_days(self) -> None:
        assert Duration.parse("30d") == Duration.days(30)

    def test_parse_rejects_unknown_suffix(self) -> None:
        with pytest.raises(ValueError):
            Duration.parse("30y")

    def test_parse_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            Duration.parse("")

    def test_parse_rejects_no_digits(self) -> None:
        with pytest.raises(ValueError):
            Duration.parse("d")

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            Duration(seconds=-1)

    def test_parse_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            Duration.parse("-1d")

    def test_equality(self) -> None:
        assert Duration.days(1) == Duration.from_seconds(86400)

    def test_ordering(self) -> None:
        assert Duration.days(1) > Duration.hours(1)
        assert Duration.hours(1) < Duration.days(1)

    def test_frozen(self) -> None:
        d = Duration.days(1)
        with pytest.raises((AttributeError, Exception)):
            setattr(d, "seconds", 1)  # noqa: B010
