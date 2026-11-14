"""Home Assistant and Music Assistant are one tap away: All tools entries and page shortcuts, each opening in a
new tab, from addresses in the private household.json only. Made-up addresses; nothing is framed."""
import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from hub import config, home
from hub.main import app
from hub.tools.house import broker, read

HA = 'https://ha.example.test:9443'
MA = 'https://music.example.test:8095'
TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / 'hub' / 'templates'


def settings(**home_section):
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'home': home_section}))


def links_to(page, href):
    """Every <a> tag on the page that points at href."""
    return re.findall(r'<a [^>]*href="' + re.escape(href) + r'"[^>]*>', page)


@pytest.fixture
def client():
    return TestClient(app)


def test_all_tools_lists_both_and_they_open_in_a_new_tab(client):
    settings(ha_url=HA, music_url=MA)
    page = client.get('/tools').text
    for href, name in ((HA, 'Home Assistant'), (MA, 'Music Assistant')):
        [tag] = links_to(page, href)
        assert 'target="_blank"' in tag and 'rel="noopener"' in tag
        assert f'<strong>{name}</strong>' in page
    assert page.count('class="tool-ico"') >= 2 and '<iframe' not in page


def test_an_address_that_isnt_set_hides_its_link(client):
    settings(music_url=MA)
    page = client.get('/tools').text
    assert 'Home Assistant' not in page and links_to(page, MA)
    settings(ha_url='javascript:alert(1)')
    page = client.get('/tools').text
    assert 'Home Assistant' not in page and 'Music Assistant' not in page and 'javascript:' not in page
    settings()
    assert read.elsewhere() == [] and read.links() == {}


def test_the_older_key_still_counts_and_the_new_one_wins():
    settings(assistant_url='https://old.example.test')
    assert read.links() == {'ha_url': 'https://old.example.test'}
    settings(assistant_url='https://old.example.test', ha_url=HA)
    assert read.links() == {'ha_url': HA}


def test_home_and_playlists_pages_have_the_shortcuts(client, monkeypatch):
    settings(ha_url=HA, music_url=MA)
    changes = client.get('/house/changes').text
    [ha] = links_to(changes, HA)
    assert 'target="_blank"' in ha and 'rel="noopener"' in ha and 'Open Home Assistant' in changes
    assert 'Open in Music Assistant' in changes
    playlists = client.get('/house/playlists').text
    [ma] = links_to(playlists, MA)
    assert 'target="_blank"' in ma and 'rel="noopener"' in ma and 'Open in Music Assistant' in playlists
    settings()
    assert 'Open Home Assistant' not in client.get('/house/changes').text
    assert 'Open in Music Assistant' not in client.get('/house/playlists').text


def test_the_home_card_on_today_opens_home_assistant_in_a_new_tab(client, monkeypatch):
    settings(ha_url=HA)

    def down(message, timeout):
        raise broker.HomeDown('The Home Assistant broker on this Mac isn’t answering.')
    monkeypatch.setattr(broker, '_exchange', down)
    read.clear_cache()
    tile = home.house()
    assert (tile['href'], tile['cta']) == (HA, 'Open Home Assistant')
    monkeypatch.setattr(home, 'tiles', lambda tools: [dict(tile, name='Home')])
    page = client.get('/today').text
    assert links_to(page, HA) and all('target="_blank"' in t and 'rel="noopener"' in t for t in links_to(page, HA))
    assert all('target=' not in t for t in re.findall(r'<a [^>]*href="/[^"]*"[^>]*>', page))     # Hub's own pages stay


def test_no_template_frames_another_site():
    assert not [p.name for p in TEMPLATES.rglob('*.html') if '<iframe' in p.read_text()]
