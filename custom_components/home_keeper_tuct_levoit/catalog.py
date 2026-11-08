"""The Levoit models the tuct/levoit ESPHome component supports.

The key is the model part of the ESPHome project name with every character but letters
and digits removed: ``tuct.levoit-vital100s`` → ``vital100s``.

Filter names come from Levoit's replacement filters as sold (researched 2026-11-08):
Core 300-RF, Core 600S-RF and Vital 100S-RF are certain; for Core 200S and Core 400S the
sources disagree on the "S" and the names with it are used. Everest Air and Sprout have
no confirmed part name and get a descriptive one. A filter name is the part's name in
the shared spare-filter stock, so keep it stable.

Every model has a pre-filter to clean every 2-4 weeks (Levoit manuals): the Vital series
and Everest Air have a washable one, the Core series and Sprout one to vacuum only.

The dust sensor behind Auto mode needs cleaning too:

- Vital 100S / 200S (and Pro): an infrared sensor behind a cover. The label on the
  device (photographed on a Vital 100S, 2026-11-08) and the manuals: clean the air
  inlet, outlet and sensor lens with a moistened cotton swab, then a dry one, every
  two months.
- Core 300S / 400S / 600S and EverestAir: a laser sensor. The manuals: hold a vacuum
  to the sensor openings for at least 10 seconds, every three months.
- Core 200S: no air-quality sensor.
- Sprout: it has a particle sensor, but no Levoit cleaning instruction was found, so
  it gets no task until one is.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

WASH = "wash"
VACUUM = "vacuum"
INFRARED = "infrared"
LASER = "laser"
# Months between dust-sensor cleanings, per sensor kind.
SENSOR_MONTHS = {INFRARED: 2, LASER: 3}


@dataclass(frozen=True, slots=True)
class LevoitModel:
    """One purifier model."""

    key: str
    label: str
    filter_part: str
    prefilter: str  # WASH or VACUUM
    sensor: str | None = None  # INFRARED, LASER, or None: no cleaning task


MODELS: dict[str, LevoitModel] = {
    m.key: m
    for m in (
        LevoitModel("core200s", "Core 200S", "Core 200S-RF", VACUUM),
        LevoitModel("core300s", "Core 300S", "Core 300-RF", VACUUM, LASER),
        LevoitModel("core400s", "Core 400S", "Core 400S-RF", VACUUM, LASER),
        LevoitModel("core600s", "Core 600S", "Core 600S-RF", VACUUM, LASER),
        LevoitModel("vital100s", "Vital 100S", "Vital 100S-RF", WASH, INFRARED),
        LevoitModel("vital200s", "Vital 200S", "Vital 200S-RF", WASH, INFRARED),
        LevoitModel("everestair", "EverestAir", "EverestAir filter", WASH, LASER),
        LevoitModel("sprout", "Sprout", "Sprout filter", VACUUM),
    )
}

# tuct builds the Vital 200S Pro from the Vital 200S config. Whether it takes the same
# filter is not confirmed.
ALIASES: dict[str, str] = {"vital200spro": "vital200s"}


def model_key(raw_model: str | None, prefix: str = "levoit") -> str | None:
    """Return the catalog key for a device registry model, if it is a Levoit one.

    ``levoit-vital100s`` → ``vital100s``. An unknown Levoit model still gets a key, so a
    new model works with generic texts before it is added here.
    """
    if not raw_model:
        return None
    slug = re.sub(r"[^a-z0-9]", "", raw_model.lower())
    if not slug.startswith(prefix) or slug == prefix:
        return None
    key = slug[len(prefix) :]
    return ALIASES.get(key, key)


def model_for(key: str) -> LevoitModel:
    """Return the model, or a generic one for a key the catalog does not know."""
    if key in MODELS:
        return MODELS[key]
    label = key.upper()
    return LevoitModel(key, label, f"Levoit {label} filter", VACUUM)
