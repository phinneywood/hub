"""Pure display helpers for the private class reader; no access or publishing code."""
import re
from datetime import datetime

def date_label(value):
    """Readable English calendar date; the machine-readable value stays unchanged."""
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        day = datetime.strptime(value, '%Y-%m-%d')
        return day.strftime('%a, %b ') + str(day.day) + ', ' + str(day.year)
    except ValueError:
        return 'Date not confirmed'


def time_label(start, end=None):
    """Display confirmed local clocks in 12-hour form, without inventing a time."""
    if start is None and end is None:
        return ''
    values = [start] if end is None else [start, end]
    if any(not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value) for value in values):
        return 'Time not confirmed'
    if end is not None and start >= end:
        return 'Time not confirmed'

    def clock(value):
        hour, minute = map(int, value.split(':'))
        return str(hour % 12 or 12) + (':' + str(minute).zfill(2) if minute else ''), 'AM' if hour < 12 else 'PM'

    first, first_period = clock(start)
    if end is None:
        return first + ' ' + first_period
    last, last_period = clock(end)
    return (first if first_period == last_period else first + ' ' + first_period) + '–' + last + ' ' + last_period


def prose_blocks(value):
    """Group authored paragraphs and explicit bullets, without rewriting copy."""
    if not isinstance(value, str):
        return []
    blocks, paragraph, items, current = [], [], [], []

    def flush():
        if paragraph:
            blocks.append({'kind': 'paragraph', 'text': '\n'.join(paragraph)})
            paragraph.clear()
        if current:
            items.append('\n'.join(current))
            current.clear()
        if items:
            blocks.append({'kind': 'list', 'items': list(items)})
            items.clear()

    for line in value.splitlines():
        if not line.strip():
            flush()
        elif re.match(r'^\s*[-*•]\s+\S', line):
            if paragraph:
                blocks.append({'kind': 'paragraph', 'text': '\n'.join(paragraph)})
                paragraph.clear()
            if current:
                items.append('\n'.join(current))
            current[:] = [line]
        elif current:
            current.append(line)
        else:
            paragraph.append(line)
    flush()
    return blocks


