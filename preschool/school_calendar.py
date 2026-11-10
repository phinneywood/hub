"""Read the publicly linked school calendar; no arbitrary URL fetching.

The Google Sheets ID comes from the private school settings (preschool.school).
"""
import csv
import hashlib
import io
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit

import httpx

from . import school, store

MAX_BYTES = 200000


def source():
    sheet = school.calendar_sheet_id()
    return f'https://docs.google.com/spreadsheets/d/{sheet}/edit?gid=0' if sheet else ''


def export_url():
    sheet = school.calendar_sheet_id()
    return f'https://docs.google.com/spreadsheets/d/{sheet}/export?format=csv&gid=0' if sheet else ''


def __getattr__(name):
    live = {'SOURCE': source, 'EXPORT': export_url}
    if name in live:
        return live[name]()
    raise AttributeError(name)


def date_iso(raw):
    raw = raw.strip()
    m = re.search(r'\b(\d{1,2}/\d{1,2}/\d{4})\b', raw)
    if m:
        try:
            return datetime.strptime(m.group(1), '%m/%d/%Y').date().isoformat()
        except ValueError:
            return None
    m = re.search(r'\b([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b', raw)
    if not m:
        return None
    for fmt in ('%B %d %Y', '%b %d %Y'):
        try:
            return datetime.strptime(' '.join(m.groups()), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse(content):
    try:
        text = content.decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(text)))
    except (UnicodeError, csv.Error):
        raise ValueError('School calendar could not be read.') from None
    if len(rows) > 1000:
        raise ValueError('School calendar is too large.')
    header = next((i for i, r in enumerate(rows) if len(r) >= 5 and [x.strip().lower() for x in r[:5]] ==
                   ['date', 'event', 'start time', 'end time', 'location']), None)
    if header is None:
        raise ValueError('School calendar format changed.')
    out = []
    for row in rows[header + 1:]:
        row = (row + [''] * 5)[:5]
        fields = [str(v).strip()[:500] for v in row]
        raw, event, start, end, location = fields
        if not event:
            continue
        key = hashlib.sha256((raw + '\0' + event).encode()).hexdigest()
        out.append({'id': key, 'date_text': raw, 'date_iso': date_iso(raw), 'event': event,
                    'start_time': start.strip('- '), 'end_time': end.strip('- '), 'location': location.strip('- ')})
    if not out or len(out) > 500:
        raise ValueError('School calendar has no usable rows.')
    return out


def download(client=None):
    url = export_url()
    if not url:
        raise ValueError('School calendar is not configured.')
    owned = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=False)
    try:
        for _ in range(4):
            p = urlsplit(url)
            if p.scheme != 'https' or not (p.hostname == 'docs.google.com' or (p.hostname or '').endswith('.googleusercontent.com')) or p.username or p.password or p.port not in (None, 443):
                raise ValueError('School calendar redirect was not allowed.')
            with client.stream('GET', url, headers={'Accept': 'text/csv'}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get('location', ''))
                    continue
                if response.status_code != 200:
                    raise ValueError('School calendar is unavailable.')
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise ValueError('School calendar is too large.')
                return parse(bytes(data))
        raise ValueError('School calendar redirected too many times.')
    except httpx.HTTPError:
        raise ValueError('School calendar is unavailable.') from None
    finally:
        if owned:
            client.close()


def refresh(now):
    rows = download()
    store.replace_school_calendar(rows, now)
    return len(rows)
