"""Every address an errand's tap opens, built by Hub and never by a model.

- A link out of an email is offered only when it is https, carries no login, and goes to the sender's own site: the
  same registrable domain as the address the email came from (`pay.example.com` for mail from
  `billing@news.example.com`). A link through a mailing service, or anywhere else, is never offered. For a bill
  or a renewal whose email had no such link (and a booking or a form with no other way to finish it), Hub offers the
  sender's own home page instead, unless the sender writes from a personal mailbox (`gmail.com` and the like), which
  has no site of its own.
- A reply opens in the owner's mail app (`mailto:`, the phone's default) or in Gmail on the web, with the address,
  the subject and the drafted words filled in. Nothing is sent until the owner sends it there.
- An event opens in Google Calendar's own "new event" page, filled in. Nothing is saved until the owner saves it.
- A phone number written in the email can be called (`tel:`).
"""
import re
from datetime import date, datetime, time, timedelta
from email.utils import getaddresses
from urllib.parse import quote, urlencode, urlsplit

from ... import home, senders

URL_MOST = 2000
LINKS_MOST = 12
HOST = re.compile(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?')
# A second level that is not a site of its own under a country's ending (`example.co.uk`, `example.com.au`).
GENERIC = {'com', 'net', 'org', 'co', 'gov', 'edu', 'ac', 'or', 'ne', 'go', 'gob', 'gouv', 'nic', 'mil', 'ltd', 'plc',
           'sch', 'nhs', 'police', 'me', 'id', 'info', 'biz', 'nom', 'k12'}
# Mailboxes anyone can have: mail from one of these is from a person, and the domain is nobody's site.
PERSONAL = {'gmail.com', 'googlemail.com', 'yahoo.com', 'ymail.com', 'outlook.com', 'hotmail.com', 'live.com', 'msn.com',
            'icloud.com', 'me.com', 'mac.com', 'aol.com', 'proton.me', 'protonmail.com', 'pm.me', 'fastmail.com', 'hey.com',
            'gmx.com', 'zoho.com', 'comcast.net', 'att.net', 'verizon.net', 'sbcglobal.net', 'yahoo.co.uk'}
# Addresses nobody reads replies at: no-reply, notifications (a calendar's invitations come from one), bounces.
NO_REPLY = re.compile(r'no-?reply|do-?not-?reply|notif|^(?:alerts?|mailer-daemon|bounces?|postmaster|automated)$', re.I)
# Links every newsletter carries that are never the way to get something done.
SKIP = re.compile(r'unsubscribe|preferences|privacy|terms|view (?:this|it|in|the email|online)|in (?:your )?browser'
                  r'|manage (?:your )?(?:e-?mail|subscription|notification)|opt[- ]?out|facebook|twitter|instagram'
                  r'|linkedin|youtube|tiktok|pinterest|app store|google play|download (?:the|our) app', re.I)
PHONE = re.compile(r'(?<![\d+])(?:\+?1[ .-]?)?\(?([2-9]\d{2})\)?[ .-]?([2-9]\d{2})[ .-]?(\d{4})(?!\d)')


def site(host):
    """The registrable part of a host name, as a person would name the site: `example.com` for `pay.example.com`,
    `example.co.uk`, a school district's `district.k12.wa.us`."""
    labels = [label for label in str(host or '').lower().strip('.').split('.') if label]
    if len(labels) <= 2:
        return '.'.join(labels)
    if labels[-1] == 'us' and len(labels) >= 4 and labels[-3] == 'k12':
        return '.'.join(labels[-4:])
    if len(labels[-1]) == 2 and (labels[-2] in GENERIC or (labels[-1] == 'us' and len(labels[-2]) == 2)):
        return '.'.join(labels[-3:])
    return '.'.join(labels[-2:])


def _site_of(address):
    domain = str(address or '').rpartition('@')[2].lower()
    return site(domain) if HOST.fullmatch(domain) else ''


def address(value):
    """The one email address in a From or Reply-To header, lower-cased, or ''."""
    found = [a for _, a in getaddresses([str(value or '')]) if senders.valid_sender(a)]
    return found[0].lower() if len(found) == 1 else ''


def personal(sender):
    """Whether the sender writes from a mailbox anyone can have, rather than from an organisation's own domain."""
    return _site_of(sender) in PERSONAL


def answer_to(message, sender):
    """Where a reply goes: the Reply-To when it is on the sender's own site, else the sender. '' when nobody reads
    replies there (a no-reply or notifications address)."""
    to = address(message.get('reply_to'))
    if not to or (_site_of(to) != _site_of(sender) and not personal(sender)):
        to = sender
    return '' if not senders.valid_sender(to) or NO_REPLY.search(to.partition('@')[0]) else to


def offered(url, sender):
    """The link as Hub would offer it, {url, host, site}, or None: https, no login or odd port in it, a plain host
    name (no Unicode look-alikes, no bare IP address), and on the sender's own site."""
    text = str(url or '').strip()
    if not text or len(text) > URL_MOST or any(c.isspace() for c in text):
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or not HOST.fullmatch(host) or 'xn--' in host or parts.username or parts.password \
            or port not in (None, 443):
        return None
    mine = _site_of(sender)
    if not mine or mine in PERSONAL or site(host) != mine:
        return None
    return {'url': text, 'host': host, 'site': mine}


def links(message, sender, most=LINKS_MOST):
    """The email's own links Hub may offer, each {ref, label, url, host, site}, in the order they appear: on the
    sender's site, with words on them, and not one every newsletter has (unsubscribe, privacy, social pages)."""
    found, seen = [], set()
    for link in message.get('links') or []:
        if not isinstance(link, dict):
            continue
        label = ' '.join(str(link.get('label') or '').split())[:60]
        checked = offered(link.get('url'), sender)
        if not label or not checked or checked['url'] in seen or SKIP.search(label + ' ' + checked['url']):
            continue
        seen.add(checked['url'])
        found.append({'ref': f'L{len(found) + 1}', 'label': label, **checked})
        if len(found) >= most:
            break
    return found


def home_page(sender):
    """The sender's own site, for a bill or a form whose email had no link Hub could offer; None for a personal
    mailbox."""
    mine = _site_of(sender)
    if not mine or mine in PERSONAL:
        return None
    return {'url': f'https://{mine}/', 'host': mine, 'site': mine, 'label': mine}


def phones(text):
    """The US phone numbers written in a text, as ten digits each."""
    return {''.join(match.groups()) for match in PHONE.finditer(str(text or ''))}


def phone_label(digits):
    return f'({digits[:3]}) {digits[3:6]}-{digits[6:]}'


def call(digits):
    return f'tel:+1{digits}'


def subject(original):
    """The subject of a reply: the original's, with "Re: " in front unless it has it."""
    said = ' '.join(str(original or '').split())[:200]
    return said if re.match(r'(?i)re:', said) else f'Re: {said}' if said else ''


def mailto(to, about, body):
    """A reply in the device's own mail app, filled in. Lines end in CRLF, as RFC 6068 asks."""
    body = body.replace('\r\n', '\n').replace('\n', '\r\n')
    query = '&'.join(f'{name}={quote(value, safe="")}' for name, value in (('subject', about), ('body', body)) if value)
    return f'mailto:{quote(to, safe="@")}' + (f'?{query}' if query else '')


def gmail(to, about, body, account=''):
    """The same reply in Gmail on the web, in the owner's own account when Hub knows which one it is."""
    fields = {'view': 'cm', 'fs': '1', 'to': to, 'su': about, 'body': body}
    if account:
        fields = {'authuser': account, **fields}
    return 'https://mail.google.com/mail/?' + urlencode(fields, quote_via=quote)


def calendar(event, details='', account=''):
    """Google Calendar's own "new event" page with the event filled in, in Hub's time zone. `event`: title, date and
    optionally start, end (HH:MM) and place. Without a start it is an all-day event."""
    day = date.fromisoformat(event['date'])
    if event.get('start'):
        begins = datetime.combine(day, time.fromisoformat(event['start']))
        ends = datetime.combine(day, time.fromisoformat(event['end'])) if event.get('end') else begins + timedelta(hours=1)
        if ends <= begins:
            ends = begins + timedelta(hours=1)
        dates = f'{begins:%Y%m%dT%H%M%S}/{ends:%Y%m%dT%H%M%S}'
    else:
        dates = f'{day:%Y%m%d}/{day + timedelta(days=1):%Y%m%d}'
    fields = {'action': 'TEMPLATE', 'text': str(event['title'])[:120], 'dates': dates, 'ctz': home.PACIFIC.key}
    if event.get('place'):
        fields['location'] = str(event['place'])[:200]
    if details:
        fields['details'] = str(details)[:500]
    if account:
        fields['authuser'] = account
    return 'https://calendar.google.com/calendar/render?' + urlencode(fields, quote_via=quote)
