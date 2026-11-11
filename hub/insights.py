"""Insights: charts from numbers Hub already keeps. Read-only: no new source, no model, nothing written.

Each chart is worked out here as plain geometry and drawn by the template as inline SVG, so the page
needs no chart library and nothing from another site. Every chart carries its numbers as a table too.
"""
from datetime import date, timedelta
from math import ceil, floor, log10

from . import home

W, H = 360, 176                              # every chart's drawing space; the SVG scales to its card
LEFT, RIGHT, TOP, BOTTOM = 40, 16, 14, 24
PLOT_W, PLOT_H = W - LEFT - RIGHT, H - TOP - BOTTOM
INBOX_DAYS = 60
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


def inbox(history=None):
    """Inbox size, one point per day Hub looked. A line: the job is change over time."""
    from . import store
    rows = [r for r in (store.inbox_history(INBOX_DAYS) if history is None else history) if isinstance(r['total'], int)]
    if len(rows) < 2:
        return {'has': False, 'now': rows[-1]['total'] if rows else None}
    days = [date.fromisoformat(r['day']) for r in rows]
    totals = [r['total'] for r in rows]
    step = _nice((max(totals) - min(totals) or 2) * 1.3 / 2)
    low = floor(min(totals) / step) * step
    high = max(low + step, ceil(max(totals) / step) * step)
    span = (days[-1] - days[0]).days
    xs = [round(LEFT + PLOT_W * (d - days[0]).days / span, 1) for d in days]
    ys = [_y(t, low, high) for t in totals]
    edges = [LEFT] + [round((a + b) / 2, 1) for a, b in zip(xs, xs[1:])] + [W - RIGHT]
    points = [{'x': x, 'y': y, 'left': edges[n], 'width': round(edges[n + 1] - edges[n], 1),
               'tip': f'{_short(d)}\n{r["total"]:,} in inbox' + (f'\n{r["unread"]:,} unread' if isinstance(r['unread'], int) else '')}
              for n, (x, y, d, r) in enumerate(zip(xs, ys, days, rows))]
    change = totals[-1] - totals[0]
    falling = ys[-2] < ys[-1]                # the line comes down into the last point, so its label goes underneath
    return {'has': True, 'now': totals[-1], 'unread': rows[-1]['unread'], 'change': change, 'span': span,
            'change_text': f'{change:+,}' if change else 'No change', 'since': _short(days[0]),
            'ticks': [{'y': _y(low + step * n, low, high), 'label': _compact(low + step * n)}
                      for n in range(round((high - low) / step) + 1)],
            'line': 'M' + ' L'.join(f'{x} {y}' for x, y in zip(xs, ys)), 'points': points, 'end': points[-1],
            'end_label': f'{totals[-1]:,}', 'end_label_y': round(ys[-1] + (16 if falling else -9), 1), 'first_day': _short(days[0]), 'last_day': _short(days[-1]),
            'rows': [{'day': _short(d), 'total': r['total'], 'unread': r['unread']} for d, r in zip(days, rows)][::-1]}


BAR, ROW, LABELS = 14, 28, 152               # the bars chart: bar thickness, row height, room for the names


def mail():
    """What is sitting in the inbox, by the kind the last sort gave it. Bars: comparing sizes of named groups."""
    from .tools.gmail import triage
    seen = triage.picture()
    if not seen['synced_at']:
        return {'has': False}
    counts = [(triage.NAMES['needs'], len(seen['needs']))]
    counts += [(entry['name'], len(entry['open'])) for entry in seen['kinds'].values()]
    counts = [(name, n) for name, n in counts if n]
    if not counts:
        return {'has': False, 'empty': True}
    room, top = W - LABELS - 44, max(n for _, n in counts)
    bars = [{'name': name, 'count': n, 'y': 6 + ROW * at, 'width': max(2, round(room * n / top, 1))}
            for at, (name, n) in enumerate(counts)]
    return {'has': True, 'bars': bars, 'height': 6 + ROW * len(bars), 'total': sum(n for _, n in counts),
            'x': LABELS, 'bar': BAR, 'needs': len(seen['needs'])}


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
            'rows': [{**summary[name], 'cost': money(summary[name]['cost'])} for name in shown],
            'runs': sum(s['runs'] for s in summary.values()), 'failed': sum(s['failed'] for s in summary.values())}


def _soft(build):
    try:
        return build()
    except Exception:  # noqa: BLE001 - one broken source must not take the page down
        return {'has': False, 'failed': True}


def page():
    from . import cards
    from .tools.network import pages as network
    try:
        net = network.summary()
    except Exception:  # noqa: BLE001
        net = None
    return {'inbox': _soft(inbox), 'mail': _soft(mail), 'spend': _soft(spend), 'network': net,
            'asks': {key: cards.askable('insight:' + key) for key in ('inbox', 'mail', 'spend', 'network')},
            'w': W, 'h': H, 'left': LEFT, 'right': W - RIGHT, 'floor_y': TOP + PLOT_H}
