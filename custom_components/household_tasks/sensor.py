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
    async_add_entities(
        [
            HouseholdTaskCountSensor(entry, engine, "open", "Open tasks", _open),
            HouseholdTaskCountSensor(
                entry, engine, "due_today", "Tasks due today", _due_today
            ),
            HouseholdTaskCountSensor(
                entry, engine, "overdue", "Overdue tasks", _overdue
            ),
            HouseholdTaskCountSensor(
                entry, engine, "blocked", "Blocked tasks", _blocked
            ),
        ]
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
