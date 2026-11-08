"""Home Assistant wiring for the glue.

Finds the purifiers in the device registry, follows their Filter Low sensors, and turns
the pure decisions of ``logic`` into service calls. Every call into Home Keeper is
guarded, so the glue does nothing (and breaks nothing) while Home Keeper is absent,
starting or reloading. Nothing is stored here: tasks and appliances are found again
through our ``source`` namespace, and the marks live on the tasks.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_OFF, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util, slugify

from . import logic
from .catalog import model_for, model_key
from .const import (
    COMPANION_NAME,
    DEFAULT_APPLIANCES_ENABLED,
    DEFAULT_PREFILTER_ENABLED,
    DEFAULT_PREFILTER_HOURS,
    DEFAULT_PREFILTER_WEEKS,
    DEFAULT_RESET_ON_DONE,
    DEFAULT_SENSOR_ENABLED,
    DEFAULT_STOCK_ENABLED,
    DOCS_URL,
    DOMAIN,
    ENTITY_KEYS,
    ESPHOME_DOMAIN,
    HK_DOMAIN,
    HK_EVENT_REGISTER_COMPANIONS,
    HK_EVENT_TASK_COMPLETED,
    HK_EVENT_TASK_DELETED,
    HK_SERVICE_ADD_ASSET,
    HK_SERVICE_ADD_TASK,
    HK_SERVICE_COMPLETE_TASK,
    HK_SERVICE_DELETE_ASSET,
    HK_SERVICE_DELETE_TASK,
    HK_SERVICE_LIST_ASSETS,
    HK_SERVICE_LIST_TASKS,
    HK_SERVICE_REGISTER_COMPANION,
    HK_SERVICE_SET_TASK_CONSUMABLE,
    HK_SERVICE_SKIP_TASK,
    HK_SERVICE_TRIGGER_TASK,
    HK_SERVICE_UPDATE_ASSET,
    HK_SERVICE_UPDATE_MANAGED_ASSET,
    HK_SERVICE_UPDATE_TASK,
    MANAGED_ICON,
    MARK_PENDING,
    OPT_APPLIANCES_ENABLED,
    OPT_DEVICE_FILTERS,
    OPT_ENTITIES,
    OPT_FILTER_NAMES,
    OPT_PREFILTER_ENABLED,
    OPT_PREFILTER_HOURS,
    OPT_PREFILTER_WEEKS,
    OPT_RESET_ON_DONE,
    OPT_SENSOR_ENABLED,
    OPT_STOCK_ENABLED,
    ORIGIN,
    PROJECT_MANUFACTURER,
    PROJECT_MODEL_PREFIX,
    ROLE_FILTER,
    ROLE_SPARES,
    SOURCE_NS,
    TEXT,
)

_LOGGER = logging.getLogger(__name__)

# Registry changes come in bursts (a device brings all its entities at once).
_REGISTRY_DEBOUNCE_SECONDS = 2.0
# While Home Keeper is not loaded, try again this often, this many times (5 min).
_CATCH_UP_SECONDS = 3.0
_CATCH_UP_ATTEMPTS = 100


class HomeKeeperUnavailable(Exception):
    """Home Keeper's services exist but its entry is not loaded; stop the pass."""


def purifier_key(hass: HomeAssistant, device: dr.DeviceEntry) -> str | None:
    """Return the catalog key if *device* is a tuct/levoit ESPHome purifier."""
    if (device.manufacturer or "").lower() != PROJECT_MANUFACTURER:
        return None
    key = model_key(device.model, PROJECT_MODEL_PREFIX)
    if key is None:
        return None
    entry = hass.config_entries.async_get_entry(device.config_entry_id)
    return key if entry is not None and entry.domain == ESPHOME_DOMAIN else None


def entity_key(entry: er.RegistryEntry) -> str | None:
    """Return which of our entities *entry* is, by its YAML name or its entity id."""
    for key, (domain, slugs) in ENTITY_KEYS.items():
        if entry.domain != domain:
            continue
        name = slugify(entry.original_name or "")
        object_id = entry.entity_id.split(".", 1)[1]
        if name in slugs or any(object_id == s or object_id.endswith(f"_{s}") for s in slugs):
            return key
    return None


def _state_float(state: Any) -> float | None:
    try:
        return float(state.state) if state is not None else None
    except TypeError, ValueError:
        return None


def _state_on(state: Any) -> bool | None:
    if state is None or state.state not in (STATE_ON, STATE_OFF):
        return None
    return state.state == STATE_ON


class LevoitGlue:
    """Follows the purifiers and drives their Home Keeper tasks and appliances."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Keep the entry; nothing is read or called until :meth:`async_setup`."""
        self.hass = hass
        self.entry = entry
        # Serialises read → decide → call, so two quick state changes can't both see
        # "no task" and create two.
        self._lock = asyncio.Lock()
        self._purifiers: dict[str, logic.Purifier] = {}  # device_id → purifier
        # Purifiers whose reset was pressed in this run since Filter Low last went
        # unavailable or off: a pending reset is pressed once per run or reconnect.
        self._pressed: set[str] = set()
        # Filter tasks a user ticked off while Home Keeper was reloading: their
        # pending-reset mark is written once it is back.
        self._deferred_marks: set[str] = set()
        self._unsub_state: CALLBACK_TYPE | None = None
        self._cancel_started: CALLBACK_TYPE | None = None
        self._cancel_debounce: CALLBACK_TYPE | None = None
        self._cancel_catch_up: CALLBACK_TYPE | None = None
        self._catch_up_attempts = 0
        self._hk_down = False

    # ── options ──────────────────────────────────────────────────────────────
    @property
    def settings(self) -> logic.Settings:
        """Return the options, resolved."""
        opts = self.entry.options
        return logic.Settings(
            config_entry_id=self.entry.entry_id,
            lang=logic.lang_of(self.hass.config.language),
            stock_enabled=bool(opts.get(OPT_STOCK_ENABLED, DEFAULT_STOCK_ENABLED)),
            appliances_enabled=bool(opts.get(OPT_APPLIANCES_ENABLED, DEFAULT_APPLIANCES_ENABLED)),
            reset_on_done=bool(opts.get(OPT_RESET_ON_DONE, DEFAULT_RESET_ON_DONE)),
            prefilter_enabled=bool(opts.get(OPT_PREFILTER_ENABLED, DEFAULT_PREFILTER_ENABLED)),
            prefilter_hours=int(opts.get(OPT_PREFILTER_HOURS, DEFAULT_PREFILTER_HOURS)),
            prefilter_weeks=int(opts.get(OPT_PREFILTER_WEEKS, DEFAULT_PREFILTER_WEEKS)),
            sensor_enabled=bool(opts.get(OPT_SENSOR_ENABLED, DEFAULT_SENSOR_ENABLED)),
        )

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def async_setup(self) -> None:
        """Subscribe to events and reconcile once Home Assistant runs."""
        bus = self.hass.bus
        on_unload = self.entry.async_on_unload
        on_unload(bus.async_listen(HK_EVENT_TASK_COMPLETED, self._on_hk_completed))
        on_unload(bus.async_listen(HK_EVENT_TASK_DELETED, self._on_hk_deleted))
        on_unload(bus.async_listen(HK_EVENT_REGISTER_COMPANIONS, self._on_register_request))
        on_unload(bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_registry_updated))
        on_unload(bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, self._on_registry_updated))
        on_unload(self._teardown)
        await self._register_companion()
        if self.hass.is_running:
            await self.async_reconcile()
        else:
            self._cancel_started = bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._on_started
            )

    @callback
    def _teardown(self) -> None:
        for name in ("_unsub_state", "_cancel_debounce", "_cancel_catch_up", "_cancel_started"):
            cancel = getattr(self, name)
            if cancel is not None:
                cancel()
                setattr(self, name, None)

    async def _on_started(self, _event: Event) -> None:
        self._cancel_started = None
        await self.async_reconcile()

    async def _on_register_request(self, _event: Event) -> None:
        """Home Keeper is setting up: announce ourselves, catch up once it is loaded."""
        await self._register_companion()
        if self._hk_loaded():
            await self.async_reconcile()
        else:
            self._schedule_catch_up(reset=True)

    async def _register_companion(self) -> None:
        if not self._hk_ready(HK_SERVICE_REGISTER_COMPANION):
            return
        try:
            await self.hass.services.async_call(
                HK_DOMAIN,
                HK_SERVICE_REGISTER_COMPANION,
                {
                    "domain": DOMAIN,
                    "name": COMPANION_NAME,
                    "icon": MANAGED_ICON,
                    "description": TEXT[self.settings.lang]["description"],
                    "config_entry_id": self.entry.entry_id,
                    "docs_url": DOCS_URL,
                },
                blocking=False,
            )
        except Exception:
            _LOGGER.debug("Home Keeper companion registration failed", exc_info=True)

    # ── registry → purifiers ─────────────────────────────────────────────────
    def detect_entities(self, device_id: str) -> dict[str, str]:
        """Return the entities of a purifier found automatically, by key."""
        found: dict[str, str] = {}
        for ent in er.async_entries_for_device(
            er.async_get(self.hass), device_id, include_disabled_entities=True
        ):
            if ent.platform != ESPHOME_DOMAIN or ent.disabled_by is not None:
                continue
            if (which := entity_key(ent)) is not None:
                found.setdefault(which, ent.entity_id)
        return found

    def assigned_entities(self, device_id: str, options: Any = None) -> dict[str, str]:
        """Return the entities assigned to a purifier by hand that still exist."""
        opts = self.entry.options if options is None else options
        ent_reg = er.async_get(self.hass)
        assigned = (opts.get(OPT_ENTITIES) or {}).get(device_id) or {}
        out: dict[str, str] = {}
        for key, entity_id in assigned.items():
            if key in ENTITY_KEYS and entity_id and ent_reg.async_get(entity_id) is not None:
                out[key] = entity_id
            elif entity_id:
                _LOGGER.warning(
                    "Entity %s assigned by hand to %s no longer exists; detecting it "
                    "automatically instead",
                    entity_id,
                    key,
                )
        return out

    @staticmethod
    def filter_for(device_id: str, model_key: str, options: Any) -> str | None:
        """Return the filter name the options give a purifier, if any."""
        return (options.get(OPT_DEVICE_FILTERS) or {}).get(device_id) or (
            options.get(OPT_FILTER_NAMES) or {}
        ).get(model_key)

    def scan(self) -> list[logic.Purifier]:
        """Read every purifier and its entities out of the registries."""
        purifiers: list[logic.Purifier] = []
        for device in dr.async_get(self.hass).devices:
            key = purifier_key(self.hass, device)
            if key is None:
                continue
            found = {**self.detect_entities(device.id), **self.assigned_entities(device.id)}
            low_entity = found.get("filter_low")
            purifiers.append(
                logic.Purifier(
                    device_id=device.id,
                    name=device.name_by_user or device.name or "Levoit",
                    model=model_for(key),
                    filter_low_entity=low_entity,
                    filter_low=_state_on(self.hass.states.get(low_entity)) if low_entity else None,
                    reset_entity=found.get("filter_reset"),
                    runtime_entity=found.get("runtime"),
                    filter_life_entity=found.get("filter_life"),
                    filter_life=_state_float(self.hass.states.get(found["filter_life"]))
                    if found.get("filter_life")
                    else None,
                    paused=device.disabled_by is not None,
                    filter=self.filter_for(device.id, key, self.entry.options),
                )
            )
        self._purifiers = {p.device_id: p for p in purifiers}
        if _LOGGER.isEnabledFor(logging.DEBUG):
            if not purifiers:
                _LOGGER.debug(
                    "No purifier found: no device of the esphome integration has "
                    "manufacturer %r and a model starting with %r (ESPHome project name "
                    "tuct.levoit-<model>)",
                    PROJECT_MANUFACTURER,
                    PROJECT_MODEL_PREFIX,
                )
            for p in purifiers:
                _LOGGER.debug(
                    "Purifier %r (%s, model %s, filter %s%s): filter_low=%s (%s), "
                    "filter_reset=%s, fan_runtime=%s",
                    p.name,
                    p.device_id,
                    p.model.key,
                    p.model.filter_part,
                    ", disabled" if p.paused else "",
                    p.filter_low_entity,
                    p.filter_low,
                    p.reset_entity,
                    p.runtime_entity,
                )
        self._follow_states()
        return purifiers

    def _follow_states(self) -> None:
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        watched = [
            entity_id
            for p in self._purifiers.values()
            for entity_id in (p.filter_low_entity, p.filter_life_entity)
            if entity_id
        ]
        if watched:
            self._unsub_state = async_track_state_change_event(self.hass, watched, self._on_state)

    @callback
    def _on_registry_updated(self, _event: Event) -> None:
        if self._cancel_debounce is not None:
            self._cancel_debounce()
        self._cancel_debounce = async_call_later(
            self.hass, _REGISTRY_DEBOUNCE_SECONDS, self._debounced_reconcile
        )

    async def _debounced_reconcile(self, _now: datetime) -> None:
        self._cancel_debounce = None
        await self.async_reconcile()

    # ── state → task ─────────────────────────────────────────────────────────
    async def _on_state(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        purifier = next(
            (
                p
                for p in self._purifiers.values()
                if entity_id in (p.filter_low_entity, p.filter_life_entity)
            ),
            None,
        )
        if purifier is None:
            return
        if entity_id == purifier.filter_life_entity:
            # Filter % moves in hundredths every few minutes; only a new 5 % step is
            # worth a call into Home Keeper.
            life = _state_float(event.data.get("new_state"))
            step = logic.filter_life_step(life)
            changed = step is not None and step != logic.filter_life_step(purifier.filter_life)
            purifier = replace(purifier, filter_life=life)
            self._purifiers[purifier.device_id] = purifier
            if not changed:
                return
            _LOGGER.debug("Filter life of %r is now %s %%", purifier.name, step)
        else:
            low = _state_on(event.data.get("new_state"))
            _LOGGER.debug("Filter Low of %r is now %s", purifier.name, low)
            purifier = replace(purifier, filter_low=low)
            self._purifiers[purifier.device_id] = purifier
            if low is not True:
                self._pressed.discard(purifier.device_id)
        if not self._hk_loaded():
            self._note_down()
            return
        async with self._lock:
            try:
                tasks = await self._apply_deferred_marks(await self._list_tasks())
                task = logic.index(logic.our_tasks(tasks)).get((purifier.device_id, ROLE_FILTER))
                actions = logic.plan_filter(
                    task,
                    purifier,
                    self.settings,
                    retry_reset=purifier.device_id not in self._pressed,
                )
                for action in actions:
                    await self._execute(action)
                if any(isinstance(a, logic.CreateTask) for a in actions):
                    await self._reconcile_assets()
            except HomeKeeperUnavailable:
                return

    async def _on_hk_completed(self, event: Event) -> None:
        """Reset the purifier's filter statistics when a user ticks its task off."""
        actions = logic.plan_user_completion(dict(event.data), self._purifiers, self.settings)
        if not actions:
            return
        async with self._lock:
            for action in actions:
                try:
                    await self._execute(action)
                except HomeKeeperUnavailable:
                    # Home Keeper is reloading (it does when a buy task appears). Press
                    # the reset anyway and write the mark once it is back, or the still
                    # low purifier would re-arm the task and draw a second filter.
                    if isinstance(action, logic.UpdateTask):
                        self._deferred_marks.add(action.task_id)

    async def _apply_deferred_marks(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Write pending-reset marks that could not be written; may raise."""
        if not self._deferred_marks:
            return tasks
        out: list[dict[str, Any]] = []
        for task in tasks:
            src = logic.ns(task)
            if task.get("id") in self._deferred_marks and src is not None:
                marked = {**src, MARK_PENDING: True}
                await self._call(
                    HK_SERVICE_UPDATE_TASK, {"task_id": task["id"], "source": {SOURCE_NS: marked}}
                )
                self._deferred_marks.discard(task["id"])
                task = {**task, "source": {**(task.get("source") or {}), SOURCE_NS: marked}}
            out.append(task)
        # Marks of tasks that are gone are dropped.
        self._deferred_marks &= {t.get("id") for t in tasks}
        return out

    @callback
    def _on_hk_deleted(self, event: Event) -> None:
        """One of our tasks was deleted in Home Keeper: reconcile (INTEGRATING.md §5).

        A deleted pre-filter task comes back; a filter task comes back on the next
        Filter Low. Our own deletions reconcile to nothing.
        """
        if logic.ns({"source": event.data.get("source")}) is not None:
            self._on_registry_updated(event)

    @property
    def purifiers(self) -> list[logic.Purifier]:
        """Return the purifiers of the last scan."""
        return list(self._purifiers.values())

    async def async_rename_spares(self, renames: dict[str, str]) -> bool:
        """Rename spare parts in place, so their stock and history stay.

        Called by the options flow before it saves new filter names. Returns False when
        Home Keeper could not be reached; the renamed filters then get new parts and
        the old ones keep any stock counted on them.
        """
        if not renames:
            return True
        async with self._lock:
            try:
                assets = await self._list_assets()
            except HomeKeeperUnavailable:
                return False
            spares = logic.index(logic.our_assets(assets)).get((None, ROLE_SPARES))
            if spares is None:
                return True
            _LOGGER.debug("Renaming spare parts %s", renames)
            try:
                await self._call(
                    HK_SERVICE_UPDATE_MANAGED_ASSET,
                    {
                        "asset_id": spares["id"],
                        "parts": logic.rename_parts(list(spares.get("parts") or []), renames),
                    },
                    required=True,
                )
            except HomeKeeperUnavailable:
                return False
        return True

    # ── reconcile ────────────────────────────────────────────────────────────
    async def async_reconcile(self) -> None:
        """Converge every task and appliance with the purifiers."""
        if not self._hk_ready(HK_SERVICE_LIST_TASKS):
            self.scan()  # still follow the sensors; Home Keeper pings us later
            if self.hass.services.has_service(HK_DOMAIN, HK_SERVICE_LIST_TASKS):
                self._note_down()
            return
        async with self._lock:
            _LOGGER.debug("Reconciling")
            purifiers = self.scan()
            self._pressed &= {p.device_id for p in purifiers if p.filter_low is True}
            try:
                tasks = await self._apply_deferred_marks(await self._list_tasks())
                for action in logic.plan_reconcile(
                    tasks,
                    purifiers,
                    self._known_device_ids(),
                    self.settings,
                    now=dt_util.now(),
                    pressed=frozenset(self._pressed),
                ):
                    await self._execute(action)
                await self._reconcile_assets()
            except HomeKeeperUnavailable:
                return
            self._note_up()

    def _known_device_ids(self) -> set[str]:
        return {device.id for device in dr.async_get(self.hass).devices}

    async def _reconcile_assets(self) -> None:
        """Converge appliances and links; may raise :class:`HomeKeeperUnavailable`."""
        if not self._hk_ready(HK_SERVICE_LIST_ASSETS):
            return
        # Two passes at most: a new spare-filter appliance gets its part ids on creation,
        # and the second pass links the filter tasks to them.
        for _ in range(2):
            assets = await self._list_assets()
            tasks = await self._list_tasks()
            actions = logic.plan_assets(
                assets,
                tasks,
                list(self._purifiers.values()),
                self._known_device_ids(),
                self.settings,
            )
            for action in actions:
                await self._execute(action)
            if not any(
                isinstance(a, (logic.EnsureAsset, logic.UpdateManagedAsset)) for a in actions
            ):
                return

    # ── Home Keeper calls ────────────────────────────────────────────────────
    def _hk_loaded(self) -> bool:
        return any(
            entry.state is ConfigEntryState.LOADED
            for entry in self.hass.config_entries.async_entries(HK_DOMAIN)
        )

    def _hk_ready(self, service: str) -> bool:
        return self._hk_loaded() and self.hass.services.has_service(HK_DOMAIN, service)

    def _note_down(self) -> None:
        if not self._hk_down:
            self._hk_down = True
            _LOGGER.info("Home Keeper is not loaded yet; purifier tasks catch up once it is")
        self._schedule_catch_up()

    @callback
    def _schedule_catch_up(self, *, reset: bool = False) -> None:
        if reset:
            self._catch_up_attempts = 0
        if self._cancel_catch_up is not None or self._catch_up_attempts >= _CATCH_UP_ATTEMPTS:
            return
        self._catch_up_attempts += 1
        self._cancel_catch_up = async_call_later(self.hass, _CATCH_UP_SECONDS, self._catch_up)

    async def _catch_up(self, _now: datetime) -> None:
        self._cancel_catch_up = None
        if not self._hk_loaded():
            self._schedule_catch_up()
            return
        await self._register_companion()
        await self.async_reconcile()

    def _note_up(self) -> None:
        self._catch_up_attempts = 0
        if self._hk_down:
            self._hk_down = False
            _LOGGER.info("Home Keeper is loaded again; purifier tasks caught up")

    async def _call(
        self, service: str, data: dict[str, Any], *, response: bool = False, required: bool = False
    ) -> Any:
        """Call a Home Keeper service; Home Keeper going away stops the pass."""
        if not self._hk_ready(service):
            self._note_down()
            raise HomeKeeperUnavailable(service)
        try:
            return await self.hass.services.async_call(
                HK_DOMAIN, service, data, blocking=True, return_response=response
            )
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None) == "integration_not_loaded":
                self._note_down()
                raise HomeKeeperUnavailable(service) from err
            _LOGGER.warning("home_keeper.%s failed: %s", service, err)
        except (ValueError, KeyError, vol.Invalid) as err:
            # A payload Home Keeper's schema refuses must not take the whole pass down.
            _LOGGER.warning("home_keeper.%s failed: %s", service, err)
        if required:
            raise HomeKeeperUnavailable(service)
        return None

    async def _list_tasks(self) -> list[dict[str, Any]]:
        resp = await self._call(HK_SERVICE_LIST_TASKS, {}, response=True, required=True)
        if not isinstance(resp, dict) or not isinstance(resp.get("tasks"), list):
            raise HomeKeeperUnavailable(HK_SERVICE_LIST_TASKS)
        return list(resp["tasks"])

    async def _list_assets(self) -> list[dict[str, Any]]:
        resp = await self._call(HK_SERVICE_LIST_ASSETS, {}, response=True, required=True)
        if not isinstance(resp, dict) or not isinstance(resp.get("assets"), list):
            raise HomeKeeperUnavailable(HK_SERVICE_LIST_ASSETS)
        return list(resp["assets"])

    async def _execute(self, action: logic.Action) -> None:
        _LOGGER.debug("Executing %s", action)
        match action:
            case logic.CreateTask(payload=payload, dormant=dormant):
                resp = await self._call(HK_SERVICE_ADD_TASK, payload, response=True)
                task_id = resp.get("task_id") if isinstance(resp, dict) else None
                if dormant and task_id:
                    # Home Keeper creates a triggered task armed; a skip disarms it
                    # without a completion (no stock drawn, no part stamped).
                    await self._call(
                        HK_SERVICE_SKIP_TASK,
                        {
                            "task_id": task_id,
                            "origin": ORIGIN,
                            "note": TEXT[self.settings.lang]["filter_ahead_note"],
                        },
                    )
            case logic.ArmTask(task_id=task_id):
                await self._call(HK_SERVICE_TRIGGER_TASK, {"task_id": task_id})
            case logic.ClearTask(task_id=task_id):
                await self._call(HK_SERVICE_COMPLETE_TASK, {"task_id": task_id, "origin": ORIGIN})
            case logic.UpdateTask(task_id=task_id, data=data):
                await self._call(HK_SERVICE_UPDATE_TASK, {"task_id": task_id, **data})
            case logic.DeleteTask(task_id=task_id):
                await self._call(HK_SERVICE_DELETE_TASK, {"task_id": task_id, "force": True})
            case logic.EnsureAsset(payload=payload):
                await self._call(HK_SERVICE_ADD_ASSET, payload)
            case logic.UpdateManagedAsset(asset_id=asset_id, parts=parts):
                await self._call(
                    HK_SERVICE_UPDATE_MANAGED_ASSET, {"asset_id": asset_id, "parts": parts}
                )
            case logic.ReleaseAsset(asset_id=asset_id):
                await self._call(
                    HK_SERVICE_UPDATE_ASSET, {"asset_id": asset_id, "managed_by": None}
                )
            case logic.DeleteAsset(asset_id=asset_id):
                await self._call(HK_SERVICE_DELETE_ASSET, {"asset_id": asset_id, "force": True})
            case logic.LinkParts(task_id=task_id, parts=parts):
                await self._call(
                    HK_SERVICE_SET_TASK_CONSUMABLE, {"task_id": task_id, "parts": parts}
                )
            case logic.PressReset(entity_id=entity_id, device_id=device_id):
                self._pressed.add(device_id)
                try:
                    await self.hass.services.async_call(
                        "button", "press", {"entity_id": entity_id}, blocking=True
                    )
                    _LOGGER.info("Filter fitted: pressed %s", entity_id)
                except HomeAssistantError as err:
                    _LOGGER.warning("Could not press %s: %s", entity_id, err)
