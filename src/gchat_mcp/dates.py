"""Tool dates: parse local-time arguments, and convert to/from the UTC store."""

import re
from datetime import datetime, timedelta, timezone, tzinfo

_RELATIVE = re.compile(r"^\s*(\d+)\s*([dhm])\s*$", re.IGNORECASE)
_UNITS = {"d": "days", "h": "hours", "m": "minutes"}
_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


def parse_when(value: str | None, now: datetime | None = None) -> datetime | None:
    """'7d' | '24h' | '90m' | 'YYYY-MM-DD' | 'YYYY-MM-DD HH:MM' → naive local datetime."""
    if value is None or not str(value).strip():
        return None
    match = _RELATIVE.match(value)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        return (now or datetime.now()).replace(second=0, microsecond=0) - timedelta(**{_UNITS[unit]: amount})
    for fmt in _FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date {value!r}. Use '7d', '24h', '90m', 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'.")


def to_utc_key(local: datetime, tz: tzinfo) -> str:
    """Naive local datetime → 'YYYY-MM-DDTHH:MM:SS' in UTC, comparable with
    substr(create_time, 1, 19). Never compare full RFC 3339 strings: as text,
    '...00.5Z' sorts before '...00Z'."""
    return local.replace(tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def to_local(rfc3339: str, tz: tzinfo) -> str:
    """Stored UTC timestamp → 'YYYY-MM-DD HH:MM' local."""
    utc = datetime.strptime(rfc3339[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return utc.astimezone(tz).strftime("%Y-%m-%d %H:%M")
