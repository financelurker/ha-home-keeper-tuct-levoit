"""Pure planning, no Home Assistant."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.home_keeper_tuct_levoit import logic
from custom_components.home_keeper_tuct_levoit.catalog import model_for, model_key
from custom_components.home_keeper_tuct_levoit.const import SOURCE_NS

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
S = logic.Settings(config_entry_id="e1", lang="en")
RESET = "button.office_filter_reset"


def _p(**kw) -> logic.Purifier:
    base = {
        "device_id": "d1",
        "name": "Office",
        "model": model_for("vital100s"),
        "filter_low_entity": "binary_sensor.office_filter_low",
        "filter_low": False,
        "reset_entity": RESET,
        "runtime_entity": "sensor.office_fan_runtime",
    }
    return logic.Purifier(**{**base, **kw})


def _task(role: str, *, armed: bool, src: dict | None = None, **kw) -> dict:
    name = "Replace filter (Office)" if role == "filter" else "Clean pre-filter (Office)"
    return {
        "id": f"t-{role}",
        "name": name,
        "next_due": NOW.isoformat() if armed else None,
        "source": {SOURCE_NS: {"device_id": "d1", "role": role, "name": name, **(src or {})}},
        **kw,
    }


def _src(action: logic.Action) -> dict:
    assert isinstance(action, logic.UpdateTask)
    return action.data["source"][SOURCE_NS]


# ── catalog ──────────────────────────────────────────────────────────────────


def test_model_key() -> None:
    assert model_key("levoit-vital100s") == "vital100s"
    assert model_key("Levoit Everest-Air") == "everestair"
    assert model_key("levoit-vital200s-pro") == "vital200s"
    assert model_key("sonoff-s31") is None
    assert model_key("levoit") is None
    assert model_key(None) is None
    assert model_for("vital100s").filter_part == "Vital 100S-RF"
    assert model_for("core300s").filter_part == "Core 300-RF"
    generic = model_for("unknown9")
    assert (generic.label, generic.filter_part) == ("UNKNOWN9", "Levoit UNKNOWN9 filter")


# ── filter task ──────────────────────────────────────────────────────────────


def test_filter_task_created_on_first_low_only() -> None:
    assert logic.plan_filter(None, _p(), S) == []
    assert logic.plan_filter(None, _p(filter_low=None), S) == []
    assert logic.plan_filter(None, _p(filter_low=True, paused=True), S) == []
    (create,) = logic.plan_filter(None, _p(filter_low=True), S)
    assert isinstance(create, logic.CreateTask)
    payload = create.payload
    assert payload["recurrence_type"] == "triggered"
    assert payload["name"] == "Replace filter (Office)"
    assert "Vital 100S-RF" in payload["notes"]
    assert payload["managed_by"]["locked_fields"] == ["device_id", "recurrence_type"]
    assert payload["managed_by"]["completion_prompt"]
    assert payload["source"][SOURCE_NS] == {
        "device_id": "d1",
        "role": "filter",
        "name": "Replace filter (Office)",
    }


def test_filter_task_without_reset_on_done_asks_for_a_manual_reset() -> None:
    manual = logic.Settings(config_entry_id="e1", reset_on_done=False)
    (create,) = logic.plan_filter(None, _p(filter_low=True), manual)
    assert "completion_prompt" not in create.payload["managed_by"]
    assert "press **Filter Reset**" in create.payload["notes"]


def test_filter_transitions() -> None:
    rearm = logic.plan_filter(_task("filter", armed=False), _p(filter_low=True), S)
    assert rearm[-1] == logic.ArmTask("t-filter")
    assert logic.plan_filter(_task("filter", armed=True), _p(filter_low=False), S) == [
        logic.ClearTask("t-filter")
    ]
    assert logic.plan_filter(_task("filter", armed=True), _p(filter_low=None), S) == []
    assert logic.plan_filter(_task("filter", armed=True), _p(filter_low=True), S) == []
    assert logic.plan_filter(_task("filter", armed=False), _p(filter_low=False), S) == []


def test_pending_reset_never_rearms_and_clears_when_confirmed() -> None:
    task = _task("filter", armed=False, src={"pending_reset": True})
    low = _p(filter_low=True)
    assert logic.plan_filter(task, low, S) == [logic.PressReset(RESET, "d1")]
    assert logic.plan_filter(task, low, S, retry_reset=False) == []
    assert logic.plan_filter(task, _p(filter_low=None), S) == []
    (clear,) = logic.plan_filter(task, _p(filter_low=False), S)
    assert "pending_reset" not in _src(clear)


def test_pause_and_resume_leave_user_disabled_tasks_alone() -> None:
    (pause,) = logic.plan_filter(_task("filter", armed=True), _p(paused=True), S)
    assert pause.data["enabled"] is False
    assert _src(pause)["paused"] is True

    paused = _task("filter", armed=True, src={"paused": True}, enabled=False)
    resume, clear = logic.plan_filter(paused, _p(filter_low=False), S)
    assert resume.data["enabled"] is True
    assert "paused" not in _src(resume)
    assert clear == logic.ClearTask("t-filter")

    by_user = _task("filter", armed=False, enabled=False)
    assert logic.plan_filter(by_user, _p(filter_low=True), S) == []
    assert logic.plan_filter(by_user, _p(paused=True), S) == []


def test_name_follows_the_device_unless_the_user_renamed_it() -> None:
    renamed = _p(name="Study", filter_low=False)
    (update,) = logic.plan_filter(_task("filter", armed=False), renamed, S)
    assert update.data["name"] == "Replace filter (Study)"
    assert _src(update)["name"] == "Replace filter (Study)"

    mine = _task("filter", armed=False)
    mine["name"] = "Change the HEPA upstairs"
    assert logic.plan_filter(mine, renamed, S) == []


def test_user_completion_marks_pending_and_presses_reset() -> None:
    purifiers = {"d1": _p(filter_low=True)}
    data = {"task_id": "t-filter", "source": {SOURCE_NS: {"device_id": "d1", "role": "filter"}}}
    mark, press = logic.plan_user_completion(data, purifiers, S)
    assert _src(mark) == {"device_id": "d1", "role": "filter", "pending_reset": True}
    assert press == logic.PressReset(RESET, "d1")

    assert logic.plan_user_completion({**data, "origin": SOURCE_NS}, purifiers, S) == []
    pre = {"task_id": "t", "source": {SOURCE_NS: {"device_id": "d1", "role": "prefilter"}}}
    assert logic.plan_user_completion(pre, purifiers, S) == []
    manual = logic.Settings(config_entry_id="e1", reset_on_done=False)
    assert logic.plan_user_completion(data, purifiers, manual) == []
    no_button = {"d1": _p(reset_entity=None)}
    assert logic.plan_user_completion(data, no_button, S) == []


# ── pre-filter task ──────────────────────────────────────────────────────────


def test_prefilter_by_runtime_then_by_weeks() -> None:
    (create,) = logic.plan_prefilter(None, _p(), S, now=NOW)
    sensor = create.payload["sensor"]
    assert (sensor["mode"], sensor["target"], sensor["also_every"]) == (
        "usage",
        300,
        {"interval": 4, "unit": "weeks"},
    )
    assert "rinse it" in create.payload["notes"]  # Vital: washable
    rule = create.payload["source"][SOURCE_NS]["rule"]
    task = _task("prefilter", armed=False, src={"rule": rule})
    assert logic.plan_prefilter(task, _p(), S, now=NOW) == []

    (update,) = logic.plan_prefilter(task, _p(runtime_entity=None), S, now=NOW)
    assert update.data["recurrence_type"] == "floating"
    assert (update.data["interval"], update.data["unit"]) == (4, "weeks")
    assert _src(update)["rule"].startswith("calendar:")


def test_prefilter_calendar_starts_today_and_core_is_vacuumed() -> None:
    core = _p(model=model_for("core300s"), runtime_entity=None)
    (create,) = logic.plan_prefilter(None, core, S, now=NOW)
    assert create.payload["last_completed"] == NOW.isoformat()
    assert "Do not wash it." in create.payload["notes"]


def test_prefilter_follows_language_and_options() -> None:
    (create,) = logic.plan_prefilter(None, _p(), S, now=NOW)
    task = _task(
        "prefilter", armed=False, src={"rule": create.payload["source"][SOURCE_NS]["rule"]}
    )
    german = logic.Settings(config_entry_id="e1", lang="de")
    (update,) = logic.plan_prefilter(task, _p(), german, now=NOW)
    assert "Lüfterlaufzeit" in update.data["notes"]
    off = logic.Settings(config_entry_id="e1", prefilter_enabled=False)
    assert logic.plan_prefilter(task, _p(), off, now=NOW) == [logic.DeleteTask("t-prefilter")]
    assert logic.plan_prefilter(None, _p(), off, now=NOW) == []


# ── reconcile ────────────────────────────────────────────────────────────────


def test_reconcile_deletes_tasks_of_removed_devices_only() -> None:
    tasks = [_task("filter", armed=True)]
    # Device still in the registry but no longer a purifier: keep the history.
    assert logic.plan_reconcile(tasks, [], {"d1"}, S, now=NOW) == []
    assert logic.plan_reconcile(tasks, [], set(), S, now=NOW) == [logic.DeleteTask("t-filter")]


def test_reconcile_does_not_press_twice_in_one_run() -> None:
    task = _task("filter", armed=False, src={"pending_reset": True})
    p = _p(filter_low=True)
    acts = logic.plan_reconcile([task], [p], {"d1"}, S, now=NOW)
    assert logic.PressReset(RESET, "d1") in acts
    acts = logic.plan_reconcile([task], [p], {"d1"}, S, now=NOW, pressed={"d1"})
    assert not any(isinstance(a, logic.PressReset) for a in acts)


# ── appliances and stock ─────────────────────────────────────────────────────


def _spares(*parts: dict) -> dict:
    return {"id": "a-sp", "parts": list(parts), "source": {SOURCE_NS: {"role": "spares"}}}


def test_one_shared_spares_appliance_with_one_part_per_filter_type() -> None:
    purifiers = [
        _p(),
        _p(device_id="d2", name="Bedroom"),
        _p(device_id="d3", name="Kitchen", model=model_for("core300s")),
    ]
    acts = logic.plan_assets([], [], purifiers, {"d1", "d2", "d3"}, S)
    spares = [
        a for a in acts if isinstance(a, logic.EnsureAsset) and a.payload["kind"] == "virtual"
    ]
    (create,) = spares
    assert [(p["name"], p["notes"]) for p in create.payload["parts"]] == [
        ("Core 300-RF", "Used by: Kitchen"),
        ("Vital 100S-RF", "Used by: Bedroom, Office"),
    ]
    appliances = [
        a for a in acts if isinstance(a, logic.EnsureAsset) and a.payload["kind"] == "existing"
    ]
    assert [a.payload["device_id"] for a in appliances] == ["d1", "d2", "d3"]


def test_spares_parts_keep_their_ids_and_only_change_when_needed() -> None:
    part = {"id": "p1", "name": "Vital 100S-RF", "type": "consumable", "notes": "Used by: Office"}
    appliance = {"id": "a-1", "device_id": "d1"}
    assert logic.plan_assets([_spares(part), appliance], [], [_p()], {"d1"}, S) == []
    acts = logic.plan_assets(
        [_spares(part), appliance], [], [_p(), _p(device_id="d2", name="Bedroom")], {"d1", "d2"}, S
    )
    (update,) = [a for a in acts if isinstance(a, logic.UpdateManagedAsset)]
    assert update.parts[0]["id"] == "p1"
    assert update.parts[0]["notes"] == "Used by: Bedroom, Office"


def test_links_keep_the_users_other_parts() -> None:
    part = {"id": "p1", "name": "Vital 100S-RF", "type": "consumable", "notes": "Used by: Office"}
    other = {"asset_id": "a-mine", "part_id": "x", "quantity": 2}
    task = _task("filter", armed=True)
    task["source"]["parts"] = [other]
    actions = logic.plan_assets([_spares(part)], [task], [_p()], {"d1"}, S)
    link = next(a for a in actions if isinstance(a, logic.LinkParts))
    assert link.parts == [other, {"asset_id": "a-sp", "part_id": "p1", "quantity": 1}]


def test_stock_off_unlinks_and_appliances_off_releases() -> None:
    part = {"id": "p1", "name": "Vital 100S-RF", "type": "consumable", "notes": "Used by: Office"}
    task = _task("filter", armed=True)
    task["source"]["parts"] = [{"asset_id": "a-sp", "part_id": "p1", "quantity": 1}]
    mine = {
        "id": "a-pur",
        "device_id": "d1",
        "managed_by": {"integration": "x"},
        "source": {SOURCE_NS: {"device_id": "d1", "role": "purifier"}},
    }
    off = logic.Settings(config_entry_id="e1", stock_enabled=False, appliances_enabled=False)
    acts = logic.plan_assets([_spares(part), mine], [task], [_p()], {"d1"}, off)
    assert logic.LinkParts("t-filter", []) in acts
    assert logic.ReleaseAsset("a-pur") in acts


def test_existing_appliance_on_the_device_page_is_respected_and_gone_devices_cleaned() -> None:
    users = {"id": "a-user", "device_id": "d1"}
    acts = logic.plan_assets([users], [], [_p()], {"d1"}, S)
    assert not any(
        isinstance(a, logic.EnsureAsset) and a.payload["kind"] == "existing" for a in acts
    )
    gone = {
        "id": "a-gone",
        "device_id": "d9",
        "source": {SOURCE_NS: {"device_id": "d9", "role": "purifier"}},
    }
    assert logic.DeleteAsset("a-gone") in logic.plan_assets([gone], [], [], set(), S)
