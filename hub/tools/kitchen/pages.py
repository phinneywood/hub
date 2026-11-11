from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ...templating import render
from . import client

router = APIRouter()
WORKER = Path(__file__).resolve().parents[2] / "static" / "kitchen-sw.js"
DOWN = {"error": "The kitchen service isn’t answering right now."}


def _login(request):
    return request.headers.get("tailscale-user-login")


def _relay(method, path, body=None):
    try:
        status, data = client.call(method, path, body)
    except client.KitchenDown:
        return JSONResponse(DOWN, status_code=502)
    return JSONResponse(data, status_code=status)


@router.get("", response_class=HTMLResponse)
def chat(request: Request):
    return render(request, "kitchen/chat.html", active="kitchen")


@router.get("/messages")
def messages(after: int = 0):
    return _relay("GET", f"/messages?after={max(after, 0)}")


@router.get("/list")
def shopping_list():
    return _relay("GET", "/list")


@router.get("/status")
def status():
    return _relay("GET", "/status")


@router.post("/messages")
async def send(request: Request):
    data = await request.json()
    return _relay("POST", "/messages", {"login": _login(request), "text": str(data.get("text", ""))[:2000]})


@router.post("/plans/{plan_id}/{choice}")
def decide(plan_id: int, choice: str, request: Request):
    # Approving fills the real cart, so it takes a tap on a signed-in member's device:
    # a program on this Mac (no Tailscale login) can't approve.
    if choice not in ("approve", "dismiss"):
        return JSONResponse({"error": "unknown choice"}, status_code=404)
    if _login(request) is None:
        return JSONResponse({"error": "Approve from your phone or laptop."}, status_code=403)
    return _relay("POST", f"/plans/{plan_id}/{choice}", {"login": _login(request)})


@router.post("/orders/{order_id}/{choice}")
def order(order_id: int, choice: str, request: Request):
    # Placing an order spends money, so like Approve it takes a tap on a signed-in member's device.
    if choice not in ("place", "dismiss"):
        return JSONResponse({"error": "unknown choice"}, status_code=404)
    if _login(request) is None:
        return JSONResponse({"error": "Place the order from your phone or laptop."}, status_code=403)
    return _relay("POST", f"/orders/{order_id}/{choice}", {"login": _login(request)})


@router.post("/learn")
def learn(request: Request):
    """Read-only: picks up new Fred Meyer orders into the household's purchase history."""
    return _relay("POST", "/learn", {"login": _login(request)})


@router.post("/subscribe")
async def subscribe(request: Request):
    if _login(request) is None:
        return JSONResponse({"error": "Turn on notifications from your phone."}, status_code=403)
    return _relay("POST", "/subscriptions", {"login": _login(request), "subscription": await request.json()})


@router.get("/sw.js")
def service_worker():
    return FileResponse(WORKER, media_type="text/javascript")
