"""WebSocket API for the Household Tasks frontend panel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
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


def _require_household_access(
    connection: websocket_api.ActiveConnection, engine: Any
) -> None:
    """Allow administrators and explicitly linked household users."""
    if connection.user.is_admin:
        return
    if any(
        person.get("user_id") == connection.user.id for person in engine.people.values()
    ):
        return
    raise Unauthorized


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


@websocket_api.async_response
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/week_preview"})
async def websocket_week_preview(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return live calendar-backed and deterministic weekly projections."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    connection.send_result(msg["id"], await engine.async_week_preview())


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
        vol.Optional("task_id"): SLUG,
        vol.Optional("scenario"): dict,
    }
)
async def websocket_preview_task(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Preview a task rule without persisting it."""
    result = await _engine(hass).async_preview_task(
        msg["task"], msg.get("task_id"), msg.get("scenario")
    )
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/reset_seasonal_executions",
        vol.Required("task_id"): SLUG,
    }
)
async def websocket_reset_seasonal_executions(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Reset once-per-season locks for one task rule."""
    engine = _engine(hass)
    removed = await engine.async_reset_seasonal_executions(msg["task_id"])
    result = engine.ui_data()
    result["seasonal_reset_count"] = removed
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
    _require_household_access(connection, engine)
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
        vol.Optional("priority", default="normal"): vol.In(
            ["low", "normal", "high", "critical"]
        ),
        vol.Optional("points", default=1): vol.All(int, vol.Range(min=0, max=100)),
    }
)
async def websocket_create_ad_hoc(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a one-off household task."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    await engine.async_create_ad_hoc(
        msg["name"],
        msg["assignee"],
        msg["due"],
        description=msg.get("description"),
        escalation=msg.get("escalation"),
        priority=msg["priority"],
        points=msg["points"],
        context=Context(user_id=connection.user.id),
    )
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/smart_task_preview",
        vol.Required("text"): str,
    }
)
@callback
def websocket_smart_task_preview(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Parse a smart quick-task phrase without changing state."""
    connection.send_result(msg["id"], _engine(hass).preview_smart_task(msg["text"]))


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/bulk",
        vol.Required("occurrence_ids"): [str],
        vol.Required("action"): vol.In(["complete", "tomorrow", "help", "decline"]),
    }
)
async def websocket_bulk(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Apply one task action to multiple occurrences."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    bulk_result = await engine.async_bulk_occurrences(
        msg["occurrence_ids"],
        msg["action"],
        Context(user_id=connection.user.id),
    )
    result = engine.ui_data()
    result["bulk_result"] = bulk_result
    connection.send_result(msg["id"], result)


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/toggle_favorite",
        vol.Required("task_id"): SLUG,
    }
)
async def websocket_toggle_favorite(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Toggle a reusable task for the current household person."""
    engine = _engine(hass)
    person_id = engine._person_for_context(Context(user_id=connection.user.id))
    if person_id is None:
        raise Unauthorized
    enabled = await engine.async_toggle_favorite(person_id, msg["task_id"])
    result = engine.ui_data()
    result["favorite_enabled"] = enabled
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/install_discovery",
        vol.Required("suggestion_id"): str,
        vol.Required("task_id"): SLUG,
        vol.Required("assignee"): SLUG,
    }
)
async def websocket_install_discovery(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Install a current local entity discovery suggestion."""
    engine = _engine(hass)
    await engine.async_install_discovery_suggestion(
        msg["suggestion_id"],
        msg["task_id"],
        msg["assignee"],
    )
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/task_batch_preview",
        vol.Required("text"): vol.All(str, vol.Length(min=1, max=10000)),
    }
)
@callback
def websocket_task_batch_preview(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Preview several smart tasks without changing state."""
    connection.send_result(msg["id"], _engine(hass).preview_task_batch(msg["text"]))


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/create_batch",
        vol.Required("text"): vol.All(str, vol.Length(min=1, max=10000)),
    }
)
async def websocket_create_batch(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a reviewed multi-task text block."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    batch_result = await engine.async_create_batch(
        msg["text"],
        Context(user_id=connection.user.id),
    )
    result = engine.ui_data()
    result["batch_result"] = batch_result
    connection.send_result(msg["id"], result)


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/move_occurrence",
        vol.Required("occurrence_id"): str,
        vol.Required("instruction"): vol.All(str, vol.Length(min=1, max=200)),
    }
)
async def websocket_move_occurrence(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Move one task using a natural-language instruction."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    move_result = await engine.async_move_occurrence(
        msg["occurrence_id"], msg["instruction"]
    )
    result = engine.ui_data()
    result["move_result"] = move_result
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save_task_stack",
        vol.Required("stack_id"): SLUG,
        vol.Optional("stack"): vol.Any(dict, None),
    }
)
async def websocket_save_task_stack(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create, update, or delete a task stack."""
    engine = _engine(hass)
    await engine.async_save_task_stack(msg["stack_id"], msg.get("stack"))
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/launch_task_stack",
        vol.Required("stack_id"): SLUG,
    }
)
async def websocket_launch_task_stack(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create the ordered tasks in one stack."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    created = await engine.async_launch_task_stack(
        msg["stack_id"], Context(user_id=connection.user.id)
    )
    result = engine.ui_data()
    result["stack_created"] = created
    connection.send_result(msg["id"], result)


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/add_attachment",
        vol.Required("occurrence_id"): str,
        vol.Required("name"): vol.All(str, vol.Length(min=1, max=120)),
        vol.Required("mime_type"): str,
        vol.Required("content"): vol.All(str, vol.Length(min=1, max=1_100_000)),
    }
)
async def websocket_add_attachment(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Attach one bounded local file to an occurrence."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    await engine.async_add_attachment(
        msg["occurrence_id"],
        msg["name"],
        msg["mime_type"],
        msg["content"],
    )
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/attachment_content",
        vol.Required("occurrence_id"): str,
        vol.Required("attachment_id"): str,
    }
)
@callback
def websocket_attachment_content(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return attachment data only when explicitly opened."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    connection.send_result(
        msg["id"],
        engine.attachment_content(msg["occurrence_id"], msg["attachment_id"]),
    )


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/delete_attachment",
        vol.Required("occurrence_id"): str,
        vol.Required("attachment_id"): str,
    }
)
async def websocket_delete_attachment(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete one occurrence attachment."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    await engine.async_delete_attachment(msg["occurrence_id"], msg["attachment_id"])
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/task_projection",
        vol.Required("task"): dict,
    }
)
@callback
def websocket_task_projection(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Estimate task creation volume before saving."""
    connection.send_result(msg["id"], _engine(hass).task_projection(msg["task"]))


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
    _require_household_access(connection, engine)
    await engine.async_complete_occurrence(
        msg["occurrence_id"], Context(user_id=connection.user.id)
    )
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_status",
        vol.Required("occurrence_id"): str,
        vol.Required("status"): vol.In(
            ["open", "in_progress", "waiting", "blocked", "completed", "cancelled"]
        ),
        vol.Optional("expected_revision"): int,
    }
)
async def websocket_set_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Transition one native task occurrence."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    await engine.async_set_occurrence_status(
        msg["occurrence_id"],
        msg["status"],
        expected_revision=msg.get("expected_revision"),
        context=Context(user_id=connection.user.id),
    )
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_checklist_item",
        vol.Required("occurrence_id"): str,
        vol.Required("item_id"): str,
        vol.Required("completed"): bool,
        vol.Optional("expected_revision"): int,
    }
)
async def websocket_set_checklist_item(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Toggle one item in a native task checklist."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    await engine.async_set_checklist_item(
        msg["occurrence_id"],
        msg["item_id"],
        msg["completed"],
        expected_revision=msg.get("expected_revision"),
        context=Context(user_id=connection.user.id),
    )
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_dependencies",
        vol.Required("occurrence_id"): str,
        vol.Required("dependencies"): [str],
        vol.Optional("expected_revision"): int,
    }
)
async def websocket_set_dependencies(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Replace the dependencies of one native task."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    await engine.async_set_occurrence_dependencies(
        msg["occurrence_id"],
        msg["dependencies"],
        expected_revision=msg.get("expected_revision"),
    )
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/task_history",
        vol.Required("occurrence_id"): str,
    }
)
@callback
def websocket_task_history(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the immutable history of one task."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    connection.send_result(msg["id"], engine.task_history(msg["occurrence_id"]))


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
    _require_household_access(connection, engine)
    await engine.async_claim_occurrence(
        msg["occurrence_id"], Context(user_id=connection.user.id)
    )
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_household_mode",
        vol.Required("mode"): vol.In(["normal", "vacation", "guest"]),
        vol.Optional("policy", default="pause"): vol.In(
            ["pause", "reduce", "delegate"]
        ),
        vol.Optional("delegate_to"): SLUG,
        vol.Optional("until"): str,
        vol.Optional("note"): str,
    }
)
async def websocket_set_household_mode(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Activate a temporary household operating mode."""
    await _engine(hass).async_set_household_mode(
        msg["mode"],
        policy=msg["policy"],
        delegate_to=msg.get("delegate_to"),
        until=msg.get("until"),
        note=msg.get("note"),
    )
    connection.send_result(msg["id"], _engine(hass).ui_data())


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/install_gallery_template",
        vol.Required("template_id"): SLUG,
        vol.Required("task_id"): SLUG,
        vol.Optional("assignee"): SLUG,
        vol.Optional("people"): [SLUG],
        vol.Optional("entity_id"): str,
    }
)
async def websocket_install_gallery_template(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Copy one curated gallery template into the household configuration."""
    await _engine(hass).async_install_gallery_template(
        msg["template_id"],
        msg["task_id"],
        msg.get("assignee"),
        msg.get("entity_id"),
        msg.get("people"),
    )
    connection.send_result(msg["id"], _engine(hass).ui_data())


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/undo"})
async def websocket_undo(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Undo the latest reversible panel action."""
    label = await _engine(hass).async_undo_last()
    result = _engine(hass).ui_data()
    result["undone"] = label
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/explain_task",
        vol.Required("task_id"): SLUG,
    }
)
@callback
def websocket_explain_task(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Explain why a template can or cannot currently generate work."""
    connection.send_result(msg["id"], _engine(hass).explain_task(msg["task_id"]))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/health"})
@callback
def websocket_health(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return current configuration health findings."""
    connection.send_result(msg["id"], _engine(hass).configuration_health())


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/snooze",
        vol.Required("occurrence_id"): str,
        vol.Required("choice"): vol.In(["evening", "tomorrow"]),
    }
)
async def websocket_snooze(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Snooze an occurrence from the panel."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    await engine.async_snooze_occurrence(msg["occurrence_id"], msg["choice"])
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/request_help",
        vol.Required("occurrence_id"): str,
    }
)
async def websocket_request_help(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Request voluntary help for an occurrence."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    await engine.async_request_help(msg["occurrence_id"])
    connection.send_result(msg["id"], engine.ui_data())


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/decline",
        vol.Required("occurrence_id"): str,
    }
)
async def websocket_decline(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Decline and redistribute an occurrence."""
    engine = _engine(hass)
    _require_household_access(connection, engine)
    await engine.async_decline_occurrence(msg["occurrence_id"])
    connection.send_result(msg["id"], engine.ui_data())


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register panel WebSocket commands."""
    for command in (
        websocket_get,
        websocket_week_preview,
        websocket_save_task,
        websocket_delete_task,
        websocket_save_person,
        websocket_save_defaults,
        websocket_save_monitors,
        websocket_preview_task,
        websocket_reset_seasonal_executions,
        websocket_test_notification,
        websocket_set_handover,
        websocket_clear_handover,
        websocket_delete_person,
        websocket_reset_config,
        websocket_export_config,
        websocket_import_config,
        websocket_create,
        websocket_create_ad_hoc,
        websocket_smart_task_preview,
        websocket_bulk,
        websocket_toggle_favorite,
        websocket_install_discovery,
        websocket_task_batch_preview,
        websocket_create_batch,
        websocket_move_occurrence,
        websocket_save_task_stack,
        websocket_launch_task_stack,
        websocket_add_attachment,
        websocket_attachment_content,
        websocket_delete_attachment,
        websocket_task_projection,
        websocket_complete,
        websocket_set_status,
        websocket_set_checklist_item,
        websocket_set_dependencies,
        websocket_task_history,
        websocket_claim,
        websocket_set_household_mode,
        websocket_install_gallery_template,
        websocket_undo,
        websocket_explain_task,
        websocket_health,
        websocket_snooze,
        websocket_request_help,
        websocket_decline,
    ):
        websocket_api.async_register_command(hass, command)
