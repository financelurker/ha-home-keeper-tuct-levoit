# Home Keeper — tuct/levoit air purifiers

[![Validate](https://github.com/financelurker/ha-home-keeper-tuct-levoit/actions/workflows/validate.yml/badge.svg)](https://github.com/financelurker/ha-home-keeper-tuct-levoit/actions/workflows/validate.yml)

Keeps the filters of Levoit air purifiers running the [tuct/levoit](https://github.com/tuct/levoit) ESPHome firmware in [Home Keeper](https://github.com/prestomation/ha-home-keeper): a *"Replace filter"* task raised by the purifier's **Filter Low**, a *"Clean pre-filter"* task by fan runtime or weeks, and one shared stock of spare filters for the whole home.

It is a small *glue* integration in the sense of Home Keeper's [glue pattern](https://github.com/prestomation/ha-home-keeper/blob/main/docs/GLUE_INTEGRATIONS.md): it changes neither the purifier firmware nor Home Keeper and talks to both only through the registries, entity states, services and events.

> **Work in progress.** The options page and the ESPHome configuration guide follow in the next releases; until then the defaults below apply.

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

## What it creates in Home Keeper

| | Created | Content |
|---|---|---|
| **Replace filter (‹purifier›)** | per purifier, on its first **Filter Low** | Raised by **Filter Low**, cleared when the purifier reports the filter fine again. Ticking it off presses the purifier's **Filter Reset** button. |
| **Clean pre-filter (‹purifier›)** | per purifier | Every 300 h of fan runtime or 4 weeks, whichever comes first, with a **Fan Runtime** sensor; every 4 weeks without one. |
| **Levoit spare filters** | once for the home | One counted part per filter type. Every filter replacement takes one off the stock, so one buy task covers all purifiers. |
| **Purifier appliance** | per purifier, unless its device page has one | Model details on the purifier's device page, and the fitted filter as a part. Ticking off *Replace filter* stamps its *last replaced* date; the spare is taken from the shared stock only. Enter no stock on this part, or a replacement takes two. Parts you add there yourself stay. |

Everything it creates is marked as managed by *tuct/levoit* and protected against accidental deletion.

### Switching an option off or removing the integration

Both follow Home Keeper's [guidance for companion integrations](https://github.com/prestomation/ha-home-keeper/blob/main/docs/INTEGRATING.md) (§8, *Uninstall*): what the integration created and no longer needs is cleaned up, except what you counted.

- **An appliance with a stock count** entered on one of its parts is handed over to you, so the spare filters in the drawer stay counted. Usually that is *Levoit spare filters*; a purifier appliance only if you added counted parts to it. It becomes an ordinary Home Keeper appliance: no longer managed, free to edit or delete.
- **Every other appliance** it created is deleted.
- **Tasks** the option covers are deleted, including their history: the pre-filter tasks when the pre-filter task is switched off, every task when the integration is removed.

What each option does:

| Switched off | Effect |
|---|---|
| Spare-filter stock | The filter tasks are unlinked from the stock, then *Levoit spare filters* is handed over or deleted as above. |
| Purifier appliances | The appliances on the device pages are handed over or deleted as above. |
| Pre-filter task | The pre-filter tasks are deleted. |

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
