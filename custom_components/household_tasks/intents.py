"""Home Assistant Assist intent handlers for household tasks."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from .const import DOMAIN

INTENT_LIST = "HouseholdTasksList"
INTENT_COMPLETE = "HouseholdTasksComplete"
INTENT_CREATE = "HouseholdTasksCreate"
_INTENT_TYPES = (INTENT_LIST, INTENT_COMPLETE, INTENT_CREATE)


def _slot(intent_obj: intent.Intent, name: str) -> str:
    """Return one normalized Assist slot value."""
    value = intent_obj.slots.get(name, {}).get("value", "")
    return str(value).strip()


class _HouseholdIntentHandler(intent.IntentHandler):
    """Base handler that resolves the current integration engine."""

    def _engine(self, intent_obj: intent.Intent) -> Any:
        from .engine import get_loaded_engine

        return get_loaded_engine(intent_obj.hass)


class ListTasksIntent(_HouseholdIntentHandler):
    """Read the current person's open tasks."""

    intent_type = INTENT_LIST
    description = "List the current user's household tasks"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        engine = self._engine(intent_obj)
        response = intent_obj.create_response()
        if engine is None:
            response.async_set_speech("Household Tasks ist nicht geladen.")
            return response
        person_id = engine._person_for_context(intent_obj.context)
        open_items = [
            item
            for item in engine.state["occurrences"].values()
            if not item.get("resolved")
            and (person_id is None or item.get("assignee") == person_id)
        ]
        if not open_items:
            response.async_set_speech(
                "Du hast aktuell keine offenen Haushaltsaufgaben."
            )
            return response
        names = [engine._plain_occurrence_title(item) for item in open_items[:5]]
        suffix = f" und {len(open_items) - 5} weitere" if len(open_items) > 5 else ""
        response.async_set_speech(
            f"Du hast {len(open_items)} offene Aufgaben: {', '.join(names)}{suffix}."
        )
        return response


class CompleteTaskIntent(_HouseholdIntentHandler):
    """Complete one matching task."""

    intent_type = INTENT_COMPLETE
    description = "Complete a matching household task"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        engine = self._engine(intent_obj)
        response = intent_obj.create_response()
        query = _slot(intent_obj, "task").casefold()
        if engine is None or not query:
            response.async_set_speech("Ich konnte keine passende Aufgabe bestimmen.")
            return response
        person_id = engine._person_for_context(intent_obj.context)
        matches = [
            (occurrence_id, occurrence)
            for occurrence_id, occurrence in engine.state["occurrences"].items()
            if not occurrence.get("resolved")
            and query in engine._plain_occurrence_title(occurrence).casefold()
            and (person_id is None or occurrence.get("assignee") in {None, person_id})
        ]
        if len(matches) != 1:
            response.async_set_speech(
                "Ich habe keine eindeutige offene Aufgabe dazu gefunden."
            )
            return response
        occurrence_id, occurrence = matches[0]
        await engine.async_complete_occurrence(occurrence_id, intent_obj.context)
        response.async_set_speech(
            f"{engine._plain_occurrence_title(occurrence)} ist erledigt."
        )
        return response


class CreateTaskIntent(_HouseholdIntentHandler):
    """Create one short voice task."""

    intent_type = INTENT_CREATE
    description = "Create a household task for a configured person"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        engine = self._engine(intent_obj)
        response = intent_obj.create_response()
        task_name = _slot(intent_obj, "task")
        person_name = _slot(intent_obj, "person").casefold()
        if engine is None or not task_name:
            response.async_set_speech("Die Aufgabe konnte nicht angelegt werden.")
            return response
        assignee = next(
            (
                person_id
                for person_id, person in engine.people.items()
                if person_name
                and str(person.get("name", person_id)).casefold() == person_name
            ),
            engine._person_for_context(intent_obj.context),
        )
        if assignee not in engine.people:
            response.async_set_speech(
                "Bitte verknüpfe zuerst deinen Home-Assistant-Benutzer mit einer Person."
            )
            return response
        due = (dt_util.now() + timedelta(hours=1)).replace(microsecond=0)
        await engine.async_create_ad_hoc(
            task_name,
            assignee,
            due.isoformat(),
            context=intent_obj.context,
        )
        response.async_set_speech(f"{task_name} wurde angelegt.")
        return response


def async_register_intents(hass: HomeAssistant) -> None:
    """Register all Household Tasks intents once."""
    registrations = hass.data.setdefault(DOMAIN, {}).setdefault("intents", False)
    if registrations:
        return
    for handler in (ListTasksIntent(), CompleteTaskIntent(), CreateTaskIntent()):
        intent.async_register(hass, handler)
    hass.data[DOMAIN]["intents"] = True


def async_unregister_intents(hass: HomeAssistant) -> None:
    """Remove Household Tasks intent handlers."""
    data = hass.data.get(DOMAIN, {})
    if not data.get("intents"):
        return
    for intent_type in _INTENT_TYPES:
        intent.async_remove(hass, intent_type)
    data["intents"] = False
