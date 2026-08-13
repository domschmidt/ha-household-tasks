"""Shared pytest configuration for Household Tasks."""

import asyncio
import sys

import pytest
import pytest_socket

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    """Use the aiohttp-compatible selector loop in Windows test runs."""
    if sys.platform == "win32":
        asyncio.get_event_loop_policy()._loop_factory = asyncio.SelectorEventLoop


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_fixture_setup(fixturedef, request):
    """Allow Windows event-loop socket pairs while blocking remote networking."""
    if sys.platform == "win32" and fixturedef.argname == "event_loop":
        pytest_socket.enable_socket()
        pytest_socket.socket_allow_hosts(["127.0.0.1", "::1", "localhost"])
    yield


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in Home Assistant tests."""
    yield


@pytest.fixture
def mock_frontend_loaded(hass):
    """Avoid loading Home Assistant's separately packaged frontend assets."""
    hass.config.components.add("frontend")
