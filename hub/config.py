import os
from pathlib import Path

BRIDGE_URL = os.environ.get("HUB_BRIDGE_URL", "http://127.0.0.1:8765")
BRIDGE_TOKEN_FILE = Path(os.environ.get(
    "HUB_BRIDGE_TOKEN_FILE", Path.home() / "Developer" / "mac-mini" / "private" / "bridge-token"))
ALLOWED_LOGINS = {x.strip().lower() for x in os.environ.get("HUB_ALLOWED_LOGINS", "").split(",") if x.strip()}
HOST = "127.0.0.1"
PORT = int(os.environ.get("HUB_PORT", "8780"))

KITCHEN_SOCKET = Path(os.environ.get(
    "HUB_KITCHEN_SOCKET", Path.home() / "Library" / "Application Support" / "Kitchen" / "kitchen.sock"))

DATA_DIR = Path(os.environ.get("HUB_DATA_DIR", Path(__file__).resolve().parent.parent / "var"))


def background_jobs_enabled(value):
    if value not in {"enabled", "disabled"}:
        raise ValueError("HUB_BACKGROUND_JOBS must be enabled or disabled")
    return value == "enabled"


# Preserve the existing production default; invalid explicit settings fail closed.
BACKGROUND_JOBS_ENABLED = background_jobs_enabled(os.environ.get("HUB_BACKGROUND_JOBS", "enabled"))
