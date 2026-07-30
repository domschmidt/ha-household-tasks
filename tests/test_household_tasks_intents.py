"""Tests for the Home Assistant Assist intent boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.core import Context
from homeassistant.helpers import intent

from custom_components.household_tasks import engine as engine_module
from custom_components.household_tasks.const import DOMAIN
from custom_components.household_tasks.intents import (
    CompleteTaskIntent,
    CreateTaskIntent,
    ListTasksIntent,
    async_register_intents,
    async_unregister_intents,
)


def _intent(hass, intent_type, slots=None):
    """Build one realistic Assist request."""
    return intent.Intent(
        hass,
        "conversation",
        intent_type,
        slots or {},
        None,
        Context(user_id="user-alex"),
        "de",
    )


def _engine(occurrences=None):
    """Build the small engine surface consumed by intent handlers."""
    return SimpleNamespace(
        people={"alex": {"name": "Alex"}},
        state={"occurrences": occurrences or {}},
        _person_for_context=lambda _context: "alex",
        _plain_occurrence_title=lambda occurrence: occurrence["title"],
        async_complete_occurrence=AsyncMock(),
        async_create_ad_hoc=AsyncMock(),
    )


async def test_list_intent_returns_persons_open_tasks(hass, monkeypatch):
    """Assist lists only unresolved tasks assigned to the current person."""
    task_engine = _engine(
        {
            "mine": {"title": "Müll rausbringen", "assignee": "alex"},
            "other": {"title": "Bad putzen", "assignee": "sam"},
            "done": {
                "title": "Einkaufen",
                "assignee": "alex",
                "resolved": True,
            },
        }
    )
    monkeypatch.setattr(engine_module, "get_loaded_engine", lambda _hass: task_engine)

    response = await ListTasksIntent().async_handle(_intent(hass, "HouseholdTasksList"))

    speech = response.as_dict()["speech"]["plain"]["speech"]
    assert "1 offene Aufgaben" in speech
    assert "Müll rausbringen" in speech
    assert "Bad putzen" not in speech


async def test_complete_intent_resolves_one_unique_match(hass, monkeypatch):
    """A unique spoken task title completes the matching occurrence."""
    task_engine = _engine({"waste": {"title": "Müll rausbringen", "assignee": "alex"}})
    monkeypatch.setattr(engine_module, "get_loaded_engine", lambda _hass: task_engine)

    response = await CompleteTaskIntent().async_handle(
        _intent(
            hass,
            "HouseholdTasksComplete",
            {"task": {"value": "Müll"}},
        )
    )

    task_engine.async_complete_occurrence.assert_awaited_once()
    assert "erledigt" in response.as_dict()["speech"]["plain"]["speech"]


async def test_create_intent_uses_named_person(hass, monkeypatch):
    """A voice-created task is assigned to a configured named person."""
    task_engine = _engine()
    monkeypatch.setattr(engine_module, "get_loaded_engine", lambda _hass: task_engine)

    response = await CreateTaskIntent().async_handle(
        _intent(
            hass,
            "HouseholdTasksCreate",
            {
                "task": {"value": "Spülmaschine ausräumen"},
                "person": {"value": "Alex"},
            },
        )
    )

    task_engine.async_create_ad_hoc.assert_awaited_once()
    call = task_engine.async_create_ad_hoc.await_args
    assert call.args[:2] == ("Spülmaschine ausräumen", "alex")
    assert call.kwargs["context"].user_id == "user-alex"
    assert "angelegt" in response.as_dict()["speech"]["plain"]["speech"]


def test_intent_registration_is_idempotent(hass, monkeypatch):
    """Integration setup and unload register and remove each handler once."""
    registered = []
    removed = []
    monkeypatch.setattr(
        intent,
        "async_register",
        lambda _hass, handler: registered.append(handler.intent_type),
    )
    monkeypatch.setattr(
        intent,
        "async_remove",
        lambda _hass, intent_type: removed.append(intent_type),
    )

    async_register_intents(hass)
    async_register_intents(hass)
    async_unregister_intents(hass)

    assert registered == [
        "HouseholdTasksList",
        "HouseholdTasksComplete",
        "HouseholdTasksCreate",
    ]
    assert removed == registered
    assert hass.data[DOMAIN]["intents"] is False
