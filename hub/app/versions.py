"""Hub's versions: what each release changed, in plain words, for the Versions screen.

The list is `versions.json` beside this file, newest first, written by hand as part of each release (see
"Releasing" in docs/app.md). The newest entry has no `sha` or `live` yet, because a release can't name its own
commit: it is the version being prepared, and it is the running one when Hub runs from a release that the list
doesn't name. The next release fills them in from the deployer's `last.json`.

Links go to the repository named in the private settings file (`"versions": {"repo": "https://github.com/..."}`),
so no account name is in the source. Without it the screen shows the short commit names unlinked.
"""
import json
import re
from pathlib import Path

from ..engine import release

FILE = Path(__file__).resolve().parent / 'versions.json'
REPO = re.compile(r'https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+')


def entries():
    return json.loads(FILE.read_text())


def repo():
    from .. import household
    try:
        found = household.section('versions').get('repo')
    except ValueError:
        return None
    return found.rstrip('/') if isinstance(found, str) and REPO.fullmatch(found.rstrip('/')) else None


def picture():
    """The Versions screen as data: each release, newest first, and which one is running."""
    listed = entries()
    running = release.running()
    known = {e['sha'] for e in listed if e['sha']}
    at = repo()
    out = []
    for entry in listed:
        sha = entry['sha'] or (running if running and running not in known else None)
        out.append({
            'sha': sha, 'short': sha[:7] if sha else None, 'live': entry['live'], 'title': entry['title'],
            'changes': entry['changes'], 'running': bool(sha) and sha == running,
            'commit': f'{at}/commit/{sha}' if at and sha else None,
            'compare': f'{at}/compare/{entry["from"][:12]}...{(sha or "main")[:12]}' if at and entry.get('from') else None,
        })
    return {'versions': out, 'running': running[:7] if running else None, 'repo': at}
