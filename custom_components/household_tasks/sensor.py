"""Aggregate sensors for the native Household Tasks store."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .engine import HouseholdTaskEngine

PERSON_WIDGET_METRICS: tuple[tuple[str, str, str], ...] = (
    ("open", "person_open_tasks", "mdi:clipboard-text-outline"),
    ("due_today", "person_tasks_due_today", "mdi:calendar-today-outline"),
    ("overdue", "person_overdue_tasks", "mdi:clock-alert-outline"),
    ("blocked", "person_blocked_tasks", "mdi:cancel"),
)
PERSON_WIDGET_TASK_SLOTS = 5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create aggregate sensors for one integration entry."""
    engine: HouseholdTaskEngine = entry.runtime_data
    entities: list[SensorEntity] = [
        HouseholdTaskCountSensor(entry, engine, description, value_fn)
        for description, value_fn in COUNT_SENSOR_DESCRIPTIONS
    ]
    known_people: set[str] = set()

    @callback
    def add_person_sensors(_event: Event | None = None) -> None:
        new_people = sorted(set(engine.people) - known_people)
        if not new_people:
            return
        known_people.update(new_people)
        person_entities: list[SensorEntity] = []
        for person in new_people:
            person_entities.extend(_person_widget_entities(entry, engine, person))
        async_add_entities(person_entities)

    async_add_entities(entities)
    add_person_sensors()
    entry.async_on_unload(
        hass.bus.async_listen(f"{DOMAIN}_updated", add_person_sensors)
    )


def _person_widget_entities(
    entry: ConfigEntry,
    engine: HouseholdTaskEngine,
    person_id: str,
) -> list[SensorEntity]:
    """Build the stable widget surface for one household person."""
    return [
        HouseholdTaskPersonWidgetSensor(entry, engine, person_id),
        HouseholdTaskPersonNextTaskSensor(entry, engine, person_id),
        *(
            HouseholdTaskPersonCountSensor(
                entry,
                engine,
                person_id,
                metric,
                translation_key,
                icon,
            )
            for metric, translation_key, icon in PERSON_WIDGET_METRICS
        ),
        *(
            HouseholdTaskPersonTaskSlotSensor(entry, engine, person_id, position)
            for position in range(1, PERSON_WIDGET_TASK_SLOTS + 1)
        ),
    ]


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


COUNT_SENSOR_DESCRIPTIONS: tuple[
    tuple[SensorEntityDescription, Callable[[HouseholdTaskEngine, datetime], int]],
    ...,
] = (
    (
        SensorEntityDescription(
            key="open",
            translation_key="open_tasks",
            icon="mdi:clipboard-check-outline",
        ),
        _open,
    ),
    (
        SensorEntityDescription(
            key="due_today",
            translation_key="tasks_due_today",
            icon="mdi:calendar-today-outline",
        ),
        _due_today,
    ),
    (
        SensorEntityDescription(
            key="overdue",
            translation_key="overdue_tasks",
            icon="mdi:clock-alert-outline",
        ),
        _overdue,
    ),
    (
        SensorEntityDescription(
            key="blocked",
            translation_key="blocked_tasks",
            icon="mdi:cancel",
        ),
        _blocked,
    ),
)


class HouseholdTaskCountSensor(SensorEntity):
    """Expose one privacy-safe aggregate from the native task store."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry,
        engine: HouseholdTaskEngine,
        description: SensorEntityDescription,
        value_fn: Callable[[HouseholdTaskEngine, datetime], int],
    ) -> None:
        self._engine = engine
        self._value_fn = value_fn
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

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


class HouseholdTaskPersonSensorBase(SensorEntity):
    """Share person-scoped feed access and update handling."""

    _attr_has_entity_name = True

    def __init__(
        self,
        engine: HouseholdTaskEngine,
        person_id: str,
    ) -> None:
        self._engine = engine
        self._person_id = person_id

    @property
    def available(self) -> bool:
        """Return whether the configured household person still exists."""
        return self._person_id in self._engine.people

    def _feed(self) -> dict[str, Any] | None:
        if not self.available:
            return None
        from .client_api import build_client_feed

        return build_client_feed(self._engine, self._person_id)

    async def async_added_to_hass(self) -> None:
        """Refresh when tasks or person configuration changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(f"{DOMAIN}_updated", self._handle_update)
        )

    @callback
    def _handle_update(self, _event: Event) -> None:
        self.async_write_ha_state()


class HouseholdTaskPersonWidgetSensor(HouseholdTaskPersonSensorBase):
    """Keep the original personal inbox entity backward compatible."""

    _attr_icon = "mdi:clipboard-account-outline"

    def __init__(
        self,
        entry: ConfigEntry,
        engine: HouseholdTaskEngine,
        person_id: str,
    ) -> None:
        super().__init__(engine, person_id)
        person_name = engine.people[person_id].get("name", person_id)
        self._attr_name = f"{person_name} task inbox"
        self._attr_unique_id = f"{entry.entry_id}_ios_widget_{person_id}"
        self._attr_suggested_object_id = f"household_tasks_{person_id}"

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


class HouseholdTaskPersonNextTaskSensor(HouseholdTaskPersonSensorBase):
    """Expose an explicitly named next-task entity for one person."""

    _attr_icon = "mdi:clipboard-account-outline"

    def __init__(
        self,
        entry: ConfigEntry,
        engine: HouseholdTaskEngine,
        person_id: str,
    ) -> None:
        super().__init__(engine, person_id)
        self._attr_translation_key = "person_next_task"
        self._attr_translation_placeholders = {
            "person_name": str(engine.people[person_id].get("name", person_id))
        }
        self._attr_unique_id = f"{entry.entry_id}_ios_widget_{person_id}_next_task"
        self._attr_suggested_object_id = f"household_tasks_{person_id}_next_task"

    @property
    def native_value(self) -> str | None:
        """Return the title of the first task in the person's ordered feed."""
        feed = self._feed()
        if feed is None:
            return None
        return _task_title(feed["tasks"], 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose bounded metadata for the selected task."""
        feed = self._feed()
        return _task_attributes(feed, 0, self._person_id)


class HouseholdTaskPersonCountSensor(HouseholdTaskPersonSensorBase):
    """Expose one numeric, person-scoped task metric."""

    def __init__(
        self,
        entry: ConfigEntry,
        engine: HouseholdTaskEngine,
        person_id: str,
        metric: str,
        translation_key: str,
        icon: str,
    ) -> None:
        super().__init__(engine, person_id)
        self._metric = metric
        self._attr_icon = icon
        self._attr_translation_key = translation_key
        self._attr_translation_placeholders = {
            "person_name": str(engine.people[person_id].get("name", person_id))
        }
        self._attr_unique_id = f"{entry.entry_id}_ios_widget_{person_id}_{metric}"
        self._attr_suggested_object_id = f"household_tasks_{person_id}_{metric}"

    @property
    def native_value(self) -> int | None:
        """Return the selected personal summary count without a text unit."""
        feed = self._feed()
        return int(feed["summary"][self._metric]) if feed is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the stable person identity behind the count."""
        feed = self._feed()
        if feed is None:
            return {}
        return {
            "person_id": self._person_id,
            "person_name": feed["person"]["name"],
        }


class HouseholdTaskPersonTaskSlotSensor(HouseholdTaskPersonSensorBase):
    """Expose one stable read-only position in a person's ordered task list."""

    _attr_icon = "mdi:format-list-numbered"

    def __init__(
        self,
        entry: ConfigEntry,
        engine: HouseholdTaskEngine,
        person_id: str,
        position: int,
    ) -> None:
        super().__init__(engine, person_id)
        self._index = position - 1
        self._attr_translation_key = "person_task_slot"
        self._attr_translation_placeholders = {
            "person_name": str(engine.people[person_id].get("name", person_id)),
            "position": str(position),
        }
        self._attr_unique_id = (
            f"{entry.entry_id}_ios_widget_{person_id}_next_task_{position}"
        )
        self._attr_suggested_object_id = (
            f"household_tasks_{person_id}_next_task_{position}"
        )

    @property
    def native_value(self) -> str | None:
        """Return the task title at this stable list position."""
        feed = self._feed()
        if feed is None:
            return None
        return _task_title(feed["tasks"], self._index)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose bounded metadata for this list position."""
        feed = self._feed()
        return _task_attributes(feed, self._index, self._person_id)


def _task_title(tasks: list[dict[str, Any]], index: int) -> str:
    """Return a bounded task title or a language-neutral empty-list marker."""
    return str(tasks[index]["title"])[:255] if index < len(tasks) else "—"


def _task_attributes(
    feed: dict[str, Any] | None,
    index: int,
    person_id: str,
) -> dict[str, Any]:
    """Return safe metadata for one task-list position."""
    if feed is None:
        return {}
    tasks = feed["tasks"]
    task = tasks[index] if index < len(tasks) else None
    return {
        "person_id": person_id,
        "person_name": feed["person"]["name"],
        "position": index + 1,
        "task_id": task["id"] if task else None,
        "due": task["due"] if task else None,
        "status": task["status"] if task else None,
        "priority": task["priority"] if task else None,
        "overdue": task["overdue"] if task else False,
    }
