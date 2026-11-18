"""Hub's morning message (hub/app/ask/morning.py): one turn a day in each person's conversation, at most three things,
ranked in code from their errands and Today; answered by typing; "not this" quiets only low-stakes kinds; "what are you
watching?" lists and switches them; the other person's things never appear. The week and the people are made up."""
import asyncio
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from hub import config, people
from hub.app import today as screen
from hub.app.ask import brain, morning, thread, turns
from hub.app.errands import read, store
from hub.golden import dryrun
from hub.main import app
from tests import errands_world
from tests.errands_world import NOW

ME, PARTNER = 'me@example.com', 'casey@example.com'
AT = NOW.timestamp()
client = TestClient(app)


def _need(key, title, why='', tone='info', href='/today'):
    return {'key': key, 'title': title, 'why': why, 'tone': tone, 'open': {'href': href, 'label': 'Open'}, 'tick': 'Not today'}


NEEDS = [_need('ai-tidy', 'Tidy the garage shelf', 'An idea from last night.'),
         _need('house-front-lock', 'The front door lock is at 4%', 'Change its batteries.', tone='warn', href='/house'),
         _need('mail:m1', 'Email from Jamie Chen', 'Asks about Saturday.'),
         _need('friction-abc', 'Lights asked twice', 'Said again.')]


@pytest.fixture
def week(monkeypatch):
    found = errands_world.install(monkeypatch)
    assert read.run(transport=found.luna)['status'] == 'ok'
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ME, PARTNER})
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'people': [
        {'name': 'Alex', 'logins': [ME], 'accounts': [people.DEFAULT]},
        {'name': 'Casey', 'logins': [PARTNER], 'accounts': []}]}))
    # Today's list as the brief cuts it: the owner's accounts bring everything, anyone else's only the household's.
    monkeypatch.setattr(screen, '_list', lambda accounts, missed=None: ([dict(n) for n in NEEDS if people.DEFAULT in accounts
                                                            or n['key'].startswith('house-')], 0, []))
    return found


def _things(login):
    found = [t for t in turns.recent(login, seconds=10 ** 10, limit=50) if not t['asked']]
    return [x['text'] for x in found[-1]['cards'][0]['things']] if found else []


def test_one_message_a_day_with_house_safety_and_money_first(week):
    assert morning.message(ME, (NOW.replace(hour=4)).timestamp()) is None          # too early
    number = morning.message(ME, AT)
    assert number and morning.message(ME, AT + 60) is None and morning.made(ME, AT)
    said = turns.recent(ME, seconds=10 ** 10)[-1]
    assert said['asked'] == '' and said['private'] and said['answer'].startswith('Good morning. Three things for today:')
    things = _things(ME)
    assert things[0].startswith('The front door lock') and things[1].startswith('Pay the Lakeside Power bill')
    assert len(things) == morning.MOST
    # The model and the running summary see that a morning message was given, never its lines.
    assert 'Lakeside' not in brain.earlier(ME) and thread.MORNING in brain.earlier(ME)


def test_the_other_person_hears_only_the_households_part(week):
    morning.message(PARTNER, AT)
    things = ' '.join(_things(PARTNER))
    assert 'front door lock' in things
    assert 'Jamie' not in things and 'garage' not in things and 'Lights asked' not in things
    assert morning.message(None, AT) is None


def test_typed_answers_finish_put_off_or_drop_one_thing(week):
    morning.message(ME, AT)
    assert morning.answer('done', ME, AT) == 'Which one? Say it with its number, like “done 2”.'
    assert morning.answer('Paid 2', ME, AT) == 'Done, number 2 is off the list.'
    bill = next(e for e in store.rows() if 'Lakeside' in e['title'])
    assert bill['state'] == 'done'
    assert morning.answer('done 2', ME, AT) == 'Number 2 is answered already.'
    said = morning.answer('Thursday 3', ME, AT)
    assert said == 'Number 3 comes back on Thursday.'
    later = next(e for e in store.rows() if e['state'] == 'later')
    assert NOW.date() + timedelta(days=3) == datetime.fromtimestamp(later['until'], NOW.tzinfo).date()
    assert morning.answer('not this 1', ME, AT).startswith('Number 1 is gone')
    assert morning.answer('turn on the kitchen lights', ME, AT) is None
    assert morning.counts(ME, now=AT) == {'shown': 3, 'done': 1, 'not': 1, 'later': 1, 'opened': 0, 'build': 0}
    assert morning.answer('done 1', PARTNER, AT) is None            # nobody answers another person's message


def test_not_this_twice_quiets_a_low_stakes_kind_never_the_house(week, monkeypatch):
    monkeypatch.setattr(screen, '_list', lambda accounts, missed=None: ([dict(NEEDS[0]), dict(NEEDS[1])], 0, []))
    monkeypatch.setattr(morning, '_errands', lambda login, now: [])
    for days in (0, 1):
        at = AT + days * 86400
        morning.message(ME, at)
        assert _things(ME) == ['The front door lock is at 4%. Change its batteries.', 'Tidy the garage shelf. An idea from last night.']
        said = morning.answer('not this 2', ME, at)
        assert ('leave ideas from the overnight review out' in said) == (days == 1)
        house = morning.answer('not this 1', ME, at)
        assert house.startswith('Number 1 is gone') and 'leave' not in house       # the house never quiets this way
    assert [i['kind'] for i in morning.pool(ME, AT + 2 * 86400)] == ['house']


def test_what_are_you_watching_lists_and_switches_kinds(week):
    said = morning.answer('What are you watching?', ME, AT)
    assert 'Email that needs you' in said and 'Ideas from the overnight review' in said
    assert 'Email that needs you' not in morning.answer('what are you watching', PARTNER, AT)
    assert 'leave this out' in morning.answer('stop watching the overnight ideas', ME, AT)
    assert 'Ideas from the overnight review (off)' in morning.answer('What are you watching?', ME, AT)
    assert 'garage' not in ' '.join(i['text'] for i in morning.pool(ME, AT))
    assert morning.answer('watch ideas again', ME, AT).startswith('Back in your morning message')
    assert 'garage' in ' '.join(i['text'] for i in morning.pool(ME, AT))


def test_ask_opens_on_the_morning_message_and_counts_a_tap(week, monkeypatch):
    monkeypatch.setattr(morning.time, 'time', lambda: AT)
    seen = client.get('/app/api/ask', headers={'tailscale-user-login': ME}).json()
    assert seen['morning'] and seen['recent'][-1]['cards'][0]['kind'] == 'morning'
    key = seen['recent'][-1]['cards'][0]['things'][0]['key']
    tap = {'origin': 'http://testserver', 'tailscale-user-login': ME}
    assert client.post('/app/api/ask/morning/opened', json={'key': key}, headers=tap).json()['ok']
    assert not client.post('/app/api/ask/morning/opened', json={'key': 'errand-999'}, headers=tap).json()['ok']
    assert morning.counts(ME, now=AT)['opened'] == 1


async def _run(text, login):
    def no_model():
        raise AssertionError('the model was asked')
    return [e async for e in brain.run(text, login, build=no_model)]


def test_a_typed_answer_is_done_in_code_without_the_model(week, monkeypatch):
    monkeypatch.setattr(morning.time, 'time', lambda: AT)
    morning.message(ME, AT)
    events = asyncio.run(_run('done 2', ME))
    assert events[0] == {'t': 'say', 'text': 'Done, number 2 is off the list.'} and events[-1]['status'] == 'ok'
    assert turns.recent(ME)[-1]['asked'] == 'done 2'


def test_a_dry_run_holds_back_what_a_typed_answer_would_change(week):
    morning.message(ME, AT)
    with dryrun.layer() as ledger:
        assert morning.answer('done 2', ME, AT) == 'Done, number 2 is off the list.'
        morning.answer('stop watching mail', ME, AT)
    assert [(c['to'], c['what']) for c in ledger.changes] == [('morning', 'done'), ('morning', 'stop watching')]
    bill = next(e for e in store.rows() if 'Lakeside' in e['title'])
    assert bill['state'] != 'done' and morning.counts(ME, now=AT)['done'] == 0
    assert 'mail' not in morning._off(ME, NOW.date().isoformat())


def test_a_first_opening_whose_sources_didnt_answer_isnt_kept_as_a_day_with_nothing_to_say(week, monkeypatch):
    """2026-11-18: a slow first opening found nothing and was kept as "nothing to say", so no morning message came
    that day. Nothing found while a source was late or broke is not an empty day: a later opening looks again."""
    from hub import today
    from hub.app.errands import picture
    monkeypatch.setattr(picture, 'ready', lambda now, login=None: [])           # no errands; only Today's list
    slow = {'on': True}

    def listed(accounts, missed=None):
        if slow['on']:                  # every source still out when the deadline came
            missed.append('late')
            return [], 0, []
        return [dict(n) for n in NEEDS], 0, []
    monkeypatch.setattr(screen, '_list', listed)
    assert morning.message(ME, AT) is None and not morning.made(ME, AT, at_all=True)
    slow['on'] = False
    assert morning.message(ME, AT + 5) is None                                   # not on every opening: a minute apart
    monkeypatch.setattr(morning, '_looked', {})
    assert morning.message(ME, AT + 70) and _things(ME)[0].startswith('The front door lock')
    monkeypatch.setattr(screen, '_list', lambda accounts, missed=None: ([], 0, []))
    assert morning.message(PARTNER, AT) is None and morning.made(PARTNER, AT, at_all=True)    # truly nothing: kept

    from concurrent.futures import Future
    broke = Future()
    broke.set_exception(OSError('a source that broke'))
    monkeypatch.setattr(today, 'DEADLINE_SECONDS', 0.01)
    missed = []
    assert today.gather([(Future(), None), (broke, None)], accounts={people.DEFAULT}, missed=missed) == []
    assert missed == ['late', 'broke']

