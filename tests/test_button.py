"""Tests for the official Home Assistant iOS widget action button."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import Context
from homeassistant.util import dt as dt_util

from custom_components.household_tasks.bootstrap import initial_config
from custom_components.household_tasks.button import HouseholdTaskWidgetActionButton
from custom_components.household_tasks.const import ACTION_PREFIX
from custom_components.household_tasks.engine import HouseholdTaskEngine


def _widget_engine(hass) -> HouseholdTaskEngine:
    config = initial_config()
    config["people"] = {
        "alex": {
            "name": "Alex",
            "notify": "notify.mobile_app_alex",
            "user_id": "user-alex",
        },
        "sam": {
            "name": "Sam",
            "notify": "notify.mobile_app_sam",
            "user_id": "user-sam",
        },
    }
    engine = HouseholdTaskEngine(hass, config)
    engine.state["occurrences"] = {
        "blocked-checklist": {
            "title": "[Alex] Filter check",
            "assignee": "alex",
            "status": "open",
            "due": (dt_util.now() + timedelta(hours=1)).isoformat(),
            "revision": 2,
            "checklist": [{"id": "photo", "title": "Take photo", "completed": False}],
            "task": {"require_checklist_completion": True},
        }
    }
    return engine


async def test_widget_action_notification_is_bound_to_exact_task(hass):
    """The widget pushes safe actions for one immutable occurrence ID."""
    engine = _widget_engine(hass)
    with patch.object(
        engine, "_send_notification", new=AsyncMock(return_value=True)
    ) as send:
        occurrence_id = await engine.async_send_widget_actions("alex")

    assert occurrence_id == "blocked-checklist"
    args = send.await_args.args
    assert args[0:2] == ("mobile_app_alex", "notify.mobile_app_alex")
    assert "Filter check" in args[3]
    assert all(not action["action"].startswith(ACTION_PREFIX) for action in args[5])
    open_action = engine._notification_actions("open", "alex", None, None)[1]
    assert open_action["uri"] == "/haushaltsaufgaben?view=mine"


async def test_task_notification_opens_personal_panel_tab(hass):
    """Tapping a task notification deep-links to the personal inbox."""
    engine = _widget_engine(hass)
    calls = []

    async def notify(call):
        calls.append(call)

    hass.services.async_register("notify", "mobile_app_alex", notify)

    delivered = await engine._send_notification(
        "mobile_app_alex",
        "notify.mobile_app_alex",
        "Task",
        "Filter check",
        "household_task_filter",
        [],
    )

    assert delivered is True
    assert calls[0].data["data"]["url"] == "/haushaltsaufgaben?view=mine"


async def test_widget_action_notification_enforces_person_boundary(hass):
    """A non-admin entity context cannot target another person's device."""
    engine = _widget_engine(hass)
    with (
        patch.object(
            hass.auth,
            "async_get_user",
            new=AsyncMock(return_value=SimpleNamespace(is_admin=False)),
        ),
        pytest.raises(vol.Invalid, match="Kein Zugriff"),
    ):
        await engine.async_send_widget_actions("sam", Context(user_id="user-alex"))


async def test_widget_action_notification_handles_empty_and_invalid_targets(hass):
    """Empty inboxes remain useful while unknown people and delivery failures fail."""
    engine = _widget_engine(hass)
    engine.state["occurrences"] = {}
    with patch.object(
        engine, "_send_notification", new=AsyncMock(return_value=True)
    ) as send:
        assert await engine.async_send_widget_actions("alex") is None
    assert send.await_args.args[5] == []
    assert "keine offenen Aufgaben" in send.await_args.args[3]

    with pytest.raises(vol.Invalid, match="unbekannt"):
        await engine.async_send_widget_actions("unknown")

    with (
        patch.object(engine, "_send_notification", new=AsyncMock(return_value=False)),
        pytest.raises(vol.Invalid, match="nicht gesendet"),
    ):
        await engine.async_send_widget_actions("alex")


async def test_blocked_widget_task_omits_mutating_shortcuts(hass):
    """Blocked tasks offer decline but no complete, snooze, or help shortcut."""
    engine = _widget_engine(hass)
    occurrence = engine.state["occurrences"]["blocked-checklist"]
    occurrence["status"] = "blocked"
    occurrence["help_status"] = "requested"
    with patch.object(
        engine, "_send_notification", new=AsyncMock(return_value=True)
    ) as send:
        await engine.async_send_widget_actions("alex")

    action_ids = [action["action"] for action in send.await_args.args[5]]
    assert action_ids == ["HOUSEHOLD_TASK_DECLINE_blocked-checklist"]


async def test_widget_button_forwards_context_and_tracks_notify_service(hass):
    """The entity is available only with its configured notify action."""
    engine = _widget_engine(hass)
    engine.async_send_widget_actions = AsyncMock()
    entry = SimpleNamespace(entry_id="entry-1")
    button = HouseholdTaskWidgetActionButton(entry, engine, "alex")
    button.hass = hass

    assert button.available is False
    hass.services.async_register("notify", "mobile_app_alex", lambda call: None)
    assert button.available is True

    button._context = Context(user_id="user-alex")
    await button.async_press()
    engine.async_send_widget_actions.assert_awaited_once_with(
        "alex", context=button._context
    )
