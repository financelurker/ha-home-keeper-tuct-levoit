"""The options pages, end to end against the real Home Keeper."""

from __future__ import annotations

from typing import Any

from custom_components.home_keeper_tuct_levoit.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .helpers import (
    add_purifier,
    hk,
    our_asset,
    our_task,
    set_stock,
    settle,
    setup_glue,
    setup_real_hk,
)


async def _open(hass: HomeAssistant, page: str) -> dict[str, Any]:
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    menu = await hass.config_entries.options.async_init(entry.entry_id)
    assert menu["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": page}
    )


async def _submit(hass: HomeAssistant, form: dict[str, Any], data: dict) -> dict[str, Any]:
    result = await hass.config_entries.options.async_configure(form["flow_id"], data)
    await settle(hass)
    return result


async def test_menu_counts_the_purifiers(hass: HomeAssistant, presses) -> None:
    await add_purifier(hass, "office", "Office")
    await add_purifier(hass, "bedroom", "Bedroom")
    await setup_real_hk(hass)
    entry = await setup_glue(hass)
    menu = await hass.config_entries.options.async_init(entry.entry_id)
    assert menu["menu_options"] == ["general", "filters", "devices"]
    assert menu["description_placeholders"] == {"count": "2"}


async def test_general_changes_the_pre_filter_schedule(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office")
    await setup_real_hk(hass)
    entry = await setup_glue(hass)

    form = await _open(hass, "general")
    assert form["type"] is FlowResultType.FORM
    result = await _submit(
        hass,
        form,
        {
            "stock_enabled": True,
            "appliances_enabled": True,
            "reset_on_done": False,
            "prefilter_enabled": True,
            "prefilter_hours": 200,
            "prefilter_weeks": 2,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["prefilter_hours"] == 200
    assert entry.options["reset_on_done"] is False
    pre = await our_task(hass, unit, "prefilter")
    assert pre["sensor"]["target"] == 200
    assert pre["sensor"]["also_every"] == {"interval": 2, "unit": "weeks"}


async def test_renaming_the_model_filter_keeps_stock_and_dates(
    hass: HomeAssistant, presses
) -> None:
    units = [
        await add_purifier(hass, "office", "Office", filter_low="on"),
        await add_purifier(hass, "bedroom", "Bedroom"),
    ]
    await setup_real_hk(hass)
    await setup_glue(hass)
    spares = await our_asset(hass, "spares")
    await set_stock(hass, spares, 3)
    await hk(hass, "complete_task", {"task_id": (await our_task(hass, units[0], "filter"))["id"]})
    await settle(hass)
    fitted = (await our_asset(hass, "purifier", units[0].device.id))["parts"][0]
    assert fitted["last_replaced"]

    form = await _open(hass, "filters")
    assert form["description_placeholders"]["defaults"] == "- Vital 100S: Vital 100S-RF"
    await _submit(
        hass,
        form,
        {"models": {"Vital 100S · 2 Geräte": "Vital 100S-RF Original"}, "devices": {}},
    )

    renamed = await our_asset(hass, "spares")
    (part,) = renamed["parts"]
    assert (part["id"], part["name"], part["stock"]) == (
        spares["parts"][0]["id"],
        "Vital 100S-RF Original",
        2,
    )
    after = (await our_asset(hass, "purifier", units[0].device.id))["parts"][0]
    assert (after["id"], after["name"], after["last_replaced"]) == (
        fitted["id"],
        "Vital 100S-RF Original",
        fitted["last_replaced"],
    )


async def test_one_purifier_with_a_filter_of_its_own(hass: HomeAssistant, presses) -> None:
    office = await add_purifier(hass, "office", "Office", filter_low="on")
    bedroom = await add_purifier(hass, "bedroom", "Bedroom", filter_low="on")
    await setup_real_hk(hass)
    entry = await setup_glue(hass)
    await set_stock(hass, await our_asset(hass, "spares"), 3)

    form = await _open(hass, "filters")
    await _submit(hass, form, {"models": {}, "devices": {"Office": "Vital 100S-RF-PA"}})
    assert entry.options["device_filters"] == {office.device.id: "Vital 100S-RF-PA"}

    spares = await our_asset(hass, "spares")
    by_name = {p["name"]: p for p in spares["parts"]}
    assert by_name["Vital 100S-RF"]["stock"] == 3  # the others' stock stays
    assert by_name["Vital 100S-RF-PA"]["stock"] is None  # its own, not counted yet
    assert by_name["Vital 100S-RF"]["notes"] == "Verwendet von: Bedroom"
    for unit, name in ((office, "Vital 100S-RF-PA"), (bedroom, "Vital 100S-RF")):
        task = await our_task(hass, unit, "filter")
        assert [
            link["part_id"] for link in task["source"]["parts"] if link["asset_id"] == spares["id"]
        ] == [by_name[name]["id"]]

    # Back to the model default: the per-device name goes.
    form = await _open(hass, "filters")
    await _submit(hass, form, {"models": {}, "devices": {"Office": ""}})
    assert entry.options["device_filters"] == {}


async def test_entities_assigned_by_hand(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on", reset_name="Swap Filter")
    await setup_real_hk(hass)
    entry = await setup_glue(hass)

    devices = await _open(hass, "devices")
    assert devices["type"] is FlowResultType.FORM
    form = await hass.config_entries.options.async_configure(
        devices["flow_id"], {"device": unit.device.id}
    )
    assert form["step_id"] == "device"
    assert "Filter-Reset-Taste: nicht gefunden" in form["description_placeholders"]["detected"]
    await _submit(hass, form, {"filter_low": unit.filter_low, "filter_reset": unit.reset})
    # Only what differs from the detection is stored.
    assert entry.options["entities"] == {unit.device.id: {"filter_reset": unit.reset}}

    task = await our_task(hass, unit, "filter")
    await hk(hass, "complete_task", {"task_id": task["id"]})
    await settle(hass)
    assert presses == [unit.reset]

    # All fields empty again: back to automatic.
    devices = await _open(hass, "devices")
    form = await hass.config_entries.options.async_configure(
        devices["flow_id"], {"device": unit.device.id}
    )
    await _submit(hass, form, {})
    assert entry.options["entities"] == {}


async def test_filter_low_is_required_when_not_detected(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office")
    await setup_real_hk(hass)
    await setup_glue(hass)
    # The sensor is called something else in the YAML, so nothing is detected.
    from homeassistant.helpers import entity_registry as er

    er.async_get(hass).async_update_entity(
        unit.filter_low, new_entity_id="binary_sensor.office_change_me", original_name="Change Me"
    )
    await settle(hass)

    devices = await _open(hass, "devices")
    form = await hass.config_entries.options.async_configure(
        devices["flow_id"], {"device": unit.device.id}
    )
    result = await _submit(hass, form, {})
    assert result["errors"] == {"filter_low": "filter_low_missing"}
    result = await _submit(hass, result, {"filter_low": "binary_sensor.office_change_me"})
    assert result["type"] is FlowResultType.CREATE_ENTRY

    hass.states.async_set("binary_sensor.office_change_me", "on")
    await settle(hass)
    assert (await our_task(hass, unit, "filter"))["next_due"]


async def test_options_need_the_integration_loaded(hass: HomeAssistant, presses) -> None:
    await setup_real_hk(hass)
    entry = await setup_glue(hass)
    await hass.config_entries.async_unload(entry.entry_id)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_loaded"
