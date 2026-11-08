# Home Keeper — tuct/levoit air purifiers

[![Validate](https://github.com/financelurker/ha-home-keeper-tuct-levoit/actions/workflows/validate.yml/badge.svg)](https://github.com/financelurker/ha-home-keeper-tuct-levoit/actions/workflows/validate.yml)

Keeps the filters of Levoit air purifiers running the [tuct/levoit](https://github.com/tuct/levoit) ESPHome firmware in [Home Keeper](https://github.com/prestomation/ha-home-keeper): a *"Replace filter"* task raised by the purifier's **Filter Low**, a *"Clean pre-filter"* task by fan runtime or weeks, and one shared stock of spare filters for the whole home.

It is a small *glue* integration in the sense of Home Keeper's [glue pattern](https://github.com/prestomation/ha-home-keeper/blob/main/docs/GLUE_INTEGRATIONS.md): it changes neither the purifier firmware nor Home Keeper and talks to both only through the registries, entity states, services and events.

## Installation

Requires Home Assistant 2026.10 or newer and Home Keeper 0.36.0 or newer.

### HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=financelurker&repository=ha-home-keeper-tuct-levoit&category=integration)

1. Click the button above. It opens the repository in HACS and adds it as a custom repository if needed.
2. Select **Download** and restart Home Assistant.

Without the button: HACS → ⋮ → **Custom repositories** → add `https://github.com/financelurker/ha-home-keeper-tuct-levoit` as type *Integration*.

### Manual

1. Copy `custom_components/home_keeper_tuct_levoit` from the ZIP of the latest release into `config/custom_components/`.
2. Restart Home Assistant.

## Setup

**Settings → Devices & services → Add integration → Home Keeper — tuct/levoit air purifiers.** One instance covers every purifier.

## ESPHome configuration

The integration needs three things from a purifier's ESPHome YAML. tuct's example configurations have the entities, but **not the project name**, so add that first.

### 1. Project name (required)

The integration finds a purifier by its ESPHome project name. Add a `project` block under `esphome:`:

```yaml
esphome:
  name: ${device_name}
  project:
    name: tuct.levoit-vital100s   # see the table below
    version: "1.0"                # any text; Home Assistant shows it as the firmware version
```

| Purifier | `project: name:` |
|---|---|
| Core 200S | `tuct.levoit-core200s` |
| Core 300S | `tuct.levoit-core300s` |
| Core 400S | `tuct.levoit-core400s` |
| Core 600S | `tuct.levoit-core600s` |
| Vital 100S | `tuct.levoit-vital100s` |
| Vital 200S | `tuct.levoit-vital200s` |
| Vital 200S Pro | `tuct.levoit-vital200s-pro` |
| EverestAir | `tuct.levoit-everest-air` |
| Sprout | `tuct.levoit-sprout` |

Home Assistant splits the name at the dot: the device page then shows manufacturer **tuct** and model **levoit-vital100s**. That is what the integration looks for. Only devices of the ESPHome integration count, so nothing else in your home is picked up by accident. Flash the device, and the integration finds it within seconds, no reload needed.

### 2. Filter entities (in tuct's configurations already)

| Entity | In tuct's YAML | Used for |
|---|---|---|
| `binary_sensor` *Filter Low* (required) | `type: filter_low` | Raises *Replace filter*. |
| `button` *Filter Reset* | `type: reset_filter_stats` | Pressed when you tick off *Replace filter*. Without it, the task asks you to press it yourself. |
| `number` *Filter Months* | `type: filter_lifetime_months` | Not read by the integration, but it sets after how many months of use the purifier reports *Filter Low*. |
| `sensor` *Filter %* | `type: filter_life_left` | Shown on *Replace filter* as a chip. tuct counts it as if the fan ran at full speed around the clock, so a filter usually lasts longer than it suggests; *Filter Low* comes on below 5 %. |

The integration matches them by their `name:` in the YAML, else by the end of the entity ID. If you renamed one (say *Swap Filter*), assign it under **Configure → Devices and entities**.

### 3. Fan runtime (optional)

With a *Fan Runtime* sensor, *Clean pre-filter* is due after 300 hours of fan time (or at the latest after 4 weeks) instead of every 4 weeks. tuct's firmware has no such sensor, but ESPHome's [`duty_time`](https://esphome.io/components/sensor/duty_time/) sensor counts the hours the fan runs. Add it to the `sensor:` list:

```yaml
sensor:
  - platform: duty_time
    name: "Fan Runtime"
    id: fan_runtime
    lambda: "return id(levoit_fan1).state;"   # the fan's id in tuct's configurations
    restore: true                             # keep the count across reboots
    unit_of_measurement: h
    device_class: duration
    state_class: total_increasing
    accuracy_decimals: 1
    filters:
      - multiply: 0.000277778                 # duty_time counts seconds; this makes hours
```

Good to know:

- It counts every fan speed the same: an hour on sleep counts as much as an hour on turbo.
- `restore: true` writes the count to flash now and then. ESPHome warns that this wears the flash over the years. Without it, the count starts at 0 after every reboot: Home Keeper notices and counts on from 0, so the hours before the reboot are lost and the pre-filter comes due later.
- Without the sensor, the integration uses the weeks only. Add the sensor later and the same task switches to fan hours.

### Not found?

Turn on debug logging (see [Troubleshooting](#troubleshooting)). When no purifier is found, the log says so and names the manufacturer and model it looks for. Check the device page: it must show manufacturer **tuct** and a model starting with **levoit**.

## Options

**Settings → Devices & services → Home Keeper — tuct/levoit air purifiers → Configure** offers three pages:

| Page | What you set |
|---|---|
| **General** | Shared spare-filter stock, appliances on the purifiers, reset filter statistics on done, the *Clean pre-filter* task and its interval (every … fan hours, default 300, or at the latest every … weeks, default 4), and the *Clean dust sensor* task. |
| **Spare filters** | The filter name per model, and optionally per purifier (a purifier that takes a Pet Allergy filter, say, gets its own part with its own stock). Leave a field empty for the default. Renaming the filter of a model renames its part, so the stock and the history stay. |
| **Devices and entities** | Per purifier, the entities found automatically, and the ones you assign by hand when your ESPHome YAML names them differently. Leave a field empty to detect it automatically. |

## What it creates in Home Keeper

Every task waits (*Monitored* in Home Keeper) and comes onto Home Keeper's to-do list only when the care is due: *Replace filter* when the purifier reports Filter Low, *Clean pre-filter* after 300 fan hours (Home Keeper's meter) or the weeks, *Clean dust sensor* after its months. Home Keeper would put a calendar task on its to-do list months ahead, so the calendar tasks are armed by the integration instead: it looks once an hour, counts from the last time the task was ticked off or skipped, and shows the date it comes due as a chip on the task.

| | Created | Content |
|---|---|---|
| **Replace filter (‹purifier›)** | per purifier, at once | Waits (*Monitored*) until **Filter Low** raises it, and is cleared when the purifier reports the filter fine again. Ticking it off presses the purifier's **Filter Reset** button. A chip shows the filter life left (*Filter %*) in 5 % steps. Made ahead of the first Filter Low, it carries one *skipped* entry in its history: that is how Home Keeper puts such a task to waiting without counting a replacement. |
| **Clean pre-filter (‹purifier›)** | per purifier | Every 300 h of fan runtime or 4 weeks, whichever comes first, with a **Fan Runtime** sensor; every 4 weeks without one. |
| **Clean dust sensor (‹purifier›)** | per purifier with a dust sensor | Every 2 months for the Vital 100S/200S (moistened, then dry cotton swab on the air inlet, outlet and sensor lens, as the label on the purifier says), every 3 months for the Core 300S/400S/600S and EverestAir (vacuum at the sensor openings), as in Levoit's manuals. The Core 200S has no sensor; the Sprout has one, but no Levoit instruction for it was found, so it gets no task for now. |
| **Levoit spare filters** | once for the home | One counted part per filter type. Every filter replacement takes one off the stock, so one buy task covers all purifiers. |
| **Purifier appliance** | per purifier, unless its device page has one | Model details on the purifier's device page, and the fitted filter as a part. Ticking off *Replace filter* stamps its *last replaced* date; the spare is taken from the shared stock only. Enter no stock on this part, or a replacement takes two. Parts you add there yourself stay. |

Everything it creates is marked as managed by *tuct/levoit* and protected against accidental deletion.

### Switching an option off or removing the integration

Both follow Home Keeper's [guidance for companion integrations](https://github.com/prestomation/ha-home-keeper/blob/main/docs/INTEGRATING.md) (§8, *Uninstall*): what the integration created and no longer needs is cleaned up, except what you counted.

- **An appliance with a stock count** entered on one of its parts is handed over to you, so the spare filters in the drawer stay counted. Usually that is *Levoit spare filters*; a purifier appliance only if you added counted parts to it. It becomes an ordinary Home Keeper appliance: no longer managed, free to edit or delete.
- **Every other appliance** it created is deleted.
- **Tasks** the option covers are deleted, including their history: the pre-filter or dust-sensor tasks when that task is switched off, every task when the integration is removed.

What each option does:

| Switched off | Effect |
|---|---|
| Spare-filter stock | The filter tasks are unlinked from the stock, then *Levoit spare filters* is handed over or deleted as above. |
| Purifier appliances | The appliances on the device pages are handed over or deleted as above. |
| Pre-filter task | The pre-filter tasks are deleted. |
| Dust-sensor task | The dust-sensor tasks are deleted. |

Switching an option on again creates what it covers anew. An appliance handed over to you stays yours and is not taken back: with a counted *Levoit spare filters* handed over, you then have two of them, the new managed one and yours to delete.

## Troubleshooting

Turn on debug logging for the integration (**Settings → Devices & services → Home Keeper — tuct/levoit air purifiers → ⋮ → Enable debug logging**) and reload it. The log then shows every purifier found with its entities, each Filter Low change and every call to Home Keeper. A call Home Keeper refuses is logged as a warning (`home_keeper.<service> failed: …`) even without debug logging.

## Releases

The GitHub release tag is the only source of the version; nothing in the repository holds it (`manifest.json` keeps the placeholder `0.0.0`, which Home Assistant needs to load the integration).

1. Create a release on GitHub with a tag such as `v1.2.3`, on a commit that contains the *Release* workflow. Pre-releases get their ZIP the same way; HACS offers them only with **Show beta versions** turned on.
2. The *Release* workflow takes the version from the tag (without the leading `v`; it stops with an error if the tag isn't a version number), writes it into `manifest.json`, packs the integration as `home_keeper_tuct_levoit.zip` and attaches it to the release.
3. HACS installs that ZIP (`zip_release` in `hacs.json`), so Home Assistant shows the release version.

To rebuild the ZIP of an existing release, run the *Release* workflow under **Actions → Release → Run workflow** with the release's tag.

Good to know:

- HACS installs **only from releases** (their ZIP). As long as there is no full release (only pre-releases or none), HACS installs fail.
- The ZIP is attached about a minute after the release is published. An update started in that minute fails; just try again.
- The workflow stops if the tag isn't a version Home Assistant accepts, so a release can't ship an integration that Home Assistant refuses to load.

## Development

```bash
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```

The tests run in a real Home Assistant test instance (`pytest-homeassistant-custom-component`), end to end against the real Home Keeper. They need Home Keeper in `custom_components/home_keeper`; CI copies a pinned release there (see `.github/workflows/validate.yml`), copy it there locally too. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT License, see [LICENSE](LICENSE).

This project is not affiliated with Home Assistant, the Open Home Foundation, Levoit, the tuct/levoit project or Home Keeper.
