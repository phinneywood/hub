"""Who is asking, and whose account a thing Hub read came from.

Hub reads mail and the calendar from Google accounts through the bridge, and may read other accounts of a person's
own. Each person in the household sees only what came from their own accounts; the household's own things (the
weather, the house, Kitchen, the house's needs) are everyone's. A request through Tailscale Serve carries the
person's Tailscale login; one without a login comes from this Mac itself.

Everything read from an account carries that account's name, never a login, and `sees(login, account)` is the one
check (`accounts(login)` for filtering a list). "default" is the bridge's main Google account: its /gmail and
/calendar reads. The shared mailbox (/gmail/shared, the preschool's) is kept apart and is no one's account here.

household.json, in the private data folder, says who is who:

    "people": [{"name": "…", "logins": ["…"], "accounts": ["default"]}]

Without "people": when exactly one login is allowed (HUB_ALLOWED_LOGINS), it owns "default"; when more are,
nobody sees an account until "people" says whose it is. No login, or a login "people" doesn't name, sees the
household's things only. Anything unreadable in the file counts as no one's: it fails closed. The owner is whoever
owns "default" (`owner(login)`); everyone else in "people" is a member.
"""
from . import config, household

DEFAULT = 'default'


def _login(login):
    return login.strip().lower() if isinstance(login, str) and login.strip() else None


def _named():
    """The people household.json names, as [{'name', 'logins', 'accounts'}]; None when it names none; [] when what
    it says can't be read, so that nobody sees an account."""
    try:
        found = household.load().get('people')
    except (OSError, ValueError):
        return []
    if found is None:
        return None
    if not isinstance(found, list):
        return []
    people = []
    for entry in found:
        if not isinstance(entry, dict):
            continue
        logins = [_login(x) for x in entry.get('logins') or () if _login(x)] if isinstance(entry.get('logins'), list) else []
        owned = [x.strip() for x in entry.get('accounts') or () if isinstance(x, str) and x.strip()] \
            if isinstance(entry.get('accounts'), list) else []
        if logins:
            people.append({'name': str(entry.get('name') or '').strip()[:60], 'logins': logins, 'accounts': owned})
    return people


def who(login):
    """The household member this login belongs to, {'name', 'logins', 'accounts'}, or None (no login, or one Hub
    doesn't know)."""
    login = _login(login)
    if login is None:
        return None
    named = _named()
    if named is None:
        allowed = {_login(x) for x in config.ALLOWED_LOGINS} - {None}
        return {'name': '', 'logins': [login], 'accounts': [DEFAULT]} if allowed == {login} else None
    return next((person for person in named if login in person['logins']), None)


def accounts(login):
    """The accounts whose things this login may see; empty for the household's things only."""
    person = who(login)
    return frozenset(person['accounts']) if person else frozenset()


def sees(login, account):
    """Whether this login may see a thing read from this account."""
    return isinstance(account, str) and account in accounts(login)


def owner(login):
    """Whether this login is the household's owner: the person whose Google account is the bridge's main one."""
    return sees(login, DEFAULT)
