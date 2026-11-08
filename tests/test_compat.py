"""Keys that existing installations depend on must not change.

The glue finds its tasks and appliances again through the keys it writes into Home
Keeper's ``source``. Changing the domain or a namespace would make an update lose the
tasks, their history and the spare-filter counts.
"""

from __future__ import annotations

from custom_components.home_keeper_tuct_levoit import const


def test_domain_and_namespaces() -> None:
    assert const.DOMAIN == "home_keeper_tuct_levoit"
    assert const.SOURCE_NS == "home_keeper_tuct_levoit"
    assert const.ORIGIN == "home_keeper_tuct_levoit"


def test_roles_and_marks() -> None:
    # Written into every task's and appliance's source namespace.
    assert (const.ROLE_FILTER, const.ROLE_PREFILTER) == ("filter", "prefilter")
    assert (const.ROLE_PURIFIER, const.ROLE_SPARES) == ("purifier", "spares")
    assert const.ROLE_SENSOR == "sensor"
    assert (const.MARK_PAUSED, const.MARK_PENDING) == ("paused", "pending_reset")


def test_option_keys() -> None:
    # Stored in the config entry's options.
    assert [
        const.OPT_STOCK_ENABLED,
        const.OPT_APPLIANCES_ENABLED,
        const.OPT_RESET_ON_DONE,
        const.OPT_PREFILTER_ENABLED,
        const.OPT_PREFILTER_HOURS,
        const.OPT_PREFILTER_WEEKS,
        const.OPT_SENSOR_ENABLED,
        const.OPT_FILTER_NAMES,
        const.OPT_DEVICE_FILTERS,
        const.OPT_ENTITIES,
    ] == [
        "stock_enabled",
        "appliances_enabled",
        "reset_on_done",
        "prefilter_enabled",
        "prefilter_hours",
        "prefilter_weeks",
        "sensor_enabled",
        "filter_names",
        "device_filters",
        "entities",
    ]


def test_spare_part_names_are_stable() -> None:
    # A part name is the key of the counted stock: renaming one loses the count.
    from custom_components.home_keeper_tuct_levoit.catalog import MODELS

    assert {k: m.filter_part for k, m in MODELS.items()} == {
        "core200s": "Core 200S-RF",
        "core300s": "Core 300-RF",
        "core400s": "Core 400S-RF",
        "core600s": "Core 600S-RF",
        "vital100s": "Vital 100S-RF",
        "vital200s": "Vital 200S-RF",
        "everestair": "EverestAir filter",
        "sprout": "Sprout filter",
    }
