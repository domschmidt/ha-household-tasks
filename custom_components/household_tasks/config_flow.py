"""Config flow for Household Tasks."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import DOMAIN


class HouseholdTasksConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up Household Tasks through the Home Assistant UI."""

    VERSION = 1

    @staticmethod
    def _todo_schema(default: str) -> vol.Schema:
        """Return the native to-do entity selector."""
        return vol.Schema(
            {
                vol.Required("todo_entity", default=default): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="todo")
                )
            }
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Create the single household task entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(
                title="Household Tasks",
                data={"todo_entity": user_input["todo_entity"]},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=self._todo_schema("todo.haushalt"),
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Change the native to-do list and reload the integration."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            return self.async_update_reload_and_abort(
                entry,
                data_updates={"todo_entity": user_input["todo_entity"]},
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._todo_schema(entry.data["todo_entity"]),
        )
