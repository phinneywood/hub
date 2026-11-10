"""POST-only trip searches keep addresses out of URLs and normal access logs."""
import threading
import time
from collections import deque

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...templating import render
from . import google, places, planner

router = APIRouter()
_lock = threading.Lock()
_searches = deque()


def page(request, **context):
    saved = places.load()
    defaults = {"origin": "", "destination": planner.area().get("default_destination", ""),
                "city": planner.area().get("city", "Your city"), "kind": "bike", "departure": "", "max_ride": 25,
                "result": None, "problem": None, "notice": None, "saved": saved,
                "kinds": planner.KINDS}
    if saved:
        defaults.update(saved)
    defaults.update(context)
    return render(request, "mobility/home.html", active="mobility", **defaults)


@router.get("", response_class=HTMLResponse)
def home(request: Request):
    return page(request)


@router.post("/search", response_class=HTMLResponse)
def search(request: Request, origin: str = Form(..., max_length=240), destination: str = Form(..., max_length=240),
           kind: str = Form("bike", max_length=10), departure: str = Form("", max_length=40), max_ride: int = Form(25),
            remember: str = Form("", max_length=8)):
    values = dict(origin=origin, destination=destination, kind=kind, departure=departure, max_ride=max_ride)
    # Invalid forms must not consume a paid-search slot.
    try:
        google.address(origin)
        google.address(destination)
        planner.parse_departure(departure)
        if kind not in planner.KINDS or max_ride not in (10, 15, 25, 40):
            raise google.MapsError("Choose a supported riding option and riding limit.")
        if origin.strip().casefold() == destination.strip().casefold():
            raise google.MapsError("Start and destination are the same. Choose two different places.")
    except google.MapsError as exc:
        return page(request, **values, problem=str(exc))
    if not _lock.acquire(blocking=False):
        return page(request, **values, problem="Another route search is running. Please wait for it to finish.")
    try:
        now = time.monotonic()
        while _searches and now - _searches[0] > 3600:
            _searches.popleft()
        if len(_searches) >= 6:
            return page(request, **values, problem="Six-search hourly safety limit reached. Please try later.")
        _searches.append(now)
        try:
            result = planner.search(origin, destination, kind, departure, max_ride)
        except google.MapsError as exc:
            return page(request, **values, problem=str(exc))
        notice = None
        if remember == "yes":
            try:
                places.save(origin, destination)
                notice = "Trip shortcut saved on this Mac. You can reverse it or forget it below."
            except OSError:
                notice = "Routes are ready, but the trip shortcut could not be saved."
        return page(request, **values, result=result, notice=notice)
    finally:
        _lock.release()


@router.post("/forget")
def forget(request: Request):
    try:
        places.forget()
    except OSError:
        return page(request, problem="The saved shortcut could not be removed. Please try again.")
    return RedirectResponse("/mobility", status_code=303)
