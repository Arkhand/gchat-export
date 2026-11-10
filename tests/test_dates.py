from datetime import datetime, timedelta, timezone

import pytest

from gchat_mcp.dates import parse_when, to_local, to_utc_key

UY = timezone(timedelta(hours=-3))


def test_parse_relative_and_absolute():
    now = datetime(2026, 11, 10, 12, 30, 45)
    assert parse_when("7d", now) == datetime(2026, 11, 3, 12, 30)
    assert parse_when("24h", now) == datetime(2026, 11, 9, 12, 30)
    assert parse_when("2026-10-01") == datetime(2026, 10, 1)
    assert parse_when("2026-10-01 14:05") == datetime(2026, 10, 1, 14, 5)
    assert parse_when(None) is None and parse_when("  ") is None


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_when("last tuesday")


def test_to_utc_key():
    assert to_utc_key(datetime(2026, 10, 1), UY) == "2026-10-01T03:00:00"


def test_to_local():
    assert to_local("2026-10-01T03:00:00.5Z", UY) == "2026-10-01 00:00"
    assert to_local("2026-10-01T03:00:00Z", UY) == "2026-10-01 00:00"
