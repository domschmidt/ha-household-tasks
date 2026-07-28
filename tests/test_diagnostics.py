"""Tests for privacy-aware diagnostics and system health."""

from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.household_tasks.const import DOMAIN, INTEGRATION_VERSION
from custom_components.household_tasks.diagnostics import (
    _redact,
    async_get_config_entry_diagnostics,
)
from custom_components.household_tasks.system_health import _async_system_health_info


def test_redact_recursively_removes_household_identifiers():
    """Sensitive identifiers are removed without destroying useful structure."""
    source = {
        "todo_entity": "todo.private",
        "people": [
            {
                "name": "Example person",
                "user_id": "secret-user",
                "nested": {"tag_id": "secret-tag"},
            }
        ],
        "handovers": {
            "alex": {
                "to": "sam",
                "reason": "Private family matter",
            }
        },
        "counts": {"tasks": 3},
    }

    result = _redact(source)

    assert result["todo_entity"] == "**REDACTED**"
    assert result["people"][0]["user_id"] == "**REDACTED**"
    assert result["people"][0]["nested"]["tag_id"] == "**REDACTED**"
    assert result["people"][0]["name"] == "Example person"
    assert result["handovers"]["alex"]["to"] == "**REDACTED**"
    assert result["handovers"]["alex"]["reason"] == "**REDACTED**"
    assert result["counts"] == {"tasks": 3}


async def test_diagnostics_cover_loaded_and_unloaded_entries(hass):
    """Diagnostics expose counts while redacting household identifiers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"todo_entity": "todo.private"},
    )
    entry.add_to_hass(hass)

    unloaded = await async_get_config_entry_diagnostics(hass, entry)
    assert unloaded == {
        "entry": {"todo_entity": "**REDACTED**"},
        "loaded": False,
    }

    entry.runtime_data = SimpleNamespace(
        people={"alex": {"name": "Alex"}},
        tasks={"laundry": {"name": "Laundry"}},
        state={"occurrences": {"private-id": {"user_id": "private-user"}}},
    )
    loaded = await async_get_config_entry_diagnostics(hass, entry)
    assert loaded["loaded"]
    assert loaded["integration"]["people_count"] == 1
    assert loaded["integration"]["todo_entity"] == "**REDACTED**"
    assert loaded["state"]["occurrences"]["private-id"]["user_id"] == "**REDACTED**"


async def test_system_health_summarizes_loaded_entries(hass):
    """System health reports only aggregate, non-identifying counts."""
    entry = MockConfigEntry(domain=DOMAIN, data={"todo_entity": "todo.private"})
    entry.runtime_data = SimpleNamespace(
        people={"alex": {}},
        tasks={"laundry": {}, "dishes": {}},
    )
    entry.add_to_hass(hass)

    result = await _async_system_health_info(hass)
    assert result == {
        "version": INTEGRATION_VERSION,
        "configured_entries": 1,
        "people": 1,
        "task_definitions": 2,
    }
