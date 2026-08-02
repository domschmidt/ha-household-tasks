"""Tests for the read-only seven-day schedule projection."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.core import SupportsResponse

from custom_components.household_tasks.bootstrap import initial_config
from custom_components.household_tasks.engine import HouseholdTaskEngine


def _engine(hass) -> HouseholdTaskEngine:
    config = initial_config()
    config["people"] = {
        "alex": {"name": "Alex", "notify": "notify.mobile_app_alex"},
        "sam": {"name": "Sam", "notify": "notify.mobile_app_sam"},
    }
    config["tasks"] = {
        "bins": {
            "enabled": True,
            "name": "Bins",
            "assignment": {
                "type": "per_person",
                "people": ["alex", "sam"],
            },
            "schedule": {
                "type": "weekly",
                "weekdays": ["mon", "wed"],
                "time": "18:00:00",
            },
        },
        "filter": {
            "enabled": True,
            "name": "Replace filter",
            "assignee": "alex",
            "assignment": {"type": "fixed"},
            "schedule": {"type": "monthly", "day": 5, "time": "09:30:00"},
        },
        "seasonal": {
            "enabled": True,
            "name": "Seasonal check",
            "assignee": "alex",
            "assignment": {"type": "fixed"},
            "schedule": {
                "type": "weekly",
                "weekdays": ["mon", "wed"],
                "time": "10:00:00",
            },
            "repeat": {"mode": "once_per_season"},
            "season": {"months": [8]},
        },
        "fair": {
            "enabled": True,
            "name": "Clean kitchen",
            "assignment": {"type": "fair", "people": ["alex", "sam"]},
            "schedule": {
                "type": "weekly",
                "weekdays": ["tue"],
                "time": "20:00:00",
            },
        },
        "manual": {
            "enabled": True,
            "name": "Manual",
            "assignment": {"type": "open"},
            "schedule": {"type": "manual"},
        },
        "disabled": {
            "enabled": False,
            "name": "Disabled",
            "assignment": {"type": "open"},
            "schedule": {
                "type": "weekly",
                "weekdays": ["mon"],
                "time": "12:00:00",
            },
        },
    }
    return HouseholdTaskEngine(hass, config)


def test_week_preview_projects_fanout_and_dynamic_assignment_without_mutation(hass):
    """Future schedules are visible while fair assignment remains unresolved."""
    engine = _engine(hass)
    start = datetime(2026, 8, 3, 8, tzinfo=UTC)  # Monday

    preview = engine.week_preview(start)

    assert len(preview) == 7
    assert all(item["read_only"] for item in preview)
    assert {item["task_id"] for item in preview} == {
        "bins",
        "filter",
        "fair",
        "seasonal",
    }
    assert [item["assignee"] for item in preview if item["task_id"] == "bins"] == [
        "alex",
        "sam",
        "alex",
        "sam",
    ]
    fair = next(item for item in preview if item["task_id"] == "fair")
    assert fair["assignee"] is None
    assert fair["assignment_pending"] is True
    assert fair["conditional"] is True
    assert engine.state["rotation_cursors"] == {}
    assert engine.state["assignment_counts"] == {}
    assert engine.state["occurrences"] == {}
    assert sum(item["task_id"] == "seasonal" for item in preview) == 1


def test_week_preview_is_replaced_by_matching_real_occurrence(hass):
    """A persisted occurrence suppresses only its matching projected card."""
    engine = _engine(hass)
    start = datetime(2026, 8, 3, 8, tzinfo=UTC)
    due = datetime(
        2026,
        8,
        3,
        18,
        tzinfo=ZoneInfo(hass.config.time_zone),
    )
    existing_id = engine._occurrence_id(
        "bins",
        due,
        manual=False,
        target_person="alex",
    )
    engine.state["occurrences"][existing_id] = {
        "task_id": "bins",
        "due": due.isoformat(),
        "status": "open",
        "resolved": False,
    }

    preview = engine.week_preview(start)

    assert len(preview) == 6
    assert not any(item["id"] == f"preview-{existing_id}" for item in preview)
    assert any(
        item["task_id"] == "bins"
        and item["assignee"] == "sam"
        and item["due"] == due.isoformat()
        for item in preview
    )


def test_ui_payload_exposes_the_week_preview(hass, freezer):
    """The panel receives projections through its normal state payload."""
    freezer.move_to("2026-08-03 08:00:00+00:00")
    engine = _engine(hass)

    payload = engine.ui_data()

    assert payload["week_preview"]
    assert all(item["read_only"] for item in payload["week_preview"])


async def test_async_week_preview_maps_calendar_events_and_fans_out(hass):
    """Calendar entries appear read-only with offsets, mappings and targets."""
    engine = _engine(hass)
    engine.tasks["waste"] = {
        "enabled": True,
        "name": "Put bins out",
        "assignment": {"type": "per_person", "people": ["alex", "sam"]},
        "repeat": {"mode": "once_per_season"},
        "season": {"months": [8]},
        "schedule": {
            "type": "calendar",
            "entity_id": "calendar.waste",
            "offset": "-12:00:00",
            "title_mappings": [
                {"pattern": "gelb", "task_title": "Gelbe Tonne rausstellen"}
            ],
            "ignore_unmapped_events": True,
        },
    }
    start = datetime(2026, 8, 3, 8, tzinfo=UTC)

    async def get_events(_call):
        return {
            "calendar.waste": {
                "events": [
                    {
                        "summary": "Gelber Sack",
                        "start": (start + timedelta(days=1, hours=-2)).isoformat(),
                    },
                    {
                        "summary": "Problemabfall",
                        "start": (start + timedelta(days=2)).isoformat(),
                    },
                ]
            }
        }

    hass.services.async_register(
        "calendar",
        "get_events",
        get_events,
        supports_response=SupportsResponse.ONLY,
    )

    preview = await engine.async_week_preview(start)
    waste = [item for item in preview if item["task_id"] == "waste"]

    assert len(waste) == 2
    assert {item["assignee"] for item in waste} == {"alex", "sam"}
    assert {item["title"] for item in waste} == {"Gelbe Tonne rausstellen"}
    assert {item["calendar_summary"] for item in waste} == {"Gelber Sack"}
    assert all(item["schedule_type"] == "calendar" for item in waste)
    assert all(item["read_only"] for item in waste)
    assert all(
        datetime.fromisoformat(item["due"]).astimezone(UTC)
        == datetime(2026, 8, 3, 18, tzinfo=UTC)
        for item in waste
    )
    assert engine.state["occurrences"] == {}

    alex = next(item for item in waste if item["assignee"] == "alex")
    engine.state["occurrences"][alex["id"].removeprefix("preview-")] = {
        "task_id": "waste",
        "due": alex["due"],
        "status": "open",
        "resolved": False,
    }

    refreshed = await engine.async_week_preview(start)
    refreshed_waste = [item for item in refreshed if item["task_id"] == "waste"]

    assert [item["assignee"] for item in refreshed_waste] == ["sam"]


async def test_async_week_preview_keeps_static_plan_when_calendar_fails(hass):
    """One unavailable calendar must not break the complete weekly plan."""
    engine = _engine(hass)
    engine.tasks["waste"] = {
        "enabled": True,
        "name": "Put bins out",
        "assignee": "alex",
        "assignment": {"type": "fixed"},
        "schedule": {"type": "calendar", "entity_id": "calendar.waste"},
    }
    start = datetime(2026, 8, 3, 8, tzinfo=UTC)

    preview = await engine.async_week_preview(start)

    assert preview == engine.week_preview(start)


async def test_async_week_preview_keeps_simultaneous_calendar_events(hass):
    """Separate calendar entries at one timestamp remain separate projections."""
    engine = _engine(hass)
    engine.tasks["waste"] = {
        "enabled": True,
        "name": "Put bins out",
        "assignee": "alex",
        "assignment": {"type": "fixed"},
        "schedule": {
            "type": "calendar",
            "entity_id": "calendar.waste",
            "title_mappings": [
                {"pattern": "gelb", "task_title": "Gelbe Tonne rausstellen"},
                {"pattern": "bio", "task_title": "Biotonne rausstellen"},
            ],
            "ignore_unmapped_events": True,
        },
    }
    start = datetime(2026, 8, 3, 8, tzinfo=UTC)
    event_start = start + timedelta(days=1)

    async def get_events(_call):
        return {
            "calendar.waste": {
                "events": [
                    {"summary": "Gelber Sack", "start": event_start.isoformat()},
                    {"summary": "Biomüll", "start": event_start.isoformat()},
                ]
            }
        }

    hass.services.async_register(
        "calendar",
        "get_events",
        get_events,
        supports_response=SupportsResponse.ONLY,
    )

    preview = await engine.async_week_preview(start)
    waste = [item for item in preview if item["task_id"] == "waste"]

    assert len(waste) == 2
    assert len({item["id"] for item in waste}) == 2
    assert {item["title"] for item in waste} == {
        "Gelbe Tonne rausstellen",
        "Biotonne rausstellen",
    }
    assert {item["calendar_summary"] for item in waste} == {
        "Gelber Sack",
        "Biomüll",
    }
