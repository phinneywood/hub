from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...templating import render
from . import broker, read

router = APIRouter()


@router.get("", response_class=HTMLResponse)
def house(request: Request):
    try:
        found, problem = read.snapshot(), None
    except broker.HomeDown as error:
        found, problem = None, str(error)
    issues = read.attention(found) if found else []
    return render(request, "house/index.html", active="house", found=found, problem=problem,
                  issues={d['id']: tone for tone, d, _ in issues})
