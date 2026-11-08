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
