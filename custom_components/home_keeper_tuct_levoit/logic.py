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

import calendar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .catalog import SENSOR_MONTHS, WASH, LevoitModel
from .const import (
    CALENDAR_LOCKED_FIELDS,
    DOMAIN,
    FILTER_LOCKED_FIELDS,
    HK_SOURCE_PARTS,
    MANAGED_DISPLAY_NAME,
    MANAGED_ICON,
    MARK_PAUSED,
    MARK_PENDING,
    PART_TYPE_CONSUMABLE,
    PART_TYPE_WEAR,
    ROLE_FILTER,
    ROLE_PREFILTER,
    ROLE_PURIFIER,
    ROLE_SENSOR,
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
    filter_life_entity: str | None = None
    filter_life: float | None = None
    reset_entity: str | None = None
    runtime_entity: str | None = None
    # Device disabled: its tasks are switched off, not deleted.
    paused: bool = False
    # The filter it takes: the per-device or per-model name from the options, else the
    # catalog's. It is the part's name in the shared stock and on the device page.
    filter: str | None = None

    @property
    def filter_part(self) -> str:
        """Return the name of the filter this purifier takes."""
        return self.filter or self.model.filter_part


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
    sensor_enabled: bool = True


# ── actions ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CreateTask:
    """``add_task``; with *dormant*, followed by ``skip_task`` to disarm it.

    Home Keeper creates a triggered task armed. A skip makes it dormant without a
    completion, so no stock is drawn and no part is stamped.
    """

    payload: dict[str, Any]
    dormant: bool = False


@dataclass(frozen=True, slots=True)
class SkipTask:
    """``skip_task``: back to waiting, without a completion."""

    task_id: str
    note: str


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
class ReleaseAsset:
    """``update_asset`` with ``managed_by: None``: hand an appliance over to the user."""

    asset_id: str


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
    | SkipTask
    | ClearTask
    | UpdateTask
    | DeleteTask
    | PressReset
    | EnsureAsset
    | UpdateManagedAsset
    | ReleaseAsset
    | DeleteAsset
    | LinkParts
)

# The keys of a part its owning integration writes (Home Keeper assets.PART_OWNER_KEYS,
# v0.36.0). Stock keys are the user's and never sent.
PART_OWNER_KEYS: tuple[str, ...] = (
    "name",
    "type",
    "notes",
    "part_number",
    "vendor",
    "url",
    "cost",
    "replace_interval",
    "replace_unit",
    "replace_also_every",
    "action",
    "use_noun",
    "use_task_name",
    "last_replaced",
    "carried_uses",
    "tag_id",
    "require_tag_scan",
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
    """Return the appliances this glue made and still manages.

    One handed over to the user keeps our ``source`` (Home Keeper never changes it) but
    is theirs now: it is not ours to change, delete or take back.
    """
    return [
        a
        for a in assets
        if ns(a) is not None and (a.get("managed_by") or {}).get("integration") == DOMAIN
    ]


def is_counted(asset: dict[str, Any]) -> bool:
    """Return whether a part of *asset* tracks stock: the user counted something."""
    return any(p.get("stock") is not None for p in asset.get("parts") or [])


def retire(asset: dict[str, Any]) -> ReleaseAsset | DeleteAsset:
    """Retire an appliance we made (Home Keeper INTEGRATING.md §8, "Uninstall").

    A delete takes the user's stock counts with it, so an appliance with a counted part
    is handed over (locks off, the user keeps it); one without is deleted.
    """
    return ReleaseAsset(asset["id"]) if is_counted(asset) else DeleteAsset(asset["id"])


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


def resets_on_done(purifier: Purifier, settings: Settings) -> bool:
    """Return whether ticking the filter task off presses the purifier's reset."""
    return settings.reset_on_done and purifier.reset_entity is not None


def filter_notes(purifier: Purifier, settings: Settings) -> str:
    key = "filter_notes" if resets_on_done(purifier, settings) else "filter_notes_manual"
    notes = _t(settings, key, part=purifier.filter_part)
    if purifier.filter_life_entity:
        notes += "\n\n" + _t(settings, "filter_life_hint")
    return notes


def filter_life_step(life: float | None) -> int | None:
    """Return the filter life left in 5 % steps, rounded down, or None if unknown."""
    if life is None:
        return None
    return int(max(0.0, min(100.0, life)) // 5 * 5)


def filter_chips(purifier: Purifier, settings: Settings) -> list[dict[str, str]]:
    """Return the chips of the filter task: the filter life left, when known."""
    step = filter_life_step(purifier.filter_life)
    if step is None:
        return []
    return [{"label": _t(settings, "filter_chip", pct=step), "icon": "mdi:air-filter"}]


def _chips_of(task: dict[str, Any]) -> list[dict[str, str]]:
    return [{"label": c.get("label"), "icon": c.get("icon")} for c in task.get("task_chips") or []]


def filter_task_payload(purifier: Purifier, settings: Settings) -> dict[str, Any]:
    """Return the ``add_task`` payload of a purifier's filter task."""
    name = _t(settings, "filter_name", device=purifier.name)
    extra = (
        {"completion_prompt": _t(settings, "filter_prompt")}
        if resets_on_done(purifier, settings)
        else {}
    )
    return {
        "name": name,
        "notes": filter_notes(purifier, settings),
        "recurrence_type": "triggered",
        "device_id": purifier.device_id,
        "task_chips": filter_chips(purifier, settings),
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
        if purifier.paused:
            return []
        # Made at once, so the most important care shows from day one: armed when the
        # filter is low already, else dormant until Filter Low arms it.
        return [
            CreateTask(
                filter_task_payload(purifier, settings),
                dormant=purifier.filter_low is not True,
            )
        ]

    edit = _TaskEdit(task)
    if not _resume_or_pause(edit, purifier):
        return [a] if (a := edit.action()) else []
    _rename(edit, _t(settings, "filter_name", device=purifier.name))
    chips = filter_chips(purifier, settings)
    if purifier.filter_life is not None and _chips_of(task) != chips:
        edit.data["task_chips"] = chips

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


# ── calendar tasks the glue arms ─────────────────────────────────────────────
#
# Home Keeper puts every calendar (floating) task on its to-do list with its next date,
# months ahead. The pre-filter task without a runtime sensor and the dust-sensor task
# are therefore triggered tasks: dormant, off the to-do list, and armed by the glue once
# their interval has passed since the last decision about them (the later of the last
# completion and the last skip, as Home Keeper counts a usage task's time backstop).
# A chip shows the date they come due.


def add_interval(anchor: datetime, interval: int, unit: str) -> datetime:
    """Return *anchor* plus *interval* weeks or months (a month end clamps)."""
    if unit == "weeks":
        return anchor + timedelta(weeks=interval)
    month = anchor.month - 1 + interval
    year, month = anchor.year + month // 12, month % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return anchor.replace(year=year, month=month, day=day)


def _parse_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def last_decision(task: dict[str, Any]) -> datetime | None:
    """Return the later of the task's last completion and last skip."""
    stamps = [
        when
        for raw in (
            task.get("last_completed"),
            *(entry.get("ts") for entry in task.get("skips") or []),
        )
        if (when := _parse_ts(raw)) is not None
    ]
    return max(stamps) if stamps else _parse_ts(task.get("created"))


def _due_chip(due: datetime, settings: Settings) -> dict[str, str]:
    date = (
        f"{due.day:02d}.{due.month:02d}.{due.year}"
        if settings.lang == "de"
        else due.date().isoformat()
    )
    return {"label": _t(settings, "due_chip", date=date), "icon": "mdi:calendar-clock"}


def plan_calendar(
    task: dict[str, Any] | None,
    purifier: Purifier,
    settings: Settings,
    *,
    role: str,
    name: str,
    notes: str,
    interval: int,
    unit: str,
    rule: str,
    now: datetime,
) -> list[Action]:
    """Keep a calendar task the glue arms itself: dormant until *interval* has passed."""
    if task is None:
        if purifier.paused:
            return []
        due = add_interval(now, interval, unit)
        return [
            CreateTask(
                {
                    "name": name,
                    "notes": notes,
                    "recurrence_type": "triggered",
                    "device_id": purifier.device_id,
                    "task_chips": [_due_chip(due, settings)],
                    "managed_by": _managed_by(settings, CALENDAR_LOCKED_FIELDS),
                    "source": {
                        SOURCE_NS: {
                            "device_id": purifier.device_id,
                            "role": role,
                            "name": name,
                            "rule": rule,
                        }
                    },
                },
                dormant=True,
            )
        ]
    edit = _TaskEdit(task)
    if not _resume_or_pause(edit, purifier):
        return [a] if (a := edit.action()) else []
    _rename(edit, name)
    if edit.src.get("rule") != rule:
        edit.data["notes"] = notes
        edit.set_src({**edit.src, "rule": rule})
    after: list[Action] = []
    converted = task.get("recurrence_type") != "triggered"
    if converted:
        # A calendar task of an earlier version, or the pre-filter task losing its
        # runtime sensor: from now on the glue arms it.
        edit.data["recurrence_type"] = "triggered"
    anchor = last_decision(task) or now
    due = add_interval(anchor, interval, unit)
    chips = [_due_chip(due, settings)]
    if _chips_of(task) != chips:
        edit.data["task_chips"] = chips
    if due <= now:
        if converted or not is_armed(task):
            after.append(ArmTask(task["id"]))
    elif converted or is_armed(task):
        # Converting arms a triggered task in Home Keeper; so would an interval made
        # longer in the options. A skip puts it back to waiting without a completion.
        after.append(SkipTask(task["id"], _t(settings, "calendar_skip_note")))
    first = edit.action()
    return ([first] if first else []) + after


# ── pre-filter task ──────────────────────────────────────────────────────────


def plan_prefilter(
    task: dict[str, Any] | None,
    purifier: Purifier,
    settings: Settings,
    *,
    now: datetime,
) -> list[Action]:
    """Keep the pre-filter task.

    A meter on fan runtime when the sensor exists (Home Keeper arms it), else a
    calendar task the glue arms.
    """
    if not settings.prefilter_enabled:
        return [DeleteTask(task["id"])] if task is not None else []
    weeks = settings.prefilter_weeks
    how = _t(settings, "how_wash" if purifier.model.prefilter == WASH else "how_vacuum")
    name = _t(settings, "prefilter_name", device=purifier.name)
    if not purifier.runtime_entity:
        return plan_calendar(
            task,
            purifier,
            settings,
            role=ROLE_PREFILTER,
            name=name,
            notes=f"{how} {_t(settings, 'due_calendar', weeks=weeks)}",
            interval=weeks,
            unit="weeks",
            rule=f"calendar:{weeks}:{settings.lang}",
            now=now,
        )
    hours = settings.prefilter_hours
    rule = f"runtime:{purifier.runtime_entity}:{hours}:{weeks}:{settings.lang}"
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
    if task is None:
        if purifier.paused:
            return []
        return [
            CreateTask(
                {
                    "name": name,
                    **fields,
                    "device_id": purifier.device_id,
                    "managed_by": _managed_by(settings, CALENDAR_LOCKED_FIELDS),
                    "source": {
                        SOURCE_NS: {
                            "device_id": purifier.device_id,
                            "role": ROLE_PREFILTER,
                            "name": name,
                            "rule": rule,
                        }
                    },
                }
            )
        ]
    edit = _TaskEdit(task)
    if _resume_or_pause(edit, purifier):
        _rename(edit, name)
        if edit.src.get("rule") != rule:
            edit.data.update(fields)
            if task.get("task_chips"):
                edit.data["task_chips"] = []  # the meter shows its own countdown
            edit.set_src({**edit.src, "rule": rule})
    return [a] if (a := edit.action()) else []


# ── dust-sensor task ─────────────────────────────────────────────────────────


def plan_sensor(
    task: dict[str, Any] | None,
    purifier: Purifier,
    settings: Settings,
    *,
    now: datetime,
) -> list[Action]:
    """Keep the dust-sensor cleaning task of a purifier whose model has a sensor.

    Levoit gives a calendar interval only, so it is a calendar task the glue arms.
    """
    kind = purifier.model.sensor
    if not settings.sensor_enabled or kind is None:
        return [DeleteTask(task["id"])] if task is not None else []
    months = SENSOR_MONTHS[kind]
    return plan_calendar(
        task,
        purifier,
        settings,
        role=ROLE_SENSOR,
        name=_t(settings, "sensor_name", device=purifier.name),
        notes=f"{_t(settings, 'sensor_' + kind)} {_t(settings, 'sensor_due', months=months)}",
        interval=months,
        unit="months",
        rule=f"{kind}:{months}:{settings.lang}",
        now=now,
    )


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
        actions += plan_sensor(idx.get((p.device_id, ROLE_SENSOR)), p, settings, now=now)
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
        users.setdefault(p.filter_part, []).append(p.name)
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


def _owner_view(part: dict[str, Any]) -> dict[str, Any]:
    """Return a stored part as the owner sends it back: its id and owner keys.

    ``update_managed_asset`` takes the whole part list and the owner keys from what is
    sent, so a part restated without them would lose them (a part number the user
    typed on a device page, say). Home Keeper itself keeps ``last_replaced``.
    """
    out = {
        key: part[key]
        for key in PART_OWNER_KEYS
        # Unset fields come back as None, which the service schema refuses; Home
        # Keeper keeps last_replaced and carried_uses itself.
        if part.get(key) is not None and key not in ("last_replaced", "carried_uses")
    }
    out["id"] = part["id"]
    return out


def _find_part(asset: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next(
        (p for p in asset.get("parts") or [] if p.get("name") == name and p.get("id")), None
    )


_DEVICE_PART_NOTES = frozenset(
    note
    for text in TEXT.values()
    for note in (
        text["device_part_notes"].format(spares=text["spares_name"]),
        text["device_part_notes_plain"],
    )
)


def _device_part(purifier: Purifier, settings: Settings) -> dict[str, Any]:
    """Return the fitted-filter part of a purifier's appliance, as the owner writes it."""
    notes = (
        _t(settings, "device_part_notes", spares=_t(settings, "spares_name"))
        if settings.stock_enabled
        else _t(settings, "device_part_notes_plain")
    )
    return {"name": purifier.filter_part, "type": PART_TYPE_WEAR, "notes": notes}


def _device_parts(
    purifier: Purifier, existing: list[dict[str, Any]], settings: Settings
) -> list[dict[str, Any]] | None:
    """Return the part list of a purifier's appliance, or None when it is right.

    The fitted filter is matched by name; every other part is the user's and is sent
    back as it is.
    """
    wanted = _device_part(purifier, settings)
    mine = next((p for p in existing if p.get("name") == wanted["name"]), None)
    if mine is None:
        # Renamed in the options: the part that carries our notes is still ours, and
        # keeping its id keeps its last_replaced.
        mine = next((p for p in existing if p.get("notes") in _DEVICE_PART_NOTES), None)
    if mine is not None and all(mine.get(k) == v for k, v in wanted.items()):
        return None
    out: list[dict[str, Any]] = []
    for part in existing:
        if not part.get("id"):
            continue
        if part is mine:
            out.append({**_owner_view(part), **wanted})
        else:
            out.append(_owner_view(part))
    if mine is None:
        out.append(wanted)
    return out


def plan_assets(
    assets: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    purifiers: list[Purifier],
    known_device_ids: set[str],
    settings: Settings,
) -> list[Action]:
    """Converge the spare-filter appliance, the purifier appliances and the links.

    An appliance an option switches off, or whose device is gone, is retired as on
    uninstall (:func:`retire`): handed over if the user counted stock on it, else
    deleted. Switched on again, a new one is made; one handed over stays the user's.
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

    # 2. Each purifier gets an appliance on its own device page, unless it has one,
    #    carrying the fitted filter as a part. Parts the user added there stay.
    taken = {a.get("device_id") for a in assets if a.get("device_id")}
    by_device = {p.device_id: p for p in purifiers}
    for p in purifiers:
        ours = mine.get((p.device_id, ROLE_PURIFIER))
        if not settings.appliances_enabled:
            continue  # retired in step 4, once the links are gone
        if ours is not None:
            existing = list(ours.get("parts") or [])
            wanted_parts = _device_parts(p, existing, settings)
            if wanted_parts is not None:
                actions.append(UpdateManagedAsset(ours["id"], wanted_parts))
            continue
        if p.device_id in taken:
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
                    "parts": [_device_part(p, settings)],
                    "managed_by": _managed_by(settings, []),
                    "source": {SOURCE_NS: {"device_id": p.device_id, "role": ROLE_PURIFIER}},
                }
            )
        )

    # 3. Each filter task stamps the filter on its device page and takes one filter of
    #    its model off the shared stock. Links the user added to other parts stay.
    spare_ids = {
        p.get("name"): p.get("id") for p in ((spares or {}).get("parts") or []) if p.get("id")
    }
    our_ids = {a.get("id") for a in mine.values()}
    for task in our_tasks(tasks):
        src = ns(task) or {}
        if src.get("role") != ROLE_FILTER:
            continue
        links = list((task.get("source") or {}).get(HK_SOURCE_PARTS) or [])
        wanted = [link for link in links if link.get("asset_id") not in our_ids]
        purifier = by_device.get(str(src.get("device_id")))
        if purifier is not None:
            device = mine.get((purifier.device_id, ROLE_PURIFIER))
            if device is not None and settings.appliances_enabled:
                part = _find_part(device, purifier.filter_part)
                if part is None:
                    continue  # the part arrives with the next pass
                wanted.append({"asset_id": device["id"], "part_id": part["id"], "quantity": 1})
            if spares is not None and settings.stock_enabled:
                part_id = spare_ids.get(purifier.filter_part)
                if part_id is None:
                    continue  # the part arrives with the next pass
                wanted.append({"asset_id": spares["id"], "part_id": part_id, "quantity": 1})
        if _links_key(wanted) != _links_key(links):
            actions.append(LinkParts(task["id"], wanted))

    # 4. What the options no longer want is retired, after the links above are gone.
    if not settings.appliances_enabled:
        actions += [
            retire(mine[(p.device_id, ROLE_PURIFIER)])
            for p in purifiers
            if (p.device_id, ROLE_PURIFIER) in mine
        ]
    if spares is not None and not settings.stock_enabled:
        actions.append(retire(spares))
    for (device_id, role), asset in mine.items():
        if (
            role == ROLE_PURIFIER
            and settings.appliances_enabled
            and device_id not in by_device
            and device_id not in known_device_ids
        ):
            actions.append(retire(asset))
    return actions


# ── renaming a spare filter in the options ───────────────────────────────────


def plan_spare_renames(before: dict[str, str], after: dict[str, str]) -> dict[str, str]:
    """Return the spare parts to rename when the options change the filter names.

    *before* and *after* map each purifier's device id to the filter it takes. A name
    is renamed (same part, so its stock and history stay) only when every purifier
    that took it now takes one and the same new name and none still takes the old one.
    A purifier moving to a name of its own (a Pet Allergy filter, say) gets a new part
    instead, and the others keep theirs.
    """
    renames: dict[str, str] = {}
    old_names = set(before.values())
    still_used = set(after.values())
    for old in old_names - still_used:
        targets = {after[d] for d, name in before.items() if name == old and d in after}
        if len(targets) == 1:
            new = targets.pop()
            if new not in old_names:
                renames[old] = new
    return renames


def rename_parts(parts: list[dict[str, Any]], renames: dict[str, str]) -> list[dict[str, Any]]:
    """Return the owner's part list of the spare-filter appliance with *renames* applied."""
    return [
        {**_owner_view(part), "name": renames.get(part.get("name"), part.get("name"))}
        for part in parts
        if part.get("id")
    ]
