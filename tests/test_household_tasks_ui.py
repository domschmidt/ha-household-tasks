"""Contract tests for the panel WebSocket adapter."""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.household_tasks import ui


class _Connection:
    """Minimal active connection used to exercise adapter behavior."""

    def __init__(self) -> None:
        self.user = SimpleNamespace(id="user-1", is_admin=True)
        self.results = []

    def send_result(self, message_id, result) -> None:
        self.results.append((message_id, result))


def _fake_engine() -> MagicMock:
    engine = MagicMock()
    engine.ui_data.side_effect = lambda: {"revision": len(engine.ui_data.mock_calls)}
    engine.async_week_preview = AsyncMock(
        return_value=[{"task_id": "waste", "read_only": True}]
    )
    engine._person_for_context.return_value = "alex"
    engine.preview_smart_task.return_value = {"name": "Laundry"}
    engine.preview_task_batch.return_value = [{"name": "Laundry"}]
    engine.task_projection.return_value = {"risk": "low"}
    engine.attachment_content.return_value = {"content": "aGVsbG8="}
    engine.explain_task.return_value = {"task_id": "laundry"}
    engine.configuration_health.return_value = {"findings": []}
    engine.async_reset_seasonal_executions = AsyncMock(return_value=2)
    engine.async_test_notification = AsyncMock()
    engine.async_create_manual = AsyncMock()
    engine.async_create_ad_hoc = AsyncMock()
    engine.async_bulk_occurrences = AsyncMock(return_value={"completed": ["one"]})
    engine.async_toggle_favorite = AsyncMock(return_value=True)
    engine.async_install_discovery_suggestion = AsyncMock()
    engine.async_create_batch = AsyncMock(return_value={"created": [0]})
    engine.async_move_occurrence = AsyncMock(return_value={"kind": "datetime"})
    engine.async_save_task_stack = AsyncMock()
    engine.async_launch_task_stack = AsyncMock(return_value=["occurrence-1"])
    engine.async_add_attachment = AsyncMock()
    engine.async_add_attachment_chunk = AsyncMock(
        return_value={"complete": False, "next_chunk": 1}
    )
    engine.async_delete_attachment = AsyncMock()
    engine.attachment_content_chunk.return_value = {
        "content": "aGVs",
        "next_offset": 4,
        "complete": False,
    }
    engine.async_complete_occurrence = AsyncMock()
    engine.async_set_occurrence_status = AsyncMock()
    engine.async_set_checklist_item = AsyncMock()
    engine.async_set_occurrence_dependencies = AsyncMock()
    engine.task_history.return_value = [{"type": "task_created"}]
    engine.async_claim_occurrence = AsyncMock()
    engine.async_set_household_mode = AsyncMock()
    engine.async_install_gallery_template = AsyncMock()
    engine.async_undo_last = AsyncMock(return_value="Aufgabe wiederherstellen")
    engine.async_snooze_occurrence = AsyncMock()
    engine.async_request_help = AsyncMock()
    engine.async_decline_occurrence = AsyncMock()
    return engine


async def _call(command, hass, connection, message) -> None:
    """Invoke the command body without Home Assistant's dispatch decorators."""
    result = inspect.unwrap(command)(hass, connection, message)
    if inspect.isawaitable(result):
        await result


async def test_caldav_admin_adapters_manage_settings_and_one_time_credentials(hass):
    """CalDAV management commands preserve scopes without leaking via the engine."""
    engine = _fake_engine()
    service = MagicMock()
    service.async_save_settings = AsyncMock(return_value={"settings": {}})
    service.async_create_credential = AsyncMock(
        return_value={
            "settings": {},
            "created_credential": {
                "username": "alex-device",
                "password": "shown-once",
            },
        }
    )
    service.async_revoke_credential = AsyncMock(return_value={"credentials": []})
    connection = _Connection()

    with (
        patch.object(ui, "_engine", return_value=engine),
        patch(
            "custom_components.household_tasks.caldav.get_caldav_service",
            return_value=service,
        ),
    ):
        await _call(
            ui.websocket_caldav_save_settings,
            hass,
            connection,
            {"id": 1, "settings": {"enabled": True}},
        )
        await _call(
            ui.websocket_caldav_create_credential,
            hass,
            connection,
            {
                "id": 2,
                "person_id": "alex",
                "label": "Alex iPhone",
                "permission": "read_write",
                "scope": "personal",
                "include_claimable": True,
                "complete_checklist_on_parent": True,
                "expires_at": None,
            },
        )
        await _call(
            ui.websocket_caldav_revoke_credential,
            hass,
            connection,
            {"id": 3, "credential_id": "credential-1"},
        )

    service.async_save_settings.assert_awaited_once_with({"enabled": True})
    service.async_create_credential.assert_awaited_once_with(
        person_id="alex",
        label="Alex iPhone",
        permission="read_write",
        scope="personal",
        include_claimable=True,
        complete_checklist_on_parent=True,
        expires_at=None,
    )
    service.async_revoke_credential.assert_awaited_once_with("credential-1")
    assert (
        connection.results[1][1]["caldav"]["created_credential"]["password"]
        == "shown-once"
    )


async def test_websocket_adapters_forward_advanced_panel_actions(hass):
    """All advanced commands preserve parameters and enrich their responses."""
    engine = _fake_engine()
    connection = _Connection()

    with patch.object(ui, "_engine", return_value=engine):
        await _call(
            ui.websocket_reset_seasonal_executions,
            hass,
            connection,
            {"id": 1, "task_id": "frost"},
        )
        await _call(
            ui.websocket_smart_task_preview,
            hass,
            connection,
            {"id": 2, "text": "Laundry tomorrow"},
        )
        await _call(
            ui.websocket_bulk,
            hass,
            connection,
            {"id": 3, "occurrence_ids": ["one"], "action": "complete"},
        )
        await _call(
            ui.websocket_toggle_favorite,
            hass,
            connection,
            {"id": 4, "task_id": "laundry"},
        )
        await _call(
            ui.websocket_install_discovery,
            hass,
            connection,
            {
                "id": 5,
                "suggestion_id": "battery_sensor",
                "task_id": "battery",
                "assignee": "alex",
            },
        )
        await _call(
            ui.websocket_task_batch_preview,
            hass,
            connection,
            {"id": 6, "text": "Laundry; dishes"},
        )
        await _call(
            ui.websocket_create_batch,
            hass,
            connection,
            {"id": 7, "text": "Laundry; dishes"},
        )
        await _call(
            ui.websocket_move_occurrence,
            hass,
            connection,
            {"id": 8, "occurrence_id": "one", "instruction": "tomorrow"},
        )
        await _call(
            ui.websocket_save_task_stack,
            hass,
            connection,
            {"id": 9, "stack_id": "evening", "stack": {"task_ids": ["laundry"]}},
        )
        await _call(
            ui.websocket_launch_task_stack,
            hass,
            connection,
            {"id": 10, "stack_id": "evening"},
        )
        await _call(
            ui.websocket_add_attachment,
            hass,
            connection,
            {
                "id": 11,
                "occurrence_id": "one",
                "name": "proof.png",
                "mime_type": "image/png",
                "content": "aGVsbG8=",
            },
        )
        await _call(
            ui.websocket_attachment_content,
            hass,
            connection,
            {"id": 12, "occurrence_id": "one", "attachment_id": "attachment-1"},
        )
        await _call(
            ui.websocket_delete_attachment,
            hass,
            connection,
            {"id": 13, "occurrence_id": "one", "attachment_id": "attachment-1"},
        )
        await _call(
            ui.websocket_task_projection,
            hass,
            connection,
            {"id": 14, "task": {"schedule": {"type": "manual"}}},
        )
        await _call(
            ui.websocket_complete, hass, connection, {"id": 15, "occurrence_id": "one"}
        )
        await _call(
            ui.websocket_claim, hass, connection, {"id": 16, "occurrence_id": "one"}
        )
        await _call(
            ui.websocket_set_household_mode,
            hass,
            connection,
            {
                "id": 17,
                "mode": "vacation",
                "policy": "delegate",
                "delegate_to": "alex",
                "until": "2026-08-31T10:00:00+00:00",
                "note": "Holiday",
            },
        )
        await _call(
            ui.websocket_install_gallery_template,
            hass,
            connection,
            {
                "id": 18,
                "template_id": "frostschutz",
                "task_id": "frost",
                "assignee": "alex",
                "people": ["alex"],
                "entity_id": "weather.home",
            },
        )
        await _call(ui.websocket_undo, hass, connection, {"id": 19})
        await _call(
            ui.websocket_explain_task,
            hass,
            connection,
            {"id": 20, "task_id": "laundry"},
        )
        await _call(ui.websocket_health, hass, connection, {"id": 21})
        await _call(
            ui.websocket_snooze,
            hass,
            connection,
            {"id": 22, "occurrence_id": "one", "choice": "tomorrow"},
        )
        await _call(
            ui.websocket_request_help,
            hass,
            connection,
            {"id": 23, "occurrence_id": "one"},
        )
        await _call(
            ui.websocket_decline, hass, connection, {"id": 24, "occurrence_id": "one"}
        )
        await _call(
            ui.websocket_set_status,
            hass,
            connection,
            {
                "id": 25,
                "occurrence_id": "one",
                "status": "in_progress",
                "expected_revision": 2,
            },
        )
        await _call(
            ui.websocket_set_checklist_item,
            hass,
            connection,
            {
                "id": 26,
                "occurrence_id": "one",
                "item_id": "tools",
                "completed": True,
                "expected_revision": 3,
            },
        )
        await _call(
            ui.websocket_set_dependencies,
            hass,
            connection,
            {
                "id": 27,
                "occurrence_id": "one",
                "dependencies": ["zero"],
                "expected_revision": 4,
            },
        )
        await _call(
            ui.websocket_task_history,
            hass,
            connection,
            {"id": 28, "occurrence_id": "one"},
        )

    assert [message_id for message_id, _result in connection.results] == list(
        range(1, 29)
    )
    assert connection.results[0][1]["seasonal_reset_count"] == 2
    assert connection.results[2][1]["bulk_result"] == {"completed": ["one"]}
    assert connection.results[3][1]["favorite_enabled"] is True
    assert connection.results[6][1]["batch_result"] == {"created": [0]}
    assert connection.results[7][1]["move_result"] == {"kind": "datetime"}
    assert connection.results[9][1]["stack_created"] == ["occurrence-1"]
    assert connection.results[18][1]["undone"] == "Aufgabe wiederherstellen"
    assert connection.results[27][1] == [{"type": "task_created"}]
    engine.async_set_occurrence_dependencies.assert_awaited_once_with(
        "one", ["zero"], expected_revision=4
    )
    engine.async_set_household_mode.assert_awaited_once_with(
        "vacation",
        policy="delegate",
        delegate_to="alex",
        until="2026-08-31T10:00:00+00:00",
        note="Holiday",
    )


async def test_websocket_attachment_chunks_forward_transport_fields(hass):
    """Large attachment blocks remain authenticated and ordered by the engine."""
    engine = _fake_engine()
    connection = _Connection()
    with patch.object(ui, "_engine", return_value=engine):
        await _call(
            ui.websocket_add_attachment_chunk,
            hass,
            connection,
            {
                "id": 1,
                "occurrence_id": "one",
                "upload_id": "upload-1",
                "name": "proof.png",
                "mime_type": "image/png",
                "chunk_index": 0,
                "total_chunks": 2,
                "content": "aGVs",
            },
        )
        await _call(
            ui.websocket_attachment_content_chunk,
            hass,
            connection,
            {
                "id": 2,
                "occurrence_id": "one",
                "attachment_id": "attachment-1",
                "offset": 0,
            },
        )

    assert connection.results == [
        (1, {"complete": False, "next_chunk": 1}),
        (2, {"content": "aGVs", "next_offset": 4, "complete": False}),
    ]
    engine.async_add_attachment_chunk.assert_awaited_once_with(
        "one", "upload-1", "proof.png", "image/png", 0, 2, "aGVs"
    )
    engine.attachment_content_chunk.assert_called_once_with("one", "attachment-1", 0)


async def test_websocket_week_preview_returns_live_projection(hass):
    """The panel adapter awaits calendar-backed weekly projections."""
    engine = _fake_engine()
    connection = _Connection()

    with patch.object(ui, "_engine", return_value=engine):
        await _call(ui.websocket_week_preview, hass, connection, {"id": 1})

    assert connection.results == [(1, [{"task_id": "waste", "read_only": True}])]
    engine.async_week_preview.assert_awaited_once_with()
