"""Config and options flow.

The config flow creates the single entry: there is nothing to ask. The options flow is
a menu of three pages (design round 2):

- **General**: the switches and the pre-filter interval.
- **Spare filters**: the filter name per model and, optionally, per purifier.
- **Devices and entities**: per purifier, the entities found automatically, and the
  ones assigned by hand when a YAML names them differently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from . import logic
from .const import (
    DEFAULT_APPLIANCES_ENABLED,
    DEFAULT_PREFILTER_ENABLED,
    DEFAULT_PREFILTER_HOURS,
    DEFAULT_PREFILTER_WEEKS,
    DEFAULT_RESET_ON_DONE,
    DEFAULT_SENSOR_ENABLED,
    DEFAULT_STOCK_ENABLED,
    DOMAIN,
    ENTITY_KEYS,
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
    TEXT,
    TITLE,
)

if TYPE_CHECKING:
    from .wiring import LevoitGlue

_SWITCHES = (
    (OPT_STOCK_ENABLED, DEFAULT_STOCK_ENABLED),
    (OPT_APPLIANCES_ENABLED, DEFAULT_APPLIANCES_ENABLED),
    (OPT_RESET_ON_DONE, DEFAULT_RESET_ON_DONE),
    (OPT_PREFILTER_ENABLED, DEFAULT_PREFILTER_ENABLED),
    (OPT_SENSOR_ENABLED, DEFAULT_SENSOR_ENABLED),
)
_SECTION_MODELS = "models"
_SECTION_DEVICES = "devices"
_FIELD_DEVICE = "device"
# The entity fields of one purifier, in form order (keys of ENTITY_KEYS).
_ENTITY_FIELDS = ("filter_low", "filter_reset", "runtime")


class TuctLevoitGlueConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single entry after one confirmation."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return TuctLevoitOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm and create the entry."""
        if user_input is not None:
            return self.async_create_entry(title=TITLE, data={})
        return self.async_show_form(step_id="user")


class TuctLevoitOptionsFlow(OptionsFlow):
    """The three option pages behind one menu."""

    def __init__(self) -> None:
        """Start without a purifier chosen."""
        self._device_id: str | None = None

    # ── helpers ──────────────────────────────────────────────────────────────
    @property
    def _glue(self) -> LevoitGlue | None:
        entry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return None
        return getattr(entry, "runtime_data", None)

    @property
    def _text(self) -> dict[str, str]:
        return TEXT[logic.lang_of(self.hass.config.language)]

    def _save(self, **changes: Any) -> ConfigFlowResult:
        options = {**self.config_entry.options, **changes}
        return self.async_create_entry(data=options)

    # ── menu ─────────────────────────────────────────────────────────────────
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the menu."""
        glue = self._glue
        if glue is None:
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "filters", "devices"],
            description_placeholders={"count": str(len(glue.purifiers))},
        )

    # ── general ──────────────────────────────────────────────────────────────
    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Switches and the pre-filter interval."""
        opts = self.config_entry.options
        if user_input is not None:
            return self._save(
                **{key: bool(user_input[key]) for key, _ in _SWITCHES},
                **{
                    OPT_PREFILTER_HOURS: int(user_input[OPT_PREFILTER_HOURS]),
                    OPT_PREFILTER_WEEKS: int(user_input[OPT_PREFILTER_WEEKS]),
                },
            )
        fields: dict[Any, Any] = {
            vol.Required(key, default=bool(opts.get(key, default))): BooleanSelector()
            for key, default in _SWITCHES
        }
        fields[
            vol.Required(
                OPT_PREFILTER_HOURS,
                default=int(opts.get(OPT_PREFILTER_HOURS, DEFAULT_PREFILTER_HOURS)),
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=10, max=5000, step=10, unit_of_measurement="h", mode=NumberSelectorMode.BOX
            )
        )
        fields[
            vol.Required(
                OPT_PREFILTER_WEEKS,
                default=int(opts.get(OPT_PREFILTER_WEEKS, DEFAULT_PREFILTER_WEEKS)),
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=1,
                max=26,
                step=1,
                unit_of_measurement=self._text["weeks_unit"],
                mode=NumberSelectorMode.BOX,
            )
        )
        return self.async_show_form(step_id="general", data_schema=vol.Schema(fields))

    # ── spare filters ────────────────────────────────────────────────────────
    def _filter_fields(
        self, purifiers: list[logic.Purifier]
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Return the form labels of the model and purifier fields, keyed by label.

        The labels are the field keys, because the models and purifiers are only known
        at run time and a field without a translation shows its key.
        """
        models: dict[str, str] = {}
        for p in sorted(purifiers, key=lambda p: p.model.label):
            count = sum(1 for q in purifiers if q.model.key == p.model.key)
            label = self._text["model_field"].format(model=p.model.label, count=count)
            models.setdefault(label, p.model.key)
        devices: dict[str, str] = {}
        for p in sorted(purifiers, key=lambda p: p.name.lower()):
            label, n = p.name, 2
            while label in devices or label in models:
                label, n = f"{p.name} ({n})", n + 1
            devices[label] = p.device_id
        return models, devices

    async def async_step_filters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Filter names per model and per purifier."""
        glue = self._glue
        if glue is None:
            return self.async_abort(reason="not_loaded")
        purifiers = glue.purifiers
        models, devices = self._filter_fields(purifiers)
        opts = self.config_entry.options
        model_names = dict(opts.get(OPT_FILTER_NAMES) or {})
        device_names = dict(opts.get(OPT_DEVICE_FILTERS) or {})

        if user_input is not None:
            by_model = user_input.get(_SECTION_MODELS) or {}
            by_device = user_input.get(_SECTION_DEVICES) or {}
            catalog = {p.model.key: p.model.filter_part for p in purifiers}
            new_models = {
                key: name
                for label, key in models.items()
                if (name := str(by_model.get(label) or "").strip()) and name != catalog[key]
            }
            # Models not shown (no purifier right now) keep their names.
            new_models |= {k: v for k, v in model_names.items() if k not in catalog}
            model_of = {p.device_id: p.model.key for p in purifiers}
            new_devices: dict[str, str] = {
                k: v for k, v in device_names.items() if k not in model_of
            }
            for label, device_id in devices.items():
                name = str(by_device.get(label) or "").strip()
                key = model_of[device_id]
                if name and name != (new_models.get(key) or catalog[key]):
                    new_devices[device_id] = name
            new_opts = {**opts, OPT_FILTER_NAMES: new_models, OPT_DEVICE_FILTERS: new_devices}
            before = {p.device_id: p.filter_part for p in purifiers}
            after = {
                p.device_id: glue.filter_for(p.device_id, p.model.key, new_opts)
                or p.model.filter_part
                for p in purifiers
            }
            await glue.async_rename_spares(logic.plan_spare_renames(before, after))
            return self._save(**{OPT_FILTER_NAMES: new_models, OPT_DEVICE_FILTERS: new_devices})

        model_fields = {
            vol.Optional(label, description={"suggested_value": model_names.get(key)}): (
                TextSelector()
            )
            for label, key in models.items()
        }
        device_fields = {
            vol.Optional(label, description={"suggested_value": device_names.get(device_id)}): (
                TextSelector()
            )
            for label, device_id in devices.items()
        }
        defaults = "\n".join(
            f"- {p.model.label}: {p.model.filter_part}"
            for p in {p.model.key: p for p in purifiers}.values()
        )
        schema: dict[Any, Any] = {}
        if model_fields:
            schema[vol.Required(_SECTION_MODELS)] = section(
                vol.Schema(model_fields), {"collapsed": False}
            )
        if device_fields:
            schema[vol.Required(_SECTION_DEVICES)] = section(
                vol.Schema(device_fields), {"collapsed": False}
            )
        return self.async_show_form(
            step_id="filters",
            data_schema=vol.Schema(schema),
            description_placeholders={"defaults": defaults or "–"},
        )

    # ── devices and entities ─────────────────────────────────────────────────
    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a purifier."""
        glue = self._glue
        if glue is None:
            return self.async_abort(reason="not_loaded")
        purifiers = sorted(glue.purifiers, key=lambda p: p.name.lower())
        if not purifiers:
            return self.async_abort(reason="no_purifiers")
        if user_input is not None:
            self._device_id = user_input[_FIELD_DEVICE]
            return await self.async_step_device()
        text = self._text
        options: list[SelectOptionDict] = []
        for p in purifiers:
            found = {**glue.detect_entities(p.device_id), **glue.assigned_entities(p.device_id)}
            by_hand = bool(glue.assigned_entities(p.device_id))
            options.append(
                SelectOptionDict(
                    value=p.device_id,
                    label=text["device_option"].format(
                        name=p.name,
                        model=p.model.label,
                        found=len(found),
                        total=len(ENTITY_KEYS),
                        how=text["by_hand"] if by_hand else text["automatic"],
                    ),
                )
            )
        return self.async_show_form(
            step_id="devices",
            data_schema=vol.Schema(
                {
                    vol.Required(_FIELD_DEVICE): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The entities of one purifier: empty fields are detected automatically."""
        glue = self._glue
        if glue is None:
            return self.async_abort(reason="not_loaded")
        device_id = self._device_id
        purifier = next((p for p in glue.purifiers if p.device_id == device_id), None)
        if device_id is None or purifier is None:
            return self.async_abort(reason="no_purifiers")
        detected = glue.detect_entities(device_id)
        errors: dict[str, str] = {}

        if user_input is not None:
            assigned = {
                key: value
                for key in _ENTITY_FIELDS
                if (value := user_input.get(key)) and value != detected.get(key)
            }
            if not (assigned.get("filter_low") or detected.get("filter_low")):
                errors["filter_low"] = "filter_low_missing"
            else:
                entities = dict(self.config_entry.options.get(OPT_ENTITIES) or {})
                if assigned:
                    entities[device_id] = assigned
                else:
                    entities.pop(device_id, None)
                return self._save(**{OPT_ENTITIES: entities})

        current = {**detected, **glue.assigned_entities(device_id)}
        ent_reg = er.async_get(self.hass)
        by_domain: dict[str, list[str]] = {}
        for ent in er.async_entries_for_device(ent_reg, device_id):
            by_domain.setdefault(ent.domain, []).append(ent.entity_id)
        fields: dict[Any, Any] = {}
        for key in _ENTITY_FIELDS:
            domain = ENTITY_KEYS[key][0]
            fields[vol.Optional(key, description={"suggested_value": current.get(key)})] = (
                EntitySelector(
                    EntitySelectorConfig(
                        domain=domain, include_entities=sorted(by_domain.get(domain, []))
                    )
                )
            )
        text = self._text
        detected_lines = "\n".join(
            f"- {text['entity_' + key]}: "
            + (f"`{detected[key]}`" if key in detected else text["not_found"])
            for key in _ENTITY_FIELDS
        )
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={
                "name": purifier.name,
                "model": purifier.model.label,
                "detected": detected_lines,
            },
        )
