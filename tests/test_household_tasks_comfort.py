"""Tests for smart capture, local discovery, and notification digests."""

from datetime import UTC, datetime

from custom_components.household_tasks.comfort import (
    discovery_suggestions,
    notification_digest_due,
    parse_smart_task,
)


def test_smart_task_parses_person_due_priority_and_points():
    """A compact German phrase becomes an editable structured preview."""
    now = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)
    preview = parse_smart_task(
        "Müll morgen 18 Uhr an Alex, dringend, 2 Punkte",
        {"alex": {"name": "Alex"}},
        now,
    )

    assert preview["name"] == "Müll"
    assert preview["assignee"] == "alex"
    assert preview["due"] == "2026-07-31T18:00:00+00:00"
    assert preview["priority"] == "critical"
    assert preview["points"] == 2
    assert preview["missing"] == []


def test_smart_task_does_not_treat_points_as_a_time():
    """A numeric point value does not accidentally change the default due time."""
    now = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)
    preview = parse_smart_task(
        "Pflanzen gießen an Sam 3 Punkte",
        {"sam": {"name": "Sam"}},
        now,
    )

    assert preview["due"] == "2026-07-30T18:00:00+00:00"
    assert preview["points"] == 3


def test_discovery_recognizes_battery_appliance_and_waste_calendar():
    """Only useful, not-yet-configured local entities are suggested."""
    states = [
        {
            "entity_id": "sensor.phone_battery",
            "attributes": {"friendly_name": "Phone", "device_class": "battery"},
        },
        {
            "entity_id": "binary_sensor.washing_machine",
            "attributes": {"friendly_name": "Waschmaschine"},
        },
        {
            "entity_id": "calendar.waste",
            "attributes": {"friendly_name": "Müllabfuhr"},
        },
    ]
    suggestions = discovery_suggestions(states, {"sensor.phone_battery"})

    assert {item["kind"] for item in suggestions} == {"appliance", "calendar"}
    assert all(item["entity_id"] != "sensor.phone_battery" for item in suggestions)


def test_notification_digest_is_due_once_per_day():
    """The configured digest time yields at most one delivery per local day."""
    before = datetime(2026, 7, 30, 17, 29, tzinfo=UTC)
    after = datetime(2026, 7, 30, 17, 30, tzinfo=UTC)

    assert not notification_digest_due(before, None, "17:30:00")
    assert notification_digest_due(after, None, "17:30:00")
    assert not notification_digest_due(after, "2026-07-30", "17:30:00")
