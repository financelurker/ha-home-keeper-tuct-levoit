"""Fixtures: load custom_components/ in the Home Assistant test harness."""

import pytest

from .helpers import record_button_presses, stub_esphome


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


@pytest.fixture
async def presses(hass):
    """Stub ESPHome, use German texts and record every button press."""
    stub_esphome(hass)
    await hass.config.async_set_time_zone("Europe/Vienna")
    hass.config.language = "de"
    pressed = record_button_presses(hass)
    hass.data["_test_presses"] = pressed
    return pressed
