"""Pure workflow timing decisions for Household Tasks."""

from __future__ import annotations

from datetime import datetime, time, timedelta


def completion_due(resolved_at: datetime, interval: timedelta) -> datetime:
    """Return the next due time relative to an actual completion."""
    if interval <= timedelta(0):
        raise ValueError("Completion interval must be positive")
    return resolved_at + interval


def state_trigger_allowed(
    *,
    happened_at: datetime,
    previous: datetime | None,
    cooldown: timedelta,
    has_open_occurrence: bool,
    skip_if_open: bool,
) -> bool:
    """Return whether a state transition may create an occurrence."""
    if skip_if_open and has_open_occurrence:
        return False
    return previous is None or happened_at - previous >= cooldown


def weekly_summary_due(
    *,
    now: datetime,
    weekday: int,
    scheduled_time: time,
    last_summary_id: str | None,
) -> str | None:
    """Return the ISO week identifier when a weekly summary is due."""
    calendar_week = now.isocalendar()
    summary_id = f"{calendar_week.year}-W{calendar_week.week:02d}"
    if (
        now.weekday() != weekday
        or now.time().replace(tzinfo=None) < scheduled_time
        or last_summary_id == summary_id
    ):
        return None
    return summary_id
