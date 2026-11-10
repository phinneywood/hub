"""Read-only Home Assistant network and mesh dashboard."""
from . import pages

TOOL = {"slug": "network", "name": "Network", "description": "Live home network, traffic history and Deco clients", "router": pages.router}
