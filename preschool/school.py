"""The configured school, read at run time from the owner's private household file.

The real school, its class, neighborhood organizations and web hosts never live in Git.
They come from the `school` section of `household.json` (see `hub.household`). Prompts,
rules and pages are built from these values, so the code only ever says "the school" or
"the class".

Keys:
  name                full school name, used in discovery searches and page headings
  search_name         shorter name phrase used for mail discovery and subject matching
  short_name          the school's acronym
  class_name          the family's class, singular (e.g. "Sprouts")
  class_plural        plural form used in prompts (default: class_name + "s")
  organization_name   the neighborhood organization that runs mixed newsletters
  organization_short  its acronym
  news_name           the neighborhood news blog
  source_hosts        hostnames whose public pages may be read (fetch allowlist)
  publishable_hosts   subset of source_hosts whose pages may appear as public links
  calendar_sheet_id   Google Sheets ID of the published school calendar

Fetching fails closed: without a household file, or with any host that is not a plain
hostname, no school or Google page is read.
"""
import dataclasses
import re

from hub import household

DEFAULTS = {
    'name': 'the preschool',
    'search_name': '',
    'short_name': 'the school',
    'class_name': 'Class',
    'class_plural': '',
    'organization_name': 'the neighborhood association',
    'organization_short': 'the association',
    'news_name': 'neighborhood news',
    'calendar_sheet_id': '',
}
GOOGLE_HOSTS = frozenset({'docs.google.com', 'drive.google.com', 'drive.usercontent.google.com'})
HOSTNAME = re.compile(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?')
SHEET_ID = re.compile(r'[A-Za-z0-9_-]{10,200}')
RESERVED = ('google.com', 'googleusercontent.com')


def section():
    try:
        return household.section('school')
    except ValueError:
        return {}


def text(key):
    value = section().get(key)
    if isinstance(value, str) and value.strip():
        return value
    if key == 'class_plural':
        return text('class_name') + 's'
    return DEFAULTS[key]


def configured(key):
    """The owner's own value, or '' when it is absent (never a default)."""
    value = section().get(key)
    return value if isinstance(value, str) and value.strip() else ''


def name(): return text('name')
def short_name(): return text('short_name')
def class_name(): return text('class_name')
def class_plural(): return text('class_plural')
def organization_name(): return text('organization_name')
def organization_short(): return text('organization_short')
def news_name(): return text('news_name')


def class_key():
    """Identifier form of the class name, used in model question and display-role keys."""
    return re.sub(r'[^a-z0-9]+', '_', class_name().lower()).strip('_') or 'class'


def class_role():
    return class_key() + '_instruction'


def useful_key():
    return 'useful_to_' + class_key()


def class_title():
    return f'{short_name()} {class_name()} class'


def calendar_filename():
    return re.sub(r'[^a-z0-9]+', '-', f'{short_name()} {class_key()}'.lower()).strip('-') + '.ics'


def valid_host(value):
    return (isinstance(value, str) and HOSTNAME.fullmatch(value) is not None and
            not any(value == root or value.endswith('.' + root) for root in RESERVED))


def _hosts(key):
    values = section().get(key)
    if not isinstance(values, list) or not values or not all(valid_host(value) for value in values):
        return frozenset()
    return frozenset(values)


def source_hosts():
    """School/neighborhood hosts allowed for reading; empty (no fetching) unless all are valid."""
    return _hosts('source_hosts')


def publishable_hosts():
    hosts = _hosts('publishable_hosts')
    return hosts if hosts <= source_hosts() else frozenset()


WORD = re.compile(r'[a-z0-9][a-z0-9 .-]{1,39}')


def resource_words():
    """Extra link words for the household's own school systems (e.g. a sign-up service).

    Private, so read from household.json. Fails closed: any invalid entry means no extra words.
    """
    values = section().get('resource_words')
    if not isinstance(values, list) or not all(isinstance(v, str) and WORD.fullmatch(v) for v in values):
        return ()
    return tuple(values)


def calendar_sheet_id():
    value = section().get('calendar_sheet_id')
    return value if isinstance(value, str) and SHEET_ID.fullmatch(value) else ''


class Live:
    """Template global: `{{ school.short_name }}` reads the current configuration."""
    def __getattr__(self, key):
        function = globals().get(key)
        if key.startswith('_') or not callable(function) or key in {'section', 'text', 'configured', 'valid_host', 'tokens', 'fill', 'Live'}:
            raise AttributeError(key)
        return function()


live = Live()


def tokens():
    return {'{SCHOOL}': name(), '{SHORT}': short_name(), '{CLASSES}': class_plural(), '{CLASS}': class_name(),
            '{CLASS_KEY}': class_key(), '{ORG_SHORT}': organization_short(), '{ORG}': organization_name(),
            '{NEWS}': news_name()}


def fill(value, values=None):
    """Replace `{SHORT}`-style tokens in strings, dict keys and nested containers.

    Plain replacement (not str.format), so literal braces in prompts stay untouched.
    """
    values = tokens() if values is None else values
    if isinstance(value, str):
        for token, replacement in values.items():
            value = value.replace(token, replacement)
        return value
    if isinstance(value, dict):
        return {fill(key, values): fill(item, values) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(fill(item, values) for item in value)
    if isinstance(value, list):
        return [fill(item, values) for item in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.replace(value, **{field.name: fill(getattr(value, field.name), values)
                                             for field in dataclasses.fields(value) if field.init})
    return value
