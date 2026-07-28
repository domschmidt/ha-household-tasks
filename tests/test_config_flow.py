"""Tests for the Household Tasks config flow."""

import pytest
from homeassistant import config_entries, data_entry_flow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.household_tasks.const import DOMAIN

pytestmark = pytest.mark.usefixtures("mock_frontend_loaded")


async def test_create_config_entry(hass):
    """A selected to-do entity creates the single config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"todo_entity": "todo.household"},
    )
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Household Tasks"
    assert result["data"] == {"todo_entity": "todo.household"}


async def test_only_one_config_entry_is_allowed(hass):
    """A second config flow aborts cleanly."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    await hass.config_entries.flow.async_configure(
        first["flow_id"],
        {"todo_entity": "todo.household"},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reconfigure_todo_entity(hass):
    """The selected native to-do list can be changed without reinstalling."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"todo_entity": "todo.household"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"todo_entity": "todo.family"},
    )
    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {"todo_entity": "todo.family"}
