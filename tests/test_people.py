"""Who sees what: each person only what came from their own accounts; no login, or an unknown one, only the
household's things (hub/people.py). The people and logins here are made up."""
import json

from hub import config, people

ALEX, SAM = 'alex@example.com', 'sam@example.com'


def write(value):
    (config.DATA_DIR / 'household.json').write_text(json.dumps(value))


def test_with_one_allowed_login_and_no_people_that_login_owns_the_main_account():
    assert people.accounts('me@example.com') == {'default'} and people.sees('me@example.com', 'default')
    assert people.owner('me@example.com') and not people.owner(ALEX) and not people.owner(None)
    assert people.sees(' Me@Example.com ', 'default')
    assert not people.sees(ALEX, 'default') and not people.sees(None, 'default') and not people.sees('', 'default')
    assert not people.sees('me@example.com', 'another') and not people.sees('me@example.com', None)


def test_with_two_allowed_logins_and_no_people_nobody_sees_an_account(monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ALEX, SAM})
    assert people.accounts(ALEX) == set() and people.accounts(SAM) == set() and people.who(ALEX) is None


def test_people_say_whose_account_is_whose(monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ALEX, SAM, 'guest@example.com'})
    write({'people': [{'name': 'Alex', 'logins': [ALEX, 'Alex@Example.net'], 'accounts': ['default']},
                      {'name': 'Sam', 'logins': [SAM], 'accounts': ['sam']}]})
    assert people.who('alex@example.net')['name'] == 'Alex'
    assert people.sees(ALEX, 'default') and not people.sees(ALEX, 'sam')
    assert people.sees(SAM, 'sam') and not people.sees(SAM, 'default')
    assert people.owner('Alex@example.net') and not people.owner(SAM)
    assert people.accounts('guest@example.com') == set() and people.accounts(None) == set()


def test_people_that_cant_be_read_count_as_no_one():
    for broken in ({'people': {'name': 'Alex'}}, {'people': ['Alex', {'name': 'Sam', 'accounts': ['default']}]},
                   {'people': [{'name': 'Alex', 'logins': 'me@example.com', 'accounts': ['default']}]}):
        write(broken)
        assert people.accounts('me@example.com') == set()
    (config.DATA_DIR / 'household.json').write_text('[not json')
    assert people.accounts('me@example.com') == set()


def test_a_person_with_no_account_sees_the_households_things_only():
    write({'people': [{'name': 'Alex', 'logins': ['me@example.com']}]})
    assert people.who('me@example.com')['name'] == 'Alex' and people.accounts('me@example.com') == set()


def test_the_catalog_says_which_sources_read_a_persons_account():
    from hub.engine import catalog
    account = {source.slug: source.account for source in catalog.SOURCES}
    assert account['gmail'] == account['calendar'] == people.DEFAULT
    assert all(account[slug] is None for slug in ('preschool', 'trip', 'network', 'house', 'hub'))
