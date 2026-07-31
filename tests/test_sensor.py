"""Tests for native task aggregate sensors."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.household_tasks.const import DOMAIN
from custom_components.household_tasks.sensor import (
    HouseholdTaskCountSensor,
    HouseholdTaskPersonWidgetSensor,
    _blocked,
    _due_today,
    _open,
    _overdue,
)

pytestmark = pytest.mark.usefixtures("mock_frontend_loaded")


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


def test_person_widget_sensor_is_bounded_and_person_scoped():
    """The iOS widget state exposes only a compact personal preview."""
    now = dt_util.now()
    engine = SimpleNamespace(
        people={
            "alex": {
                "name": "Alex",
                "notify": "notify.mobile_app_alex",
                "presence": "person.alex",
            },
            "sam": {"name": "Sam", "notify": "notify.mobile_app_sam"},
        },
        state={
            "household_mode": {"mode": "normal"},
            "occurrences": {
                "alex-next": {
                    "title": "[Alex] Filter check",
                    "assignee": "alex",
                    "status": "open",
                    "due": (now + timedelta(hours=1)).isoformat(),
                    "revision": 2,
                    "checklist": [],
                    "task": {"market": {"priority": "high", "points": 2}},
                },
                "sam-only": {
                    "title": "[Sam] Shopping",
                    "assignee": "sam",
                    "status": "open",
                    "due": now.isoformat(),
                    "task": {},
                },
            },
        },
    )
    entry = SimpleNamespace(entry_id="entry-1")
    sensor = HouseholdTaskPersonWidgetSensor(entry, engine, "alex")

    assert sensor.native_value == "Filter check"
    assert sensor.unique_id == "entry-1_ios_widget_alex"
    assert sensor.extra_state_attributes["open"] == 1
    assert sensor.extra_state_attributes["next_task_id"] == "alex-next"
    assert sensor.extra_state_attributes["preview"] == [
        {
            "id": "alex-next",
            "title": "Filter check",
            "due": engine.state["occurrences"]["alex-next"]["due"],
            "status": "open",
            "overdue": False,
        }
    ]
    assert "notify.mobile_app_alex" not in str(sensor.extra_state_attributes)
    assert "person.alex" not in str(sensor.extra_state_attributes)

    engine.people.pop("alex")
    assert sensor.available is False
    assert sensor.native_value is None


async def test_widget_entities_follow_people_added_after_setup(
    hass, hass_client, unused_tcp_port
):
    """A person created in the panel receives sensor and button entities live."""
    hass.services.async_register("notify", "mobile_app_alex", lambda call: None)
    assert await async_setup_component(
        hass,
        DOMAIN,
        {"http": {"server_host": "127.0.0.1", "server_port": unused_tcp_port}},
    )
    entry = MockConfigEntry(domain=DOMAIN, data={}, version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await entry.runtime_data.async_save_person(
        "alex",
        {
            "name": "Alex",
            "notify": "notify.mobile_app_alex",
            "user_id": "user-alex",
        },
    )
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    sensor_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_ios_widget_alex"
    )
    button_id = registry.async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_ios_widget_actions_alex"
    )
    assert sensor_id is not None
    assert button_id is not None
    assert hass.states.get(sensor_id).state == "All done"
    assert hass.states.get(button_id).state == "unknown"
