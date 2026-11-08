"""End to end against the real Home Keeper integration (vendored for the tests).

Each test sets up a different household: one purifier or several, one model or mixed,
with or without a Fan Runtime sensor or a reset button, with different options.
"""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.home_keeper_tuct_levoit.const import DOMAIN, SOURCE_NS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .helpers import (
    add_purifier,
    assets,
    hk,
    hk_idle,
    our_asset,
    our_task,
    ours,
    record_button_presses,
    set_stock,
    settle,
    setup_glue,
    setup_real_hk,
    tasks,
)


async def test_setup_builds_prefilter_spares_and_appliance(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office")
    await setup_real_hk(hass)
    await setup_glue(hass)

    # Filter fine → no filter task yet (created on the first Filter Low).
    assert await our_task(hass, unit, "filter") is None

    pre = await our_task(hass, unit, "prefilter")
    assert pre is not None
    assert pre["name"] == "Vorfilter reinigen (Office)"
    assert pre["recurrence_type"] == "sensor"
    assert pre["sensor"]["entity_id"] == unit.runtime
    assert pre["sensor"]["mode"] == "usage"
    assert pre["sensor"]["target"] == 300
    assert pre["sensor"]["also_every"] == {"interval": 4, "unit": "weeks"}
    assert pre["device_id"] == unit.device.id
    assert pre["managed_by"]["integration"] == DOMAIN
    assert "ausspülen" in pre["notes"]  # Vital: washable pre-filter
    assert pre["next_due"] is None  # monitored, counting fan hours

    spares = await our_asset(hass, "spares")
    assert spares is not None
    assert spares["name"] == "Levoit-Ersatzfilter"
    assert [p["name"] for p in spares["parts"]] == ["Vital 100S-RF"]
    assert spares["parts"][0]["type"] == "consumable"
    assert spares["parts"][0]["notes"] == "Verwendet von: Office"

    appliance = await our_asset(hass, "purifier", unit.device.id)
    assert appliance is not None
    assert appliance["kind"] == "existing"
    assert appliance["model"] == "Vital 100S"

    # Idempotent: a reload changes nothing.
    before = (await tasks(hass), await assets(hass))
    await hass.config_entries.async_reload(hass.config_entries.async_entries(DOMAIN)[0].entry_id)
    await settle(hass)
    assert (await tasks(hass), await assets(hass)) == before
    assert presses == []


async def test_filter_cycle_draws_shared_stock_and_resets_purifier(
    hass: HomeAssistant, presses
) -> None:
    unit = await add_purifier(hass, "office", "Office")
    await setup_real_hk(hass)
    await setup_glue(hass)
    await set_stock(hass, await our_asset(hass, "spares"), 2)

    # Filter Low → an armed task, linked to the shared spare filter.
    hass.states.async_set(unit.filter_low, "on")
    await settle(hass)
    task = await our_task(hass, unit, "filter")
    assert task is not None
    assert task["next_due"]
    assert task["name"] == "Filter tauschen (Office)"
    assert task["managed_by"]["completion_prompt"]
    spares = await our_asset(hass, "spares")
    assert task["source"]["parts"] == [
        {"asset_id": spares["id"], "part_id": spares["parts"][0]["id"], "quantity": 1}
    ]

    # The user ticks it off → the purifier's statistics are reset, one filter comes off
    # the stock, and the task waits for the purifier to confirm.
    await hk(hass, "complete_task", {"task_id": task["id"]})
    await settle(hass)
    assert presses == [unit.reset]
    assert (await our_asset(hass, "spares"))["parts"][0]["stock"] == 1
    task = await our_task(hass, unit, "filter")
    assert ours(task)["pending_reset"] is True
    assert task["next_due"] is None

    # The purifier confirms: Filter Low off. The mark goes, nothing else happens.
    hass.states.async_set(unit.filter_low, "off")
    await settle(hass)
    task = await our_task(hass, unit, "filter")
    assert task["next_due"] is None
    assert "pending_reset" not in ours(task)
    assert len(task["completions"]) == 1

    # Months later: low again → the same task re-arms (history kept).
    hass.states.async_set(unit.filter_low, "on")
    await settle(hass)
    again = await our_task(hass, unit, "filter")
    assert again["id"] == task["id"]
    assert again["next_due"]
    assert presses == [unit.reset]


async def test_reset_on_the_device_clears_the_task(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on")
    await setup_real_hk(hass)
    await setup_glue(hass)
    await set_stock(hass, await our_asset(hass, "spares"), 3)
    task = await our_task(hass, unit, "filter")
    assert task["next_due"]

    # Filter swapped and reset pressed in HA / on the device: the glue clears the task.
    hass.states.async_set(unit.filter_low, "off")
    await settle(hass)
    task = await our_task(hass, unit, "filter")
    assert task["next_due"] is None
    assert len(task["completions"]) == 1
    assert (await our_asset(hass, "spares"))["parts"][0]["stock"] == 2
    assert presses == []  # the echo of our own clear presses nothing


async def test_reset_that_did_not_arrive_is_pressed_again(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on")
    await setup_real_hk(hass)
    entry = await setup_glue(hass)
    task = await our_task(hass, unit, "filter")

    # Ticked off while the purifier is offline: the press goes nowhere, Filter Low
    # stays on. A reconnect must not re-arm the task but press the reset again.
    await hk(hass, "complete_task", {"task_id": task["id"]})
    await settle(hass)
    assert presses == [unit.reset]
    hass.states.async_set(unit.filter_low, "unavailable")
    await settle(hass)
    hass.states.async_set(unit.filter_low, "on")
    await settle(hass)
    assert presses == [unit.reset, unit.reset]
    assert (await our_task(hass, unit, "filter"))["next_due"] is None

    # The mark lives on the task, so it survives a restart of the glue.
    await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert (await our_task(hass, unit, "filter"))["next_due"] is None
    assert presses == [unit.reset, unit.reset, unit.reset]

    # Once the purifier confirms, the next Filter Low arms the task normally.
    hass.states.async_set(unit.filter_low, "off")
    await settle(hass)
    hass.states.async_set(unit.filter_low, "on")
    await settle(hass)
    assert (await our_task(hass, unit, "filter"))["next_due"]
    assert len(presses) == 3


async def test_three_purifiers_share_one_part_and_one_buy_task(
    hass: HomeAssistant, presses
) -> None:
    units = [
        await add_purifier(hass, slug, name, filter_low="on")
        for slug, name in (("office", "Office"), ("bedroom", "Bedroom"), ("living", "Living Room"))
    ]
    await setup_real_hk(hass)
    await setup_glue(hass)

    mine = [a for a in await assets(hass) if ours(a)]
    spares = [a for a in mine if ours(a)["role"] == "spares"]
    assert len(spares) == 1
    assert len(spares[0]["parts"]) == 1
    part = spares[0]["parts"][0]
    assert part["notes"] == "Verwendet von: Bedroom, Living Room, Office"
    await set_stock(hass, spares[0], 3, reorder_at=1, create_buy_task=True)
    await hk_idle(hass)  # Home Keeper reloads to set up its buy-task entities

    for unit in units:
        task = await our_task(hass, unit, "filter")
        assert task["source"]["parts"][0]["part_id"] == part["id"]
        await hk(hass, "complete_task", {"task_id": task["id"]})
        await hk_idle(hass)  # the stock running low makes Home Keeper reload
    assert (await our_asset(hass, "spares"))["parts"][0]["stock"] == 0
    for unit in units:
        assert ours(await our_task(hass, unit, "filter"))["pending_reset"] is True
    assert sorted(presses) == sorted(u.reset for u in units)
    # One household stock → exactly one buy reminder, not one per purifier.
    buys = [t for t in await tasks(hass) if (t.get("source") or {}).get("buy")]
    assert len(buys) == 1
    assert "Vital 100S-RF" in buys[0]["name"]
    await hk_idle(hass)


async def test_completion_during_a_home_keeper_reload_keeps_its_mark(
    hass: HomeAssistant, presses
) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on")
    hk_entry = await setup_real_hk(hass)
    await setup_glue(hass)
    task = await our_task(hass, unit, "filter")

    # Home Keeper reloads (it does when a buy task appears) right while the glue handles
    # a completion: the reset is pressed anyway and the mark written once it is back.
    await hass.config_entries.async_unload(hk_entry.entry_id)
    hass.bus.async_fire(
        "home_keeper_task_completed", {"task_id": task["id"], "source": task["source"]}
    )
    await hass.async_block_till_done()
    assert presses == [unit.reset]
    await hass.config_entries.async_setup(hk_entry.entry_id)
    record_button_presses(hass, presses)
    await hk(hass, "complete_task", {"task_id": task["id"], "origin": DOMAIN})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hk_idle(hass)

    task = await our_task(hass, unit, "filter")
    assert ours(task)["pending_reset"] is True
    assert task["next_due"] is None  # the still-low purifier does not re-arm it
    assert presses == [unit.reset]


async def test_mixed_models_get_their_own_parts_and_pre_filter_care(
    hass: HomeAssistant, presses
) -> None:
    vital = await add_purifier(hass, "office", "Office", filter_low="on")
    core = await add_purifier(
        hass, "bedroom", "Bedroom", model="levoit-core300s", runtime=False, filter_low="on"
    )
    pro = await add_purifier(hass, "living", "Living Room", model="levoit-vital200s-pro")
    await setup_real_hk(hass)
    await setup_glue(hass)

    spares = await our_asset(hass, "spares")
    assert [(p["name"], p["notes"]) for p in spares["parts"]] == [
        ("Core 300-RF", "Verwendet von: Bedroom"),
        ("Vital 100S-RF", "Verwendet von: Office"),
        ("Vital 200S-RF", "Verwendet von: Living Room"),
    ]
    by_name = {p["name"]: p["id"] for p in spares["parts"]}
    for unit, part in ((vital, "Vital 100S-RF"), (core, "Core 300-RF")):
        task = await our_task(hass, unit, "filter")
        assert [link["part_id"] for link in task["source"]["parts"]] == [by_name[part]]

    core_pre = await our_task(hass, core, "prefilter")
    assert core_pre["recurrence_type"] == "floating"
    assert "Nicht waschen." in core_pre["notes"]
    assert (await our_asset(hass, "purifier", pro.device.id))["model"] == "Vital 200S"


async def test_runtime_sensor_comes_and_goes(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office", runtime=False)
    await setup_real_hk(hass)
    await setup_glue(hass)

    pre = await our_task(hass, unit, "prefilter")
    assert pre["recurrence_type"] == "floating"
    assert (pre["interval"], pre["unit"]) == (4, "weeks")
    assert dt_util.parse_datetime(pre["next_due"]) > dt_util.now() + timedelta(days=27)

    # The user flashes the Fan Runtime sensor: same task, now by fan hours.
    ent = er.async_get(hass).async_get_or_create(
        "sensor",
        "esphome",
        "office-sensor-fan_runtime",
        suggested_object_id="office_fan_runtime",
        config_entry=unit.entry,
        device_id=unit.device.id,
        original_name="Fan Runtime",
    )
    hass.states.async_set(ent.entity_id, "5.0", {"unit_of_measurement": "h"})
    await settle(hass)
    pre2 = await our_task(hass, unit, "prefilter")
    assert pre2["id"] == pre["id"]
    assert pre2["recurrence_type"] == "sensor"
    assert pre2["sensor"]["entity_id"] == ent.entity_id

    # Home Keeper's next tick anchors the meter at the current 5 h …
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await settle(hass)
    assert (await our_task(hass, unit, "prefilter"))["sensor"]["baseline"] == 5.0
    # … and 300 fan hours later Home Keeper itself arms the task.
    hass.states.async_set(ent.entity_id, "306.0", {"unit_of_measurement": "h"})
    await settle(hass)
    assert (await our_task(hass, unit, "prefilter"))["next_due"]

    # The sensor is removed again: back to weeks.
    er.async_get(hass).async_remove(ent.entity_id)
    await settle(hass)
    assert (await our_task(hass, unit, "prefilter"))["recurrence_type"] == "floating"


async def test_device_rename_and_user_rename(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on")
    await setup_real_hk(hass)
    await setup_glue(hass)
    pre = await our_task(hass, unit, "prefilter")
    await hk(hass, "update_task", {"task_id": pre["id"], "name": "Wash the office pre-filter"})

    dr.async_get(hass).async_update_device(unit.device.id, name_by_user="Study")
    await settle(hass)
    assert (await our_task(hass, unit, "filter"))["name"] == "Filter tauschen (Study)"
    assert (await our_task(hass, unit, "prefilter"))["name"] == "Wash the office pre-filter"
    assert "Study" in (await our_asset(hass, "spares"))["parts"][0]["notes"]


async def test_disabled_device_pauses_and_user_disabled_task_stays_off(
    hass: HomeAssistant, presses
) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on")
    await setup_real_hk(hass)
    await setup_glue(hass)
    pre = await our_task(hass, unit, "prefilter")
    await hk(hass, "update_task", {"task_id": pre["id"], "enabled": False})

    dev_reg = dr.async_get(hass)
    dev_reg.async_update_device(unit.device.id, disabled_by=dr.DeviceEntryDisabler.USER)
    await settle(hass)
    task = await our_task(hass, unit, "filter")
    assert task["enabled"] is False
    assert ours(task)["paused"] is True

    dev_reg.async_update_device(unit.device.id, disabled_by=None)
    await settle(hass)
    task = await our_task(hass, unit, "filter")
    assert task["enabled"] is True
    assert "paused" not in ours(task)
    # The task the user switched off stays off.
    assert (await our_task(hass, unit, "prefilter"))["enabled"] is False


async def test_without_reset_button_or_reset_on_done_nothing_is_pressed(
    hass: HomeAssistant, presses
) -> None:
    plain = await add_purifier(hass, "office", "Office", filter_low="on", reset=False)
    other = await add_purifier(
        hass, "bedroom", "Bedroom", filter_low="on", reset_name="Reset Filter Stats"
    )
    await setup_real_hk(hass)
    await setup_glue(hass, options={"reset_on_done": False})

    for unit in (plain, other):
        task = await our_task(hass, unit, "filter")
        assert "completion_prompt" not in task["managed_by"]
        assert "**Filter Reset** drücken" in task["notes"]
        await hk(hass, "complete_task", {"task_id": task["id"]})
    await settle(hass)
    assert presses == []
    # Without the pending mark, the still-low purifier re-arms on its next report.
    hass.states.async_set(plain.filter_low, "off")
    hass.states.async_set(plain.filter_low, "on")
    await settle(hass)
    assert (await our_task(hass, plain, "filter"))["next_due"]


async def test_options_off_clean_up_and_on_again_rebuild(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on")
    await setup_real_hk(hass)
    entry = await setup_glue(hass)
    await set_stock(hass, await our_asset(hass, "spares"), 2)

    # What the glue made and the options no longer want is deleted, not left behind.
    hass.config_entries.async_update_entry(
        entry,
        options={"prefilter_enabled": False, "appliances_enabled": False, "stock_enabled": False},
    )
    await settle(hass)
    assert await our_task(hass, unit, "prefilter") is None
    assert await our_asset(hass, "purifier", unit.device.id) is None
    assert await our_asset(hass, "spares") is None
    task = await our_task(hass, unit, "filter")
    assert not task["source"].get("parts")
    assert task["next_due"]  # the filter task itself stays

    # Switched on again: built anew, the filter task linked to the new stock.
    hass.config_entries.async_update_entry(entry, options={})
    await settle(hass)
    assert await our_task(hass, unit, "prefilter") is not None
    assert await our_asset(hass, "purifier", unit.device.id) is not None
    spares = await our_asset(hass, "spares")
    assert spares["parts"][0].get("stock") is None
    task = await our_task(hass, unit, "filter")
    assert task["source"]["parts"][0]["asset_id"] == spares["id"]


async def test_removal_keeps_counted_stock_for_the_user(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on")
    await setup_real_hk(hass)
    entry = await setup_glue(hass)
    await set_stock(hass, await our_asset(hass, "spares"), 2)

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert await our_task(hass, unit, "filter") is None
    spares = await our_asset(hass, "spares")
    assert spares is not None  # counted stock stays with the user
    assert spares["managed_by"] is None


async def test_removed_device_takes_its_tasks(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on")
    keep = await add_purifier(hass, "bedroom", "Bedroom")
    await setup_real_hk(hass)
    await setup_glue(hass)

    dr.async_get(hass).async_remove_device(unit.device.id)
    await settle(hass)
    assert [ours(t)["device_id"] for t in await tasks(hass) if ours(t)] == [keep.device.id]
    assert await our_asset(hass, "purifier", unit.device.id) is None
    spares = await our_asset(hass, "spares")
    assert spares["parts"][0]["notes"] == "Verwendet von: Bedroom"


async def test_other_esphome_devices_are_ignored(hass: HomeAssistant, presses) -> None:
    other = await add_purifier(
        hass, "plug", "Some Plug", model="sonoff-s31", manufacturer="esphome", filter_low="on"
    )
    lookalike = await add_purifier(hass, "fake", "Fake", manufacturer="someone", filter_low="on")
    await setup_real_hk(hass)
    await setup_glue(hass)
    assert await our_task(hass, other, "filter") is None
    assert await our_task(hass, lookalike, "filter") is None
    assert await our_asset(hass, "spares") is None
    assert [t for t in await tasks(hass) if (t.get("source") or {}).get(SOURCE_NS)] == []


async def test_late_home_keeper_catches_up(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on")
    await setup_glue(hass)  # Home Keeper not there yet
    await setup_real_hk(hass)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await settle(hass)
    assert (await our_task(hass, unit, "filter"))["next_due"]
    assert await our_asset(hass, "spares") is not None
