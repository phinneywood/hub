"""Money (hub/app/money/), on a made-up household: Alex and Sam, four months of an ordinary family's money, and a
stand-in for SimpleFIN Bridge (tests/money_world.py). No bank, no network and no model."""
import io
import json
import os
import stat
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from hub import config
from hub.app.ask import tools
from hub.app.money import ahead, ask, budget, kinds, categories, changes, files, picture, plan, recurring, share, simplefin, store, sync, taps
from hub.main import app
from tests import money_world
from tests.money_world import ALEX, GUEST, SAM, TODAY

client = TestClient(app)


def signed(login):
    return {'origin': 'http://testserver', 'tailscale-user-login': login}


@pytest.fixture
def world(monkeypatch):
    return money_world.install(monkeypatch)


def account(name):
    return store.one('select * from money_accounts where name=?', (name,))


def turn(login=ALEX, text='', strangers=False):
    return SimpleNamespace(deps=tools.Turn(text=text, login=login, strangers=strangers))


def private_sams_checking():
    taps.private(SAM, account('Sam Checking')['id'], True)


# ---- reading banks: read-only, guarded, limited ----

def test_connecting_claims_the_token_once_and_keeps_the_access_apart_owner_only(world):
    assert len(store.rows('select * from money_accounts')) == 6
    access = config.DATA_DIR / store.ACCESS
    assert stat.S_IMODE(os.stat(access).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(config.DATA_DIR / store.FILE).st_mode) == 0o600
    assert 'secret-alex' in access.read_text()
    assert b'secret-alex' not in (config.DATA_DIR / store.FILE).read_bytes()
    claims = [r for r in world.requests if r.method == 'POST']
    assert len(claims) == 2 and all(r.content == b'' for r in claims)
    with pytest.raises(sync.Refused, match='already used'):
        sync.connect(money_world.token('alex'), ALEX)
    reads = [r for r in world.requests if r.method == 'GET']
    assert all('secret' not in str(r.url) and r.headers['authorization'].startswith('Basic ') for r in reads)
    span = float(reads[0].url.params['end-date']) - float(reads[0].url.params['start-date'])
    assert span <= (simplefin.DAYS + 1) * 86400


def test_the_guard_lets_only_the_claim_and_reading_accounts_leave():
    for method, url in (('GET', 'https://beta-bridge.simplefin.org/simplefin/transfer'), ('POST', 'https://beta-bridge.simplefin.org/simplefin/accounts'),
                        ('GET', 'https://evil.example/simplefin/accounts'), ('GET', 'http://beta-bridge.simplefin.org/simplefin/accounts'),
                        ('DELETE', 'https://beta-bridge.simplefin.org/simplefin/accounts')):
        with pytest.raises(simplefin.WrongRoute):
            simplefin.guard(httpx.Request(method, url))
    with pytest.raises(simplefin.WrongRoute):
        simplefin.guard(httpx.Request('POST', 'https://beta-bridge.simplefin.org/simplefin/claim/x', content=b'{"pay": 1}'))
    simplefin.guard(httpx.Request('GET', 'https://beta-bridge.simplefin.org/simplefin/accounts'))
    with pytest.raises(simplefin.Refused):
        simplefin.claim_address('aHR0cHM6Ly9ldmlsLmV4YW1wbGUvY2xhaW0veA==')      # https://evil.example/claim/x


def test_reading_again_is_limited_and_overlaps_the_last_reading(world):
    link = sync.links(ALEX)[0]['id']
    with pytest.raises(sync.Refused, match='less than an hour'):
        sync.read(link)
    world.clock['now'] += 2 * 3600
    before = len(world.requests)
    sync.read(link)
    last = world.requests[before]
    assert float(last.url.params['start-date']) == pytest.approx(money_world.NOW - 5 * 86400)
    for _ in range(sync.READS_A_DAY - 2):
        world.clock['now'] += 2 * 3600
        sync.read(link)
    world.clock['now'] += 2 * 3600
    with pytest.raises(sync.Refused, match='enough for today'):
        sync.read(link)


def test_a_pending_charge_is_replaced_when_it_posts(world):
    pending = store.rows("select * from money_txns where pending=1")
    assert len(pending) == 1 and pending[0]['amount'] == -87.12
    visa = world.data['alex'][1]
    visa['transactions'] = [t for t in visa['transactions'] if not t['pending']] + [
        {'id': 'TRN-POSTED', 'posted': int(money_world.NOW), 'amount': '-91.40', 'description': 'TRADER JOE S #552 SPRINGFIELD WA', 'pending': False}]
    world.clock['now'] += 2 * 3600
    sync.read(sync.links(ALEX)[0]['id'])
    assert not store.rows('select * from money_txns where pending=1')
    assert store.one("select amount from money_txns where ext='TRN-POSTED'")['amount'] == -91.40


def test_a_bank_problem_and_a_revoked_access_become_a_need(world):
    world.errors.append('Cascade Bank needs you to sign in again at SimpleFIN Bridge.')
    world.clock['now'] += 2 * 3600
    said = sync.read(sync.links(ALEX)[0]['id'])
    assert 'sign in again' in said
    assert any(n['title'] == 'A bank connection needs a look' for n in picture.needs(ALEX, TODAY))
    world.status = 403
    world.clock['now'] += 2 * 3600
    assert 'turned off' in sync.read(sync.links(ALEX)[0]['id'])


# ---- where the money goes ----

def test_card_payments_and_moves_between_accounts_are_never_spending(world):
    paid = store.rows("select t.*, a.name from money_txns t join money_accounts a on a.id=t.account where t.payee like '%AUTOPAY%' "
                      "or t.payee like '%THANK YOU%'")
    assert paid and all(p['category'] == 'transfer' and p['pair'] for p in paid)
    ira = store.rows("select category from money_txns where payee like '%ROTH CONTRIB%'")
    assert ira and all(r['category'] == 'saving' for r in ira)
    month = budget.month(TODAY)
    assert 'transfer' not in {c['key'] for c in month['categories']} and 'cards' not in {c['key'] for c in month['categories']}
    groceries = next(c for c in month['categories'] if c['key'] == 'groceries')
    assert groceries['spent'] > 300                          # the card's purchases are the spending
    assert not any(r['pending'] for r in share.txns(ALEX, 'category=?', ('groceries',), household_only=True) if r['day'] < '2026-11-17')


def test_a_refund_comes_off_what_was_spent_there(world):
    refund = store.one("select * from money_txns where payee like '%RETURN%'")
    assert refund['category'] == 'shopping' and refund['how'] == 'refund'


def test_a_correction_teaches_hub_the_payee_and_undo_puts_it_all_back(world):
    before = store.rows("select id, category from money_txns where payee like '%FARMERS%'")
    assert before and all(r['category'] == 'other' for r in before)
    said, batch = taps.sort(ALEX, 'Farmers market', 'groceries')
    assert 'groceries from now on' in said and 'earlier charge' in said
    assert all(r['category'] == 'groceries' for r in store.rows("select category from money_txns where payee like '%FARMERS%'"))
    with pytest.raises(changes.Refused, match='Only whoever'):
        changes.undo(batch, SAM)
    changes.undo(batch, ALEX)
    assert store.rows("select id, category from money_txns where payee like '%FARMERS%'") == before
    assert not store.rows('select * from money_rules')


# ---- bills, the month, the weeks ahead, the long run ----

def test_bills_and_paydays_come_back_on_their_day(world):
    flows = {f['name']: f for f in recurring.flows(TODAY)}
    assert flows['Oakwood Property Mgmt']['next'] == '2026-12-01' and flows['Oakwood Property Mgmt']['amount'] == -2400
    assert flows['Acme Payroll Dir']['every'] == '2 weeks' and flows['Acme Payroll Dir']['next'] == '2026-11-27'
    card = flows['Rewards Visa payment']
    assert card['kind'] == 'card' and card['amount'] == -abs(account('Rewards Visa')['balance'])
    assert flows['Springfield City Light']['about'] and flows['Spotify']['up'] and flows['Spotify']['amount'] == -12.99
    assert not any(f['name'] in ('Trader Joe’s', 'Safeway', 'Chevron') for f in flows.values())     # the weekly shop isn't a bill
    taps.bill(ALEX, 'add', 'Window cleaning', 120, 'quarter', '2026-12-10')
    assert any(f['name'] == 'Window cleaning' and f['source'] == 'you' for f in recurring.flows(TODAY))
    taps.bill(ALEX, 'not', 'Netflix')
    assert not any(f['name'] == 'Netflix' for f in recurring.flows(TODAY))


def test_the_month_against_the_budget_with_pace(world):
    suggested = budget.suggest(TODAY)
    assert suggested['groceries'] >= 500 and suggested['housing'] == 2400
    taps.use_suggested(ALEX, suggested)
    taps.set_budget(ALEX, 'eating out', 120)
    month = budget.month(TODAY)
    dining = next(c for c in month['categories'] if c['key'] == 'dining')
    assert dining['state'] == 'over' and dining['left'] < 0
    first, _ = picture.sentence(month, ahead.forecast(TODAY), True)
    assert first.startswith('November is ') and 'days to go' in first
    assert any(n['key'] == 'over-dining' for n in picture.needs(ALEX, TODAY))


def test_cash_ahead_and_whether_we_can_afford_something(world):
    cash = ahead.forecast(TODAY)
    assert cash['ok'] and len(cash['points']) == ahead.DAYS + 1
    rent = next(e for e in cash['events'] if e['name'] == 'Oakwood Property Mgmt')
    assert rent['day'] == '2026-12-01'
    assert cash['low']['steep'] <= cash['low']['balance']
    yes = ahead.afford(500, TODAY + __import__('datetime').timedelta(days=10), 'a new stroller', TODAY)
    assert yes['verdict'] == 'yes' and 'cushion' in yes['said']
    no = ahead.afford(60000, TODAY + __import__('datetime').timedelta(days=10), 'a car', TODAY)
    assert no['verdict'] == 'no'
    later = ahead.afford(4000, __import__('datetime').date(2027, 3, 15), 'a trip', TODAY)
    assert later['verdict'] in ('yes', 'tight') and 'March' in later['said'] and later['rests_on']


def test_retirement_needs_ages_then_gives_a_range(world):
    assert plan.retirement(TODAY, ALEX)['missing'] == ['ages']
    taps.set_plan(ALEX, people=[{'name': 'Alex', 'age': 41, 'ss': 2400}, {'name': 'Sam', 'age': 39, 'ss': 2000}])
    found = plan.retirement(TODAY, ALEX)
    worse, middle, better = (p['age'] for p in found['paths'])
    assert found['ready'] and middle is not None and (worse or 99) >= middle >= better
    assert found['assumptions']['saving'] == 500                # the Roth contributions Hub found, not the savings transfer
    richer = plan.retirement(TODAY, ALEX, {'saving': 3000})
    assert richer['paths'][1]['age'] <= middle


def test_goals_track_an_account_or_what_was_typed(world):
    taps.goal(ALEX, 'add', 'Emergency fund', 20000, '2027-06-30', account='High Yield Savings')
    taps.goal(ALEX, 'add', 'Trip to the coast', 4000, '2027-03-01', saved=500)
    goals = {g['name']: g for g in plan.goals(TODAY, ALEX)}
    assert goals['Emergency fund']['saved'] == account('High Yield Savings')['balance'] and goals['Emergency fund']['monthly'] > 0
    assert goals['Trip to the coast']['share'] == 12
    taps.goal(ALEX, 'done', 'trip')
    assert [g['name'] for g in plan.goals(TODAY, ALEX)] == ['Emergency fund']


# ---- who sees what ----

def test_a_private_account_shows_only_as_a_rounded_total_to_the_other_member(world):
    private_sams_checking()
    alex_full, alex_hidden = share.accounts(ALEX)
    assert 'Sam Checking' not in {a['name'] for a in alex_full}
    assert len(alex_hidden) == 1 and alex_hidden[0]['name'] == 'Sam' and alex_hidden[0]['worth'] % 100 == 0
    assert 'Sam Checking' in {a['name'] for a in share.accounts(SAM)[0]}
    assert not [t for t in share.txns(ALEX) if t['account_name'] == 'Sam Checking']
    screen = json.dumps(picture.screen(ALEX))
    assert 'SEPHORA' not in screen and 'Sephora' not in screen and 'Sam Checking' not in screen
    assert 'From a private account' in screen                      # the joint side of Sam's monthly transfer
    assert 'Sam Checking' not in {a['name'] for a in share.household()}
    flows = {f['name'] for f in recurring.flows(TODAY)}
    assert 'From a private account' in flows and 'Northwind Traders Payroll' not in flows
    with pytest.raises(taps.Refused, match='Only whoever'):
        taps.private(ALEX, account('Sam Checking')['id'], False)


def test_nobody_but_a_member_sees_or_changes_money(world):
    for login in (None, GUEST):
        headers = signed(login) if login else {'origin': 'http://testserver'}
        assert client.get('/app/api/money', headers=headers).status_code == 403
        assert client.get('/app/api/money/today', headers=headers).json() == {'line': None}
        assert client.get('/app/api/money/spending?search=groceries', headers=headers).status_code == 403
        for path in ('connect', 'read', 'budget', 'sort', 'goal', 'private', 'model', 'plan', 'undo/m123'):
            assert client.post(f'/app/api/money/{path}', json={}, headers=headers).status_code == 403
    seen = client.get('/app/api/money', headers=signed(ALEX)).json()
    assert seen['ok'] and seen['worth']['total'] and seen['links']
    assert client.post('/app/api/money/budget', json={'category': 'groceries', 'amount': 700},
                       headers={'origin': 'https://evil.example', 'tailscale-user-login': ALEX}).status_code == 403


def test_the_apps_one_undo_route_puts_a_money_change_back(world):
    done = client.post('/app/api/money/budget', json={'category': 'groceries', 'amount': 700}, headers=signed(ALEX)).json()
    assert done['batch'].startswith('m') and budget.budgets() == {'groceries': 700}
    assert client.post(f'/app/api/undo/{done["batch"]}', headers=signed(ALEX)).json()['ok']
    assert budget.budgets() == {}


# ---- Ask ----

def test_a_plain_money_question_has_an_overview_beside_it_and_no_numbers_or_raw_lines(world):
    assert ask.about('How are we doing on the budget this month?') and ask.about('can we afford a trip in March?')
    assert not ask.about('play some music') and not ask.about('turn off the lights to save money')
    private_sams_checking()
    found = json.dumps(ask.prefill('how much is in checking?', ALEX))
    assert 'Everyday Checking' in found and 'PPD ID' not in found and '9182736450' not in found
    assert 'Sam Checking' not in found and 'others_private_totals' in found
    taps.model(SAM, False)                      # Sam's switch keeps Sam's private account from the model on anyone's turn
    assert '"whose": "Sam"' not in json.dumps(ask.prefill('how much is in checking?', ALEX))
    assert ask.prefill('how much is in checking?', SAM) is None
    assert ask.prefill('how much is in checking?', None) is None and ask.prefill('how much is in checking?', GUEST) is None


def test_matching_transactions_go_by_payee_or_category_without_long_numbers(world):
    found = ask.matching(ALEX, 'Starbucks', 2)
    assert found['count'] > 5 and all(t['payee'] == 'Starbucks' for t in found['transactions'])
    by_category = ask.matching(ALEX, 'groceries', 1)
    assert {t['category'] for t in by_category['transactions']} == {'Groceries'}
    assert not any(any(ch.isdigit() for ch in t['payee']) for t in ask.matching(ALEX, 'amazon', 3)['transactions'])


def test_the_money_tool_gives_the_model_data_or_with_the_switch_off_only_a_card(world):
    ctx = turn()
    answer = ask.money(ctx, 'month')
    assert 'data' in answer and ctx.deps.again and ctx.deps.strangers and ctx.deps.cards[0]['kind'] == 'money'
    taps.model(ALEX, False)
    ctx = turn()
    answer = ask.money(ctx, 'month')
    assert answer == {'done': 'Shown to them on a card. Say nothing more.'} and ctx.deps.said and ctx.deps.strangers
    assert ask.money(turn(GUEST), 'month')['done'] and not ask.prefill('how are we doing?', GUEST)


def test_ask_changes_money_with_undo_but_never_privacy_and_not_with_payees_in_view(world):
    ctx = turn()
    ask.money_budget(ctx, 'groceries', 650)
    card = ctx.deps.cards[0]
    assert budget.budgets() == {'groceries': 650} and card['batch'].startswith('m') and ctx.deps.said == ['Groceries is $650 a month.']
    seen = turn(strangers=True)
    ask.money_budget(seen, 'groceries', 9999)
    assert budget.budgets() == {'groceries': 650} and 'changed nothing' in seen.deps.said[0]
    ask.money_sort(turn(), 'Farmers', 'groceries')
    assert store.one('select category from money_rules')['category'] == 'groceries'
    names = {t.__name__ for t in tools.ALL}
    assert {'money', 'what_if', 'money_budget', 'money_plan'} <= names and not any('private' in n or 'model' in n for n in names)
    refused = turn()
    assert 'not_done' in ask.money_budget(refused, 'bananas', 10)


def test_what_if_answers_without_keeping_anything(world):
    ctx = turn()
    ask.what_if(ctx, 'afford', 4000, '2027-03-15', 'a trip')
    card = ctx.deps.cards[0]
    assert card['show'] == 'afford' and card['verdict'] in ('yes', 'tight', 'no') and ctx.deps.said
    ctx = turn()
    ask.what_if(ctx, 'retirement', save_monthly=2000)
    assert 'ages' in ctx.deps.said[0]
    assert not store.rows('select * from money_plan') and not store.rows('select * from money_changes')


# ---- statement files ----

OFX = """OFXHEADER:100
DATA:OFXSGML
<OFX><SIGNONMSGSRSV1><SONRS><FI><ORG>Pine Credit Union</ORG></FI></SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><BANKID>123456789<ACCTID>000111222333<ACCTTYPE>SAVINGS</BANKACCTFROM>
<BANKTRANLIST><STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261102<TRNAMT>250.00<FITID>F1<NAME>DEPOSIT</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261105<TRNAMT>-40.00<FITID>F2<NAME>PINE CU SERVICE FEE</STMTTRN></BANKTRANLIST>
<LEDGERBAL><BALAMT>5310.00<DTASOF>20261110</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""


def test_a_statement_file_adds_an_account_keeping_only_the_last_four_digits(world):
    said = client.post('/app/api/money/file?name=pine.ofx', content=OFX.encode(), headers=signed(SAM)).json()['said']
    assert '2 new transactions' in said
    added = store.one("select * from money_accounts where bank='Pine Credit Union'")
    assert added['ends'] == '2333' and added['owner'] == SAM and added['balance'] == 5310
    assert b'000111222333' not in (config.DATA_DIR / store.FILE).read_bytes()
    again = client.post('/app/api/money/file?name=pine.ofx', content=OFX.encode(), headers=signed(SAM)).json()['said']
    assert '0 new transactions' in again


def test_a_csv_goes_to_a_new_account_or_skips_what_simplefin_already_read(world):
    rows = 'Date,Description,Amount\n11/02/2026,FARMERS MARKET,-35.00\n11/03/2026,BAKERY ON 5TH,-12.25\n'
    said = sync.add_file('cash.csv', rows.encode(), ALEX, 'new', new_name='Cash jar', new_kind='cash')
    assert 'Cash jar: 2 new' in said
    checking = account('Everyday Checking')
    sat = store.one("select day, amount from money_txns where account=? and payee like '%FARMERS%' order by day desc", (checking['id'],))
    m, d, y = sat['day'][5:7], sat['day'][8:], sat['day'][:4]
    copy = f'Date,Description,Amount\n{m}/{d}/{y},SQ *FARMERS MARKET SPRINGFIELD WA,{sat["amount"]}\n{m}/{d}/{y},LIBRARY FINE,-3.00\n'
    assert '1 new transaction' in sync.add_file('chk.csv', copy.encode(), ALEX, checking['id'])
    with pytest.raises(sync.Refused):
        sync.add_file('x.csv', b'just words', ALEX, checking['id'])
    with pytest.raises(files.Unreadable):
        files.read('x.ofx', b'<OFX></OFX>')


# ---- the screen, Today, and the dry run ----

def test_the_screen_and_todays_line(world):
    screen = picture.screen(ALEX)
    assert screen['sentence'] and screen['ahead']['ok'] and screen['coming'] and screen['worth']['groups']
    assert {c['key'] for c in screen['categories']} >= {'groceries', 'housing'}
    line = client.get('/app/api/money/today', headers=signed(ALEX)).json()['line']
    assert line['title']
    nothing = money_world.Bridge()
    assert nothing and picture.today_line(None) is None


def test_before_anything_is_connected_the_screen_says_how_to_start(monkeypatch):
    money_world.install(monkeypatch, connect=False)
    screen = picture.screen(ALEX)
    assert screen['sentence'] == 'Connect your banks to start.' and screen['needs'][0]['do'] == 'connect'
    assert picture.today_line(ALEX) is None


def test_the_dry_run_keeps_money_changes_in_a_copy(world):
    from hub.golden import dryrun
    with dryrun.layer():
        taps.set_budget(ALEX, 'groceries', 1)
        assert budget.budgets() == {'groceries': 1}
    assert budget.budgets() == {}


# ---- the Money dashboard (hub/app/boards/money.py): Money's one page, its Today line and each member's settings ----

def test_the_money_dashboard_is_the_members_and_keeps_a_private_account_a_total(world):
    from hub.app import boards
    private_sams_checking()
    assert 'money' not in [b['id'] for b in boards.listing(GUEST)]
    assert boards.picture('money', None) is None and boards.picture('money', GUEST) is None
    seen = boards.picture('money', ALEX)
    blocks = {b['id']: b for b in seen['blocks']}
    assert {'numbers', 'month', 'coming', 'accounts', 'about'} <= set(blocks)
    names = [r['title'] for r in blocks['accounts']['rows']]
    assert 'Sam Checking' not in names and 'Net worth' in names            # Sam's private account: only in the totals
    assert 'Sam Checking' in [r['title'] for r in {b['id']: b for b in boards.picture('money', SAM)['blocks']}['accounts']['rows']]
    keys = {s['key'] for s in seen['settings']}
    assert 'token' in keys and 'ask' in keys and not any(account('Sam Checking')['id'] in k for k in keys)
    assert boards.facts('money', ALEX)['accounts'] and boards.facts('money', GUEST) == {'note': 'That dashboard isn’t this person’s to read.'}


def test_the_dashboards_settings_are_the_members_own(world):
    from hub.app import boards
    mine = account('Everyday Checking')['id']
    boards.change(ALEX, 'money', f'private:{mine}', 'on')
    assert account('Everyday Checking')['share'] == 'private'
    with pytest.raises(boards.Refused):
        boards.change(SAM, 'money', f'private:{mine}', '')                 # not Sam's account to open up
    boards.change(ALEX, 'money', 'ask', '')
    assert not share.model_on(ALEX) and share.model_on(SAM)
    assert boards.facts('money', ALEX) == {'note': 'This member keeps their money out of Ask’s model.'}
    with pytest.raises(boards.Refused):
        boards.change(None, 'money', 'ask', 'on')
    r = client.post('/app/api/board/money/set', json={'key': 'ask', 'value': 'on'}, headers=signed(ALEX))
    assert r.status_code == 200 and share.model_on(ALEX)


# ---- what the review found: private totals, refusals and polite changes ----

def test_a_goal_on_anothers_private_account_shows_only_a_rounded_total_and_never_reaches_a_switched_off_model(world):
    private_sams_checking()
    taps.goal(SAM, 'add', 'Trip', 20000, account='Sam Checking')
    exact = account('Sam Checking')['balance']
    goal = next(g for g in plan.goals(TODAY, ALEX) if g['name'] == 'Trip')
    assert goal['saved'] % 100 == 0 and goal['account'] is None and goal['saved'] != exact
    assert next(g for g in plan.goals(TODAY, SAM) if g['name'] == 'Trip')['saved'] == round(exact, 2)
    taps.model(SAM, False)
    seen = ask.overview(ALEX, TODAY)
    assert not any(g['name'] == 'Trip' for g in seen['goals']) and seen['others_private_totals'] == []
    listed = sum(kinds.worth(a['kind'], a['balance']) for a in share.accounts(ALEX)[0] if share.model_may_see(a))
    assert seen['net_worth'] == round(listed, 2) and seen['retirement'] is None


def test_which_one_goes_to_the_person_not_the_model_and_ends_changes_for_the_request(world):
    t = turn(ALEX, 'remove the a bill')
    result = ask.money_bill(t, 'remove', 'a')
    assert 'Which' not in json.dumps(result) and t.deps.said and t.deps.said[-1].startswith('Which')
    assert t.deps.strangers
    ask.money_budget(t, 'groceries', 700)
    assert 'changed nothing' in t.deps.said[-1]


def test_a_polite_change_is_not_a_question_and_nan_is_no_amount(world):
    assert not ask.about('Can you set the groceries budget to $700?') and ask.about('How is the budget looking?')
    with pytest.raises(taps.Refused):
        taps.set_budget(ALEX, 'groceries', 'nan')
