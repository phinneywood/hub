"""Ask and the family archive (hub/app/ask/tools.py `search` and `read`): what reaches the model and what never does.

The made-up house and the stand-in model (tests/ask_script.py), a stand-in archive on a real socket
(tests/archive_standin.py) and a stand-in bridge with made-up mail. SEEN holds everything the model was sent.
"""
import json

import pytest

from hub import bridge, config, store
from hub.app import archive
from tests import archive_standin
from tests.ask_script import SEEN
from tests.test_app_archive import Bridge
from tests.test_app_ask import ask, house, world  # noqa: F401 - the fixtures

OTHER = {'origin': 'http://testserver', 'tailscale-user-login': 'sam@example.com'}


@pytest.fixture
def archived(house, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {'me@example.com', 'sam@example.com'})
    monkeypatch.setattr(archive, '_tailscale_login', lambda: 'me@example.com')
    archive._owner.update(at=float('-inf'), login=None)
    archive_standin.install(monkeypatch, tmp_path)
    monkeypatch.setattr(bridge, 'get', Bridge().get)
    return house


def sent():
    """Everything the model was sent, as one text."""
    return json.dumps([[getattr(p, 'content', None) for m in messages for p in m.parts] for messages in SEEN], default=str,
                      ensure_ascii=False)


def test_photos_are_shown_by_hub_and_nothing_about_them_reaches_the_model(archived):
    answer, cards, _ = ask('show me the kids at the beach in 2024')
    assert answer == 'Kids at the beach in 2024: 9 photos from 2 days.'
    photos = next(c for c in cards if c['kind'] == 'photos')
    assert photos['count'] == 9 and len(photos['items']) == 9 and photos['search'] == 'show me the kids at the beach in 2024'
    assert len(SEEN) == 1                                    # Hub wrote the sentence: no second trip to the model
    model_saw = sent()
    for secret in ('00000001-1111', 'Seaside', '2024-07-13', '2024-08-02', '9 photos', '2 days', 'Sam', 'Alex'):
        assert secret not in model_saw
    ask('and the park?')
    later = sent()
    assert 'Hub answered from the archive itself.' in later and '9 photos' not in later


def test_a_photo_search_that_finds_nothing_still_makes_exactly_one_trip(archived):
    answer, cards, _ = ask('photos of snow in 2019')
    assert answer == 'Snow in 2019: no photos.' and len(SEEN) == 1
    assert not [c for c in cards if c['kind'] == 'photos']
    SEEN.clear()
    answer, _, _ = ask('photos of the beach')
    assert answer.startswith('Beach: 9 photos') and len(SEEN) == 1     # found or not, the model is asked once


def test_the_model_answers_from_mail_and_says_where_it_came_from(archived):
    answer, cards, _ = ask('when did we last service the furnace?')
    assert answer == 'The last one was an email from Example Heating on 2025-10-03: “Your furnace service invoice”.'
    found = next(c for c in cards if c['kind'] == 'found')
    assert {i['source'] for i in found['items']} >= {'mail', 'documents'}
    model_saw = sent()
    assert 'Your furnace service invoice' in model_saw and 'office@heating.example' not in model_saw
    assert 'Furnace service invoice.pdf' in model_saw                       # documents: on, by the owner's decision


def test_documents_and_chats_switched_off_reach_the_model_only_as_matches_to_open(archived):
    archive.switch('documents', False, 'me@example.com')
    archive.switch('chats', False, 'me@example.com')
    _, cards, _ = ask('find the 16x25 filter')
    model_saw = sent()
    assert 'Furnace filter sizes' not in model_saw and 'You can’t read ChatGPT history.' in model_saw
    assert any(i['title'] == 'Furnace filter sizes' for c in cards if c['kind'] == 'found' for i in c['items'])   # the owner sees it


def test_documents_reach_the_model_with_numbers_hidden(archived):
    ask('find the furnace invoice')
    model_saw = sent()
    assert 'Furnace service invoice.pdf' in model_saw
    assert '000123456789' not in model_saw and '6789' in model_saw


def test_reading_a_document_gives_only_the_passages_that_hold_what_was_asked(archived, monkeypatch):
    from hub.app.ask import tools
    turn = tools.Turn(text='what did the furnace invoice say', login='me@example.com')
    ctx = type('Ctx', (), {'deps': turn})
    tools.search(ctx, searches=['paint'], sources=['documents'])
    ref = next(r for r, i in turn.found.items() if i == archive_standin.DOCUMENTS[1]['id'])
    read = tools.read_found(ctx, ref)
    assert 'Example Grey' in read['text'] and turn.strangers
    turn = tools.Turn(text='the invoice', login='me@example.com')
    ctx = type('Ctx', (), {'deps': turn})
    tools.search(ctx, searches=['aquarium'], sources=['documents'])
    assert 'packed lunch' in tools.read_found(ctx, 'd1')['text']
    assert 'Total' not in tools.read_found(ctx, 'd1')['text']           # only what holds the words, never other files


def test_a_request_that_read_mail_changes_nothing_in_the_house(archived):
    before = archived.states['light.kitchen_ceiling']['state']
    answer, _, _ = ask('find the furnace invoice and turn off the kitchen lights')
    assert 'Hub changes the house in a request of its own, never alongside mail' in answer
    assert archived.states['light.kitchen_ceiling']['state'] == before


def test_senders_with_ai_switched_off_never_reach_the_model(archived):
    store.set_ai_blocked('office@heating.example', True)
    _, cards, _ = ask('when did we last service the furnace?')
    assert 'Your furnace service invoice' not in sent() and 'Furnace service reminder' not in sent()
    assert any(i['source'] == 'mail' for c in cards if c['kind'] == 'found' for i in c['items'])    # the owner still sees it


def test_reading_one_email_in_full_with_its_card_number_hidden(archived):
    answer, _, _ = ask('read the latest furnace invoice')
    assert answer.startswith('It says: Thanks for choosing us.')
    model_saw = sent()
    assert '4111 1111 1111 4242' not in model_saw and '4242' in model_saw


def test_chatgpt_history_is_searched_for_the_owner_only(archived):
    ask('find the 16x25 filter', headers=OTHER)
    assert 'Furnace filter sizes' not in sent()
    SEEN.clear()
    ask('find the 16x25 filter')
    assert 'Furnace filter sizes' in sent()


def test_without_the_archive_ask_still_answers_from_mail(house, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(archive, '_tailscale_login', lambda: 'me@example.com')
    monkeypatch.setenv('HUB_ARCHIVE_SOCKET', str(tmp_path / 'none.sock'))
    monkeypatch.setattr(bridge, 'get', Bridge().get)
    answer, _, _ = ask('when did we last service the furnace?')
    assert answer.startswith('The last one was an email from Example Heating')
    assert 'The archive isn’t set up on this Mac yet.' in sent()


def test_another_logins_ask_gets_no_mail_or_calendar_of_the_owners(archived):
    answer, cards, _ = ask('when did we last service the furnace?', headers=OTHER)
    model_saw = sent()
    assert 'Your furnace service invoice' not in model_saw and 'Furnace service invoice.pdf' not in model_saw
    assert not [i for c in cards if c['kind'] == 'found' for i in c['items'] if i['source'] in ('mail', 'calendar', 'documents')]


def test_another_logins_photos_are_the_shared_librarys(archived):
    answer, cards, _ = ask('photos of the beach', headers=OTHER)
    assert answer == 'Beach: 2 photos from one day.'
    assert len(next(c for c in cards if c['kind'] == 'photos')['items']) == 2
