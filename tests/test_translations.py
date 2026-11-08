"""strings.json mirrors en.json, and de.json has the same keys."""

from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).parents[1] / "custom_components" / "home_keeper_tuct_levoit"


def _keys(data: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for key, value in data.items():
        out |= _keys(value, f"{prefix}{key}.") if isinstance(value, dict) else {prefix + key}
    return out


def test_translations_match() -> None:
    strings = json.loads((BASE / "strings.json").read_text())
    en = json.loads((BASE / "translations" / "en.json").read_text())
    de = json.loads((BASE / "translations" / "de.json").read_text())
    assert en == strings
    assert _keys(de) == _keys(en)
