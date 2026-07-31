"""Authenticated REST API for small external Household Tasks clients."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import voluptuous as vol
from aiohttp import web
from homeassistant.components.http import (
    KEY_HASS,
    KEY_HASS_USER,
    HomeAssistantView,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util

from .const import INTEGRATION_VERSION
from .engine import get_loaded_engine

API_VERSION = 1
NO_STORE_HEADERS = {"Cache-Control": "no-store"}
OPEN_STATUSES = {"open", "in_progress", "waiting", "blocked"}


@dataclass(slots=True)
class ClientApiError(Exception):
    """An expected client-facing API failure."""

    code: str
    message: str
    status: int = 400


def _plain_title(value: Any) -> str:
    """Remove the legacy assignee prefix from a task title."""
    return re.sub(r"^\[[^\]]+\]\s*", "", str(value or "Aufgabe"))


def resolve_client_person(engine: Any, user: Any, requested: str | None) -> str:
    """Resolve one configured household person without crossing user boundaries."""
    if requested:
        person = engine.people.get(requested)
        if person is None:
            raise ClientApiError("unknown_person", "Die Person ist unbekannt.", 404)
        if user.is_admin or person.get("user_id") == user.id:
            return requested
        raise ClientApiError("forbidden_person", "Kein Zugriff auf diese Person.", 403)

    linked = [
        person_id
        for person_id, person in engine.people.items()
        if person.get("user_id") == user.id
    ]
    if len(linked) == 1:
        return linked[0]
    if user.is_admin and len(engine.people) == 1:
        return next(iter(engine.people))
    raise ClientApiError(
        "person_required",
        "Bitte person_id konfigurieren oder den HA-Benutzer einer Person zuordnen.",
    )


def _is_open(occurrence: dict[str, Any]) -> bool:
    status = occurrence.get("status")
    if status is None:
        return not occurrence.get("resolved", False)
    return status in OPEN_STATUSES


def _is_claimable(occurrence: dict[str, Any], person_id: str) -> bool:
    if occurrence.get("assignee") is not None:
        return False
    allowed = occurrence.get("task", {}).get("assignment", {}).get("people", [])
    return not allowed or person_id in allowed


def _is_personal(occurrence: dict[str, Any], person_id: str) -> bool:
    return (
        occurrence.get("assignee") == person_id
        or person_id in occurrence.get("helpers", [])
        or _is_claimable(occurrence, person_id)
    )


def _parse_due(value: Any) -> datetime | None:
    parsed = dt_util.parse_datetime(str(value or ""))
    if parsed is None:
        return None
    return dt_util.as_local(parsed)


def _task_payload(
    occurrence_id: str,
    occurrence: dict[str, Any],
    person_id: str,
    now: datetime,
) -> dict[str, Any]:
    checklist = occurrence.get("checklist", [])
    completed_steps = sum(bool(item.get("completed")) for item in checklist)
    checklist_required = occurrence.get("task", {}).get(
        "require_checklist_completion", True
    )
    due = _parse_due(occurrence.get("due"))
    blocked = occurrence.get("status") == "blocked"
    owns_task = occurrence.get("assignee") == person_id or person_id in occurrence.get(
        "helpers", []
    )
    return {
        "id": occurrence_id,
        "title": _plain_title(occurrence.get("title")),
        "description": occurrence.get("task", {}).get("description", ""),
        "due": occurrence.get("due"),
        "status": occurrence.get("status", "open"),
        "revision": int(occurrence.get("revision", 1)),
        "assignee": occurrence.get("assignee"),
        "priority": occurrence.get("task", {})
        .get("market", {})
        .get("priority", "normal"),
        "points": int(occurrence.get("task", {}).get("market", {}).get("points", 0)),
        "overdue": bool(due and due < now),
        "due_today": bool(due and due.date() == now.date()),
        "help_status": occurrence.get("help_status"),
        "checklist": {
            "completed": completed_steps,
            "total": len(checklist),
            "required": checklist_required,
            "items": [
                {
                    "id": str(item.get("id", "")),
                    "title": str(item.get("title", "")),
                    "completed": bool(item.get("completed")),
                }
                for item in checklist
            ],
        },
        "actions": {
            "complete": owns_task
            and not blocked
            and (not checklist_required or completed_steps == len(checklist)),
            "claim": _is_claimable(occurrence, person_id),
            "snooze": owns_task and not blocked,
            "help": owns_task and occurrence.get("help_status") != "requested",
            "checklist": owns_task and not blocked,
        },
    }


def build_client_feed(engine: Any, person_id: str) -> dict[str, Any]:
    """Build a stable, secret-free representation for mobile clients."""
    now = dt_util.now()
    tasks = [
        _task_payload(occurrence_id, occurrence, person_id, now)
        for occurrence_id, occurrence in engine.state.get("occurrences", {}).items()
        if _is_open(occurrence) and _is_personal(occurrence, person_id)
    ]
    tasks.sort(key=lambda item: (item["due"] or "9999", item["title"]))
    return {
        "api_version": API_VERSION,
        "integration_version": INTEGRATION_VERSION,
        "generated_at": dt_util.utcnow().isoformat(),
        "person": {
            "id": person_id,
            "name": engine.people[person_id].get("name", person_id),
        },
        "household_mode": engine.state.get("household_mode", {}).get("mode", "normal"),
        "summary": {
            "open": len(tasks),
            "due_today": sum(item["due_today"] for item in tasks),
            "overdue": sum(item["overdue"] for item in tasks),
            "blocked": sum(item["status"] == "blocked" for item in tasks),
        },
        "tasks": tasks,
    }


def _engine(hass: HomeAssistant) -> Any:
    engine = get_loaded_engine(hass)
    if engine is None:
        raise ClientApiError(
            "not_loaded", "Household Tasks ist noch nicht geladen.", 503
        )
    return engine


def _error_response(error: ClientApiError) -> web.Response:
    return HomeAssistantView.json(
        {"error": {"code": error.code, "message": error.message}},
        status_code=error.status,
        headers=NO_STORE_HEADERS,
    )


class HouseholdTasksClientTasksView(HomeAssistantView):
    """Return tasks visible to the authenticated mobile user."""

    url = "/api/household_tasks/v1/tasks"
    name = "api:household_tasks:client_tasks"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        try:
            engine = _engine(request.app[KEY_HASS])
            person_id = resolve_client_person(
                engine, request[KEY_HASS_USER], request.query.get("person_id")
            )
            return self.json(
                build_client_feed(engine, person_id), headers=NO_STORE_HEADERS
            )
        except ClientApiError as error:
            return _error_response(error)


class HouseholdTasksClientActionView(HomeAssistantView):
    """Execute one constrained mobile task action."""

    url = "/api/household_tasks/v1/tasks/{occurrence_id}/{action}"
    name = "api:household_tasks:client_action"
    requires_auth = True

    async def post(
        self,
        request: web.Request,
        occurrence_id: str,
        action: str,
    ) -> web.Response:
        try:
            payload = await self._payload(request)
            hass = request.app[KEY_HASS]
            user = request[KEY_HASS_USER]
            engine = _engine(hass)
            requested_person = payload.get("person_id") or request.query.get(
                "person_id"
            )
            person_id = resolve_client_person(engine, user, requested_person)
            occurrence = engine.state.get("occurrences", {}).get(occurrence_id)
            if occurrence is None or not _is_open(occurrence):
                raise ClientApiError(
                    "task_not_open", "Die Aufgabe ist nicht mehr offen.", 409
                )
            if not _is_personal(occurrence, person_id):
                raise ClientApiError(
                    "forbidden_task", "Kein Zugriff auf diese Aufgabe.", 403
                )
            await self._execute(
                engine,
                occurrence_id,
                action,
                payload,
                person_id,
                Context(user_id=user.id),
            )
            result = build_client_feed(engine, person_id)
            result["action_result"] = {
                "action": action,
                "occurrence_id": occurrence_id,
            }
            return self.json(result, headers=NO_STORE_HEADERS)
        except ClientApiError as error:
            return _error_response(error)
        except vol.Invalid as error:
            return _error_response(ClientApiError("invalid_action", str(error), 409))

    @staticmethod
    async def _payload(request: web.Request) -> dict[str, Any]:
        if not request.can_read_body:
            return {}
        try:
            payload = await request.json()
        except (ValueError, TypeError) as error:
            raise ClientApiError(
                "invalid_json", "Der Request enthält kein gültiges JSON."
            ) from error
        if not isinstance(payload, dict):
            raise ClientApiError("invalid_json", "Das JSON muss ein Objekt sein.")
        return payload

    @staticmethod
    async def _execute(
        engine: Any,
        occurrence_id: str,
        action: str,
        payload: dict[str, Any],
        person_id: str,
        context: Context,
    ) -> None:
        revision = payload.get("expected_revision")
        if action == "complete":
            await engine.async_complete_occurrence(
                occurrence_id, context, expected_revision=revision
            )
            return
        if action == "claim":
            await engine.async_claim_occurrence_for_person(
                occurrence_id, person_id, context=context
            )
            return
        if action == "snooze":
            choice = payload.get("choice", "tomorrow")
            if choice not in {"evening", "tomorrow"}:
                raise ClientApiError("invalid_choice", "Ungültige Verschiebung.")
            await engine.async_snooze_occurrence(occurrence_id, choice)
            return
        if action == "help":
            await engine.async_request_help(occurrence_id)
            return
        if action == "decline":
            await engine.async_decline_occurrence(occurrence_id)
            return
        if action == "status":
            status = payload.get("status")
            if status not in {"open", "in_progress", "waiting"}:
                raise ClientApiError("invalid_status", "Ungültiger Status.")
            await engine.async_set_occurrence_status(
                occurrence_id,
                status,
                expected_revision=revision,
                context=context,
            )
            return
        if action == "checklist":
            item_id = payload.get("item_id")
            if not isinstance(item_id, str) or not item_id:
                raise ClientApiError(
                    "item_required", "Ein Checklistenpunkt ist erforderlich."
                )
            await engine.async_set_checklist_item(
                occurrence_id,
                item_id,
                bool(payload.get("completed", True)),
                expected_revision=revision,
                context=context,
            )
            return
        raise ClientApiError("unknown_action", "Die Aktion ist unbekannt.", 404)


def async_register_client_api(hass: HomeAssistant) -> None:
    """Register the versioned external-client endpoints."""
    hass.http.register_view(HouseholdTasksClientTasksView)
    hass.http.register_view(HouseholdTasksClientActionView)
