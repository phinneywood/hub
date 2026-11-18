"""Money in words: amounts, months and days the way Hub says them in a sentence."""
from datetime import date


def dollars(amount, cents=None):
    """"$84", "$1,240", "$12.50", "−$40". Cents only under $100 unless asked, and never ".00"."""
    if amount is None:
        return ''
    value = float(amount)
    sign = '−' if value < 0 else ''
    value = abs(value)
    show_cents = cents if cents is not None else value < 100 and round(value % 1, 2) != 0
    text = f'{value:,.2f}' if show_cents else f'{round(value):,}'
    return f'{sign}${text}'


def short(amount):
    """"$1.2k", "$950k", "$1.4M": for a chart's edge or a tight line."""
    value = abs(float(amount or 0))
    sign = '−' if (amount or 0) < 0 else ''
    if value >= 1_000_000:
        return f'{sign}${value / 1_000_000:.1f}M'.replace('.0M', 'M')
    if value >= 10_000:
        return f'{sign}${round(value / 1000):,}k'
    if value >= 1000:
        return f'{sign}${value / 1000:.1f}k'.replace('.0k', 'k')
    return f'{sign}${round(value):,}'


def day(on, today=None):
    """"today", "tomorrow", "Friday" within a week, else "Dec 2"."""
    on = on if isinstance(on, date) else date.fromisoformat(str(on)[:10])
    if today is not None:
        gap = (on - today).days
        if gap == 0:
            return 'today'
        if gap == 1:
            return 'tomorrow'
        if gap == -1:
            return 'yesterday'
        if 1 < gap < 7:
            return on.strftime('%A')
    return on.strftime('%b %-d')


def month(on):
    return on.strftime('%B')


def months(count):
    count = int(round(count))
    return f'{count} month{"s" * (count != 1)}'


def years(count):
    count = int(round(count))
    return f'{count} year{"s" * (count != 1)}'


def share(part, whole):
    return 0 if not whole else round(100 * part / whole)
