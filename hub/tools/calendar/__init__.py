"""Read-only view of the owner's own Google Calendar, through the local helper."""
from .pages import router

TOOL = {"slug": "calendar", "name": "Calendar", "description": "The next seven days from your Google Calendar", "router": router}
