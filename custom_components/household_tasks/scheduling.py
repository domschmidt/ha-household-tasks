"""Pure date and duration helpers for task scheduling."""

from __future__ import annotations

import calendar
from datetime import time, timedelta


def parse_time(value: str) -> time:
    """Parse an ISO time value."""
    return time.fromisoformat(str(value))


def parse_duration(value: str | int | float) -> timedelta:
    """Parse seconds or a signed HH:MM:SS duration."""
    if isinstance(value, int | float):
        return timedelta(seconds=value)
    raw = str(value).strip()
    sign = -1 if raw.startswith("-") else 1
    parts = raw.lstrip("+-").split(":")
    if len(parts) != 3:
        raise ValueError(f"Duration must be HH:MM:SS: {value}")
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError as err:
        raise ValueError(f"Duration must be HH:MM:SS: {value}") from err
    if minutes not in range(60) or seconds not in range(60):
        raise ValueError(f"Duration has invalid minutes or seconds: {value}")
    return sign * timedelta(
        hours=hours,
        minutes=minutes,
        seconds=seconds,
    )


def month_day(year: int, month: int, value: int | str) -> int:
    """Clamp a requested day to the final day of a month."""
    last = calendar.monthrange(year, month)[1]
    if str(value).lower() == "last":
        return last
    return min(int(value), last)
