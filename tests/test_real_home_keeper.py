"""End to end against the real Home Keeper integration (vendored for the tests).

Each test sets up a different household: one purifier or several, one model or mixed,
with or without a Fan Runtime sensor or a reset button, with different options.
"""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.home_keeper_tuct_levoit import logic
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


def links_to(task: dict, asset: dict) -> list[str]:
    """The part ids *task* draws on in *asset*."""
    return [
        link["part_id"]
        for link in (task.get("source") or {}).get("parts") or []
        if link["asset_id"] == asset["id"]
    ]


async def test_setup_builds_prefilter_spares_and_appliance(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office")
    await setup_real_hk(hass)
    await setup_glue(hass)

    # Filter fine → the filter task is there, dormant, without a completion.
    task = await our_task(hass, unit, "filter")
    assert task["recurrence_type"] == "triggered"
    assert task["next_due"] is None
    assert task["completions"] == []
    assert len(task["skips"]) == 1
    assert "Vor dem ersten Filter Low" in task["skips"][0]["note"]

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
    device = await our_asset(hass, "purifier", unit.device.id)
    assert task["source"]["parts"] == [
        {"asset_id": device["id"], "part_id": device["parts"][0]["id"], "quantity": 1},
        {"asset_id": spares["id"], "part_id": spares["parts"][0]["id"], "quantity": 1},
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


async def test_device_page_shows_the_fitted_filter_and_its_last_change(
    hass: HomeAssistant, presses
) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on")
    await setup_real_hk(hass)
    await setup_glue(hass)
    await set_stock(hass, await our_asset(hass, "spares"), 2)

    device = await our_asset(hass, "purifier", unit.device.id)
    (fitted,) = device["parts"]
    assert (fitted["name"], fitted["type"]) == ("Vital 100S-RF", "wear")
    assert fitted["stock"] is None
    assert fitted["last_replaced"] is None
    # A wear part without an interval derives no task of its own.
    assert not [t for t in await tasks(hass) if (t.get("source") or {}).get("part")]

    task = await our_task(hass, unit, "filter")
    await hk(hass, "complete_task", {"task_id": task["id"]})
    await settle(hass)
    fitted = (await our_asset(hass, "purifier", unit.device.id))["parts"][0]
    assert fitted["last_replaced"] == dt_util.now().date().isoformat()
    assert fitted["stock"] is None
    assert (await our_asset(hass, "spares"))["parts"][0]["stock"] == 1  # one filter, not two


async def test_upgrade_from_v010_adds_the_part_and_keeps_the_users_parts(
    hass: HomeAssistant, presses
) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on")
    hk_entry = await setup_real_hk(hass)
    entry = await setup_glue(hass)
    # As v0.1.0 left it: the appliance without parts, the task linked to the stock only.
    device = await our_asset(hass, "purifier", unit.device.id)
    await hass.config_entries.async_unload(entry.entry_id)
    await hk(hass, "update_managed_asset", {"asset_id": device["id"], "parts": []})
    task = await our_task(hass, unit, "filter")
    spares = await our_asset(hass, "spares")
    await hk(
        hass,
        "set_task_consumable",
        {
            "task_id": task["id"],
            "parts": [x for x in task["source"]["parts"] if x["asset_id"] == spares["id"]],
        },
    )
    # The user added a part of their own on the device page.
    await hk(
        hass,
        "update_asset",
        {
            "asset_id": device["id"],
            "parts": [{"name": "Pre-filter", "type": "wear", "part_number": "PF-1"}],
        },
    )
    assert hk_entry.state.name == "LOADED"

    await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    device = await our_asset(hass, "purifier", unit.device.id)
    by_name = {p["name"]: p for p in device["parts"]}
    assert by_name["Pre-filter"]["part_number"] == "PF-1"
    assert by_name["Vital 100S-RF"]["type"] == "wear"
    task = await our_task(hass, unit, "filter")
    assert links_to(task, device) == [by_name["Vital 100S-RF"]["id"]]
    assert links_to(task, spares) == [spares["parts"][0]["id"]]


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
        assert links_to(task, spares[0]) == [part["id"]]
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
        assert links_to(task, spares) == [by_name[part]]

    core_pre = await our_task(hass, core, "prefilter")
    assert (core_pre["recurrence_type"], core_pre["next_due"]) == ("triggered", None)
    assert "Nicht waschen." in core_pre["notes"]
    assert (await our_asset(hass, "purifier", pro.device.id))["model"] == "Vital 200S"


async def test_runtime_sensor_comes_and_goes(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office", runtime=False)
    await setup_real_hk(hass)
    await setup_glue(hass)

    pre = await our_task(hass, unit, "prefilter")
    assert (pre["recurrence_type"], pre["next_due"]) == ("triggered", None)
    assert pre["task_chips"][0]["label"].startswith("Fällig am ")

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
    assert not pre2.get("task_chips")  # the meter shows its own countdown

    # Home Keeper's next tick anchors the meter at the current 5 h …
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await settle(hass)
    assert (await our_task(hass, unit, "prefilter"))["sensor"]["baseline"] == 5.0
    # … and 300 fan hours later Home Keeper itself arms the task.
    hass.states.async_set(ent.entity_id, "306.0", {"unit_of_measurement": "h"})
    await settle(hass)
    assert (await our_task(hass, unit, "prefilter"))["next_due"]

    # The sensor is removed again: back to weeks. It was just armed by the meter, but
    # 4 weeks have not passed since the last decision, so it waits again.
    er.async_get(hass).async_remove(ent.entity_id)
    await settle(hass)
    pre3 = await our_task(hass, unit, "prefilter")
    assert (pre3["recurrence_type"], pre3["next_due"]) == ("triggered", None)
    assert pre3["completions"] == []


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


async def test_options_off_retire_and_on_again_rebuild(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on")
    await setup_real_hk(hass)
    entry = await setup_glue(hass)
    counted = await our_asset(hass, "spares")
    await set_stock(hass, counted, 2)

    # Retired as on uninstall: counted stock is handed over, the rest is deleted.
    hass.config_entries.async_update_entry(
        entry,
        options={"prefilter_enabled": False, "appliances_enabled": False, "stock_enabled": False},
    )
    await settle(hass)
    assert await our_task(hass, unit, "prefilter") is None
    assert await our_asset(hass, "purifier", unit.device.id) is None
    kept = await our_asset(hass, "spares")
    assert kept["id"] == counted["id"]
    assert kept["managed_by"] is None
    assert kept["parts"][0]["stock"] == 2
    task = await our_task(hass, unit, "filter")
    assert not task["source"].get("parts")  # neither the device part nor the stock
    assert task["next_due"]  # the filter task itself stays

    # Switched on again: a new managed stock; the handed-over one stays the user's.
    hass.config_entries.async_update_entry(entry, options={})
    await settle(hass)
    assert await our_task(hass, unit, "prefilter") is not None
    assert await our_asset(hass, "purifier", unit.device.id) is not None
    managed = [a for a in await assets(hass) if ours(a).get("role") == "spares" and a["managed_by"]]
    assert len(managed) == 1
    assert managed[0]["id"] != counted["id"]
    task = await our_task(hass, unit, "filter")
    assert links_to(task, managed[0]) == [managed[0]["parts"][0]["id"]]
    still = next(a for a in await assets(hass) if a["id"] == counted["id"])
    assert still["managed_by"] is None


async def test_stock_off_without_count_deletes_it(hass: HomeAssistant, presses) -> None:
    await add_purifier(hass, "office", "Office", filter_low="on")
    await setup_real_hk(hass)
    entry = await setup_glue(hass)
    hass.config_entries.async_update_entry(entry, options={"stock_enabled": False})
    await settle(hass)
    assert await our_asset(hass, "spares") is None


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
    assert await our_asset(hass, "purifier", unit.device.id) is None  # nothing counted


async def test_removal_without_stock_leaves_nothing(hass: HomeAssistant, presses) -> None:
    await add_purifier(hass, "office", "Office", filter_low="on")
    await setup_real_hk(hass)
    entry = await setup_glue(hass)

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert [t for t in await tasks(hass) if ours(t)] == []
    assert [a for a in await assets(hass) if ours(a)] == []


async def test_deleted_prefilter_task_comes_back(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office")
    await setup_real_hk(hass)
    await setup_glue(hass)
    pre = await our_task(hass, unit, "prefilter")

    await hk(hass, "delete_task", {"task_id": pre["id"], "force": True})
    await settle(hass)
    again = await our_task(hass, unit, "prefilter")
    assert again is not None
    assert again["id"] != pre["id"]


async def test_removed_device_takes_its_tasks(hass: HomeAssistant, presses) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_low="on")
    keep = await add_purifier(hass, "bedroom", "Bedroom")
    await setup_real_hk(hass)
    await setup_glue(hass)

    dr.async_get(hass).async_remove_device(unit.device.id)
    await settle(hass)
    assert {ours(t)["device_id"] for t in await tasks(hass) if ours(t)} == {keep.device.id}
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


async def test_dust_sensor_task_per_model(hass: HomeAssistant, presses) -> None:
    vital = await add_purifier(hass, "office", "Office")
    core = await add_purifier(hass, "bedroom", "Bedroom", model="levoit-core300s")
    small = await add_purifier(hass, "hall", "Hall", model="levoit-core200s")
    sprout = await add_purifier(hass, "kids", "Kids", model="levoit-sprout")
    await setup_real_hk(hass)
    entry = await setup_glue(hass)

    task = await our_task(hass, vital, "sensor")
    assert task["name"] == "Staubsensor reinigen (Office)"
    assert (task["recurrence_type"], task["next_due"]) == ("triggered", None)
    assert task["completions"] == []
    assert "Wattestäbchen" in task["notes"]
    assert "Schaumstoff" in task["notes"]
    due = logic.add_interval(dt_util.now(), 2, "months")
    assert task["task_chips"][0]["label"] == f"Fällig am {due.day:02d}.{due.month:02d}.{due.year}"

    task = await our_task(hass, core, "sensor")
    assert "Staubsauger" in task["notes"]

    # No sensor (Core 200S), or no Levoit instruction found (Sprout): no task.
    assert await our_task(hass, small, "sensor") is None
    assert await our_task(hass, sprout, "sensor") is None

    # Switched off: deleted.
    hass.config_entries.async_update_entry(entry, options={"sensor_enabled": False})
    await settle(hass)
    assert await our_task(hass, vital, "sensor") is None
    assert await our_task(hass, core, "sensor") is None
    assert await our_task(hass, vital, "prefilter") is not None


async def _todo(hass: HomeAssistant) -> list[str]:
    """The items on Home Keeper's to-do list (its entity id follows the language)."""
    (entity_id,) = [s.entity_id for s in hass.states.async_all("todo")]
    resp = await hass.services.async_call(
        "todo", "get_items", {"entity_id": entity_id}, blocking=True, return_response=True
    )
    return sorted(item["summary"] for item in resp[entity_id]["items"])


async def test_only_due_care_is_on_the_to_do_list(hass: HomeAssistant, presses, freezer) -> None:
    unit = await add_purifier(hass, "office", "Office", runtime=False)
    await setup_real_hk(hass)
    await setup_glue(hass)
    assert await _todo(hass) == []  # filter, pre-filter and dust sensor all wait

    # 4 weeks on: the hourly check arms the pre-filter task, the dust sensor still waits.
    freezer.tick(timedelta(weeks=4, hours=1))
    async_fire_time_changed(hass)
    await settle(hass)
    assert await _todo(hass) == ["Vorfilter reinigen (Office)"]

    # Ticked off: it waits another 4 weeks; 2 months on, the dust sensor is due.
    await hk(hass, "complete_task", {"task_id": (await our_task(hass, unit, "prefilter"))["id"]})
    await settle(hass)
    assert await _todo(hass) == []
    freezer.tick(timedelta(weeks=5))
    async_fire_time_changed(hass)
    await settle(hass)
    assert await _todo(hass) == ["Staubsensor reinigen (Office)", "Vorfilter reinigen (Office)"]


async def test_calendar_task_of_the_previous_version_is_converted(
    hass: HomeAssistant, presses
) -> None:
    unit = await add_purifier(hass, "office", "Office")
    await setup_real_hk(hass)
    # As the previous version left it: a floating task, on the to-do list.
    await hk(
        hass,
        "add_task",
        {
            "name": "Staubsensor reinigen (Office)",
            "recurrence_type": "floating",
            "interval": 2,
            "unit": "months",
            "last_completed": dt_util.now().isoformat(),
            "device_id": unit.device.id,
            "source": {
                SOURCE_NS: {
                    "device_id": unit.device.id,
                    "role": "sensor",
                    "name": "Staubsensor reinigen (Office)",
                    "rule": "infrared:2:de",
                }
            },
        },
        response=True,
    )
    old = await our_task(hass, unit, "sensor")
    assert await _todo(hass) == ["Staubsensor reinigen (Office)"]

    await setup_glue(hass)
    task = await our_task(hass, unit, "sensor")
    assert task["id"] == old["id"]  # the same task, history kept
    assert (task["recurrence_type"], task["next_due"]) == ("triggered", None)
    assert task["completions"] == old["completions"]  # converting completes nothing
    assert task["task_chips"][0]["label"].startswith("Fällig am ")
    assert await _todo(hass) == []


async def test_filter_task_ahead_draws_nothing_and_shows_filter_life(
    hass: HomeAssistant, presses
) -> None:
    unit = await add_purifier(hass, "office", "Office", filter_life=63.4)
    await setup_real_hk(hass)
    await setup_glue(hass)
    await set_stock(hass, await our_asset(hass, "spares"), 6)
    await settle(hass)

    task = await our_task(hass, unit, "filter")
    assert task["next_due"] is None
    assert [c["label"] for c in task["task_chips"]] == ["Filter noch 60 %"]
    # Made ahead: no filter taken off the stock, no date stamped on the device page.
    assert (await our_asset(hass, "spares"))["parts"][0]["stock"] == 6
    assert (await our_asset(hass, "purifier", unit.device.id))["parts"][0]["last_replaced"] is None
    assert presses == []

    life = f"{unit.filter_low.split('.')[1].removesuffix('_filter_low')}"
    hass.states.async_set(f"sensor.{life}_filter", "61.2", {"unit_of_measurement": "%"})
    await settle(hass)
    assert [c["label"] for c in (await our_task(hass, unit, "filter"))["task_chips"]] == [
        "Filter noch 60 %"
    ]
    hass.states.async_set(f"sensor.{life}_filter", "4.9", {"unit_of_measurement": "%"})
    hass.states.async_set(unit.filter_low, "on")
    await settle(hass)
    task = await our_task(hass, unit, "filter")
    assert task["next_due"]  # Filter Low arms the same task
    assert [c["label"] for c in task["task_chips"]] == ["Filter noch 0 %"]
