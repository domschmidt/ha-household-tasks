"""Tests for the Household Tasks config-entry lifecycle."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.household_tasks.bootstrap import initial_config
from custom_components.household_tasks.const import DOMAIN, FRONTEND_PATH, PANEL_URL
from custom_components.household_tasks.engine import (
    HouseholdTaskEngine,
    async_setup_entry,
    async_unload_entry,
)

pytestmark = pytest.mark.usefixtures("mock_frontend_loaded")


async def test_setup_uses_native_task_store(hass):
    """Setup does not depend on another Home Assistant integration."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, version=2)
    entry.add_to_hass(hass)

    with (
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ),
    ):
        assert await async_setup_entry(hass, entry)
        assert entry.runtime_data.state["task_schema_version"] == 2
        await async_unload_entry(hass, entry)


async def test_setup_and_unload_config_entry(hass):
    """Runtime data and listeners follow the config-entry lifecycle."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, version=2)
    entry.add_to_hass(hass)

    with (
        patch.object(HouseholdTaskEngine, "async_setup", new=AsyncMock()) as setup,
        patch.object(
            HouseholdTaskEngine, "async_shutdown", new=AsyncMock()
        ) as shutdown,
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ),
        patch("custom_components.household_tasks.engine.async_register_built_in_panel"),
        patch("custom_components.household_tasks.engine.async_remove_panel"),
    ):
        assert await async_setup_entry(hass, entry)
        assert isinstance(entry.runtime_data, HouseholdTaskEngine)
        setup.assert_awaited_once()

        assert await async_unload_entry(hass, entry)
        shutdown.assert_awaited_once()
        assert entry.runtime_data is None


async def test_real_runtime_service_persistence_panel_and_unload(
    hass, hass_client, hass_ws_client, unused_tcp_port
):
    """Exercise the complete critical path against a running HA instance."""
    notifications = []
    hass.services.async_register(
        "notify",
        "mobile_app_alex",
        lambda call: notifications.append(dict(call.data)),
    )
    calendar_start = datetime.now(UTC) + timedelta(days=2)

    async def get_events(call):
        return {
            "calendar.waste": {
                "events": [
                    {
                        "summary": "Restmüll",
                        "start": calendar_start.isoformat(),
                    },
                    {
                        "summary": "Papier",
                        "start": (calendar_start + timedelta(days=1)).isoformat(),
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

    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            "http": {
                "server_host": "127.0.0.1",
                "server_port": unused_tcp_port,
            }
        },
    )
    entry = MockConfigEntry(domain=DOMAIN, data={}, version=2)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    engine = entry.runtime_data

    assert PANEL_URL in hass.data["frontend_panels"]
    websocket = await hass_ws_client(hass)
    client = await hass_client()
    response = await client.get(f"{FRONTEND_PATH}/household-tasks-panel.js")
    assert response.status == 200
    assert "customElements.define" in await response.text()

    await websocket.send_json({"id": 1, "type": f"{DOMAIN}/get"})
    message = await websocket.receive_json()
    assert message["success"]
    assert message["result"]["task_store"]["schema_version"] == 2

    await engine.async_save_person(
        "alex",
        {
            "name": "Alex",
            "notify": "notify.mobile_app_alex",
        },
    )
    await engine.async_save_task(
        "laundry",
        {
            "enabled": True,
            "name": "Laundry",
            "assignee": "alex",
            "assignment": {"type": "fixed"},
            "schedule": {"type": "manual"},
        },
    )

    hass.states.async_set("binary_sensor.washer", "on")
    weekly_preview = await engine.async_preview_task(
        {
            "schedule": {
                "type": "weekly",
                "weekdays": ["mon"],
                "time": "18:00:00",
            }
        }
    )
    assert weekly_preview["next_due"] is not None
    state_preview = await engine.async_preview_task(
        {
            "schedule": {
                "type": "state_trigger",
                "triggers": [
                    {
                        "entity_id": "binary_sensor.washer",
                        "to": "on",
                    }
                ],
            }
        }
    )
    assert state_preview["state_triggers"][0]["matches"]
    calendar_preview = await engine.async_preview_task(
        {
            "schedule": {
                "type": "calendar",
                "entity_id": "calendar.waste",
                "match": "rest",
                "offset": "-12:00:00",
            }
        }
    )
    assert len(calendar_preview["calendar_events"]) == 1
    assert calendar_preview["calendar_events"][0]["summary"] == "Restmüll"
    assert calendar_preview["next_due"] is not None
    await engine.async_test_notification("alex")
    assert notifications[-1]["data"]["tag"] == "household_tasks_test"

    await websocket.send_json(
        {
            "id": 2,
            "type": f"{DOMAIN}/preview_task",
            "task": {"schedule": {"type": "manual"}},
        }
    )
    message = await websocket.receive_json()
    assert message["success"]
    assert message["result"]["schedule_type"] == "manual"

    await websocket.send_json(
        {
            "id": 3,
            "type": f"{DOMAIN}/task_batch_preview",
            "text": "Müll morgen 18 Uhr an Alex; Pflanzen heute Abend an Alex",
        }
    )
    message = await websocket.receive_json()
    assert message["success"]
    assert len(message["result"]) == 2
    assert all(item["assignee"] == "alex" for item in message["result"])

    await websocket.send_json(
        {
            "id": 4,
            "type": f"{DOMAIN}/task_projection",
            "task": {
                "schedule": {
                    "type": "after_completion",
                    "interval": "08:00:00",
                }
            },
        }
    )
    message = await websocket.receive_json()
    assert message["success"]
    assert message["result"]["risk"] == "high"

    await engine.async_save_task_stack(
        "evening",
        {"name": "Evening", "task_ids": ["laundry"]},
    )
    assert engine.ui_data()["task_stacks"]["evening"]["task_ids"] == ["laundry"]

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "create",
            {"task_id": "unknown"},
            blocking=True,
        )

    await hass.services.async_call(
        DOMAIN,
        "create",
        {"task_id": "laundry"},
        blocking=True,
    )

    assert len(engine.state["occurrences"]) == 1
    occurrence = next(iter(engine.state["occurrences"].values()))
    assert occurrence["title"] == "[Alex] Laundry"
    assert len(engine.state["occurrences"]) == 1
    assert engine.state["ui_config"]["tasks"]["laundry"]["name"] == "Laundry"
    occurrence_id = next(iter(engine.state["occurrences"]))
    move = await engine.async_move_occurrence(occurrence_id, "morgen 9 Uhr")
    assert move["kind"] == "datetime"
    assert datetime.fromisoformat(occurrence["due"]).hour == 9
    attachment = await engine.async_add_attachment(
        occurrence_id,
        "proof.png",
        "image/png",
        "aGVsbG8=",
    )
    assert attachment["name"] == "proof.png"
    assert "content" not in engine.ui_data()["attachments"][occurrence_id][0]
    assert engine.attachment_content(occurrence_id, attachment["id"])["content"] == (
        "aGVsbG8="
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert getattr(entry, "runtime_data", None) is None
    assert engine.remove_interval is None
    assert engine.remove_tag_listener is None
    assert PANEL_URL not in hass.data["frontend_panels"]

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    restored_engine = entry.runtime_data
    assert restored_engine.tasks["laundry"]["name"] == "Laundry"
    assert len(restored_engine.state["occurrences"]) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    await websocket.close()
    await client.close()


async def test_presence_handover_and_resource_monitor_runtime(hass):
    """Exercise assignment, handover, and resource lifecycle side effects."""

    hass.states.async_set("person.alex", "not_home")
    hass.states.async_set("person.sam", "home")
    hass.states.async_set(
        "sensor.salt_level",
        "12",
        {"unit_of_measurement": "%"},
    )

    config = initial_config()
    config["people"] = {
        "alex": {
            "name": "Alex",
            "notify": "notify.mobile_app_alex",
            "presence": "person.alex",
        },
        "sam": {
            "name": "Sam",
            "notify": "notify.mobile_app_sam",
            "presence": "person.sam",
        },
    }
    config["tasks"] = {
        "presence_task": {
            "enabled": True,
            "name": "Receive parcel",
            "assignee": "alex",
            "assignment": {
                "type": "fixed",
                "people": ["alex", "sam"],
                "presence_required": True,
            },
            "schedule": {"type": "manual"},
        },
        "fixed_task": {
            "enabled": True,
            "name": "Prepare documents",
            "assignee": "alex",
            "assignment": {"type": "fixed"},
            "schedule": {"type": "manual"},
            "escalation": [
                {
                    "after": "00:00:00",
                    "recipients": "all",
                    "action": "open",
                }
            ],
        },
    }
    config["monitors"]["resources"] = {
        "salt": {
            "enabled": True,
            "entity_id": "sensor.salt_level",
            "condition": "below",
            "threshold": 20,
            "task_name": "Refill salt",
            "assignee": "sam",
            "auto_resolve": True,
        }
    }
    engine = HouseholdTaskEngine(hass, config)
    engine._validate_config()

    await engine.async_create_manual("presence_task")
    presence_occurrence = next(iter(engine.state["occurrences"].values()))
    assert presence_occurrence["assignee"] == "sam"
    assert presence_occurrence["assignment_reason"]["type"] == "presence"

    await engine.async_create_manual("fixed_task")
    fixed_id, _ = next(
        (occurrence_id, occurrence)
        for occurrence_id, occurrence in engine.state["occurrences"].items()
        if occurrence["task_id"] == "fixed_task"
    )
    await engine.async_set_handover("alex", "sam", reason="Vacation")
    assert engine.state["occurrences"][fixed_id]["assignee"] == "sam"
    assert engine.state["occurrences"][fixed_id]["assignment_reason"]["type"] == (
        "handover"
    )
    with patch.object(engine, "_notify", new=AsyncMock(return_value=True)):
        await engine._process_escalations(datetime.now(UTC) + timedelta(minutes=1))
    assert engine.state["occurrences"][fixed_id]["assignee"] is None
    assert engine.state["occurrences"][fixed_id]["assignment_reason"]["type"] == (
        "escalated_open"
    )

    await engine._scan_resource_monitors()
    resource_id = engine.state["resource_issues"]["salt"]["occurrence_id"]
    assert engine.state["occurrences"][resource_id]["title"] == "[Sam] Refill salt"

    hass.states.async_set("sensor.salt_level", "70", {"unit_of_measurement": "%"})
    await engine._scan_resource_monitors()
    assert engine.state["occurrences"][resource_id]["resolved"]
    assert engine.state["occurrences"][resource_id]["resolution_reason"] == (
        "resource_recovered"
    )
