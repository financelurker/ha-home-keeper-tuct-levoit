"""Home Keeper ↔ tuct/levoit glue integration.

Finds Levoit air purifiers running tuct/levoit ESPHome firmware (ESPHome project name
``tuct.levoit-<model>``) and keeps, per purifier, a filter-replacement task driven by its
Filter Low sensor and a pre-filter cleaning task (by fan runtime when a Fan Runtime
sensor exists, else by weeks), plus one shared spare-filter appliance whose stock every
filter replacement draws on.

Stateless: tasks and appliances are found again through our ``source`` namespace on
every start, and the marks the glue needs live on the tasks.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import logic
from .const import (
    HK_DOMAIN,
    HK_SERVICE_DELETE_ASSET,
    HK_SERVICE_DELETE_TASK,
    HK_SERVICE_LIST_ASSETS,
    HK_SERVICE_LIST_TASKS,
    HK_SERVICE_UPDATE_ASSET,
)
from .wiring import LevoitGlue

_LOGGER = logging.getLogger(__name__)

type LevoitGlueConfigEntry = ConfigEntry[LevoitGlue]


async def async_setup_entry(hass: HomeAssistant, entry: LevoitGlueConfigEntry) -> bool:
    """Set up the glue from its config entry."""
    glue = LevoitGlue(hass, entry)
    entry.runtime_data = glue
    await glue.async_setup()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: LevoitGlueConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: LevoitGlueConfigEntry) -> bool:
    """Unload; the tasks stay, because an unload is usually a reload."""
    return True


async def async_remove_entry(hass: HomeAssistant, entry: LevoitGlueConfigEntry) -> None:
    """On removal: delete our tasks, hand appliances worth keeping to the user."""
    services = hass.services
    if services.has_service(HK_DOMAIN, HK_SERVICE_LIST_TASKS):
        try:
            resp = await services.async_call(
                HK_DOMAIN, HK_SERVICE_LIST_TASKS, {}, blocking=True, return_response=True
            )
            for task in logic.our_tasks(list((resp or {}).get("tasks", []))):
                await services.async_call(
                    HK_DOMAIN,
                    HK_SERVICE_DELETE_TASK,
                    {"task_id": task["id"], "force": True},
                    blocking=True,
                )
        except Exception:
            _LOGGER.debug("Could not remove the purifier tasks", exc_info=True)

    if not services.has_service(HK_DOMAIN, HK_SERVICE_LIST_ASSETS):
        return
    try:
        resp = await services.async_call(
            HK_DOMAIN, HK_SERVICE_LIST_ASSETS, {}, blocking=True, return_response=True
        )
        for asset in logic.our_assets(list((resp or {}).get("assets", []))):
            counted = any(p.get("stock") is not None for p in asset.get("parts") or [])
            if counted or asset.get("kind") == "existing":
                # Spares still in the drawer, or details on the user's own device page:
                # keep it, drop only our ownership (INTEGRATING.md §8, "Uninstall").
                await services.async_call(
                    HK_DOMAIN,
                    HK_SERVICE_UPDATE_ASSET,
                    {"asset_id": asset["id"], "managed_by": None},
                    blocking=True,
                )
            else:
                await services.async_call(
                    HK_DOMAIN,
                    HK_SERVICE_DELETE_ASSET,
                    {"asset_id": asset["id"], "force": True},
                    blocking=True,
                )
    except Exception:
        _LOGGER.debug("Could not release the appliances", exc_info=True)
