"""Pure decisions of the glue: what Home Keeper should look like.

No Home Assistant import here. ``wiring`` reads the registries and states into
:class:`Purifier` records, hands them with Home Keeper's task and appliance lists to the
``plan_*`` functions, and executes the actions they return. Every plan is idempotent:
running it again against its own result plans nothing.

How a purifier's filter task moves:

- **Filter Low on** and no task yet → the task is created (it starts armed). Created on
  the first report, as the Battery Notes, IPP and Valetudo glues do, so the history has
  no invented first entry.
- **Filter Low on** and the task dormant → it is armed again (same task, history kept).
- **Filter Low off** and the task armed → it is completed with our origin: the filter was
  replaced and reset on the purifier or in Home Assistant.
- **A user ticks the task off** → the task is marked *pending reset* and the purifier's
  Filter Reset is pressed. While pending, a low reading never re-arms the task (the
  purifier may be offline); the reset is pressed again once per run or reconnect. The
  mark clears when Filter Low goes off.
- **Device disabled** → its tasks are switched off and marked *paused*; enabling the
  device switches them on again. A task the user switched off is never touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .catalog import WASH, LevoitModel
from .const import (
    DOMAIN,
    FILTER_LOCKED_FIELDS,
    HK_SOURCE_PARTS,
    MANAGED_DISPLAY_NAME,
    MANAGED_ICON,
    MARK_PAUSED,
    MARK_PENDING,
    PART_TYPE_CONSUMABLE,
    PREFILTER_LOCKED_FIELDS,
    ROLE_FILTER,
    ROLE_PREFILTER,
    ROLE_PURIFIER,
    ROLE_SPARES,
    SOURCE_NS,
    SPARES_LOCKED_FIELDS,
    TEXT,
)

# ── inputs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Purifier:
    """One purifier as Home Assistant sees it."""

    device_id: str
    name: str
    model: LevoitModel
    filter_low_entity: str | None = None
    # True / False from the binary sensor, None while unknown or unavailable.
    filter_low: bool | None = None
    reset_entity: str | None = None
    runtime_entity: str | None = None
    # Device disabled: its tasks are switched off, not deleted.
    paused: bool = False


@dataclass(frozen=True, slots=True)
class Settings:
    """The options, resolved."""

    config_entry_id: str
    lang: str = "en"
    stock_enabled: bool = True
    appliances_enabled: bool = True
    reset_on_done: bool = True
    prefilter_enabled: bool = True
    prefilter_hours: int = 300
    prefilter_weeks: int = 4


# ── actions ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CreateTask:
    """``add_task``."""

    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ArmTask:
    """``trigger_task``."""

    task_id: str


@dataclass(frozen=True, slots=True)
class ClearTask:
    """``complete_task`` with our origin: the purifier says the filter is fine again."""

    task_id: str


@dataclass(frozen=True, slots=True)
class UpdateTask:
    """``update_task`` with *data* (name, notes, schedule, enabled, our namespace)."""

    task_id: str
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DeleteTask:
    """``delete_task`` with force: our tasks are deletion-protected."""

    task_id: str


@dataclass(frozen=True, slots=True)
class PressReset:
    """Press the purifier's Filter Reset button: a filter was fitted."""

    entity_id: str
    device_id: str


@dataclass(frozen=True, slots=True)
class EnsureAsset:
    """``add_asset``."""

    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UpdateManagedAsset:
    """``update_managed_asset``: the owner's part list of the spare-filter appliance."""

    asset_id: str
    parts: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class DeleteAsset:
    """``delete_asset`` with force."""

    asset_id: str


@dataclass(frozen=True, slots=True)
class LinkParts:
    """``set_task_consumable`` with the task's complete list of links."""

    task_id: str
    parts: list[dict[str, Any]] = field(default_factory=list)


type Action = (
    CreateTask
    | ArmTask
    | ClearTask
    | UpdateTask
    | DeleteTask
    | PressReset
    | EnsureAsset
    | UpdateManagedAsset
    | DeleteAsset
    | LinkParts
)

# ── helpers ──────────────────────────────────────────────────────────────────


def lang_of(language: str | None) -> str:
    """Return the text language for a Home Assistant language code."""
    lang = (language or "en").split("-")[0].lower()
    return lang if lang in TEXT else "en"


def _t(settings: Settings, key: str, **kwargs: Any) -> str:
    return TEXT[settings.lang][key].format(**kwargs)


def ns(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return our ``source`` namespace of a task or appliance, if it is ours."""
    src = ((record or {}).get("source") or {}).get(SOURCE_NS)
    return src if isinstance(src, dict) and src.get("role") else None


def our_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the tasks this glue made."""
    return [t for t in tasks if ns(t) is not None]


def our_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the appliances this glue made."""
    return [a for a in assets if ns(a) is not None]


def index(records: list[dict[str, Any]]) -> dict[tuple[str | None, str], dict[str, Any]]:
    """Our records by (device id, role)."""
    out: dict[tuple[str | None, str], dict[str, Any]] = {}
    for record in records:
        if (src := ns(record)) is not None:
            out.setdefault((src.get("device_id"), src["role"]), record)
    return out


def is_armed(task: dict[str, Any]) -> bool:
    return task.get("next_due") is not None


def is_enabled(task: dict[str, Any]) -> bool:
    return task.get("enabled", True) is not False


def is_pending(task: dict[str, Any] | None) -> bool:
    return bool((ns(task) or {}).get(MARK_PENDING))


def _managed_by(settings: Settings, locked: list[str], **extra: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "integration": DOMAIN,
        "display_name": MANAGED_DISPLAY_NAME,
        "icon": MANAGED_ICON,
        "config_entry_id": settings.config_entry_id,
        "deletion_protected": True,
        "locked_fields": list(locked),
    }
    block.update(extra)
    return block


def _with_marks(src: dict[str, Any], **marks: bool) -> dict[str, Any]:
    out = dict(src)
    for key, on in marks.items():
        if on:
            out[key] = True
        else:
            out.pop(key, None)
    return out


class _TaskEdit:
    """Collects the changes to one task into a single ``update_task``."""

    def __init__(self, task: dict[str, Any]) -> None:
        self.task = task
        self.src = dict(ns(task) or {})
        self.data: dict[str, Any] = {}
        self._src_changed = False

    def set_src(self, src: dict[str, Any]) -> None:
        if src != self.src:
            self.src = src
            self._src_changed = True

    def action(self) -> UpdateTask | None:
        data = dict(self.data)
        if self._src_changed:
            data["source"] = {SOURCE_NS: self.src}
        return UpdateTask(self.task["id"], data) if data else None


def _resume_or_pause(edit: _TaskEdit, purifier: Purifier) -> bool:
    """Apply the device's enabled state; return whether the task should be planned on."""
    enabled = is_enabled(edit.task)
    if purifier.paused:
        if enabled:
            edit.data["enabled"] = False
            edit.set_src(_with_marks(edit.src, **{MARK_PAUSED: True}))
        return False
    if not enabled:
        if not edit.src.get(MARK_PAUSED):
            return False  # the user switched it off: leave it alone
        edit.data["enabled"] = True
        edit.set_src(_with_marks(edit.src, **{MARK_PAUSED: False}))
    return True


def _rename(edit: _TaskEdit, wanted: str) -> None:
    """Follow a device rename, unless the user renamed the task themselves."""
    current = edit.task.get("name")
    ours = edit.src.get("name")
    if ours is None:
        edit.set_src({**edit.src, "name": current})
    elif current == ours and current != wanted:
        edit.data["name"] = wanted
        edit.set_src({**edit.src, "name": wanted})


# ── filter task ──────────────────────────────────────────────────────────────


def filter_notes(purifier: Purifier, settings: Settings) -> str:
    key = "filter_notes" if settings.reset_on_done else "filter_notes_manual"
    return _t(settings, key, part=purifier.model.filter_part)


def filter_task_payload(purifier: Purifier, settings: Settings) -> dict[str, Any]:
    """Return the ``add_task`` payload of a purifier's filter task (it starts armed)."""
    name = _t(settings, "filter_name", device=purifier.name)
    extra = {"completion_prompt": _t(settings, "filter_prompt")} if settings.reset_on_done else {}
    return {
        "name": name,
        "notes": filter_notes(purifier, settings),
        "recurrence_type": "triggered",
        "device_id": purifier.device_id,
        "managed_by": _managed_by(settings, FILTER_LOCKED_FIELDS, **extra),
        "source": {SOURCE_NS: {"device_id": purifier.device_id, "role": ROLE_FILTER, "name": name}},
    }


def plan_filter(
    task: dict[str, Any] | None,
    purifier: Purifier,
    settings: Settings,
    *,
    retry_reset: bool = True,
) -> list[Action]:
    """Bring one purifier's filter task in line with its Filter Low sensor.

    *retry_reset* is False when the reset was already pressed in this run since the
    sensor last went unavailable or off; a pending reset is then not pressed again.
    """
    if task is None:
        if purifier.paused or purifier.filter_low is not True:
            return []
        return [CreateTask(filter_task_payload(purifier, settings))]

    edit = _TaskEdit(task)
    if not _resume_or_pause(edit, purifier):
        return [a] if (a := edit.action()) else []
    _rename(edit, _t(settings, "filter_name", device=purifier.name))

    after: list[Action] = []
    low = purifier.filter_low
    armed = is_armed(task)
    pending = bool(edit.src.get(MARK_PENDING))
    if low is True and not armed:
        if pending:
            if settings.reset_on_done and purifier.reset_entity and retry_reset:
                after.append(PressReset(purifier.reset_entity, purifier.device_id))
        else:
            edit.data["notes"] = filter_notes(purifier, settings)
            after.append(ArmTask(task["id"]))
    elif low is False:
        if pending:
            edit.set_src(_with_marks(edit.src, **{MARK_PENDING: False}))
        if armed:
            after.append(ClearTask(task["id"]))
    first = edit.action()
    return ([first] if first else []) + after


def plan_user_completion(
    data: dict[str, Any], purifiers: dict[str, Purifier], settings: Settings
) -> list[Action]:
    """Plan the reset of a purifier whose filter task a user ticked off."""
    if data.get("origin") == SOURCE_NS or not data.get("task_id"):
        return []  # the echo of our own clear
    src = (data.get("source") or {}).get(SOURCE_NS)
    if not isinstance(src, dict) or src.get("role") != ROLE_FILTER:
        return []
    purifier = purifiers.get(str(src.get("device_id")))
    if (
        purifier is None
        or purifier.paused
        or not settings.reset_on_done
        or purifier.reset_entity is None
    ):
        return []
    marked = {SOURCE_NS: _with_marks(src, **{MARK_PENDING: True})}
    return [
        UpdateTask(data["task_id"], {"source": marked}),
        PressReset(purifier.reset_entity, purifier.device_id),
    ]


# ── pre-filter task ──────────────────────────────────────────────────────────


def _prefilter_rule(purifier: Purifier, settings: Settings) -> tuple[str, dict[str, Any]]:
    """Return the rule key and the schedule fields of the pre-filter task."""
    weeks = settings.prefilter_weeks
    how = _t(settings, "how_wash" if purifier.model.prefilter == WASH else "how_vacuum")
    if purifier.runtime_entity:
        hours = settings.prefilter_hours
        key = f"runtime:{purifier.runtime_entity}:{hours}:{weeks}:{settings.lang}"
        fields: dict[str, Any] = {
            "recurrence_type": "sensor",
            "sensor": {
                "entity_id": purifier.runtime_entity,
                "mode": "usage",
                "target": hours,
                "unit": "h",
                "also_every": {"interval": weeks, "unit": "weeks"},
                "combinator": "any",
            },
            "notes": f"{how} {_t(settings, 'due_runtime', hours=hours, weeks=weeks)}",
        }
    else:
        key = f"calendar:{weeks}:{settings.lang}"
        fields = {
            "recurrence_type": "floating",
            "interval": weeks,
            "unit": "weeks",
            "notes": f"{how} {_t(settings, 'due_calendar', weeks=weeks)}",
        }
    return key, fields


def plan_prefilter(
    task: dict[str, Any] | None,
    purifier: Purifier,
    settings: Settings,
    *,
    now: datetime,
) -> list[Action]:
    """Keep the pre-filter task: by fan runtime when the sensor exists, else by weeks."""
    if not settings.prefilter_enabled:
        return [DeleteTask(task["id"])] if task is not None else []
    rule, fields = _prefilter_rule(purifier, settings)
    if task is None:
        if purifier.paused:
            return []
        name = _t(settings, "prefilter_name", device=purifier.name)
        payload: dict[str, Any] = {
            "name": name,
            **fields,
            "device_id": purifier.device_id,
            "managed_by": _managed_by(settings, PREFILTER_LOCKED_FIELDS),
            "source": {
                SOURCE_NS: {
                    "device_id": purifier.device_id,
                    "role": ROLE_PREFILTER,
                    "name": name,
                    "rule": rule,
                }
            },
        }
        if fields["recurrence_type"] == "floating":
            payload["last_completed"] = now.isoformat()  # start counting today
        return [CreateTask(payload)]
    edit = _TaskEdit(task)
    if _resume_or_pause(edit, purifier):
        _rename(edit, _t(settings, "prefilter_name", device=purifier.name))
        if edit.src.get("rule") != rule:
            edit.data.update(fields)
            edit.set_src({**edit.src, "rule": rule})
    return [a] if (a := edit.action()) else []


# ── reconcile: tasks ─────────────────────────────────────────────────────────


def plan_reconcile(
    tasks: list[dict[str, Any]],
    purifiers: list[Purifier],
    known_device_ids: set[str],
    settings: Settings,
    *,
    now: datetime,
    pressed: frozenset[str] | set[str] = frozenset(),
) -> list[Action]:
    """Converge every task of ours with the purifiers.

    A task whose device left the device registry is deleted. A task whose device is still
    there but no longer looks like a purifier (project name changed, entity renamed) is
    left alone, so a config mistake does not wipe its history.
    """
    idx = index(our_tasks(tasks))
    actions: list[Action] = []
    by_device = {p.device_id: p for p in purifiers}
    for p in purifiers:
        actions += plan_filter(
            idx.get((p.device_id, ROLE_FILTER)),
            p,
            settings,
            retry_reset=p.device_id not in pressed,
        )
        actions += plan_prefilter(idx.get((p.device_id, ROLE_PREFILTER)), p, settings, now=now)
    for (device_id, _role), task in idx.items():
        if device_id not in by_device and device_id not in known_device_ids:
            actions.append(DeleteTask(task["id"]))
    return actions


# ── reconcile: appliances and stock ──────────────────────────────────────────


def _spares_parts(
    purifiers: list[Purifier], existing: list[dict[str, Any]], settings: Settings
) -> list[dict[str, Any]]:
    """Return the owner's part list: one consumable per filter type, with its users."""
    users: dict[str, list[str]] = {}
    for p in purifiers:
        users.setdefault(p.model.filter_part, []).append(p.name)
    by_name = {part.get("name"): part for part in existing}
    parts: list[dict[str, Any]] = []
    for name in sorted(users):
        part: dict[str, Any] = {
            "name": name,
            "type": PART_TYPE_CONSUMABLE,
            "notes": _t(settings, "part_notes", devices=", ".join(sorted(users[name]))),
        }
        if (old := by_name.get(name)) is not None and old.get("id"):
            part["id"] = old["id"]
        parts.append(part)
    return parts


def _parts_differ(wanted: list[dict[str, Any]], existing: list[dict[str, Any]]) -> bool:
    have = {(p.get("name"), p.get("type"), p.get("notes") or "") for p in existing}
    want = {(p["name"], p["type"], p["notes"]) for p in wanted}
    return not want <= have


def _links_key(links: list[dict[str, Any]]) -> list[tuple[str, str, float]]:
    return sorted(
        (str(link.get("asset_id")), str(link.get("part_id")), float(link.get("quantity") or 1))
        for link in links
    )


def plan_assets(
    assets: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    purifiers: list[Purifier],
    known_device_ids: set[str],
    settings: Settings,
) -> list[Action]:
    """Converge the spare-filter appliance, the purifier appliances and the links.

    Appliances an option switches off are deleted, not handed over: what the glue made
    and the user does not want is cleaned up.
    """
    actions: list[Action] = []
    mine = index(our_assets(assets))

    # 1. One shared spare-filter appliance for the whole home.
    spares = mine.get((None, ROLE_SPARES))
    if settings.stock_enabled and purifiers:
        if spares is None:
            actions.append(
                EnsureAsset(
                    {
                        "kind": "virtual",
                        "name": _t(settings, "spares_name"),
                        "icon": "mdi:air-filter",
                        "manufacturer": "Levoit",
                        "notes": _t(settings, "spares_notes"),
                        "parts": _spares_parts(purifiers, [], settings),
                        "managed_by": _managed_by(settings, SPARES_LOCKED_FIELDS),
                        "source": {SOURCE_NS: {"role": ROLE_SPARES}},
                    }
                )
            )
        else:
            existing = list(spares.get("parts") or [])
            wanted = _spares_parts(purifiers, existing, settings)
            if _parts_differ(wanted, existing):
                actions.append(UpdateManagedAsset(spares["id"], wanted))

    # 2. Each filter task takes one filter of its model off the shared stock. Links the
    #    user added to other parts stay.
    part_ids = {
        p.get("name"): p.get("id") for p in ((spares or {}).get("parts") or []) if p.get("id")
    }
    by_device = {p.device_id: p for p in purifiers}
    for task in our_tasks(tasks):
        src = ns(task) or {}
        if src.get("role") != ROLE_FILTER:
            continue
        links = list((task.get("source") or {}).get(HK_SOURCE_PARTS) or [])
        others = [
            link for link in links if spares is None or link.get("asset_id") != spares.get("id")
        ]
        purifier = by_device.get(str(src.get("device_id")))
        wanted = list(others)
        if settings.stock_enabled and spares is not None and purifier is not None:
            part_id = part_ids.get(purifier.model.filter_part)
            if part_id is None:
                continue  # the part arrives with the next pass
            wanted.append({"asset_id": spares["id"], "part_id": part_id, "quantity": 1})
        if _links_key(wanted) != _links_key(links):
            actions.append(LinkParts(task["id"], wanted))

    # 3. Each purifier gets an appliance on its own device page, unless it has one.
    taken = {a.get("device_id") for a in assets if a.get("device_id")}
    for p in purifiers:
        ours = mine.get((p.device_id, ROLE_PURIFIER))
        if not settings.appliances_enabled:
            if ours is not None:
                actions.append(DeleteAsset(ours["id"]))
            continue
        if ours is not None or p.device_id in taken:
            continue
        actions.append(
            EnsureAsset(
                {
                    "kind": "existing",
                    "device_id": p.device_id,
                    "manufacturer": "Levoit",
                    "model": p.model.label,
                    "icon": "mdi:air-purifier",
                    "notes": _t(settings, "purifier_notes", model=p.model.label),
                    "managed_by": _managed_by(settings, []),
                    "source": {SOURCE_NS: {"device_id": p.device_id, "role": ROLE_PURIFIER}},
                }
            )
        )
    # 4. What we made and the options no longer want goes, after the links above are gone.
    if spares is not None and not settings.stock_enabled:
        actions.append(DeleteAsset(spares["id"]))
    for (device_id, role), asset in mine.items():
        if (
            role == ROLE_PURIFIER
            and settings.appliances_enabled
            and device_id not in by_device
            and device_id not in known_device_ids
        ):
            actions.append(DeleteAsset(asset["id"]))
    return actions
