"""Aggregate sensors for the native Household Tasks store."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .engine import HouseholdTaskEngine


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create aggregate sensors for one integration entry."""
    engine: HouseholdTaskEngine = entry.runtime_data
    entities: list[SensorEntity] = [
        HouseholdTaskCountSensor(entry, engine, "open", "Open tasks", _open),
        HouseholdTaskCountSensor(
            entry, engine, "due_today", "Tasks due today", _due_today
        ),
        HouseholdTaskCountSensor(entry, engine, "overdue", "Overdue tasks", _overdue),
        HouseholdTaskCountSensor(entry, engine, "blocked", "Blocked tasks", _blocked),
    ]
    known_people: set[str] = set()

    @callback
    def add_person_sensors(_event: Event | None = None) -> None:
        new_people = sorted(set(engine.people) - known_people)
        if not new_people:
            return
        known_people.update(new_people)
        async_add_entities(
            [
                HouseholdTaskPersonWidgetSensor(entry, engine, person)
                for person in new_people
            ]
        )

    async_add_entities(entities)
    add_person_sensors()
    entry.async_on_unload(
        hass.bus.async_listen(f"{DOMAIN}_updated", add_person_sensors)
    )


def _active(engine: HouseholdTaskEngine) -> list[dict[str, Any]]:
    return [
        occurrence
        for occurrence in engine.state.get("occurrences", {}).values()
        if occurrence.get("status") not in {"completed", "cancelled"}
    ]


def _open(engine: HouseholdTaskEngine, _now: datetime) -> int:
    return len(_active(engine))


def _due_today(engine: HouseholdTaskEngine, now: datetime) -> int:
    today = now.date()
    return sum(
        dt_util.parse_datetime(str(occurrence.get("due"))) is not None
        and dt_util.as_local(dt_util.parse_datetime(str(occurrence["due"]))).date()
        == today
        for occurrence in _active(engine)
    )


def _overdue(engine: HouseholdTaskEngine, now: datetime) -> int:
    return sum(
        (due := dt_util.parse_datetime(str(occurrence.get("due")))) is not None
        and dt_util.as_local(due) < now
        for occurrence in _active(engine)
    )


def _blocked(engine: HouseholdTaskEngine, _now: datetime) -> int:
    return sum(occurrence.get("status") == "blocked" for occurrence in _active(engine))


class HouseholdTaskCountSensor(SensorEntity):
    """Expose one privacy-safe aggregate from the native task store."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "tasks"
    _attr_icon = "mdi:clipboard-check-outline"

    def __init__(
        self,
        entry: ConfigEntry,
        engine: HouseholdTaskEngine,
        key: str,
        name: str,
        value_fn: Callable[[HouseholdTaskEngine, datetime], int],
    ) -> None:
        self._engine = engine
        self._value_fn = value_fn
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def native_value(self) -> int:
        """Return the current aggregate count."""
        return self._value_fn(self._engine, dt_util.now())

    async def async_added_to_hass(self) -> None:
        """Refresh when the engine persists a change."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(f"{DOMAIN}_updated", self._handle_update)
        )

    @callback
    def _handle_update(self, _event: Event) -> None:
        self.async_write_ha_state()


class HouseholdTaskPersonWidgetSensor(SensorEntity):
    """Expose a compact person-scoped feed for the official HA iOS widget."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clipboard-account-outline"

    def __init__(
        self,
        entry: ConfigEntry,
        engine: HouseholdTaskEngine,
        person_id: str,
    ) -> None:
        self._engine = engine
        self._person_id = person_id
        person_name = engine.people[person_id].get("name", person_id)
        self._attr_name = f"{person_name} task inbox"
        self._attr_unique_id = f"{entry.entry_id}_ios_widget_{person_id}"
        self._attr_suggested_object_id = f"household_tasks_{person_id}"

    @property
    def available(self) -> bool:
        """Return whether the configured household person still exists."""
        return self._person_id in self._engine.people

    def _feed(self) -> dict[str, Any] | None:
        if not self.available:
            return None
        from .client_api import build_client_feed

        return build_client_feed(self._engine, self._person_id)

    @property
    def native_value(self) -> str | None:
        """Return the next task title for a glanceable widget state."""
        feed = self._feed()
        if feed is None:
            return None
        tasks = feed["tasks"]
        return str(tasks[0]["title"])[:255] if tasks else "All done"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return a bounded, secret-free preview for templates and widgets."""
        feed = self._feed()
        if feed is None:
            return {}
        tasks = feed["tasks"]
        next_task = tasks[0] if tasks else None
        return {
            **feed["summary"],
            "person_id": self._person_id,
            "person_name": feed["person"]["name"],
            "household_mode": feed["household_mode"],
            "next_task_id": next_task["id"] if next_task else None,
            "next_due": next_task["due"] if next_task else None,
            "next_status": next_task["status"] if next_task else None,
            "next_priority": next_task["priority"] if next_task else None,
            "preview": [
                {
                    "id": task["id"],
                    "title": str(task["title"])[:160],
                    "due": task["due"],
                    "status": task["status"],
                    "overdue": task["overdue"],
                }
                for task in tasks[:3]
            ],
        }

    async def async_added_to_hass(self) -> None:
        """Refresh when tasks or person configuration changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(f"{DOMAIN}_updated", self._handle_update)
        )

    @callback
    def _handle_update(self, _event: Event) -> None:
        self.async_write_ha_state()
