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
    from .caldav import get_caldav_service

    engines = [
        getattr(entry, "runtime_data", None)
        for entry in hass.config_entries.async_entries(DOMAIN)
    ]
    engines = [engine for engine in engines if engine is not None]
    occurrences = [
        occurrence
        for engine in engines
        for occurrence in engine.state.get("occurrences", {}).values()
    ]
    caldav = get_caldav_service(hass)
    caldav_status = caldav.public_status() if caldav else None
    return {
        "version": INTEGRATION_VERSION,
        "configured_entries": len(engines),
        "people": sum(len(engine.people) for engine in engines),
        "task_definitions": sum(len(engine.tasks) for engine in engines),
        "task_schema_version": max(
            (engine.state.get("task_schema_version", 1) for engine in engines),
            default=0,
        ),
        "task_occurrences": len(occurrences),
        "open_tasks": sum(
            occurrence.get("status") not in {"completed", "cancelled"}
            for occurrence in occurrences
        ),
        "caldav_enabled": bool(
            caldav_status and caldav_status["settings"].get("enabled")
        ),
        "caldav_credentials": len(caldav_status["credentials"]) if caldav_status else 0,
    }
