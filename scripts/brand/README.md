# Brand icon

`icon.svg` is the source of the icon in `custom_components/home_keeper_tuct_levoit/brand/`
(`icon.png` 256×256, `icon@2x.png` and `logo.png` 512×512), which Home Assistant shows
on the integration page.

## Origin

The icon was drawn from scratch for this project, as hand-written SVG from basic shapes:
a tower air purifier with clean air rising from its outlet, and a darker inset panel on
its front holding three filter levels, one of them low. It shares the blue gradient and
the level bars with the icons of
[ha-home-keeper-ipp](https://github.com/financelurker/ha-home-keeper-ipp) and
[ha-home-keeper-valetudo](https://github.com/financelurker/ha-home-keeper-valetudo). No
template, icon set, stock image, Levoit artwork or other third-party artwork was used.

## Changing it

Edit `icon.svg`, then render the PNGs (Playwright with Chromium) and commit both:

```bash
cd scripts/brand
node render.js
```

`CHROME_PATH=/path/to/chrome node render.js` uses an installed Chrome instead of
Playwright's own.

The source stays outside the integration folder, so it is not part of the release ZIP.
