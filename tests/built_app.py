"""The app as built into Hub's package (hub/static/app), for tests that ask what it shows. The installed package ships
the built app, not web/app's source, so a test that reads the source passes here and fails installed."""
import re
from pathlib import Path

import hub


def in_system(name, href):
    """Whether the app's System lists a page by this name and address."""
    built = (Path(hub.__file__).parent / 'static/app/app.js').read_text()
    return re.search(rf'["\'`]{re.escape(name)}["\'`],\s*["\'`]{re.escape(href)}["\'`]', built) is not None
