"""Tests for native task aggregate sensors."""

from datetime import timedelta
from types import SimpleNamespace

from homeassistant.util import dt as dt_util

from custom_components.household_tasks.sensor import (
    HouseholdTaskCountSensor,
    _blocked,
    _due_today,
    _open,
    _overdue,
)


def test_native_aggregate_counts_ignore_terminal_tasks():
    """Dashboard sensors consistently classify the native lifecycle."""
    now = dt_util.now()
    engine = SimpleNamespace(
        state={
            "occurrences": {
                "today": {"status": "open", "due": now.isoformat()},
                "late": {
                    "status": "in_progress",
                    "due": (now - timedelta(days=1)).isoformat(),
                },
                "blocked": {"status": "blocked", "due": now.isoformat()},
                "done": {
                    "status": "completed",
                    "due": (now - timedelta(days=2)).isoformat(),
                },
                "invalid_due": {"status": "waiting", "due": "unknown"},
            }
        }
    )

    assert _open(engine, now) == 4
    assert _due_today(engine, now) == 2
    assert _overdue(engine, now) == 1
    assert _blocked(engine, now) == 1


def test_sensor_metadata_and_live_value():
    """Every aggregate has a stable config-entry-scoped identity."""
    engine = SimpleNamespace(state={"occurrences": {}})
    entry = SimpleNamespace(entry_id="entry-1")
    sensor = HouseholdTaskCountSensor(entry, engine, "open", "Open tasks", _open)

    assert sensor.unique_id == "entry-1_open"
    assert sensor.name == "Open tasks"
    assert sensor.native_value == 0
