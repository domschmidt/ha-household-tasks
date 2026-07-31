"""Action buttons for the official Home Assistant iOS widget."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_SERVICE_REGISTERED, EVENT_SERVICE_REMOVED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .engine import HouseholdTaskEngine


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one safe notification button for each configured person."""
    engine: HouseholdTaskEngine = entry.runtime_data
    known_people: set[str] = set()

    @callback
    def add_person_buttons(_event: Event | None = None) -> None:
        new_people = sorted(set(engine.people) - known_people)
        if not new_people:
            return
        known_people.update(new_people)
        async_add_entities(
            [
                HouseholdTaskWidgetActionButton(entry, engine, person)
                for person in new_people
            ]
        )

    add_person_buttons()
    entry.async_on_unload(
        hass.bus.async_listen(f"{DOMAIN}_updated", add_person_buttons)
    )


class HouseholdTaskWidgetActionButton(ButtonEntity):
    """Send exact, background-capable actions for the current next task."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:cellphone-message"

    def __init__(
        self,
        entry: ConfigEntry,
        engine: HouseholdTaskEngine,
        person_id: str,
    ) -> None:
        self._engine = engine
        self._person_id = person_id
        person_name = engine.people[person_id].get("name", person_id)
        self._attr_name = f"{person_name} task actions"
        self._attr_unique_id = f"{entry.entry_id}_ios_widget_actions_{person_id}"
        self._attr_suggested_object_id = f"household_tasks_{person_id}_actions"

    @property
    def available(self) -> bool:
        """Return whether the person and notification action are available."""
        person = self._engine.people.get(self._person_id)
        if person is None:
            return False
        service = str(person.get("notify", "")).removeprefix("notify.")
        return bool(service) and self.hass.services.has_service("notify", service)

    async def async_press(self) -> None:
        """Push actions for one immutable occurrence to the person's phone."""
        await self._engine.async_send_widget_actions(
            self._person_id,
            context=self._context,
        )

    async def async_added_to_hass(self) -> None:
        """Refresh availability when configuration or services change."""
        await super().async_added_to_hass()
        for event_type in (
            f"{DOMAIN}_updated",
            EVENT_SERVICE_REGISTERED,
            EVENT_SERVICE_REMOVED,
        ):
            self.async_on_remove(
                self.hass.bus.async_listen(event_type, self._handle_update)
            )

    @callback
    def _handle_update(self, _event: Event) -> None:
        self.async_write_ha_state()
