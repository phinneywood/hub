"""Household kitchen chat: a front end for the separate kitchen service (see its repository)."""
from .pages import router

TOOL = {"slug": "kitchen", "name": "Kitchen", "description": "Shared grocery list, recipes and the Fred Meyer cart",
        "router": router}
