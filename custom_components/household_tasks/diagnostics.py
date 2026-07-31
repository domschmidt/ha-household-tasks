"""Diagnostics support for Household Tasks."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

TO_REDACT = {
    "assignee",
    "assigned_user_id",
    "candidates",
    "completed_by",
    "device_id",
    "entity_id",
    "from",
    "original",
    "person_id",
    "previous",
    "reason",
    "selected",
    "seasonal_executions",
    "tag_id",
    "target_person",
    "to",
    "user_id",
}


def _redact(value: Any) -> Any:
    """Recursively redact identifiers that can reveal household information."""
    if isinstance(value, dict):
        return {
            key: ("**REDACTED**" if key in TO_REDACT else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return privacy-aware diagnostics for a config entry."""
    engine = getattr(entry, "runtime_data", None)
    if engine is None:
        return {"entry": _redact(dict(entry.data)), "loaded": False}

    return {
        "entry": _redact(dict(entry.data)),
        "loaded": True,
        "integration": {
            "people_count": len(engine.people),
            "task_count": len(engine.tasks),
            "occurrence_count": len(engine.state.get("occurrences", {})),
            "task_schema_version": engine.state.get("task_schema_version"),
        },
        "state": _redact(engine.state),
    }
