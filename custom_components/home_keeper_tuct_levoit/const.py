"""Constants for the Home Keeper ↔ tuct/levoit glue integration.

The glue finds Levoit air purifiers that run the ESPHome firmware of
https://github.com/tuct/levoit and keeps their filter tasks and one shared spare-filter
stock in Home Keeper. It talks to both sides only through the registries, entity states,
services and events, never by importing them.

This file starts with the keys that existing installations will depend on; the decision
logic lands in later pull requests.
"""

from __future__ import annotations

DOMAIN = "home_keeper_tuct_levoit"

# Our namespace in a Home Keeper task's / appliance's ``source``, and the ``origin`` we
# send on completions we make ourselves, so our listener can drop the echo.
SOURCE_NS = DOMAIN
ORIGIN = DOMAIN

HK_DOMAIN = "home_keeper"
ESPHOME_DOMAIN = "esphome"

TITLE = "tuct/levoit air purifiers"
