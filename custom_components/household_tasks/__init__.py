"""Household Tasks integration for Home Assistant."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .engine import async_setup, async_setup_entry, async_unload_entry

__all__ = ("async_setup", "async_setup_entry", "async_unload_entry")


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove the legacy external task-list selector from config entries."""
    if entry.version > 2:
        return False
    if entry.version < 2 or entry.data:
        hass.config_entries.async_update_entry(entry, data={}, version=2)
    return True
