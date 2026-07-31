"""Config flow for Household Tasks."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries

from .const import DOMAIN


class HouseholdTasksConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up Household Tasks through the Home Assistant UI."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Create the single household task entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        return self.async_create_entry(title="Household Tasks", data={})

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Explain that the native store requires no reconfiguration."""
        return self.async_abort(reason="native_store_no_reconfigure")
