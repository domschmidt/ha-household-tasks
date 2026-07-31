"""Tests for the authenticated external-client REST API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.household_tasks.client_api import (
    API_VERSION,
    ClientApiError,
    HouseholdTasksClientActionView,
    _error_response,
    build_client_feed,
    resolve_client_person,
)
from custom_components.household_tasks.client_api import (
    _engine as get_client_engine,
)
from custom_components.household_tasks.const import DOMAIN

pytestmark = pytest.mark.usefixtures("mock_frontend_loaded")


def _engine() -> SimpleNamespace:
    now = datetime(2026, 7, 15, 12, tzinfo=UTC)
    return SimpleNamespace(
        people={
            "alex": {
                "name": "Alex",
                "user_id": "user-alex",
                "notify": "notify.mobile_app_alex",
                "presence": "person.alex",
            },
            "sam": {
                "name": "Sam",
                "user_id": "user-sam",
                "notify": "notify.mobile_app_sam",
            },
        },
        state={
            "household_mode": {"mode": "normal"},
            "occurrences": {
                "own": {
                    "title": "[Alex] Filter prüfen",
                    "assignee": "alex",
                    "due": (now - timedelta(hours=1)).isoformat(),
                    "status": "open",
                    "revision": 3,
                    "checklist": [{"id": "photo", "title": "Foto", "completed": False}],
                    "task": {
                        "description": "HEPA-Filter kontrollieren",
                        "market": {"priority": "high", "points": 3},
                    },
                },
                "market": {
                    "title": "Paket annehmen",
                    "assignee": None,
                    "due": (now + timedelta(hours=2)).isoformat(),
                    "status": "open",
                    "revision": 1,
                    "checklist": [],
                    "task": {"assignment": {"type": "open", "people": ["alex"]}},
                },
                "other": {
                    "title": "[Sam] Einkaufen",
                    "assignee": "sam",
                    "due": (now + timedelta(days=1)).isoformat(),
                    "status": "open",
                    "task": {},
                },
                "done": {
                    "title": "[Alex] Erledigt",
                    "assignee": "alex",
                    "due": now.isoformat(),
                    "status": "completed",
                    "resolved": True,
                    "task": {},
                },
            },
        },
    )


def test_client_feed_is_personal_minimal_and_actionable():
    """The mobile response contains only relevant tasks and no person secrets."""
    with patch(
        "custom_components.household_tasks.client_api.dt_util.now",
        return_value=datetime(2026, 7, 15, 12, tzinfo=UTC),
    ):
        feed = build_client_feed(_engine(), "alex")

    assert feed["api_version"] == API_VERSION
    assert feed["person"] == {"id": "alex", "name": "Alex"}
    assert feed["summary"] == {
        "open": 2,
        "due_today": 2,
        "overdue": 1,
        "blocked": 0,
    }
    assert [task["id"] for task in feed["tasks"]] == ["own", "market"]
    assert feed["tasks"][0]["title"] == "Filter prüfen"
    assert feed["tasks"][0]["actions"]["complete"] is False
    assert feed["tasks"][1]["actions"]["claim"] is True
    serialized = str(feed)
    assert "notify.mobile_app" not in serialized
    assert "person.alex" not in serialized


def test_client_person_resolution_enforces_user_boundary():
    """Non-admin tokens cannot select another household identity."""
    engine = _engine()
    alex = SimpleNamespace(id="user-alex", is_admin=False)
    admin = SimpleNamespace(id="admin", is_admin=True)

    assert resolve_client_person(engine, alex, None) == "alex"
    assert resolve_client_person(engine, admin, "sam") == "sam"
    with pytest.raises(ClientApiError) as error:
        resolve_client_person(engine, alex, "sam")
    assert error.value.status == 403
    with pytest.raises(ClientApiError, match="person_id"):
        resolve_client_person(engine, admin, None)

    with pytest.raises(ClientApiError) as error:
        resolve_client_person(engine, admin, "unknown")
    assert error.value.status == 404

    single_person_engine = SimpleNamespace(people={"alex": engine.people["alex"]})
    assert resolve_client_person(single_person_engine, admin, None) == "alex"


def test_client_engine_and_error_response_are_explicit(hass):
    """An unloaded integration and expected failures have stable responses."""
    with pytest.raises(ClientApiError) as error:
        get_client_engine(hass)
    assert error.value.code == "not_loaded"
    assert error.value.status == 503

    response = _error_response(ClientApiError("conflict", "Schon erledigt.", 409))
    assert response.status == 409
    assert response.headers["Cache-Control"] == "no-store"
    assert b'"code":"conflict"' in response.body


async def test_client_action_dispatch_is_explicit_and_revision_aware():
    """The mobile adapter forwards only supported, constrained commands."""
    engine = SimpleNamespace(
        async_complete_occurrence=AsyncMock(),
        async_claim_occurrence_for_person=AsyncMock(),
        async_snooze_occurrence=AsyncMock(),
        async_request_help=AsyncMock(),
        async_decline_occurrence=AsyncMock(),
        async_set_occurrence_status=AsyncMock(),
        async_set_checklist_item=AsyncMock(),
    )
    view = HouseholdTasksClientActionView()
    context = SimpleNamespace(user_id="user-alex")

    await view._execute(
        engine,
        "task-1",
        "claim",
        {},
        "alex",
        context,
    )
    engine.async_claim_occurrence_for_person.assert_awaited_once_with(
        "task-1", "alex", context=context
    )

    await view._execute(
        engine,
        "task-1",
        "checklist",
        {"item_id": "photo", "completed": True, "expected_revision": 4},
        "alex",
        context,
    )
    engine.async_set_checklist_item.assert_awaited_once_with(
        "task-1",
        "photo",
        True,
        expected_revision=4,
        context=context,
    )

    await view._execute(
        engine,
        "task-1",
        "complete",
        {"expected_revision": 5},
        "alex",
        context,
    )
    engine.async_complete_occurrence.assert_awaited_once_with(
        "task-1", context, expected_revision=5
    )

    await view._execute(
        engine, "task-1", "snooze", {"choice": "evening"}, "alex", context
    )
    engine.async_snooze_occurrence.assert_awaited_once_with("task-1", "evening")

    await view._execute(engine, "task-1", "help", {}, "alex", context)
    engine.async_request_help.assert_awaited_once_with("task-1")

    await view._execute(engine, "task-1", "decline", {}, "alex", context)
    engine.async_decline_occurrence.assert_awaited_once_with("task-1")

    await view._execute(
        engine,
        "task-1",
        "status",
        {"status": "waiting", "expected_revision": 6},
        "alex",
        context,
    )
    engine.async_set_occurrence_status.assert_awaited_once_with(
        "task-1", "waiting", expected_revision=6, context=context
    )

    for action, payload, code in (
        ("snooze", {"choice": "next_week"}, "invalid_choice"),
        ("status", {"status": "completed"}, "invalid_status"),
        ("checklist", {}, "item_required"),
    ):
        with pytest.raises(ClientApiError) as error:
            await view._execute(engine, "task-1", action, payload, "alex", context)
        assert error.value.code == code

    with pytest.raises(ClientApiError) as error:
        await view._execute(engine, "task-1", "delete", {}, "alex", context)
    assert error.value.status == 404


async def test_client_payload_validation():
    """The action endpoint accepts only JSON objects."""
    view = HouseholdTasksClientActionView()
    assert await view._payload(SimpleNamespace(can_read_body=False)) == {}

    request = SimpleNamespace(can_read_body=True, json=AsyncMock(return_value=[]))
    with pytest.raises(ClientApiError) as error:
        await view._payload(request)
    assert error.value.code == "invalid_json"

    request.json = AsyncMock(side_effect=ValueError)
    with pytest.raises(ClientApiError) as error:
        await view._payload(request)
    assert error.value.code == "invalid_json"


async def test_authenticated_rest_round_trip(hass, hass_client, unused_tcp_port):
    """A real HA HTTP client can list and complete a native occurrence."""
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
    engine = entry.runtime_data
    await engine.async_save_person(
        "alex",
        {"name": "Alex", "notify": "notify.mobile_app_alex"},
    )
    await engine.async_save_task(
        "filter",
        {
            "enabled": True,
            "name": "Filter prüfen",
            "assignee": "alex",
            "assignment": {"type": "fixed"},
            "schedule": {"type": "manual"},
        },
    )
    await engine.async_create_manual("filter")
    occurrence_id, occurrence = next(iter(engine.state["occurrences"].items()))

    client = await hass_client()
    response = await client.get("/api/household_tasks/v1/tasks?person_id=alex")
    assert response.status == 200
    feed = await response.json()
    assert feed["tasks"][0]["id"] == occurrence_id
    assert response.headers["Cache-Control"] == "no-store"

    response = await client.post(
        f"/api/household_tasks/v1/tasks/{occurrence_id}/complete",
        json={
            "person_id": "alex",
            "expected_revision": occurrence["revision"],
        },
    )
    assert response.status == 200
    result = await response.json()
    assert result["tasks"] == []
    assert result["action_result"] == {
        "action": "complete",
        "occurrence_id": occurrence_id,
    }
