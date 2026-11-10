"""Conservative local guards, not a general natural-language fact checker.

Only unambiguous supported date formats and numeric currency amounts are accepted.
Anything outside those bounds needs source review rather than a guessed date.
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

MONTHS = {name: number for number, names in enumerate((
    ('january', 'jan'), ('february', 'feb'), ('march', 'mar'), ('april', 'apr'),
    ('may',), ('june', 'jun'), ('july', 'jul'), ('august', 'aug'),
    ('september', 'sep', 'sept'), ('october', 'oct'), ('november', 'nov'),
    ('december', 'dec')), 1) for name in names}
NAMED_DATE = re.compile(r'\b(' + '|'.join(MONTHS) + r')\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4})\b)?', re.I)
DAY_FIRST_DATE = re.compile(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(' + '|'.join(MONTHS) + r')\.?(?:,?\s+(\d{4})\b)?', re.I)
SLASH_DATE = re.compile(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b')
ISO_DATE = re.compile(r'\b\d{4}-\d{2}-\d{2}\b')
WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
WEEKDAY = re.compile(r'\b(' + '|'.join(WEEKDAYS) + r')\b', re.I)
CHANGED = re.compile(r'\b(?:cancel(?:l)?ed|cancellation|reschedul\w*|postpon\w*)\b|\bmoved\s+(?:from|to)\b|\binstead of\b', re.I)
NUMBER = r'[-+]?\d+(?:,\d{3})*(?:\.\d+)?'
MONEY = re.compile(r'(?P<currency>US\$|USD\b|\$|EUR\b|€|GBP\b|£)\s*(?P<value>' + NUMBER +
                   r')(?!\w|[.,]\d)|(?<![\w.])(?P<suffix_value>' + NUMBER +
                   r')\s*(?P<suffix_currency>dollars?|USD|euros?|EUR|pounds?|GBP)\b', re.I)
CURRENCY = re.compile(r'US\$|\$|€|£|\b(?:USD|EUR|GBP|dollars?|euros?|pounds?)\b', re.I)


def amounts(text):
    out = set()
    for match in MONEY.finditer(text):
        currency = (match['currency'] or match['suffix_currency']).lower()
        currency = 'EUR' if currency in ('€', 'eur', 'euro', 'euros') else 'GBP' if currency in ('£', 'gbp', 'pound', 'pounds') else 'USD'
        out.add((currency, Decimal((match['value'] or match['suffix_value']).replace(',', ''))))
    return out


def referenced_dates(text, sent=None):
    """Return date evidence, including None when a recognized expression is ambiguous.

    Yearless dates use only the sent year, within 183 days; year rollovers and
    weekday-only phrases require review. Relative today/tomorrow/yesterday use LA.
    """
    origin = None
    try:
        parsed = datetime.fromisoformat(sent)
        if parsed.tzinfo:
            origin = parsed.astimezone(ZoneInfo('America/Los_Angeles')).date()
    except (TypeError, ValueError):
        pass
    result = set()

    def add(month, day, year):
        try:
            if year and len(year) != 4:
                raise ValueError()
            value = date(int(year) if year else origin.year, int(month), int(day))
            if not year and abs((value - origin).days) > 183:
                raise ValueError()
            result.add(value)
        except (ValueError, AttributeError):
            result.add(None)

    for match in ISO_DATE.finditer(text):
        try:
            result.add(date.fromisoformat(match[0]))
        except ValueError:
            result.add(None)
    for match in NAMED_DATE.finditer(text):
        add(MONTHS[match[1].lower()], match[2], match[3])
    for match in DAY_FIRST_DATE.finditer(text):
        add(MONTHS[match[2].lower()], match[1], match[3])
    for match in SLASH_DATE.finditer(text):
        add(match[1], match[2], match[3])
    for match in re.finditer(r'\b(today|tomorrow|yesterday)\b', text, re.I):
        offset = {'today': 0, 'tomorrow': 1, 'yesterday': -1}[match[1].lower()]
        result.add(origin + timedelta(days=offset) if origin else None)
    weekdays = {WEEKDAYS.index(match[1].lower()) for match in WEEKDAY.finditer(text)}
    if weekdays and (len(result) != 1 or None in result or {d.weekday() for d in result} != weekdays):
        result.add(None)
    return result


def check_claims(item, source, sent=None):
    """Return false for unsupported numeric amounts/dates or unresolved changes.

    Evidence is the item's own quote, not another unrelated fact in the email.
    This deliberately does not claim to verify every paraphrase or assignment.
    """
    if CHANGED.search(source):
        return False
    quote = item['source_quote']
    claims = ' '.join(item[key] or '' for key in ('title', 'details', 'time', 'location', 'uncertainty'))
    spans = [match.span() for match in MONEY.finditer(claims)]
    if any(not any(start <= match.start() and match.end() <= end for start, end in spans)
           for match in CURRENCY.finditer(claims)):
        return False
    if not amounts(claims) <= amounts(quote):
        return False
    supported = referenced_dates(quote, sent)
    if item['date'] and supported != {date.fromisoformat(item['date'])}:
        return False
    # Also check concrete dates embedded in prose, even if the date field is null.
    claimed_dates = referenced_dates(claims, sent)
    if not (claimed_dates - {None}) <= (supported - {None}):
        return False
    if None in claimed_dates:
        # Do not let an invented invalid date or a different weekday bypass the
        # guard merely because neither phrase resolves to a concrete date.
        for pattern in (NAMED_DATE, DAY_FIRST_DATE, SLASH_DATE, ISO_DATE, WEEKDAY):
            for match in pattern.finditer(claims):
                if None in referenced_dates(match[0], sent) and ' '.join(match[0].casefold().split()) not in ' '.join(quote.casefold().split()):
                    return False
    if None in supported and item['kind'] in ('event', 'action') and not item['uncertainty'].strip():
        return False
    return True
