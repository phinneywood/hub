"""Explicit, local-only family snapshots. No mail, network, model or calendar writes.

Free text requires privacy review (manual or the opt-in automatic_review path). Field allowlists and conservative
holds are defense in depth, not a claim to recognize every person's name/secret.
Only the frozen, public-shaped snapshot is passed to the family renderer/feed.
"""
import hashlib
import json
import re
import time
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from . import audience, schedule, school, site_store, website

PACIFIC = ZoneInfo('America/Los_Angeles')
MAX_EVENTS = 2000
EVENT_FIELDS = {'title', 'details', 'date', 'start_time', 'end_time', 'location',
                'status', 'time_note', 'kind'}
PRIVATE_TEXT = re.compile(
    r'[\w.+-]+@[\w.-]+\.[a-z]{2,}|https?://|www\.|'
    r'\b(?:password|passcode|secret|token|roster|gallery|bank account|routing number|'
    r'account identifier|medical record|immunization record)\b|'
    r'(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)', re.I)
CLOCK = r'(?:[1-9]|1[0-2])(?::[0-5]\d)?\s*(?:AM|PM)'
RANGE = re.compile(r'^(' + CLOCK + r')\s*(?:–|—|\-|to)\s*(' + CLOCK + r')(?:\s+(?:Pacific|PT|PST|PDT))?$', re.I)
SHORT_RANGE = re.compile(r'^((?:[1-9]|1[0-2])(?::[0-5]\d)?)\s*(?:–|—|\-|to)\s*(' + CLOCK + r')(?:\s+(?:Pacific|PT|PST|PDT))?$', re.I)
SINGLE = re.compile(r'^(' + CLOCK + r')(?:\s+(?:Pacific|PT|PST|PDT))?$', re.I)


def serialized(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def fingerprint(value):
    return hashlib.sha256(serialized(value).encode()).hexdigest()


def safe_text(value, limit):
    return (isinstance(value, str) and len(value) <= limit and
            not PRIVATE_TEXT.search(value) and
            not any(ord(char) < 32 and char not in '\n\t' for char in value) and '\x7f' not in value)


def public_resource_url(value):
    """Deliberately narrower than the source fetcher. Never fetch this URL here.

    Private Google documents, galleries, sign-in and access-bearing query strings
    need a separate supplied/public resource, not automatic family publication.
    """
    from .sources import normalize_url
    if not isinstance(value, str):
        return None
    try:
        normalized, _ = normalize_url(value)
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (not normalized or parsed.scheme != 'https' or parsed.query or parsed.fragment or
            parsed.hostname not in school.publishable_hosts() or
            re.search(r'photo|gallery|album|roster|password|token', parsed.path, re.I)):
        return None
    return normalized


def clock(value):
    return datetime.strptime(re.sub(r'\s+', '', value.upper()), '%I:%M%p' if ':' in value else '%I%p').strftime('%H:%M')


def event_time(value):
    if not value:
        return None, None, 'Time not confirmed; date marker only.'
    match = RANGE.fullmatch(value.strip())
    if match:
        start, end = (clock(part) for part in match.groups())
        if start < end:
            return start, end, ''
    match = SHORT_RANGE.fullmatch(value.strip())
    if match:
        start = clock(match[1] + match[2][-2:])
        end = clock(match[2])
        if start < end:
            return start, end, ''
    match = SINGLE.fullmatch(value.strip())
    if match:
        return clock(match[1]), None, 'End time not confirmed.'
    # Do not infer an end time, parse ambiguous prose or silently discard it.
    return None, None, 'Time as published: ' + value + '. Calendar uses a date marker; confirm the time.'


def all_occurrences(timetables, sources):
    """Entire reviewed term, not a rolling horizon that drops/cancels older slots."""
    rows = []
    for timetable in timetables:
        rule = schedule.validate(timetable['payload'])
        cursor = date.fromisoformat(rule['start_date'])
        end = date.fromisoformat(rule['end_date'])
        while cursor <= end:
            rows.extend(schedule.occurrences([timetable], cursor.isoformat(), sources))
            cursor += timedelta(days=90)
    return rows


def candidate(site, timetables):
    """Prepare a bounded proposal. Nothing here automatically becomes published."""
    pages, events, resources, holds = [], [], [], []
    prior=latest()
    approved=prior['payload'] if prior else {'pages':[],'events':[],'resources':[]}
    approved_pages={serialized(row) for row in approved['pages']}
    approved_events={row['uid']:serialized({key:row[key] for key in EVENT_FIELDS}) for row in approved['events']}
    approved_resources={serialized(row) for row in approved['resources']}
    bound=audience.binding_hash()
    known_sources={row['view_key']:row for row in site['all_sources']}
    allowed=lambda row:audience.source_allowed(row,bound)
    for slug in website.TOPICS:
        article = site['articles'].get(slug)
        if not article:
            holds.append('A class topic has no maintained page.')
            continue
        if not safe_text(article['title'], 200) or not safe_text(article['body'], 20_000):
            holds.append('A class topic contains text requiring private review.')
            continue
        public_page={key:article[key] for key in ('slug','title','body')}
        # Prior owner approval is scoped to exact public text, never to a sender,
        # source/thread identity or future edits. Source-free owner-authored text
        # still requires the separate explicit privacy review before freezing.
        keys=article.get('source_keys',[])
        if (serialized(public_page) not in approved_pages and keys and
                not all(key in known_sources and allowed(known_sources[key]) for key in keys)):
            holds.append('A changed class page has private or unverified source audience.')
            continue
        pages.append(public_page)
    with closing(site_store.db()) as conn:
        known_keys = {row['source_key']:row['uid'] for row in conn.execute('select source_key,uid from family_event_keys')}
    rows = [row for row in site['facts'] if not row.get('scheduled') and
            row['topic'] not in site['articles'] and row.get('kind') in ('event', 'action') and row.get('date') and
            (row['date'] >= site['today'] or any('source:' + source['view_key'] in known_keys for source in row['sources']))]
    rows += all_occurrences(timetables, site['all_sources'])
    for row in rows:
        if not all(safe_text(row.get(key) or '', limit) for key, limit in
                   (('title', 200), ('details', 1200), ('location', 300), ('time', 100))):
            holds.append('A dated entry contains text requiring private review.')
            continue
        if row.get('scheduled'):
            keys = ['schedule:' + row['key']]
        else:
            keys = sorted('source:' + source['view_key'] for source in row['sources'])
        start, end, note = event_time(row.get('time'))
        status = 'cancelled' if row.get('schedule_status') == 'cancelled' else 'confirmed'
        if status == 'cancelled':
            note = 'No regular class on this date.'
        elif row['kind'] == 'action' and not row.get('time'):
            note = 'Date-only deadline; completion and payment status are not tracked.'
        event={'_keys': keys, 'title': row['title'], 'details': row.get('details') or '',
                       'date': row['date'], 'start_time': start, 'end_time': end,
                       'location': row.get('location') or '', 'status': status,
                       'time_note': note, 'kind': row['kind']}
        public_text=serialized({key:event[key] for key in EVENT_FIELDS})
        previously_reviewed=any(approved_events.get(known_keys.get(key))==public_text for key in keys)
        eligible=(bool(row['sources']) and all(allowed(s) for s in row['sources'])) if row.get('scheduled') else any(allowed(s) for s in row['sources'])
        if not previously_reviewed and not eligible:
            holds.append('A new or changed dated entry has private or unverified source audience.')
            continue
        events.append(event)
    for row in site['resources']:
        url = public_resource_url(row.get('url'))
        if not url or not safe_text(row['title'], 200) or not safe_text(row.get('details') or '', 1200):
            holds.append('A resource requires a separately reviewed public link or text.')
            continue
        resource={'title':row['title'],'details':row.get('details') or '','url':url}
        if serialized(resource) not in approved_resources and not any(allowed(s) for s in row['sources']):
            holds.append('A resource has private or unverified source audience.')
            continue
        resources.append(resource)
    if len(events) > MAX_EVENTS:
        raise ValueError('Too many class calendar entries; narrow the reviewed timetable.')
    proposal = {'pages': pages, 'events': sorted(events, key=lambda row: (row['date'], row['_keys'])),
                'resources': resources, 'holds': holds}
    validate_candidate(proposal)
    return proposal


def validate_candidate(proposal):
    """Fail closed even for operator/CLI callers; no raw source dictionaries."""
    if (not isinstance(proposal, dict) or set(proposal) != {'pages', 'events', 'resources', 'holds'} or
            not all(isinstance(value, list) for value in proposal.values()) or
            len(proposal['events']) > MAX_EVENTS or len(proposal['pages']) > len(website.TOPICS) or
            len(proposal['resources']) > 100):
        raise ValueError('Invalid family preview fields.')
    seen = set()
    for page in proposal['pages']:
        if (set(page) != {'slug', 'title', 'body'} or page['slug'] not in website.TOPICS or
                page['slug'] in seen or not page['title'].strip() or not page['body'].strip() or
                not safe_text(page['title'], 200) or not safe_text(page['body'], 20_000)):
            raise ValueError('Invalid family topic text.')
        seen.add(page['slug'])
    keys_seen = set()
    for row in proposal['events']:
        if (set(row) != EVENT_FIELDS | {'_keys'} or not isinstance(row['_keys'], list) or
                not 1 <= len(row['_keys']) <= 500):
            raise ValueError('Invalid family event fields.')
        for key in row['_keys']:
            if not re.fullmatch(r'(?:schedule:[a-f0-9]{24}|source:[a-f0-9]{64})', key) or key in keys_seen:
                raise ValueError('Conflicting family event identities; review duplicates.')
            keys_seen.add(key)
        if (not row['title'].strip() or row['status'] not in ('confirmed', 'cancelled') or
                row['kind'] not in ('action', 'event') or
                not all(safe_text(row[field], limit) for field, limit in
                        (('title', 200), ('details', 1200), ('location', 300), ('time_note', 300)))):
            raise ValueError('Invalid family event text.')
        if date.fromisoformat(row['date']).isoformat() != row['date']:
            raise ValueError('Invalid family event date.')
        start, end = row['start_time'], row['end_time']
        if start is None and end is not None:
            raise ValueError('A calendar end time needs a confirmed start.')
        if start is not None:
            times = [start, end] if end is not None else [start]
            if (not all(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value) for value in times) or
                    (end is not None and start >= end)):
                raise ValueError('Invalid family event time.')
            for value in times:
                local_datetime(row['date'], value)
    for resource in proposal['resources']:
        if (set(resource) != {'title', 'details', 'url'} or not public_resource_url(resource['url']) or
                not safe_text(resource['title'], 200) or not safe_text(resource['details'], 1200)):
            raise ValueError('Invalid public family resource.')


def latest():
    with closing(site_store.db()) as conn:
        row = conn.execute('select * from family_previews order by revision desc limit 1').fetchone()
    return {**dict(row), 'payload': json.loads(row['payload'])} if row else None


def save_preview(proposal, expected_revision, reviewed=False):
    """Freeze site and calendar together, retaining identities and cancellation tombstones.

    This is not an external publish operation and creates no subscription tokens.
    Machine-reviewed editions use automatic_review.save_preview, which checks its
    immutable local assessment before entering this shared storage boundary.
    Private source aliases never enter the stored public payload.
    """
    if not reviewed:
        raise ValueError('Review every included paragraph, event and resource for family privacy first.')
    validate_candidate(proposal)
    digest = fingerprint(proposal)
    stamp = time.time()
    with closing(site_store.db()) as conn, conn:
        conn.execute('begin immediate')
        current = conn.execute('select * from family_previews order by revision desc limit 1').fetchone()
        revision = current['revision'] if current else 0
        if expected_revision != revision:
            raise site_store.RevisionConflict('The local family preview changed. Reopen its review page.')
        if current and current['fingerprint'] == digest:
            return revision
        existing = {row['uid']: {**dict(row), 'payload': json.loads(row['payload'])}
                    for row in conn.execute('select * from family_calendar')}
        aliases = {row['source_key']: row['uid'] for row in conn.execute('select * from family_event_keys')}
        included = set()
        for row in proposal['events']:
            matches = {aliases[key] for key in row['_keys'] if key in aliases}
            if len(matches) > 1:
                raise ValueError('Previously separate events now overlap; resolve their identities before previewing.')
            uid = next(iter(matches)) if matches else uuid.uuid4().hex
            if uid in included:
                raise ValueError('A previously combined event was split; review its identities before previewing.')
            included.add(uid)
            payload = {key: row[key] for key in EVENT_FIELDS}
            prior = existing.get(uid)
            changed = not prior or prior['payload'] != payload
            sequence = prior['sequence'] + int(changed) if prior else 0
            created = prior['created'] if prior else stamp
            updated = stamp if changed else prior['updated']
            conn.execute('insert or replace into family_calendar values(?,?,?,?,?)',
                         (uid, sequence, created, updated, serialized(payload)))
            for key in row['_keys']:
                conn.execute('insert or ignore into family_event_keys values(?,?)', (key, uid))
        for uid, prior in existing.items():
            if uid in included:
                continue
            # Withdrawing private/incorrect text must not republish that text in a tombstone.
            withdrawn = {**prior['payload'], 'title': 'Class calendar entry withdrawn', 'details': '',
                         'location': '', 'status': 'cancelled', 'time_note': 'Withdrawn from the reviewed class calendar.'}
            if withdrawn != prior['payload']:
                conn.execute('update family_calendar set sequence=sequence+1,updated=?,payload=? where uid=?',
                             (stamp, serialized(withdrawn), uid))
        ledger = [{**json.loads(row['payload']), 'uid': row['uid'], 'sequence': row['sequence'],
                   'created': row['created'], 'updated': row['updated']}
                  for row in conn.execute('select * from family_calendar order by uid')]
        if len(ledger) > MAX_EVENTS:
            raise ValueError('The retained calendar reached its safety limit; review before adding entries.')
        snapshot = {'schema': 1, 'title': school.class_title(), 'revision': revision + 1, 'updated': stamp,
                    'timezone': 'America/Los_Angeles', 'pages': proposal['pages'],
                    'events': sorted(ledger, key=lambda row: (row['date'], row['uid'])),
                    'resources': proposal['resources']}
        conn.execute('insert into family_previews values(?,?,?,?)',
                     (revision + 1, digest, stamp, serialized(snapshot)))
    return revision + 1


def local_datetime(day, value):
    naive = datetime.fromisoformat(day + 'T' + value)
    local = naive.replace(tzinfo=PACIFIC)
    if (local.astimezone(timezone.utc).astimezone(PACIFIC).replace(tzinfo=None) != naive or
            local.utcoffset() != naive.replace(tzinfo=PACIFIC, fold=1).utcoffset()):
        raise ValueError('A calendar time is ambiguous at a daylight-saving transition.')
    return local


def utc_stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def ical_text(value):
    return value.replace('\\', '\\\\').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def fold_line(value):
    """RFC 5545: 75 octets, including continuation space, never split UTF-8."""
    lines, current, size = [], '', 0
    for char in value:
        length = len(char.encode('utf-8'))
        if size + length > 75:
            lines.append(current)
            current, size = ' ', 1
        current += char
        size += length
    lines.append(current)
    return '\r\n'.join(lines)


def calendar_feed(snapshot):
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', f'PRODID:-//Hub//Reviewed {school.short_name()} class calendar//EN',
             'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'X-WR-CALNAME:' + school.class_title(),
             'X-WR-TIMEZONE:America/Los_Angeles', 'REFRESH-INTERVAL;VALUE=DURATION:PT6H', 'X-PUBLISHED-TTL:PT6H']
    for row in snapshot['events']:
        lines += ['BEGIN:VEVENT', 'UID:' + row['uid'] + '@preschool.hub.invalid',
                  'SEQUENCE:' + str(row['sequence']), 'DTSTAMP:' + utc_stamp(row['updated']),
                  'CREATED:' + utc_stamp(row['created']), 'LAST-MODIFIED:' + utc_stamp(row['updated'])]
        if row['start_time']:
            for field, value in (('DTSTART', row['start_time']), ('DTEND', row['end_time'])):
                if value is not None:
                    lines.append(field + ':' + local_datetime(row['date'], value).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        else:
            day = date.fromisoformat(row['date'])
            lines += ['DTSTART;VALUE=DATE:' + day.strftime('%Y%m%d'),
                      'DTEND;VALUE=DATE:' + (day + timedelta(days=1)).strftime('%Y%m%d')]
        description = '\n\n'.join(part for part in (row['details'], row['time_note']) if part)
        lines += ['SUMMARY:' + ical_text(row['title']), 'DESCRIPTION:' + ical_text(description),
                  'LOCATION:' + ical_text(row['location']), 'STATUS:' + row['status'].upper(),
                  'TRANSP:TRANSPARENT', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    return ('\r\n'.join(fold_line(line) for line in lines) + '\r\n').encode('utf-8')


def family_html(snapshot):
    """Standalone renderer: no Hub base template, private context, tools or sources."""
    from .templating import templates
    today = datetime.now(PACIFIC).date().isoformat()
    upcoming = [row for row in snapshot['events'] if row['date'] >= today]
    earlier = [row for row in snapshot['events'] if row['date'] < today]
    return templates.env.get_template('preschool/family.html').render(
        snapshot=snapshot, upcoming=upcoming[:6], later=upcoming[6:], earlier=earlier)


def stylesheet():
    return (Path(__file__).parent / 'static' / 'preschool-family.css').read_text()
