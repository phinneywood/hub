"""Ask and the family archive (hub/app/ask/tools.py `search` and `read`): what reaches the model and what never does.

The made-up house and the stand-in model (tests/ask_script.py), a stand-in archive on a real socket
(tests/archive_standin.py) and a stand-in bridge with made-up mail. SEEN holds everything the model was sent.
"""
import json

import pytest

from hub import bridge, store
from hub.app import archive
from tests import archive_standin
from tests.ask_script import SEEN
from tests.test_app_archive import Bridge
from tests.test_app_ask import SIGNED_IN, ask, client, house, world  # noqa: F401 - the fixtures

OTHER = {'origin': 'http://testserver', 'tailscale-user-login': 'sam@example.com'}


@pytest.fixture
def archived(house, monkeypatch, tmp_path):  # noqa: F811
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


def test_show_me_people_is_a_photo_search_even_when_other_things_match(archived, monkeypatch):
    from tests.test_app_archive import Bridge, event
    monkeypatch.setattr(bridge, 'get', Bridge({'2024-07': [event('b1', '2024-07-13', 'Beach day')]}).get)
    archive.calendar_collect(pause=0)
    answer, cards, _ = ask('show me the kids at the beach in 2024')
    assert answer.startswith('Kids at the beach in 2024: 9 photos from 2 days.')


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



# ---- the archive tool: Hub answers, and the model learns nothing ----

def curated(tmp_path):
    """A made-up month the curator chose, two of its picks in the Shared Library."""
    here = tmp_path / 'curator'
    (here / 'months').mkdir(parents=True)
    picks = [{'id': f'{n:08X}-0000-4000-8000-000000000000', 'taken': '2026-10-03T12:00:00-07:00', 'people': ['Sam'], 'shared': n < 2}
             for n in range(5)]
    (here / 'months' / '2026-10.json').write_text(json.dumps({'month': '2026-10', 'album': 'October 2026', 'picks': picks, 'more': []}))
    archive_standin.household(photos={'dir': str(here)})


def test_the_months_best_is_hubs_card_and_the_model_hears_a_fixed_line(archived, tmp_path):
    curated(tmp_path)
    answer, cards, _ = ask("show me this month's best photos")
    assert answer == 'The best of October 2026: 5 photos to keep, print or skip.'
    assert cards == [{'kind': 'best', 'month': '2026-10'}] and len(SEEN) == 1
    ask('and on this day?')
    later = sent()
    assert 'October 2026' not in later and '5 photos' not in later and 'Hub answered from the archive itself.' in later


def test_the_months_best_for_another_login_is_the_shared_librarys(archived, tmp_path):
    curated(tmp_path)
    answer, _, _ = ask("show me this month's best photos", headers=OTHER)
    assert answer == 'The best of October 2026: 2 photos to keep, print or skip.'


def test_on_this_day_draws_a_card_and_tells_the_model_nothing(archived):
    answer, cards, _ = ask('photos from on this day')
    assert answer == 'On this day: photos from 1 earlier year.'
    assert cards[0]['kind'] == 'onthisday' and cards[0]['years'][0]['year'] == 2023
    model_saw = sent()
    assert '2023' not in model_saw and 'earlier year' not in model_saw


def test_what_ask_reads_switches_off_from_ask_and_on_only_in_a_request_of_its_own(archived):
    answer, _, _ = ask('stop reading my chatgpt history')
    assert answer == 'Ask won’t read ChatGPT history.' and archive.switches()['chats'] is False
    assert ask('stop reading my documents', headers=OTHER)[0] == 'Only the owner can change what Ask may read.'
    from hub.app.ask import tools
    turn = tools.Turn(text='you can read my chatgpt history', login='me@example.com', strangers=True)
    tools.archive_tool(type('Ctx', (), {'deps': turn}), do='read', what='chats', on=True)
    assert turn.said[0].startswith('Hub changes what Ask may read in a request of its own') and archive.switches()['chats'] is False
    assert ask('you can read my chatgpt history')[0] == 'Ask may read ChatGPT history now.'


def test_the_kids_from_ask_and_a_name_photos_doesnt_know(archived):
    assert ask('Alex is one of the kids')[0] == 'Alex is one of the kids now.'
    assert {p['name']: p['kid'] for p in archive.people('me@example.com')}['Alex'] is True
    answer, _, _ = ask('Jordan is one of the kids')
    assert answer == 'Photos knows nobody called Jordan.'
    assert 'Jordan' not in json.dumps([[getattr(p, 'content', None) for m in SEEN[-1] for p in m.parts if p.part_kind == 'tool-return']],
                                      default=str)


def test_a_month_the_mac_hasnt_chosen_and_the_month_the_owner_named(archived, tmp_path, monkeypatch):
    import datetime as dt
    from hub import home
    from hub.app.ask import tools
    monkeypatch.setattr(home, 'today', lambda: dt.date(2026, 11, 16))
    named = {'October': '2026-10', 'last month': '2026-10', 'october 2025': '2025-10', '2026-09': '2026-09', 'December': '2025-12',
             'November': '2025-11', "this month's": '', 'the newest': '', '': '', '2026-13': '', 'maybe': ''}
    assert {said: tools._month(said) for said in named} == named
    curated(tmp_path)
    answer, cards, _ = ask("show me september's best photos")
    assert answer == 'The Mac hasn’t chosen the best of September 2026.' and cards == [] and len(SEEN) == 1
    assert ask('show me the best photos of october')[1] == [{'kind': 'best', 'month': '2026-10'}]


def test_nothing_the_archive_tool_shows_or_says_reaches_the_model(archived, tmp_path):
    from hub.app.ask import brain, tools
    curated(tmp_path)
    said = [ask(words)[0] for words in ("show me this month's best photos", 'photos from on this day', 'Sam is one of the kids',
                                        'Jordan is one of the kids', 'stop reading my documents')]
    assert said == ['The best of October 2026: 5 photos to keep, print or skip.', 'On this day: photos from 1 earlier year.',
                    'Sam is one of the kids now.', 'Photos knows nobody called Jordan.', 'Ask won’t read documents.']
    record = brain.earlier('me@example.com')
    ask('dim the kitchen')                                   # a later request carries the record of the earlier ones
    model_saw = sent()
    for secret in ('October 2026', '5 photos', 'earlier year', '2023', 'kids now', 'knows nobody', 'won’t read', '0000-4000-8000'):
        assert secret not in model_saw, secret
    assert record.count('Hub: ' + tools.ARCHIVE_KEPT) == 5 and record in model_saw.replace('\\n', '\n')
    # the owner's own history shows what Hub said to them
    recent = client.get('/app/api/ask', headers=SIGNED_IN).json()['recent']
    assert [turn['answer'] for turn in recent[:5]] == said and 'shown' not in recent[0]


def test_hubs_sentence_and_the_models_words_are_on_lines_of_their_own(archived, monkeypatch):
    from tests.test_app_archive import Bridge, event
    monkeypatch.setattr(bridge, 'get', Bridge({'2024-07': [event('b1', '2024-07-13', 'Beach day')]}).get)
    archive.calendar_collect(pause=0)
    answer, _, _ = ask('show me the kids at the beach in 2024')
    first, second = answer.split('\n')
    assert first == 'Kids at the beach in 2024: 9 photos from 2 days.' and second.startswith('It’s Beach day')


def test_the_archive_tool_for_no_login_and_a_kid_named_without_saying_which_way(archived):
    from hub.app.ask import tools
    nobody = tools.Turn(text='this month’s best photos', login=None)
    for do in ('best', 'on_this_day', 'read', 'kid'):
        assert tools.archive_tool(type('Ctx', (), {'deps': nobody}), do=do, what='chats', on=False, name='Alex') == {'done': tools.ARCHIVE_KEPT}
    assert set(nobody.said) == {'Open the archive on your phone or laptop.'} and nobody.cards == []
    assert archive.switches()['chats'] is True
    turn = tools.Turn(text='Alex is one of the kids', login='me@example.com', again=True)
    tools.archive_tool(type('Ctx', (), {'deps': turn}), do='kid', name='Alex')
    assert turn.said == ['Alex is one of the kids now.'] and turn.again is True      # another tool of the request still decides
    tools.archive_tool(type('Ctx', (), {'deps': turn}), do='kid', name='Alex', on=False)
    assert turn.said[-1] == 'Alex isn’t one of the kids now.' and turn.kept() == [tools.ARCHIVE_KEPT] * 2


def test_the_dry_run_holds_the_archives_settings_back(archived):
    from hub.golden import dryrun
    with dryrun.layer() as ledger:
        ask('stop reading my chatgpt history')
        ask('Alex is one of the kids')
    assert [(c['to'], c['what']) for c in ledger.changes] == [('archive', 'switch'), ('archive', 'kid')]
    assert archive.switches()['chats'] is True and {p['name']: p['kid'] for p in archive.people('me@example.com')}['Alex'] is False
