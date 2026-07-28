"""Shared pytest configuration for Household Tasks."""

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in Home Assistant tests."""
    yield


@pytest.fixture
def mock_frontend_loaded(hass):
    """Avoid loading Home Assistant's separately packaged frontend assets."""
    hass.config.components.add("frontend")
