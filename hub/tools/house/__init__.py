"""Home: the house through Home Assistant, by way of the HA broker. Read-only for now (docs/home-control.md)."""
from . import pages

TOOL = {"slug": "house", "name": "Home", "description": "Lights, plugs, speakers, thermostat and locks", "router": pages.router}
