# Home Keeper — tuct/levoit air purifiers

[![Validate](https://github.com/financelurker/ha-home-keeper-tuct-levoit/actions/workflows/validate.yml/badge.svg)](https://github.com/financelurker/ha-home-keeper-tuct-levoit/actions/workflows/validate.yml)

Keeps the filters of Levoit air purifiers running the [tuct/levoit](https://github.com/tuct/levoit) ESPHome firmware in [Home Keeper](https://github.com/prestomation/ha-home-keeper): a *"Replace filter"* task raised by the purifier's **Filter Low**, a *"Clean pre-filter"* task by fan runtime or weeks, and one shared stock of spare filters for the whole home.

It is a small *glue* integration in the sense of Home Keeper's [glue pattern](https://github.com/prestomation/ha-home-keeper/blob/main/docs/GLUE_INTEGRATIONS.md): it changes neither the purifier firmware nor Home Keeper and talks to both only through the registries, entity states, services and events.

> **Work in progress.** This version sets up the integration only; the purifier handling, the options and the ESPHome configuration guide follow in the next releases.

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

The tests run in a real Home Assistant test instance (`pytest-homeassistant-custom-component`), against Home Keeper's test fake. They need Home Keeper in `custom_components/home_keeper`; CI copies a pinned release there (see `.github/workflows/validate.yml`), locally those tests are skipped without it. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT License, see [LICENSE](LICENSE).

This project is not affiliated with Home Assistant, the Open Home Foundation, Levoit, the tuct/levoit project or Home Keeper.
