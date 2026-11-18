"""Dashboards (hub/app/boards/): the pregnancy board's dates, each board's reader, who sees which, and the taps.

Made-up people (Alex owns the main Google account, Casey is a member) and a made-up due date, in a throwaway data
folder. The network board reads a stand-in for the older Network page's overview; nothing reaches Home Assistant.
"""
import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from hub import config, store
from hub.app import boards, today
from hub.app.ask import private, tools
from hub.app.boards import calendar, network, pregnancy, school, spend
from hub.main import app

client = TestClient(app)
REAL_WEEK = calendar._week
ALEX = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}
CASEY = {'origin': 'http://testserver', 'tailscale-user-login': 'casey@example.com'}
STRANGER = {'origin': 'http://testserver', 'tailscale-user-login': 'someone@example.com'}
DUE = date(2027, 3, 4)                 # made up; week 1 starts 2026-05-28
ON = date(2026, 9, 9)                  # 104 days in: 14 weeks 6 days, week 15, second trimester


@pytest.fixture
def household(monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {'me@example.com', 'casey@example.com'})
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'pregnancy': {'due': DUE.isoformat()}, 'people': [
        {'name': 'Alex', 'logins': ['me@example.com'], 'accounts': ['default']},
        {'name': 'Casey', 'logins': ['casey@example.com'], 'accounts': []}]}))
    from hub import home
    monkeypatch.setattr(home, 'today', lambda: ON)
    monkeypatch.setattr(network, 'read', lambda: OVERVIEW)
    monkeypatch.setattr(calendar, '_week', lambda now: ([], 0, None))
    monkeypatch.setattr(spend, '_spend', lambda: SPEND)
    monkeypatch.setattr(spend, '_checked', lambda: None)
    monkeypatch.setattr(school, '_guide', lambda: GUIDE)


OVERVIEW = {
    'configured': True, 'connected': True, 'headline': 'Internet is up', 'tone': 'warn', 'facts': '18 ms ping · router link up',
    'summary_label': 'External reachability', 'updated': '9:41 am', 'node_count': 3, 'client_count': 24, 'online_count': 21,
    'summary': {'reliability': 99.71, 'outages': 2, 'downtime_text': '4 min', 'incidents': []},
    'incidents': [{'label': 'Reported offline', 'when': 'Tue 2:10 am', 'duration': '3 min', 'ongoing': False}],
    'device_incidents': [],
    'attention': [{'title': 'Elevated latency to the selected target', 'evidence': 'Current ping is 120.0 ms.', 'action': '', 'level': 'warn'}],
    'cards': [{'title': 'Download traffic', 'current': '41.2 Mbit/s', 'graph': {'segments': ['2,170 718,60'], 'max': 300, 'unit': 'Mbit/s',
                                                                           'start_label': 'Mon 9:41 am', 'end_label': 'Tue 9:41 am'}},
              {'title': 'Upload traffic', 'current': '3.1 Mbit/s', 'graph': {}},
              {'title': 'External latency', 'current': '18.0 ms', 'graph': {}},
              {'title': 'External packet loss', 'current': '0.0 %', 'graph': {}}],
    'top': [{'name': 'Living room TV', 'download_text': '25 Mbit/s', 'upload_text': '1 Mbit/s'}],
}


SPEND = {'month': 'September', 'meters': [
    {'label': 'Overnight review and mail sorting', 'spent': '$1.20', 'cap': '$5.00', 'percent': 24, 'state': 'ok', 'words': ''},
    {'label': 'Ask Hub', 'spent': '$6.10', 'cap': '$10.00', 'percent': 61, 'state': 'ok', 'words': ''}],
    'rows': [{'name': 'Ask Hub', 'runs': 40, 'failed': 1, 'calls': 52, 'dollars': 8.1, 'cost': '$8.10'}]}


GUIDE = {'events': [{'date': '2026-09-10', 'title': 'Picture day', 'start_time': '09:00', 'location': 'Room 2', 'status': 'confirmed'},
                    {'date': '2026-09-14', 'title': 'Field trip', 'status': 'cancelled'},
                    {'date': '2026-12-01', 'title': 'Too far ahead'}],
         'pages': [{'slug': 'drop-off', 'title': 'Drop-off', 'body': 'Made up.'}],
         'resources': [{'title': 'Lunch menu', 'details': '', 'url': 'https://school.example/menu'}]}


def _block(found, block_id):
    return next(b for b in found['blocks'] if b['id'] == block_id)


# ---- the pregnancy board's dates ----

def test_where_counts_weeks_the_way_a_clinic_does():
    at = pregnancy.where(DUE, ON)
    assert (at['weeks'], at['days'], at['week'], at['trimester'], at['left']) == (14, 6, 15, 2, 176)
    assert at['size'] == 'an apple'
    first = pregnancy.where(DUE, date(2026, 5, 28))
    assert (first['day'], first['week'], first['trimester']) == (0, 1, 1)
    assert pregnancy.where(DUE, date(2026, 12, 10))['trimester'] == 3         # 28w0d
    assert pregnancy.where(DUE, DUE)['left'] == 0


def test_the_board_from_the_due_date(household):
    found = boards.picture('pregnancy', 'casey@example.com')
    assert found['say'] == 'Week 15: about the size of an apple'
    assert found['sub'] == 'Due Thursday, March 4, an estimate. 176 days to go.'
    assert _block(found, 'progress')['big'] == '14 weeks, 6 days'
    coming = [r['title'] for r in _block(found, 'coming')['rows']]
    assert coming[:3] == ['Quad screen', 'First movements', 'Anatomy scan']           # the first trimester's tests are behind
    assert 'First prenatal visit' not in coming
    prep = {i['id']: i for i in _block(found, 'prep')['items']}
    assert prep['provider']['tone'] == 'warn' and prep['childcare']['tone'] == ''      # by week 6 is past; week 20 isn't
    months = _block(found, 'months')['rows']
    assert months[0]['title'] == 'May 2026' and months[-1]['title'] == 'March 2027'
    assert [m['title'] for m in months if m['tone'] == 'lit'] == ['September 2026']


def test_today_and_ask_get_a_line_and_the_facts(household):
    line = pregnancy.line('me@example.com', ON)
    assert line['text'] == 'Week 15, 176 days to go'
    assert line['next'] == 'Take a prenatal vitamin with folic acid'       # the oldest one not done
    facts = boards.facts('pregnancy', 'casey@example.com')
    assert facts['week'] == 15 and facts['due_date'] == '2027-03-04' and facts['trimester'] == 2
    assert boards.facts('pregnancy', 'someone@example.com') == {'note': 'That dashboard isn’t this person’s to read.'}


def test_without_a_due_date_it_asks_for_one(household):
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'pregnancy': {}, 'people': [
        {'name': 'Alex', 'logins': ['me@example.com'], 'accounts': ['default']}]}))
    found = boards.picture('pregnancy', 'me@example.com')
    assert found['say'] == 'Set the due date' and found['settings'][0]['value'] == ''
    assert pregnancy.line('me@example.com', ON) is None


def test_no_pregnancy_section_means_no_board(household):
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'people': [
        {'name': 'Alex', 'logins': ['me@example.com'], 'accounts': ['default']}]}))
    assert [b['id'] for b in boards.listing('me@example.com')] == ['money', 'career', 'calendar', 'school', 'trip', 'network', 'mail', 'spend']


# ---- who sees which ----

def test_who_sees_which_board(household):
    assert [b['id'] for b in boards.listing('me@example.com')] == ['pregnancy', 'money', 'career', 'calendar', 'school', 'trip', 'network', 'mail', 'spend']
    assert [b['id'] for b in boards.listing('casey@example.com')] == ['pregnancy', 'money', 'school', 'trip', 'network', 'spend']        # mail and calendar are Alex's; money is both members'
    assert boards.listing('someone@example.com') == [] and boards.listing(None) == []
    assert client.get('/app/api/board/mail', headers=CASEY).status_code == 404
    assert client.get('/app/api/board/pregnancy', headers=STRANGER).status_code in (403, 404)    # Hub lets them in at all?
    assert client.get('/app/api/board/pregnancy').status_code == 404                    # this Mac, no login
    assert client.get('/app/api/board/nothing', headers=ALEX).status_code == 404


def test_today_lists_the_boards_but_the_wall_never_does(household, monkeypatch):
    monkeypatch.setattr(today.brief, 'start', lambda now: None)
    mine = today.picture(login='casey@example.com')
    assert [b['id'] for b in mine['boards']] == ['pregnancy', 'network']
    assert today.picture(wall=True, login='casey@example.com').get('boards', []) == []


# ---- arranging ----

def test_each_person_arranges_their_own(household):
    said = client.post('/app/api/board/pregnancy/layout', headers=CASEY,
                       json={'order': ['prep', 'progress', 'made-up'], 'hidden': ['months', 'made-up']}).json()
    assert said['ok']
    theirs = client.get('/app/api/board/pregnancy', headers=CASEY).json()
    ids = [b['id'] for b in theirs['blocks']]
    assert ids == ['prep', 'progress', 'numbers', 'odds', 'odds-curve', 'born', 'labour-curve', 'odds-about', 'week', 'coming', 'visits', 'about']     # the rest where the board puts them
    assert theirs['hidden'] == [{'id': 'months', 'title': 'Month by month'}]
    alex = client.get('/app/api/board/pregnancy', headers=ALEX).json()
    assert [b['id'] for b in alex['blocks']][:2] == ['progress', 'numbers'] and alex['hidden'] == []


def test_arranging_needs_a_login_and_the_board(household):
    assert client.post('/app/api/board/pregnancy/layout', json={'order': []}).status_code == 403
    assert client.post('/app/api/board/mail/layout', headers=CASEY, json={'order': []}).status_code == 403
    assert client.post('/app/api/board/pregnancy/nonsense', headers=CASEY, json={}).status_code == 400


# ---- ticks and the due date, the household's ----

def test_ticks_are_shared(household):
    client.post('/app/api/board/pregnancy/tick', headers=ALEX, json={'item': 'vitamins', 'done': True})
    assert client.post('/app/api/board/pregnancy/tick', headers=CASEY, json={'item': 'provider', 'done': True}).json()['said'] == 'Ticked'
    prep = {i['id']: i for i in _block(client.get('/app/api/board/pregnancy', headers=ALEX).json(), 'prep')['items']}
    assert prep['provider']['done'] and prep['provider']['tone'] == ''
    assert pregnancy.line('me@example.com', ON)['next'] == 'Check the insurance: maternity cover, the hospital in network, the deductible'
    client.post('/app/api/board/pregnancy/tick', headers=ALEX, json={'item': 'provider', 'done': False})
    assert 'provider' not in boards.ticks('pregnancy')
    assert client.post('/app/api/board/pregnancy/tick', headers=ALEX, json={'item': 'made-up'}).status_code == 409


def test_the_due_date_can_change_on_the_board(household):
    said = client.post('/app/api/board/pregnancy/set', headers=CASEY, json={'key': 'due', 'value': '2027-03-11'}).json()
    assert said['said'] == 'Due date set to Thursday, March 11.'
    assert pregnancy.due() == date(2027, 3, 11)
    assert client.post('/app/api/board/pregnancy/set', headers=CASEY, json={'key': 'due', 'value': '2029-01-01'}).status_code == 409
    assert client.post('/app/api/board/pregnancy/set', headers=CASEY, json={'key': 'due', 'value': 'soon'}).status_code == 409
    client.post('/app/api/board/pregnancy/set', headers=CASEY, json={'key': 'due', 'value': ''})
    assert pregnancy.due() == DUE                          # back to the settings file's


def test_a_due_date_is_an_estimate_until_the_dating_ultrasound(household):
    early = DUE - timedelta(days=280 - 7 * 6)                 # week 7: before the dating scan
    found = pregnancy.picture('me@example.com', early)
    assert ', an estimate.' in found['sub'] and _block(found, 'dating')['kind'] == 'note'
    assert _block(found, 'numbers')['tiles'][1]['label'] == 'due date, estimated'
    assert not any(b['id'] == 'dating' for b in pregnancy.picture('me@example.com', ON)['blocks'])       # week 15: the note has had its say
    assert boards.facts('pregnancy', 'me@example.com')['due_date_is'].startswith('an estimate')
    said = client.post('/app/api/board/pregnancy/set', headers=CASEY, json={'key': 'scan', 'value': 'on'}).json()
    assert said['said'] == 'Marked as the date from the dating ultrasound.' and pregnancy.scanned()
    found = pregnancy.picture('me@example.com', early)
    assert 'estimate' not in found['sub'] and not any(b['id'] == 'dating' for b in found['blocks'])
    assert [s['value'] for s in found['settings']] == [DUE.isoformat(), 'on']
    client.post('/app/api/board/pregnancy/set', headers=CASEY, json={'key': 'scan', 'value': ''})
    assert not pregnancy.scanned()


def test_the_settings_file_can_say_a_scan_set_it(household):
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'pregnancy': {'due': DUE.isoformat(), 'scan': True}, 'people': [
        {'name': 'Alex', 'logins': ['me@example.com'], 'accounts': ['default']}]}))
    assert pregnancy.scanned()
    client.post('/app/api/board/pregnancy/set', headers=ALEX, json={'key': 'scan', 'value': ''})
    assert not pregnancy.scanned()                         # the dashboard's word wins


def test_the_chance_of_a_live_birth_and_when_babies_come(household):
    assert pregnancy.in_words(pregnancy.live_chance(28)) == 'About 75 in 100'          # a positive test at 4 weeks
    assert pregnancy.in_words(pregnancy.live_chance(42)) == 'About 90 in 100'          # 6 weeks
    assert pregnancy.in_words(pregnancy.live_chance(30 * 7)) == 'More than 99 in 100'
    chances = [pregnancy.live_chance(d) for d in range(0, 290)]
    assert chances == sorted(chances)                                                  # it never falls as the weeks pass
    found = pregnancy.picture('me@example.com', ON)                                    # 14 weeks 6 days
    odds = _block(found, 'odds')
    assert odds['big'] == 'About 99 in 100' and odds['small'].startswith('On average, for a pregnancy still going at 14 weeks, 6 days.')
    curve = _block(found, 'odds-curve')
    assert len(curve['segments']) == 1 and 0.29 < curve['mark']['at'] < 0.31 and curve['mark']['label'] == 'You are here'
    born = _block(found, 'born')['rows']
    assert [r['title'] for r in born] == ['Before Feb 11', 'Feb 11 to Feb 17', 'Feb 18 to Feb 24', 'Feb 25 to Mar 10', 'Mar 11 or later']
    assert born[3]['right'] == '57% · 95% by then' and born[0]['right'] == '9%' and born[4]['right'] == '5%'
    labour = _block(found, 'labour-curve')
    assert labour['now'] == 'About 4% on the day' and labour['mark']['label'] == 'Due Mar 4'
    assert 'Tong et al., Obstet Gynecol 2008' in _block(found, 'odds-about')['text']
    facts = boards.facts('pregnancy', 'me@example.com')
    assert facts['chance_of_a_live_birth_on_average'].startswith('About 99 in 100') and len(facts['when_babies_are_born']) == 5


# ---- the network and mail boards ----

def test_the_network_board(household):
    found = client.get('/app/api/board/network', headers=CASEY).json()
    assert found['say'] == 'Internet is up'
    assert [t['value'] for t in _block(found, 'now')['tiles']] == ['41.2 Mbit/s', '3.1 Mbit/s', '18.0 ms', '0.0 %']
    assert _block(found, 'day')['tiles'][0]['value'] == '99.71%'
    assert _block(found, 'download')['segments'] == ['2,170 718,60']
    assert _block(found, 'latency')['segments'] == []
    assert network.line('casey@example.com', ON) == {'text': 'Internet is up', 'next': 'Elevated latency to the selected target', 'tone': 'warn'}
    assert boards.facts('network', 'casey@example.com')['drops_last_24h'] == 2


def test_the_network_board_when_home_assistant_is_away(household, monkeypatch):
    monkeypatch.setattr(network, 'read', lambda: {'configured': True, 'connected': False})
    found = boards.picture('network', 'me@example.com')
    assert found['say'] == 'Can’t read the network right now'
    assert network.line('me@example.com', ON) is None


def test_the_mail_board(household, monkeypatch):
    from hub.app import mail
    monkeypatch.setattr(mail, 'picture', lambda: {
        'needs': [{'id': 'm1', 'who': 'Maple Street Dental', 'subject': 'Your appointment', 'step': 'Confirm Tuesday.', 'days': 1, 'find': '/gmail/search?q=x'}],
        'needs_more': 0, 'waiting_days': 3, 'asked': [], 'odd': [], 'synced': '5 minutes ago', 'sorted_today': '4 receipts',
        'unsorted': 2, 'shelves': [{'name': 'Receipts', 'count': 40}, {'name': 'Reading', 'count': 0}]})
    with store.db() as conn:
        for n, total in enumerate((1000, 980, 930)):
            conn.execute('insert into inbox_history(day, total, unread) values(?,?,?)', (f'2026-09-0{n + 1}', total, 0))
    found = client.get('/app/api/board/mail', headers=ALEX).json()
    assert found['say'] == '1 email needs you'
    assert _block(found, 'counts')['tiles'][3] == {'value': '930', 'label': 'in the inbox · down 70 in 2 days'}
    assert [b['label'] for b in _block(found, 'kinds')['bars']] == ['Receipts', 'Not sorted']
    assert len(_block(found, 'inbox')['segments']) == 1
    assert boards.lines('me@example.com', ON)[-1]['text'] == '930 in the inbox'


# ---- Ask ----

@pytest.mark.parametrize('text,topic', [('how far along are we?', 'pregnancy'), ('when is the anatomy scan', 'pregnancy'),
                                        ('is the internet down?', 'network'), ('why is the wifi slow', 'network')])
def test_ask_hears_a_question_about_a_board(text, topic):
    assert topic in private.about(text)


@pytest.mark.parametrize('text', ['reload the network integration', 'play some internet radio', 'add baby wipes to the list'])
def test_ask_leaves_the_house_s_business_alone(text):
    assert not private.about(text) & {'pregnancy', 'network'}


def test_ask_is_given_the_facts_and_draws_the_board(household):
    data = private.data('how far along are we?', {'pregnancy'}, 'casey@example.com')
    assert data['pregnancy']['week'] == 15
    assert private.data('how far along?', {'pregnancy'}, 'someone@example.com')['pregnancy'] == {'note': 'That dashboard isn’t this person’s to read.'}
    turn = tools.Turn(text='is the wifi ok and how far along are we', login='casey@example.com')
    tools.shown(turn, {'network', 'pregnancy'})
    tools.shown(turn, {'network'})
    assert turn.cards == [{'kind': 'board', 'board': 'network'}, {'kind': 'board', 'board': 'pregnancy'}]


# ---- the calendar and AI spend boards (the older Calendar and Insights pages) ----

def test_the_calendar_board_reads_the_week_the_older_page_did(household, monkeypatch):
    from datetime import datetime, timedelta
    from hub import home
    from hub.tools.calendar import read
    now = datetime.now(home.PACIFIC)
    soon = now + timedelta(hours=1)
    later = now + timedelta(days=2)
    events = [{'title': 'Synthetic dentist', 'day': soon.date(), 'last_day': soon.date(), 'start': soon, 'end': soon + timedelta(hours=1),
               'all_day': False, 'location': 'Main St', 'busy': True},
              {'title': 'Synthetic standup', 'day': soon.date(), 'last_day': soon.date(), 'start': soon, 'end': soon + timedelta(minutes=30),
               'all_day': False, 'location': '', 'busy': True},
              {'title': 'Synthetic trip', 'day': later.date(), 'last_day': later.date(), 'start': None, 'end': None,
               'all_day': True, 'location': '', 'busy': False}]
    monkeypatch.setattr(calendar, '_week', REAL_WEEK)            # the real reader, over a stand-in helper
    monkeypatch.setattr(read, 'week', lambda now: events)
    found = boards.picture('calendar', 'me@example.com')
    today_rows = _block(found, 'today')['rows']
    if soon.date() == now.date():
        assert found['say'].startswith('Next: Synthetic') and {r['title'] for r in today_rows} == {'Synthetic dentist', 'Synthetic standup'}
        assert all(r['tone'] == 'warn' for r in today_rows)              # they clash
    assert _block(found, 'numbers')['tiles'][2] == {'value': '1', 'label': 'clash', 'tone': 'warn'}
    assert boards.picture('calendar', 'casey@example.com') is None       # one person's calendar
    monkeypatch.setattr(read, 'week', lambda now: (_ for _ in ()).throw(read.SignInNeeded()))
    assert boards.picture('calendar', 'me@example.com')['say'] == 'Calendar needs signing in again'
    assert calendar.line('me@example.com', ON) is None


def test_the_spend_board_and_its_line(household, monkeypatch):
    found = boards.picture('spend', 'casey@example.com')
    assert found['say'] == 'Within limits in September' and spend.line('casey@example.com', ON) is None
    assert _block(found, 'kinds')['bars'] == [{'label': 'Ask Hub', 'value': 8.1, 'text': '$8.10'}]
    assert _block(found, 'runs')['rows'][0]['sub'] == '40 runs, 52 model calls, 1 didn’t finish'
    near = dict(SPEND, meters=[SPEND['meters'][0], dict(SPEND['meters'][1], state='warn', words='Close to the limit.')])
    monkeypatch.setattr(spend, '_spend', lambda: near)
    assert boards.picture('spend', 'me@example.com')['say'] == 'Ask Hub: close to the limit'
    assert spend.line('me@example.com', ON)['tone'] == 'warn'


def test_the_older_addresses_open_the_boards(household):
    for old, new in (('/calendar', '/app/#board/calendar'), ('/insights', '/app/#board/spend')):
        moved = client.get(old, headers=ALEX, follow_redirects=False)
        assert moved.status_code == 302 and moved.headers['location'] == new


def test_the_school_board_reads_the_published_guide(household):
    found = boards.picture('school', 'casey@example.com')
    assert found['say'] == 'Next: Picture day, Tomorrow'
    rows = _block(found, 'dates')['rows']
    assert [r['title'] for r in rows] == ['Picture day', 'Cancelled: Field trip'] and rows[1]['tone'] == 'warn'
    assert _block(found, 'topics')['rows'] == [{'title': 'Drop-off', 'href': '/preschool/guide/drop-off'}]
    assert _block(found, 'numbers')['tiles'][2]['value'] == '1'


def test_the_trip_board_reads_the_saved_trip_and_leads_to_its_workspace(household):
    found = boards.picture('trip', 'casey@example.com')               # the invented starter trip, in a throwaway folder
    assert found['say'].startswith('Port Ellery & Larch Valley in ') and found['sub'].endswith('Planning, not booked.')
    assert _block(found, 'plan')['tiles'][0] == {'value': '4', 'label': 'nights, Port Ellery'}
    assert 'Find welcome dinner venue and start time' in [r['title'] for r in _block(found, 'decide')['rows']]
    assert 'Prefer the Wednesday move' not in [r['title'] for r in _block(found, 'decide')['rows']]
    assert _block(found, 'edit')['rows'][0]['href'] == '/trip'
