"""System health support for Household Tasks."""

from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, INTEGRATION_VERSION


@callback
def async_register(
    hass: HomeAssistant,
    register: system_health.SystemHealthRegistration,
) -> None:
    """Register Household Tasks system health information."""
    register.async_register_info(_async_system_health_info)


async def _async_system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Return information for the Home Assistant system health page."""
    engines = [
        getattr(entry, "runtime_data", None)
        for entry in hass.config_entries.async_entries(DOMAIN)
    ]
    engines = [engine for engine in engines if engine is not None]
    return {
        "version": INTEGRATION_VERSION,
        "configured_entries": len(engines),
        "people": sum(len(engine.people) for engine in engines),
        "task_definitions": sum(len(engine.tasks) for engine in engines),
    }
