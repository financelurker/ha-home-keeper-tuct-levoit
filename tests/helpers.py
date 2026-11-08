"""Shared helpers: stub ESPHome purifiers, the real Home Keeper, and the glue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    async_fire_time_changed,
    mock_integration,
)

from custom_components.home_keeper_tuct_levoit.const import DOMAIN, SOURCE_NS
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util


@dataclass
class Unit:
    """One stub purifier."""

    entry: MockConfigEntry
    device: dr.DeviceEntry
    filter_low: str
    reset: str | None
    runtime: str | None


async def settle(hass: HomeAssistant) -> None:
    """Let the registry debounce fire and every job finish."""
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()


def stub_esphome(hass: HomeAssistant) -> None:
    async def _ok(hass, entry):
        return True

    mock_integration(hass, MockModule("esphome", async_setup_entry=_ok, async_unload_entry=_ok))


def record_button_presses(hass: HomeAssistant, pressed: list[str] | None = None) -> list[str]:
    """Stand in for ``button.press`` and record every press.

    Home Keeper sets up the button domain (its mark-done buttons), which registers the
    real ``button.press``; call this again after it to take the service back.
    """
    pressed = [] if pressed is None else pressed

    async def _press(call: ServiceCall) -> None:
        ids = call.data["entity_id"]
        pressed.extend([ids] if isinstance(ids, str) else ids)

    hass.services.async_register("button", "press", _press)
    return pressed


async def add_purifier(
    hass: HomeAssistant,
    slug: str,
    name: str,
    *,
    model: str = "levoit-vital100s",
    manufacturer: str = "tuct",
    runtime: bool = True,
    reset: bool = True,
    filter_low: str = "off",
    runtime_hours: float = 10.0,
    reset_name: str = "Filter Reset",
) -> Unit:
    """An ESPHome entry with one purifier device and its entities in the registry."""
    entry = MockConfigEntry(domain="esphome", title=name, unique_id=slug)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("esphome", slug)},
        name=name,
        manufacturer=manufacturer,
        model=model,
        sw_version="1.6.0 (ESPHome 2026.10.0)",
    )
    ent_reg = er.async_get(hass)

    def _add(domain: str, key: str, original_name: str) -> str:
        return ent_reg.async_get_or_create(
            domain,
            "esphome",
            f"{slug}-{domain}-{key}",
            suggested_object_id=f"{slug}_{key}",
            config_entry=entry,
            device_id=device.id,
            original_name=original_name,
        ).entity_id

    low = _add("binary_sensor", "filter_low", "Filter Low")
    btn = _add("button", slugify_name(reset_name), reset_name) if reset else None
    run = _add("sensor", "fan_runtime", "Fan Runtime") if runtime else None
    hass.states.async_set(low, filter_low)
    if run:
        hass.states.async_set(
            run, str(runtime_hours), {"unit_of_measurement": "h", "state_class": "total_increasing"}
        )
    return Unit(entry=entry, device=device, filter_low=low, reset=btn, runtime=run)


def slugify_name(name: str) -> str:
    return name.lower().replace(" ", "_")


async def hk_idle(hass: HomeAssistant) -> None:
    """Wait until Home Keeper is loaded and done reloading (it reloads for buy tasks)."""
    from homeassistant.config_entries import ConfigEntryState

    for _ in range(20):
        await settle(hass)
        entries = hass.config_entries.async_entries("home_keeper")
        if all(e.state is ConfigEntryState.LOADED for e in entries):
            await settle(hass)
            return
    raise AssertionError("Home Keeper did not finish loading")


async def setup_real_hk(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="home_keeper", data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    if (pressed := hass.data.get("_test_presses")) is not None:
        record_button_presses(hass, pressed)
    return entry


async def setup_glue(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    return entry


async def hk(hass: HomeAssistant, service: str, data: dict, *, response: bool = False) -> Any:
    return await hass.services.async_call(
        "home_keeper", service, data, blocking=True, return_response=response
    )


async def tasks(hass: HomeAssistant) -> list[dict]:
    return (await hk(hass, "list_tasks", {}, response=True))["tasks"]


async def assets(hass: HomeAssistant) -> list[dict]:
    return (await hk(hass, "list_assets", {}, response=True))["assets"]


def ours(record: dict) -> dict:
    return (record.get("source") or {}).get(SOURCE_NS) or {}


async def our_task(hass: HomeAssistant, unit: Unit, role: str) -> dict | None:
    for task in await tasks(hass):
        src = ours(task)
        if src.get("device_id") == unit.device.id and src.get("role") == role:
            return task
    return None


async def our_asset(hass: HomeAssistant, role: str, device_id: str | None = None) -> dict | None:
    for asset in await assets(hass):
        src = ours(asset)
        if src.get("role") == role and src.get("device_id") == device_id:
            return asset
    return None


async def set_stock(hass: HomeAssistant, asset: dict, count: int, **extra: Any) -> None:
    """Enter a spare-filter count the way a user does, on every part."""
    await hk(
        hass,
        "update_asset",
        {
            "asset_id": asset["id"],
            "parts": [
                {"id": p["id"], "name": p["name"], "stock": count, **extra} for p in asset["parts"]
            ],
        },
    )
