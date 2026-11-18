from datetime import timedelta

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from ... import home
from . import read

router = APIRouter()


@router.get("")
def week():
    """The older Calendar page is the Calendar board now (hub/app/boards/calendar.py)."""
    return RedirectResponse("/app/#board/calendar", status_code=302)


def covered(event, today):
    """Each day of the week shown that the event runs on, so one that began yesterday still shows under Today."""
    first, last = max(event['day'], today), min(event['last_day'], today + timedelta(days=read.DAYS - 1))
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def clock(event, day):
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
