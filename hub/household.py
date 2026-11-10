"""Owner-specific settings, read at run time from the private data directory.

Real names, places, school details and similar household facts never live in Git. They live in
`household.json` inside `config.DATA_DIR` (owner-only). Each tool reads its own section and falls
back to neutral defaults, so a checkout without the file runs, and anything that would need a real
value (for example fetching school pages) stays off.
"""
import json

from . import config

FILE = "household.json"


def load():
    try:
        value = json.loads((config.DATA_DIR / FILE).read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{FILE} must hold a JSON object")
    return value


def section(name):
    value = load().get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"{FILE}: '{name}' must be an object")
    return value
