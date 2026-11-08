"""Home Keeper ↔ tuct/levoit glue integration.

Turns the filter state of Levoit air purifiers running tuct/levoit ESPHome firmware into
Home Keeper tasks and keeps one shared spare-filter stock. This first version only sets
up the config entry; the purifier handling follows in later releases.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the glue from its config entry."""
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the config entry."""
    return True
