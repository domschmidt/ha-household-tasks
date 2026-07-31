"""Tests for the Household Tasks config flow."""

import pytest
from homeassistant import config_entries, data_entry_flow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.household_tasks.const import DOMAIN

pytestmark = pytest.mark.usefixtures("mock_frontend_loaded")


async def test_create_config_entry(hass):
    """The native store is installed without external configuration."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Household Tasks"
    assert result["data"] == {}


async def test_only_one_config_entry_is_allowed(hass):
    """A second config flow aborts cleanly."""
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reconfigure_is_not_needed_for_native_store(hass):
    """The native store exposes no obsolete external-list selector."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "native_store_no_reconfigure"


async def test_migration_removes_legacy_entry_data(hass):
    """Version-one entries migrate without discarding task-store data."""
    entry = MockConfigEntry(domain=DOMAIN, data={"legacy_entity": "old"}, version=1)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.version == 2
    assert entry.data == {}
