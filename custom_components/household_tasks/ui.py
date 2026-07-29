"""WebSocket API for the Household Tasks frontend panel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components import websocket_api
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.exceptions import Unauthorized

from .const import DOMAIN
from .engine import get_loaded_engine

SLUG = vol.All(str, vol.Match(r"^[a-z0-9_]+$"))


def _engine(hass: HomeAssistant) -> Any:
    engine = get_loaded_engine(hass)
    if engine is None:
        raise RuntimeError("Household Tasks is not loaded")
    return engine


def _require_todo_control(
    connection: websocket_api.ActiveConnection, entity_id: str
) -> None:
    if connection.user.is_admin:
        return
    if not connection.user.permissions.check_entity(entity_id, POLICY_CONTROL):
        raise Unauthorized(entity_id=entity_id, permission=POLICY_CONTROL)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get"})
@callback
def websocket_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return panel configuration and runtime state."""
    result = _engine(hass).ui_data()
    result["is_admin"] = connection.user.is_admin
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save_task",
        vol.Required("task_id"): SLUG,
        vol.Required("task"): dict,
    }
)
async def websocket_save_task(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create or update a task template."""
    await _engine(hass).async_save_task(msg["task_id"], msg["task"])
    connection.send_result(msg["id"], _engine(hass).ui_data())


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/delete_task",
        vol.Required("task_id"): SLUG,
    }
)
async def websocket_delete_task(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a task template."""
    await _engine(hass).async_delete_task(msg["task_id"])
    connection.send_result(msg["id"], _engine(hass).ui_data())


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save_person",
        vol.Required("person_id"): SLUG,
        vol.Required("person"): dict,
    }
)
async def websocket_save_person(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create or update a person mapping."""
    await _engine(hass).async_save_person(msg["person_id"], msg["person"])
    connection.send_result(msg["id"], _engine(hass).ui_data())


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save_defaults",
        vol.Required("defaults"): dict,
    }
)
async def websocket_save_defaults(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update global escalation defaults."""
    await _engine(hass).async_save_defaults(msg["defaults"])
    connection.send_result(msg["id"], _engine(hass).ui_data())


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save_monitors",
        vol.Required("monitors"): dict,
    }
)
async def websocket_save_monitors(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update automatic device monitors."""
    await _engine(hass).async_save_monitors(msg["monitors"])
    connection.send_result(msg["id"], _engine(hass).ui_data())


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/preview_task",
        vol.Required("task"): dict,
    }
)
async def websocket_preview_task(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Preview a task rule without persisting it."""
    result = await _engine(hass).async_preview_task(msg["task"])
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/test_notification",
        vol.Required("person_id"): SLUG,
    }
)
async def websocket_test_notification(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Send an explicit test notification to a configured person."""
    await _engine(hass).async_test_notification(msg["person_id"])
    connection.send_result(msg["id"], {"sent": True})


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_handover",
        vol.Required("from_person"): SLUG,
        vol.Required("to_person"): SLUG,
        vol.Optional("until"): str,
        vol.Optional("reason"): str,
    }
)
async def websocket_set_handover(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create or replace a temporary household handover."""
    await _engine(hass).async_set_handover(
        msg["from_person"],
        msg["to_person"],
        until=msg.get("until"),
        reason=msg.get("reason"),
    )
    connection.send_result(msg["id"], _engine(hass).ui_data())


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/clear_handover",
        vol.Required("from_person"): SLUG,
    }
)
async def websocket_clear_handover(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Clear a temporary household handover."""
    await _engine(hass).async_clear_handover(msg["from_person"])
    connection.send_result(msg["id"], _engine(hass).ui_data())


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/delete_person",
        vol.Required("person_id"): SLUG,
    }
)
async def websocket_delete_person(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete an unused person mapping."""
    await _engine(hass).async_delete_person(msg["person_id"])
    connection.send_result(msg["id"], _engine(hass).ui_data())


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/reset_config"})
async def websocket_reset_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Restore task templates and people to their initial values."""
    await _engine(hass).async_reset_ui_config()
    connection.send_result(msg["id"], _engine(hass).ui_data())


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/export_config"})
@callback
def websocket_export_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Export the editable configuration."""
    connection.send_result(msg["id"], _engine(hass).export_ui_config())


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/import_config",
        vol.Required("document"): dict,
    }
)
async def websocket_import_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Import and validate an editable configuration."""
    await _engine(hass).async_import_ui_config(msg["document"])
    connection.send_result(msg["id"], _engine(hass).ui_data())


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/create",
        vol.Required("task_id"): SLUG,
    }
)
async def websocket_create(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a task occurrence."""
    engine = _engine(hass)
    _require_todo_control(connection, engine.todo_entity)
    await engine.async_create_manual(
        msg["task_id"], Context(user_id=connection.user.id)
    )
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/create_ad_hoc",
        vol.Required("name"): str,
        vol.Required("assignee"): SLUG,
        vol.Required("due"): str,
        vol.Optional("description"): str,
        vol.Optional("escalation"): list,
    }
)
async def websocket_create_ad_hoc(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a one-off household task."""
    engine = _engine(hass)
    _require_todo_control(connection, engine.todo_entity)
    await engine.async_create_ad_hoc(
        msg["name"],
        msg["assignee"],
        msg["due"],
        description=msg.get("description"),
        escalation=msg.get("escalation"),
        context=Context(user_id=connection.user.id),
    )
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/complete",
        vol.Required("occurrence_id"): str,
    }
)
async def websocket_complete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Complete a tracked occurrence."""
    engine = _engine(hass)
    _require_todo_control(connection, engine.todo_entity)
    await engine.async_complete_occurrence(
        msg["occurrence_id"], Context(user_id=connection.user.id)
    )
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/claim",
        vol.Required("occurrence_id"): str,
    }
)
async def websocket_claim(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Claim an open tracked occurrence."""
    engine = _engine(hass)
    _require_todo_control(connection, engine.todo_entity)
    await engine.async_claim_occurrence(
        msg["occurrence_id"], Context(user_id=connection.user.id)
    )
    connection.send_result(msg["id"], engine.ui_data())


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register panel WebSocket commands."""
    for command in (
        websocket_get,
        websocket_save_task,
        websocket_delete_task,
        websocket_save_person,
        websocket_save_defaults,
        websocket_save_monitors,
        websocket_preview_task,
        websocket_test_notification,
        websocket_set_handover,
        websocket_clear_handover,
        websocket_delete_person,
        websocket_reset_config,
        websocket_export_config,
        websocket_import_config,
        websocket_create,
        websocket_create_ad_hoc,
        websocket_complete,
        websocket_claim,
    ):
        websocket_api.async_register_command(hass, command)
