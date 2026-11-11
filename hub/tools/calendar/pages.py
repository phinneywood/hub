from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ... import bridge, home
from ...templating import render
from . import read

router = APIRouter()


@router.get("", response_class=HTMLResponse)
def week(request: Request):
    now = datetime.now(home.PACIFIC)
    problem, days = None, []
    try:
        events = read.week(now)
    except read.SignInNeeded:
        problem, events = 'signin', []
    except bridge.BridgeError:
        problem, events = 'down', []
    clashes = {id(e) for pair in read.overlaps(events, {e['day'] for e in events}) for e in pair}
    shown = {}
    for event in events:
        for day in _covered(event, now.date()):
            shown.setdefault(day, []).append(dict(event, clash=id(event) in clashes and day == event['day'],
                                                  time=_time(event, day)))
    for day in sorted(shown):
        listed = sorted(shown[day], key=lambda e: (e['time'] != 'All day', e['start'] or now))
        days.append({'date': day, 'label': home.day_label(day.isoformat(), now.date()), 'events': listed})
    return render(request, "calendar/week.html", active="calendar", days=days, problem=problem)


def _covered(event, today):
    """Each day of the week shown that the event runs on, so one that began yesterday still shows under Today."""
    first, last = max(event['day'], today), min(event['last_day'], today + timedelta(days=read.DAYS - 1))
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def _time(event, day):
    if event['all_day']:
        return 'All day'
    clock = lambda moment: home.clock_label(moment.strftime('%H:%M'))  # noqa: E731
    starts, ends = event['day'] == day, event['last_day'] == day
    if starts and ends:
        return f'{clock(event["start"])}–{clock(event["end"])}'
    if starts:
        return f'From {clock(event["start"])}'
    if ends:
        return f'Until {clock(event["end"])}'
    return 'All day'
