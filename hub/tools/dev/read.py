"""What the Dev page shows, read at request time. Nothing here is kept in Hub.

The paths come from the private `household.json` → `dev` (docs/dev-page.md): the home repo, whose
README says how the owner works and whose AGENTS.md lists the repositories, and the Manager's brief,
whose "Waiting on the owner" list is shown as it stands. Git and tmux are only asked, never told:
status, log, branch and worktree list, with a timeout and no optional locks; never fetch or pull.
"""
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from markupsafe import Markup, escape

from ... import household
from ...engine import release
from ..gmail.common import age_label

TIMEOUT = 5
SESSION = 'manager'
TMUX_PLACES = ('/opt/homebrew/bin/tmux', '/usr/local/bin/tmux', '/usr/bin/tmux')
POINTER = re.compile(r'- \*\*(?P<name>.+?)\*\*(?:\s*\((?P<about>[^)]*)\))?.*?`(?P<path>~/[^`]+)`')
ITEM = re.compile(r'^(\d+)\.\s+(.*)')


def settings():
    """{'home_repo', 'manager_brief'}: a Path each, or None when not set. 'session': the tmux session."""
    dev = household.section('dev')
    def place(key):
        value = dev.get(key)
        return Path(value).expanduser() if isinstance(value, str) and value.strip() else None
    session = dev.get('tmux_session')
    return {'home_repo': place('home_repo'), 'manager_brief': place('manager_brief'),
            'session': session if isinstance(session, str) and session.strip() else SESSION}


def _text(path):
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError, AttributeError):
        return None


def section(text, heading):
    """The lines under `## heading`, up to the next heading of that level."""
    lines, inside = [], False
    for line in (text or '').splitlines():
        if line.startswith('## '):
            if inside:
                break
            inside = line[3:].strip().casefold() == heading.casefold()
            continue
        if inside:
            lines.append(line)
    return lines


def numbered(lines):
    """The items of a numbered Markdown list, each with its continuation lines joined."""
    items = []
    for line in lines:
        match = ITEM.match(line)
        if match:
            items.append(match.group(2).strip())
        elif items and line.startswith((' ', '\t')) and line.strip():
            items[-1] += ' ' + line.strip()
        elif items and not line.strip():
            continue
        elif items:
            break
    return items


def inline(text):
    """Markdown's `code` and **bold** as HTML; everything else escaped."""
    html = str(escape(text))
    html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    return Markup(html)


def pointers(text):
    """[(name, about, path)] for the bullets under "Other Repos" in the home repo's AGENTS.md."""
    found = []
    for line in section(text, 'Other Repos'):
        match = POINTER.match(line)
        if match:
            found.append((match['name'], (match['about'] or '').strip(), match['path']))
    return found


def _run(args, cwd=None):
    """stdout of a read-only command, or None when it fails or takes too long."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', LC_ALL='C')
    try:
        done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _git(path, *args):
    return _run(['git', '-C', str(path), *args])


def repository(name, about, shown):
    """One row of the Projects table. 'missing' when the folder isn't a Git checkout."""
    path = Path(shown).expanduser()
    row = {'name': name, 'about': about, 'missing': True}
    branch = _git(path, 'rev-parse', '--abbrev-ref', 'HEAD') if path.is_dir() else None
    if branch is None:
        return row
    row.update(missing=False, branch=branch.strip(), subject='', when='')
    last = _git(path, 'log', '-1', '--format=%s%x00%ct')
    if last and '\0' in last:
        subject, stamp = last.rstrip('\n').split('\0', 1)
        row.update(subject=subject[:120], when=age_label(int(stamp)) if stamp.isdigit() else '')
    branches = (_git(path, 'for-each-ref', '--format=%(refname:short)', 'refs/heads') or '').split()
    row['others'] = [b for b in branches if b != row['branch']]
    status = _git(path, 'status', '--porcelain')
    row['changes'] = None if status is None else bool(status.strip())
    worktrees = _git(path, 'worktree', 'list', '--porcelain')
    row['worktrees'] = None if worktrees is None else max(0, sum(line.startswith('worktree ') for line in worktrees.splitlines()) - 1)
    if name == 'hub':
        row['release'] = {'on': release.folder() is not None, 'running': release.running(),
                          'pending': release.pending(), 'last': release.last()}
    return row


def projects(home_repo):
    """None when the home repo isn't configured or readable; else one row per "Other Repos" pointer."""
    text = _text(home_repo / 'AGENTS.md') if home_repo else None
    if text is None:
        return None
    found = pointers(text)
    with ThreadPoolExecutor(max_workers=max(1, min(6, len(found)))) as pool:
        return list(pool.map(lambda p: repository(*p), found))


def how(home_repo):
    """The numbered steps of "How the owner works" in the home repo's README, as HTML; None when unreadable."""
    text = _text(home_repo / 'README.md') if home_repo else None
    if text is None:
        return None
    return [inline(item) for item in numbered(section(text, 'How the owner works'))]


def waiting(brief):
    """The "Waiting on the owner" list from the Manager's brief, as HTML; None when unreadable."""
    text = _text(brief)
    if text is None:
        return None
    return [inline(item) for item in numbered(section(text, 'Waiting on the owner'))]


def _tmux():
    return shutil.which('tmux') or next((p for p in TMUX_PLACES if Path(p).is_file()), None)


def _claude_under(pids):
    """The pane pids that have a process named claude somewhere beneath them."""
    table = _run(['ps', '-A', '-o', 'pid=,ppid=,comm='])
    if table is None:
        return set()
    children, names = {}, {}
    for line in table.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(parts[1], []).append(parts[0])
            names[parts[0]] = Path(parts[2].strip()).name
    found = set()
    for pid in pids:
        stack, seen = list(children.get(pid, [])), set()
        while stack:
            child = stack.pop()
            if child in seen:
                continue
            seen.add(child)
            if names.get(child) == 'claude':
                found.add(pid)
                break
            stack.extend(children.get(child, []))
    return found


def windows(session=SESSION, now=None):
    """The session's windows: [{'index', 'name', 'claude', 'active'}]; None when tmux can't be reached."""
    binary = _tmux()
    if not binary:
        return None
    out = _run([binary, 'list-windows', '-t', session, '-F', '#{window_index}\t#{window_name}\t#{window_activity}\t#{pane_pid}'])
    if out is None:
        return None
    rows = [line.split('\t') for line in out.splitlines() if line.count('\t') == 3]
    busy = _claude_under({pid for *_, pid in rows})
    now = now or time.time()
    return [{'index': index, 'name': name, 'claude': pid in busy,
             'active': age_label(int(stamp), now) if stamp.isdigit() else ''} for index, name, stamp, pid in rows]


def page():
    """Everything the Dev page shows. Never raises for a missing file, repo or tmux."""
    where = settings()
    with ThreadPoolExecutor(max_workers=2) as pool:
        live = pool.submit(windows, where['session'])
        rows = projects(where['home_repo'])
        return {'configured': where['home_repo'] is not None, 'brief_configured': where['manager_brief'] is not None,
                'how': how(where['home_repo']), 'windows': live.result(), 'projects': rows,
                'waiting': waiting(where['manager_brief']), 'session': where['session']}
