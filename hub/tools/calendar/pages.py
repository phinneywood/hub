from datetime import datetime

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
    for event in events:
        if not days or days[-1]['date'] != event['day']:
            days.append({'date': event['day'], 'label': home.day_label(event['day'].isoformat(), now.date()), 'events': []})
        days[-1]['events'].append(dict(event, clash=id(event) in clashes,
                                       time='All day' if event['all_day'] else
                                       f'{home.clock_label(event["start"].strftime("%H:%M"))}–{home.clock_label(event["end"].strftime("%H:%M"))}'))
    return render(request, "calendar/week.html", active="calendar", days=days, problem=problem)
