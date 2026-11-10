"""Bounded iCalendar extraction; never RSVP, expand recurrences or change a calendar."""
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOCAL = ZoneInfo('America/Los_Angeles')


def unescape(value):
    return re.sub(r'\\([nN,;\\])', lambda m: '\n' if m.group(1) in 'nN' else m.group(1), value).strip()


def datetime_value(prop):
    if not prop:
        return None, None, 'No start date in invitation.'
    params, value = prop
    value = value.strip()
    try:
        if re.fullmatch(r'\d{8}', value):
            return datetime.strptime(value, '%Y%m%d').date().isoformat(), None, ''
        if not re.fullmatch(r'\d{8}T\d{6}Z?', value):
            raise ValueError()
        dt = datetime.strptime(value.rstrip('Z'), '%Y%m%dT%H%M%S')
        if value.endswith('Z'):
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            zone_match = re.search(r'(?:^|;)TZID=([^;:]+)', params, re.I)
            if not zone_match:
                return dt.date().isoformat(), None, 'Invitation has a floating time without a time zone.'
            zone = ZoneInfo(zone_match.group(1).strip('"'))
            first = dt.replace(tzinfo=zone, fold=0)
            second = dt.replace(tzinfo=zone, fold=1)
            if first.utcoffset() != second.utcoffset():
                return dt.date().isoformat(), None, 'Invitation time falls in an ambiguous or skipped daylight-saving hour.'
            dt = first
        local = dt.astimezone(LOCAL)
        return local.date().isoformat(), local.strftime('%-I:%M %p'), ''
    except (ValueError, ZoneInfoNotFoundError):
        return None, None, 'Invitation date or time zone needs review.'


def parse(text):
    if not isinstance(text, str) or not 1 <= len(text) <= 50000:
        raise ValueError('Calendar attachment size is invalid.')
    text = re.sub(r'\r?\n[ \t]', '', text)
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    if not any(line.upper() == 'BEGIN:VCALENDAR' for line in lines) or not any(line.upper() == 'END:VCALENDAR' for line in lines):
        raise ValueError('Calendar attachment is incomplete.')
    events, current = [], None
    method = ''
    for line in lines:
        if len(line) > 10000:
            raise ValueError('Calendar line is too long.')
        if current is None and line.upper().startswith('METHOD:'):
            method = line.split(':', 1)[1].strip().upper()
        if line.upper() == 'BEGIN:VEVENT':
            if current is not None:
                raise ValueError('Invalid calendar structure.')
            current = {}
        elif line.upper() == 'END:VEVENT':
            if current is None:
                raise ValueError('Invalid calendar structure.')
            events.append(current)
            current = None
            if len(events) > 50:
                raise ValueError('Too many calendar events.')
        elif current is not None and ':' in line:
            left, value = line.split(':', 1)
            key = left.split(';', 1)[0].upper()
            if key in {'SUMMARY', 'DTSTART', 'DTEND', 'LOCATION', 'STATUS', 'RRULE', 'RECURRENCE-ID', 'DURATION'} and key not in current:
                current[key] = (left, value)
    if current is not None:
        raise ValueError('Calendar attachment is incomplete.')
    out = []
    for event in events:
        title = unescape(event.get('SUMMARY', ('', 'Untitled invitation'))[1])[:200] or 'Untitled invitation'
        day, start, issue = datetime_value(event.get('DTSTART'))
        end_day, end, end_issue = datetime_value(event.get('DTEND')) if event.get('DTEND') else (None, None, '')
        warnings = [issue, end_issue]
        if event.get('RRULE'):
            warnings.append('Recurring invitation: individual occurrences and exceptions need review.')
        if event.get('RECURRENCE-ID'):
            warnings.append('This is an individual occurrence of a recurring event.')
        if method == 'CANCEL' or event.get('STATUS', ('', ''))[1].upper() == 'CANCELLED':
            warnings.append('Invitation is marked cancelled; check related events.')
        if event.get('DURATION') and not event.get('DTEND'):
            warnings.append('The invitation specifies a duration; end time needs review.')
        if end_day and day and end_day != day:
            if start or (date.fromisoformat(end_day) - date.fromisoformat(day)).days != 1:
                warnings.append('Event spans multiple dates; check its end date in the invitation.')
        time = (start + (' – ' + end if end and end_day == day else '') + ' Pacific') if start else None
        location = unescape(event.get('LOCATION', ('', ''))[1])[:300] or None
        summary = event.get('SUMMARY', ('SUMMARY', 'Untitled invitation'))
        quote = summary[0] + ':' + summary[1]
        out.append({'kind': 'event', 'title': title, 'details': 'From a calendar invitation attachment.',
                    'date': day, 'time': time, 'location': location, 'url': None,
                    'uncertainty': ' '.join(w for w in warnings if w)[:500], 'source_quote': quote[:500]})
    return out
