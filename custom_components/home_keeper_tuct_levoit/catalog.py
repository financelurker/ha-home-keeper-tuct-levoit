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
"""

from __future__ import annotations

from dataclasses import dataclass
import re

WASH = "wash"
VACUUM = "vacuum"


@dataclass(frozen=True, slots=True)
class LevoitModel:
    """One purifier model."""

    key: str
    label: str
    filter_part: str
    prefilter: str  # WASH or VACUUM


MODELS: dict[str, LevoitModel] = {
    m.key: m
    for m in (
        LevoitModel("core200s", "Core 200S", "Core 200S-RF", VACUUM),
        LevoitModel("core300s", "Core 300S", "Core 300-RF", VACUUM),
        LevoitModel("core400s", "Core 400S", "Core 400S-RF", VACUUM),
        LevoitModel("core600s", "Core 600S", "Core 600S-RF", VACUUM),
        LevoitModel("vital100s", "Vital 100S", "Vital 100S-RF", WASH),
        LevoitModel("vital200s", "Vital 200S", "Vital 200S-RF", WASH),
        LevoitModel("everestair", "EverestAir", "EverestAir filter", WASH),
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
