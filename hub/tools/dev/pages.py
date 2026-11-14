"""The Dev page: read at request time, nothing to change."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...templating import render
from . import read

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return render(request, "dev/home.html", active="dev", **read.page())
