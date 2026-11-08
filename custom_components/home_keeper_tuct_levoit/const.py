"""Constants for the Home Keeper ↔ tuct/levoit glue integration.

The glue finds Levoit air purifiers that run the ESPHome firmware of
https://github.com/tuct/levoit and keeps their filter tasks and one shared spare-filter
stock in Home Keeper. It talks to both sides only through the registries, entity states,
services and events, never by importing them.
"""

from __future__ import annotations

DOMAIN = "home_keeper_tuct_levoit"
TITLE = "tuct/levoit air purifiers"

# Our namespace in a Home Keeper task's / appliance's ``source``, and the ``origin`` we
# send on completions we make ourselves, so our listener can drop the echo.
SOURCE_NS = DOMAIN
ORIGIN = DOMAIN

# The roles in our ``source`` namespace. A task's namespace also carries ``device_id``,
# the ``name`` we last gave it, and these marks:
#   paused         we switched the task off because its device is disabled
#   pending_reset  a user ticked the filter task off; the purifier has not confirmed the
#                  reset yet (Filter Low still on), so a low reading must not re-arm it
#   rule           the pre-filter schedule we last wrote
ROLE_FILTER = "filter"
ROLE_PREFILTER = "prefilter"
ROLE_PURIFIER = "purifier"
ROLE_SPARES = "spares"
MARK_PAUSED = "paused"
MARK_PENDING = "pending_reset"

# ── Home Keeper side (EXTERNAL CONTRACT — docs/INTEGRATING.md, v0.36.0) ───────
HK_DOMAIN = "home_keeper"
HK_EVENT_TASK_COMPLETED = "home_keeper_task_completed"
HK_EVENT_TASK_DELETED = "home_keeper_task_deleted"
HK_EVENT_REGISTER_COMPANIONS = "home_keeper_register_companions"
HK_SERVICE_REGISTER_COMPANION = "register_companion"
HK_SERVICE_LIST_TASKS = "list_tasks"
HK_SERVICE_ADD_TASK = "add_task"
HK_SERVICE_UPDATE_TASK = "update_task"
HK_SERVICE_DELETE_TASK = "delete_task"
HK_SERVICE_COMPLETE_TASK = "complete_task"
HK_SERVICE_TRIGGER_TASK = "trigger_task"
HK_SERVICE_LIST_ASSETS = "list_assets"
HK_SERVICE_ADD_ASSET = "add_asset"
HK_SERVICE_UPDATE_ASSET = "update_asset"
HK_SERVICE_DELETE_ASSET = "delete_asset"
HK_SERVICE_UPDATE_MANAGED_ASSET = "update_managed_asset"
HK_SERVICE_SET_TASK_CONSUMABLE = "set_task_consumable"
# Home Keeper keeps a task's consumable links in ``source["parts"]``.
HK_SOURCE_PARTS = "parts"

# ── ESPHome / tuct firmware side (EXTERNAL CONTRACT) ──────────────────────────
# Home Assistant's ESPHome integration splits ``esphome: project: name:`` at the dot into
# the device's manufacturer and model (esphome/manager.py, async_get_manufacturer_model).
# A purifier is a device of the esphome integration whose manufacturer is ``tuct`` and
# whose model starts with ``levoit``: the project name ``tuct.levoit-vital100s`` gives
# manufacturer ``tuct`` and model ``levoit-vital100s``.
ESPHOME_DOMAIN = "esphome"
PROJECT_MANUFACTURER = "tuct"
PROJECT_MODEL_PREFIX = "levoit"

# Entities are matched by the slug of their name in the ESPHome YAML (the registry's
# ``original_name``), else by the end of their entity id. tuct's configs call the button
# "Filter Reset"; "Reset Filter Stats" is common too. "Fan Runtime" is not part of the
# tuct firmware: users add it as a duty_time sensor (see README).
ENTITY_KEYS: dict[str, tuple[str, tuple[str, ...]]] = {
    "filter_low": ("binary_sensor", ("filter_low",)),
    "filter_reset": ("button", ("filter_reset", "reset_filter_stats", "reset_filter")),
    "runtime": ("sensor", ("fan_runtime",)),
}

# ── Options ──────────────────────────────────────────────────────────────────
OPT_STOCK_ENABLED = "stock_enabled"
OPT_APPLIANCES_ENABLED = "appliances_enabled"
OPT_RESET_ON_DONE = "reset_on_done"
OPT_PREFILTER_ENABLED = "prefilter_enabled"
OPT_PREFILTER_HOURS = "prefilter_hours"
OPT_PREFILTER_WEEKS = "prefilter_weeks"
# {model key: filter name}: the filter every purifier of that model takes.
OPT_FILTER_NAMES = "filter_names"
# {device id: filter name}: a purifier that takes a filter of its own.
OPT_DEVICE_FILTERS = "device_filters"
# {device id: {"filter_low" | "filter_reset" | "runtime": entity id}}: entities assigned
# by hand; a key left out is detected automatically.
OPT_ENTITIES = "entities"

DEFAULT_STOCK_ENABLED = True
DEFAULT_APPLIANCES_ENABLED = True
DEFAULT_RESET_ON_DONE = True
DEFAULT_PREFILTER_ENABLED = True
# Levoit: clean the pre-filter every 2–4 weeks. 300 h is 2 weeks around the clock or
# 4 weeks at 10 h a day; the calendar backstop catches a purifier that idles.
DEFAULT_PREFILTER_HOURS = 300
DEFAULT_PREFILTER_WEEKS = 4

# ── Managed ownership ────────────────────────────────────────────────────────
MANAGED_DISPLAY_NAME = "tuct/levoit"
MANAGED_ICON = "mdi:air-purifier"
COMPANION_NAME = "tuct/levoit air purifiers"
DOCS_URL = "https://github.com/financelurker/ha-home-keeper-tuct-levoit"
# Fields written once and never changed. Home Keeper strips locked fields from every
# update_task, ours too, so the name stays unlocked (it follows a device rename) and the
# pre-filter schedule stays unlocked (it follows the runtime sensor and the options).
FILTER_LOCKED_FIELDS = ["device_id", "recurrence_type"]
PREFILTER_LOCKED_FIELDS = ["device_id"]
SPARES_LOCKED_FIELDS = ["parts"]
PART_TYPE_CONSUMABLE = "consumable"
# The filter fitted in a purifier, on its device page: a wear part without a
# replacement interval, so Home Keeper derives no task of its own from it.
PART_TYPE_WEAR = "wear"

# ── Text per language (task data, so it follows the Home Assistant language) ─
TEXT: dict[str, dict[str, str]] = {
    "en": {
        "filter_name": "Replace filter ({device})",
        "filter_notes": (
            "The purifier reports **Filter Low**. Fit a new **{part}** and tick this "
            "off: the filter statistics on the purifier are reset for you."
        ),
        "filter_notes_manual": (
            "The purifier reports **Filter Low**. Fit a new **{part}**, press "
            "**Filter Reset** on the purifier and tick this off."
        ),
        "filter_prompt": "Ticking this off resets the filter statistics on the purifier.",
        "prefilter_name": "Clean pre-filter ({device})",
        "how_wash": "Take out the washable pre-filter, vacuum it, rinse it under running "
        "water and let it dry completely.",
        "how_vacuum": "Vacuum the pre-filter or brush it off. Do not wash it.",
        "due_runtime": "Due every {hours} h of fan runtime or every {weeks} weeks, "
        "whichever comes first.",
        "due_calendar": "Due every {weeks} weeks. Add a `Fan Runtime` sensor to the "
        "ESPHome config to count fan hours instead.",
        "spares_name": "Levoit spare filters",
        "spares_notes": (
            "Created by the tuct/levoit companion: one shared stock of replacement "
            "filters for every purifier in the home. Enter the count, a reorder point "
            "and turn on *Auto-create buy task* on a part."
        ),
        "part_notes": "Used by: {devices}",
        "purifier_notes": "Levoit {model}, ESPHome firmware by tuct/levoit.",
        "device_part_notes": (
            "The filter fitted in this purifier, stamped each time *Replace filter* is "
            "ticked off. Spares are counted in *{spares}*: enter no stock here, or a "
            "replacement takes two."
        ),
        "device_part_notes_plain": (
            "The filter fitted in this purifier, stamped each time *Replace filter* is ticked off."
        ),
        "description": (
            "Levoit air purifiers on tuct/levoit ESPHome firmware: filter replacement "
            "from Filter Low, pre-filter cleaning, one shared spare-filter stock."
        ),
        # Options flow labels built at run time (models and purifiers vary).
        "weeks_unit": "weeks",
        "model_field": "{model} · {count} devices",
        "device_option": "{name} · {model} · {found} of {total} entities, {how}",
        "automatic": "automatic",
        "by_hand": "some assigned by hand",
        "not_found": "not found",
        "entity_filter_low": "Filter Low",
        "entity_filter_reset": "Filter reset button",
        "entity_runtime": "Fan runtime",
    },
    "de": {
        "filter_name": "Filter tauschen ({device})",
        "filter_notes": (
            "Der Luftreiniger meldet **Filter Low**. Neuen **{part}** einsetzen und "
            "abhaken: Die Filterstatistik am Gerät wird dabei zurückgesetzt."
        ),
        "filter_notes_manual": (
            "Der Luftreiniger meldet **Filter Low**. Neuen **{part}** einsetzen, am "
            "Gerät **Filter Reset** drücken und abhaken."
        ),
        "filter_prompt": "Abhaken setzt die Filterstatistik am Luftreiniger zurück.",
        "prefilter_name": "Vorfilter reinigen ({device})",
        "how_wash": "Waschbaren Vorfilter herausnehmen, absaugen, unter fließendem Wasser "
        "ausspülen und vollständig trocknen lassen.",
        "how_vacuum": "Vorfilter absaugen oder abbürsten. Nicht waschen.",
        "due_runtime": "Fällig alle {hours} h Lüfterlaufzeit oder alle {weeks} Wochen, je "
        "nachdem, was zuerst eintritt.",
        "due_calendar": "Fällig alle {weeks} Wochen. Mit einem `Fan Runtime`-Sensor in der "
        "ESPHome-Config wird stattdessen nach Lüfterstunden gezählt.",
        "spares_name": "Levoit-Ersatzfilter",
        "spares_notes": (
            "Von der tuct/levoit-Companion angelegt: ein gemeinsamer Vorrat an "
            "Ersatzfiltern für alle Luftreiniger im Haushalt. Beim Teil Bestand, "
            "Nachbestellgrenze und *Auto-create buy task* setzen."
        ),
        "part_notes": "Verwendet von: {devices}",
        "purifier_notes": "Levoit {model}, ESPHome-Firmware von tuct/levoit.",
        "device_part_notes": (
            "Der in diesem Luftreiniger eingesetzte Filter; das Datum wird bei jedem "
            "Abhaken von *Filter tauschen* gesetzt. Ersatzfilter werden in *{spares}* "
            "gezählt: hier keinen Bestand eintragen, sonst zählt ein Tausch doppelt."
        ),
        "device_part_notes_plain": (
            "Der in diesem Luftreiniger eingesetzte Filter; das Datum wird bei jedem "
            "Abhaken von *Filter tauschen* gesetzt."
        ),
        "description": (
            "Levoit-Luftreiniger mit tuct/levoit-ESPHome-Firmware: Filtertausch aus "
            "Filter Low, Vorfilterreinigung, ein gemeinsamer Ersatzfilter-Vorrat."
        ),
        "weeks_unit": "Wochen",
        "model_field": "{model} · {count} Geräte",
        "device_option": "{name} · {model} · {found} von {total} Entitäten, {how}",
        "automatic": "automatisch",
        "by_hand": "teils von Hand zugeordnet",
        "not_found": "nicht gefunden",
        "entity_filter_low": "Filter Low",
        "entity_filter_reset": "Filter-Reset-Taste",
        "entity_runtime": "Lüfterlaufzeit",
    },
}
