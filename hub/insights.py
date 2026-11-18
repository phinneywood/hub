"""Hub's record of its own AI spend, worked out for the AI spend board (`hub/app/boards/spend.py`) and Ask's
`ai_spend` lookup. Read-only: no new source, no model, nothing written. (The older Insights page it once drew is
retired; `/insights` opens the board.)
"""
from datetime import timedelta
from math import floor, log10

from . import home

W, H = 360, 176                              # every chart's drawing space; the SVG scales to its card
LEFT, RIGHT, TOP, BOTTOM = 40, 16, 14, 24
PLOT_W, PLOT_H = W - LEFT - RIGHT, H - TOP - BOTTOM
SPEND_DAYS = 30
SERIES = (('overnight', 'Overnight review'), ('sort', 'Mail sorting'), ('chat', 'Ask Hub'))   # fixed order: colour follows the kind
OTHER = 'Other'
NEAR_LIMIT = 75                              # percent of a monthly limit at which the meter warns


def _nice(value):
    """The next round number at or above `value`: 1, 2, 2.5 or 5 times a power of ten."""
    if value <= 0:
        return 1
    scale = 10 ** floor(log10(value))
    return next(m * scale for m in (1, 2, 2.5, 5, 10) if value <= m * scale * (1 + 1e-9))


def _compact(n):
    return f'{n / 1000:g}K' if abs(n) >= 1000 else f'{n:g}'


def money(value):
    if not value:
        return '$0'
    return f'${value:,.2f}' if value >= 0.0995 else f'${value:.3f}'


def _short(day):
    return f'{day:%b} {day.day}'


def _y(value, low, high):
    return round(TOP + PLOT_H * (1 - (value - low) / (high - low)), 1)


def _meter(label, spent, cap, covers):
    percent = round(100 * spent / cap) if cap else 0
    state = 'bad' if spent >= cap else 'warn' if percent >= NEAR_LIMIT else 'ok'
    words = {'bad': 'Limit reached. Paused until next month.', 'warn': 'Close to the limit.', 'ok': ''}[state]
    return {'label': label, 'covers': covers, 'spent': money(spent), 'cap': money(cap), 'percent': percent, 'state': state,
            'words': words, 'width': round(min(1, spent / cap if cap else 0) * 100, 1)}


def _column(x, width, top, bottom, rounded):
    """One stacked segment: square at its base, and rounded at the top when it is the column's cap."""
    r = min(3, width / 2, bottom - top) if rounded else 0
    return (f'M{x} {bottom}V{round(top + r, 1)}Q{x} {top} {round(x + r, 1)} {top}H{round(x + width - r, 1)}'
            f'Q{x + width} {top} {x + width} {round(top + r, 1)}V{bottom}Z')


def spend(now=None):
    """Model cost by day against the two monthly limits, and how the runs went. Stacked columns: parts of a daily total."""
    from .agent import routes as chat
    from .engine import overnight, state
    now = now or home.today()
    day = now.isoformat()
    rows = state.daily(day, SPEND_DAYS)
    names = dict(SERIES)
    order = [name for _, name in SERIES] + [OTHER]
    cost = {}
    summary = {name: {'name': name, 'runs': 0, 'failed': 0, 'calls': 0, 'cost': 0.0} for name in order}
    for row in rows:
        name = names.get(row['kind'], OTHER)
        cost.setdefault(row['day'], dict.fromkeys(order, 0.0))[name] += row['cost'] or 0
        for field in ('runs', 'failed', 'calls', 'cost'):
            summary[name][field] += row[field] or 0
    days = [now - timedelta(days=SPEND_DAYS - 1 - n) for n in range(SPEND_DAYS)]
    top = _nice(max([sum(c.values()) for c in cost.values()] + [0.01]))
    slot = PLOT_W / SPEND_DAYS
    width = round(min(24, slot - 2), 1)
    floor_y, columns = TOP + PLOT_H, []
    for n, d in enumerate(days):
        parts = [(name, value) for name, value in cost.get(d.isoformat(), {}).items() if value > 0]
        x, at, segments = round(LEFT + slot * n + (slot - width) / 2, 1), floor_y, []
        for index, (name, value) in enumerate(parts):
            height = max(1.5, PLOT_H * value / top)
            cap = index == len(parts) - 1
            # A 2px gap in the surface colour separates the parts, where the part is tall enough to spare it.
            upper = at - height + (0 if cap or height < 5 else 2)
            segments.append({'series': order.index(name) + 1, 'path': _column(x, width, round(upper, 1), round(at, 1), cap)})
            at -= height
        total = sum(value for _, value in parts)
        tip = _short(d) + ''.join(f'\n{money(value)} {name}' for name, value in parts) + (
            f'\n{money(total)} in all' if len(parts) > 1 else '' if parts else '\nNothing spent')
        columns.append({'left': round(LEFT + slot * n, 1), 'width': round(slot, 1), 'segments': segments, 'tip': tip})
    shown = [name for name in order if summary[name]['runs']]
    shared = state.used(day, overnight.KIND)['month_cost']
    asked = state.spent(day, chat.KIND)['month_cost']
    return {'has': bool(rows), 'columns': columns, 'top_tick': money(top), 'mid_tick': money(top / 2),
            'mid_y': _y(top / 2, 0, top), 'top_y': TOP, 'floor_y': floor_y,
            'first_day': _short(days[0]), 'last_day': _short(days[-1]), 'month': f'{now:%B}',
            'legend': [{'series': order.index(name) + 1, 'name': name} for name in shown],
            'meters': [_meter('Overnight review and mail sorting', shared, overnight.MONTH_COST_CAP, 'The shared monthly limit'),
                       _meter('Ask Hub', asked, chat.MONTH_COST_CAP, 'The chat’s own monthly limit')],
            'rows': [{**summary[name], 'dollars': summary[name]['cost'], 'cost': money(summary[name]['cost'])} for name in shown],
            'runs': sum(s['runs'] for s in summary.values()), 'failed': sum(s['failed'] for s in summary.values())}
