"""The Dev page reads the home repo, tmux and Git at request time, and shows "not configured" without them.

Every repository here is a throwaway Git repo under tmp_path; nothing reads the real ~/Developer.
"""
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from hub import config
from hub.main import app
from hub.tools.dev import read

README = """# Home repo

## How the owner works

1. **Talk to the Manager.** One session in tmux `manager`.
   Reach it from the app.
2. **Say "go".** <script>x</script> stays text.

## Keys

| Key | What |
| --- | --- |
| `Ctrl-b w` | See all work |
| `Ctrl-b 1` | Go to the **Manager** <b>now</b> |

## Layout

1. Not this list.
"""

AGENTS = """# Home repo

## Other Repos

- **alpha** (Alex's demo service; source is public-ready): `~/code/alpha`. Read its AGENTS.md first.
- **beta** (Sam's notes): `~/code/beta`.
- **gone** (moved away): `~/code/gone`.

## Other folders

- **scratch** (not a repo): `~/code/scratch`.
"""

BRIEF = """# Brief

## Waiting on the owner

1. Pick a name for the **demo** app.
2. Approve `beta` going public.

## Done

1. Old item.
"""


def _git(path, *args):
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=Alex', '-c', 'user.email=alex@example.com', *args],
                   check=True, capture_output=True)


@pytest.fixture
def world(tmp_path, monkeypatch):
    try:    # these tests make real throwaway repositories; the installed-package check forbids running programs
        subprocess.run(['git', '--version'], check=True, capture_output=True)
    except (AssertionError, OSError, subprocess.SubprocessError):
        pytest.skip('needs to run git, which this run does not allow')
    home = tmp_path / 'home'
    monkeypatch.setenv('HOME', str(home))   # Git's own config and every ~ path stay in tmp_path
    repo = home / 'code' / 'home-repo'
    repo.mkdir(parents=True)
    (repo / 'README.md').write_text(README)
    (repo / 'AGENTS.md').write_text(AGENTS)
    alpha, beta = home / 'code' / 'alpha', home / 'code' / 'beta'
    for path in (alpha, beta):
        path.mkdir(parents=True)
        _git(path, 'init', '-q', '-b', 'main')
        (path / 'a.txt').write_text('one')
        _git(path, 'add', 'a.txt')
        _git(path, 'commit', '-q', '-m', f'First commit in {path.name}')
    _git(alpha, 'branch', 'feature-x')
    _git(alpha, 'worktree', 'add', '-q', str(tmp_path / 'wt'), 'feature-x')
    (beta / 'a.txt').write_text('changed')
    brief = tmp_path / 'brief.md'
    brief.write_text(BRIEF)
    (config.DATA_DIR / 'household.json').write_text(json.dumps(
        {'dev': {'home_repo': str(repo), 'manager_brief': str(brief), 'tmux_session': 'demo'}}))
    monkeypatch.setattr(read, 'windows', lambda session='manager', now=None: [
        {'index': '1', 'name': 'manager', 'claude': True, 'active': 'just now'},
        {'index': '2', 'name': 'demo-task', 'claude': False, 'active': '3 hr ago'}])
    return home


def test_sections_and_lists_are_read_from_markdown():
    assert read.numbered(read.section(README, 'How the owner works')) == [
        '**Talk to the Manager.** One session in tmux `manager`. Reach it from the app.',
        '**Say "go".** <script>x</script> stays text.']
    assert read.numbered(read.section(BRIEF, 'Waiting on the owner'))[1] == 'Approve `beta` going public.'
    assert read.table(read.section(README, 'Keys')) == [['`Ctrl-b w`', 'See all work'],
                                                         ['`Ctrl-b 1`', 'Go to the **Manager** <b>now</b>']]
    assert str(read.inline('**a** `b` <i>')) == '<strong>a</strong> <code>b</code> &lt;i&gt;'
    assert read.pointers(AGENTS) == [('alpha', "Alex's demo service; source is public-ready", '~/code/alpha'),
                                     ('beta', "Sam's notes", '~/code/beta'), ('gone', 'moved away', '~/code/gone')]


def test_projects_show_each_repositorys_git_state(world):
    rows = {row['name']: row for row in read.projects(world / 'code' / 'home-repo')}
    assert rows['alpha']['branch'] == 'main' and rows['alpha']['others'] == ['feature-x']
    assert rows['alpha']['subject'] == 'First commit in alpha' and rows['alpha']['when'] == 'just now'
    assert rows['alpha']['changes'] is False and rows['alpha']['worktrees'] == 1
    assert rows['beta']['changes'] is True and rows['beta']['worktrees'] == 0 and rows['beta']['others'] == []
    assert rows['gone']['missing'] is True


def test_git_is_only_asked_never_told(world, monkeypatch):
    seen = []
    real = read._run
    monkeypatch.setattr(read, '_run', lambda args, cwd=None: seen.append(args) or real(args, cwd))
    read.projects(world / 'code' / 'home-repo')
    verbs = {args[3] for args in seen if args[0] == 'git'}
    assert verbs <= {'rev-parse', 'log', 'for-each-ref', 'status', 'worktree'}
    assert all(args[4:6] == ['list', '--porcelain'] for args in seen if args[3] == 'worktree')


def test_the_page_shows_all_four_sections(world):
    page = TestClient(app).get('/dev').text
    assert '<strong>Talk to the Manager.</strong>' in page and '&lt;script&gt;' in page and 'Not this list' not in page
    assert '<td><code>Ctrl-b w</code></td><td>See all work</td>' in page
    assert 'Go to the <strong>Manager</strong> &lt;b&gt;now&lt;/b&gt;' in page
    assert 'demo-task' in page and 'Claude running' in page
    assert 'First commit in alpha' in page and 'feature-x' in page and 'uncommitted changes' in page
    assert 'Folder missing or not a Git checkout' in page
    assert 'Pick a name for the <strong>demo</strong> app.' in page and 'Old item' not in page


def test_without_settings_the_page_says_not_configured(monkeypatch):
    monkeypatch.setattr(read, 'windows', lambda session='manager', now=None: None)
    page = TestClient(app).get('/dev')
    assert page.status_code == 200
    assert page.text.count('Not configured') == 3 and 'Not available' in page.text


def test_tmux_out_of_reach_is_not_available(monkeypatch):
    monkeypatch.setattr(read, '_tmux', lambda: None)
    assert read.windows() is None
    monkeypatch.setattr(read, '_tmux', lambda: 'tmux')
    monkeypatch.setattr(read, '_run', lambda args, cwd=None: None)
    assert read.windows() is None


def test_tmux_windows_and_claude_beneath_them(monkeypatch):
    out = {'list-windows': '1\tmanager\t1000\t10\n2\tdemo-task\t400\t20\n',
           'ps': '10 1 /bin/zsh\n11 10 claude\n20 1 /bin/zsh\n21 20 vim\n'}
    monkeypatch.setattr(read, '_tmux', lambda: 'tmux')
    monkeypatch.setattr(read, '_run', lambda args, cwd=None: out['ps' if args[0] == 'ps' else args[1]])
    assert read.windows('demo', now=1060) == [{'index': '1', 'name': 'manager', 'claude': True, 'active': 'just now'},
                                              {'index': '2', 'name': 'demo-task', 'claude': False, 'active': '11 min ago'}]


def test_dev_is_in_all_tools_but_not_a_card_on_today(world):
    client = TestClient(app)
    assert 'href="/dev"' in client.get('/tools').text
    today = client.get('/today').text
    assert 'href="/dev"' not in today and 'windows in progress' not in today
