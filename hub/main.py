import threading
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import actions, bridge, config, delayed, luna, worker
from .auth import require_owner
from .bridge import BridgeError
from .templating import render
from .tools import TOOLS

HERE = Path(__file__).parent
_preschool_quality_lock = threading.Lock()


def refresh_cache(request: Request, refresh: int = 0):
    if refresh and request.url.path != "/readyz":
        bridge.clear_cache()


def warm_cache():
    from .tools.gmail import common
    try:
        common.overview()
    except BridgeError:
        pass


app = FastAPI(dependencies=[Depends(require_owner), Depends(refresh_cache)], docs_url=None, redoc_url=None, openapi_url=None)
import preschool
app.mount("/preschool/static", StaticFiles(directory=str(Path(preschool.__file__).parent / "static")), name="preschool-static")
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

for tool in TOOLS:
    app.include_router(tool["router"], prefix="/" + tool["slug"])

from .golden.routes import router as golden_router  # noqa: E402
app.include_router(golden_router, prefix="/golden")


@app.on_event("startup")
def startup():
    if not config.BACKGROUND_JOBS_ENABLED:
        return
    threading.Thread(target=warm_cache, daemon=True).start()
    worker.resume_if_interrupted()
    delayed.start()


@app.get("/readyz")
def ready():
    """Process readiness only: no connectors, Keychain or storage access."""
    return {"ok": True, "mode": "dashboard", "production_ready": False,
            "background_jobs_enabled": config.BACKGROUND_JOBS_ENABLED}


@app.exception_handler(BridgeError)
async def bridge_error(request: Request, exc: BridgeError):
    return render(request, "error.html", message=str(exc), status_code=502)


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        fetch_site = request.headers.get("sec-fetch-site")
        same = (origin and urlsplit(origin).netloc == request.headers.get("host")) or (not origin and fetch_site in ("same-origin", "none"))
        if not same:
            return PlainTextResponse("Forbidden", status_code=403)
    return await call_next(request)


@app.middleware("http")
async def no_store(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
    return response


@app.get("/status", response_class=HTMLResponse)
def status(request: Request, luna_result=None, jev_result=None):
    reachable = bridge.health()
    profile, problem = None, None
    if reachable:
        try:
            profile = bridge.get("/gmail/profile", ttl=0)
        except BridgeError as err:
            problem = str(err)
    from . import model_key
    key_from = model_key.source(fresh=True)
    key_words = KEY_WORDS[key_from]
    if key_from == "1password" and model_key.legacy_present():
        key_words += ". The older Keychain copy is still there; to delete it, run deploy/remove-old-model-key in Termius"
    checks = [
        ("Hub running", True, "serving this page"),
        ("Allowlist configured", bool(config.ALLOWED_LOGINS), "required for requests through Tailscale"),
        ("Bridge reachable", reachable, config.BRIDGE_URL),
        ("Bridge token readable", config.BRIDGE_TOKEN_FILE.is_file(), "file exists; contents never shown"),
        ("Gmail authorized", profile is not None, problem or "profile read succeeded"),
        ("Model key", key_from is not None, key_words),
    ]
    if profile:
        paused = profile.get("organization_paused")
        checks += [
            ("Changes enabled", not paused,
             "Paused by a maintenance marker on the Mac mini (~/Developer/mac-mini/private/organization-paused). "
             "Plans are saved but can't be applied until it is removed." if paused else "plans can be applied"),
            ("Filter editing authorized", bool(profile.get("filter_editing_enabled")), "Gmail settings scope granted to the bridge"),
            ("Sending unavailable", profile.get("sending_available") is False, "the bridge can't send mail"),
        ]
    who = request.headers.get("tailscale-user-login", "this Mac (direct)")
    return render(request, "status.html", active="status", checks=checks, who=who, luna_result=luna_result, jev_result=jev_result,
                  luna_ready=luna.configured())


@app.post("/status/luna-test", response_class=HTMLResponse)
def luna_test(request: Request):
    """Round trip with made-up data only: no mailbox content leaves the Mac."""
    labels = [{"id": "Label_1", "name": "Receipts", "type": "user"}]
    try:
        raw = luna.propose("shop@example.invalid", 120, ["Your order has shipped", "Receipt for order 1234", "Refund processed"],
                           ["Receipts", "Work"], "Put these in Receipts and mark read, but keep refund emails in my inbox")
        spec = actions.validate(raw, "shop@example.invalid", labels)
        text = actions.describe(spec, 120)
        # Sender assessment with invented evidence: checks the reply format and that options pass validation.
        evidence = {"sender": "deals@example.invalid", "messages_in_inbox": 340, "inbox_count_exact": True,
                    "messages_last_30_days": 41, "messages_total": 1200, "total_count_exact": True,
                    "unread_share_percent": 94, "already_filtered_out_of_inbox": False,
                    "recent_messages": [{"subject": s, "age_days": d, "unread": True} for s, d in
                                        (("48-hour flash sale", 1), ("Your weekly picks", 3), ("Order confirmation #5521", 9))]}
        reply = luna.assess(evidence, ["Receipts", "Work"])
        usable = 0
        for option in reply["options"][:luna.MAX_OPTIONS]:
            if option.get("kind") in ("keep", "archive_after_30_days"):
                usable += 1
            elif option.get("kind") == "action" and isinstance(option.get("action"), dict):
                try:
                    actions.validate({**option["action"], "unsupported": None}, "deals@example.invalid", labels)
                    usable += 1
                except actions.ActionError:
                    pass
        if not usable:
            raise actions.ActionError("Sender assessment returned no usable options")
        text += f" Sender assessment: {usable} usable option{'s' if usable != 1 else ''}."
        result = {"ok": True, "text": text, "query": actions.future_query(spec)}
    except (luna.LunaError, actions.ActionError) as err:
        result = {"ok": False, "text": str(err), "query": None}
    return status(request, result)






@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({"name": "Hub", "short_name": "Hub", "start_url": "/", "display": "standalone",
                         "background_color": "#3050c4", "theme_color": "#3050c4",
                         "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                                   {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                                   {"src": "/static/icon-maskable-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
                                   {"src": "/static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
                                   {"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]},
                        media_type="application/manifest+json")


def _icon_at_the_root(name):
    """An icon at an address browsers and phones ask for without being told. Plain routes, so they are
    answered like the files under /static: no page dependencies, the same headers."""
    async def serve(request: Request):
        return FileResponse(HERE / "static" / name)
    return serve


for _address, _name in (("/favicon.ico", "favicon.ico"), ("/apple-touch-icon.png", "apple-touch-icon.png"),
                        ("/apple-touch-icon-precomposed.png", "apple-touch-icon.png")):
    app.add_route(_address, _icon_at_the_root(_name), methods=["GET", "HEAD"])


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    """Hub opens on Ask. The same page answers at /ask; the day's cards are at /today."""
    return render(request, "ask/chat.html", active="ask")


@app.get("/today", response_class=HTMLResponse)
def today_page(request: Request, snoozed: int = 0, home: str = ""):
    from . import cards, home as summaries
    from .tools.house import control
    greeting_text, date_text = summaries.greeting()
    try:
        home_change = control.summary(home) if home else None
    except Exception:  # noqa: BLE001 - the banner is a nicety; Today shows without it
        home_change = None
    return render(request, "today.html", active="today", snoozed=snoozed if snoozed in (1, 7) else 0, home_change=home_change,
                  greeting_text=greeting_text, date_text=date_text, **cards.page())


@app.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request):
    from . import insights
    return render(request, "insights.html", active="insights", **insights.page())


@app.get("/tools", response_class=HTMLResponse)
def all_tools(request: Request):
    """The secondary menu: every tool's own page."""
    return render(request, "tools.html", active="tools", listed=[t for t in TOOLS if t["slug"] != "ask"])


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    return render(request, "settings.html", active="settings")


@app.post("/today/snooze")
def today_snooze(key: str = Form(...)):
    """Hide one recommendation until tomorrow (a week for a repeating offer). Changes nothing outside Hub."""
    from . import today
    from .engine import state
    today.hide(key)
    if key.startswith("ai-"):
        state.dismiss(key)  # remembered for a week so the overnight review doesn't raise it again
    return RedirectResponse("/today?snoozed=" + ("7" if key.startswith(today.WEEK_KEYS) else "1"), status_code=303)


@app.post("/today/prepare")
def today_prepare(request: Request):
    """Sort, trusted actions, then the overnight review. For the morning job on this Mac only; limits enforced inside."""
    from .engine import auto, overnight, prep, sorting
    if request.headers.get("tailscale-user-login") is not None:
        return JSONResponse({"error": "Local only. Use the Engine page."}, status_code=403)
    sorting.run()      # each source places what is new, so the actions and the review start from today's picture
    done = auto.run()  # actions the owner trusts to run unasked, before the review sees the day
    prep.run()         # then each source gets its pages ready, so the morning list has nothing to wait for
    return JSONResponse({**overnight.prepare(), "done": len(done)})


@app.get("/engine", response_class=HTMLResponse)
def engine_page(request: Request, ran: str = "", asked: str = "", deploy: str = ""):
    from .engine import improve
    from . import home as summaries
    from .engine import ask, catalog, overnight, registry, release, state
    day = summaries.today().isoformat()
    acts = [{"action": a, "level": state.trust(a), "levels": a.levels()} for a in catalog.ACTIONS]
    from .tools.gmail import triage
    limits = {"luna": overnight.LUNA_CALLS_PER_DAY, "month": overnight.MONTH_COST_CAP,
              "sort_luna": triage.LUNA_CALLS_PER_DAY, "sort_jev": triage.JEV_CALLS_PER_DAY}
    said = RAN_WORDS.get(ran) or ASKED_WORDS.get(asked) or DEPLOY_WORDS.get(deploy) or ""
    version = {"on": release.folder() is not None, "running": release.running(), "pending": release.pending(),
               "last": release.last(), "requested": release.requested()}
    return render(request, "engine.html", active="engine", sources=catalog.SOURCES, acts=acts, words=registry.TRUST_WORDS,
                  instructions=state.instructions(), requests=state.requests(), improvements=improve.requests(), version=version,
                  runs=state.runs(overnight.KIND), used=state.used(day, overnight.KIND), limits=limits, said=said,
                  sort_runs=state.runs(triage.KIND, limit=7), sort_used=state.used(day, triage.KIND),
                  said_ok=ran in ("ok", "already") or asked in ("instruction", "request", "unsorted") or deploy == "asked",
                  sorting=luna.configured() and state.used(day, ask.KIND)["luna_calls"] < ask.CALLS_PER_DAY,
                  ready=luna.configured())


KEY_WORDS = {"1password": "from 1Password", "keychain": "from the Keychain (older setup)", None: "not set up"}
RAN_WORDS = {"ok": "Done. Anything it found is on Today.", "already": "Already ran today.",
             "daily_limit": "Today’s limit of model calls is used up.", "monthly_limit": "This month’s spending limit is reached.",
             "not_set_up": "The model key isn’t set up.", "error": "The model route didn’t answer. Nothing changed.",
             "busy": "A review is already running."}
ASKED_WORDS = {"instruction": "Got it. The overnight review will follow that from tonight.",
               "request": "Hub can’t do that yet. It’s saved under “Asked for, not built yet”.",
               "unsorted": "Saved under “Asked for, not built yet”. Hub couldn’t sort it just now.",
               "full": "That list is full. Remove something first.", "empty": "Nothing to save."}
DEPLOY_WORDS = {"asked": "Updating. This takes about a minute; reload to see the new version.",
                "none": "There’s no checked version waiting.",
                "local": "Deploy only works from your own signed-in device."}


@app.post("/engine/ask")
def engine_ask(text: str = Form("")):
    """File one line the owner typed: guidance for the review, or a note of something to build."""
    from .engine import ask
    result = ask.submit(text)
    store_log("engine_ask", result)
    return RedirectResponse("/engine?asked=" + result, status_code=303)


@app.post("/engine/requests/done")
def engine_request_done(request_id: int = Form(...)):
    from .engine import state
    state.close_request(request_id)
    return RedirectResponse("/engine", status_code=303)


@app.get("/engine/requests.json")
def engine_requests():
    """What the owner asked for that isn't built. A to-do list for builders; it authorizes nothing."""
    from .engine import state
    return JSONResponse({"requests": state.requests()})


@app.post("/engine/deploy")
def engine_deploy(request: Request):
    """Ask the deployer for the checked version. Only a signed-in owner device can ask, not a program on this Mac."""
    from .engine import release
    login = request.headers.get("tailscale-user-login")
    if login is None:
        return RedirectResponse("/engine?deploy=local", status_code=303)
    asked = release.request(login)
    if asked:
        store_log("engine_deploy", "asked")
    return RedirectResponse("/engine?deploy=" + ("asked" if asked else "none"), status_code=303)


@app.post("/engine/prepare")
def engine_prepare():
    from .engine import overnight
    return RedirectResponse("/engine?ran=" + overnight.prepare()["status"], status_code=303)


@app.post("/engine/trust")
def engine_trust(action: str = Form(...), level: str = Form(...)):
    from .engine import catalog, state
    found = catalog.action(action)
    if found:
        state.set_trust(found, level)
    return RedirectResponse("/engine", status_code=303)


@app.post("/engine/instructions/remove")
def engine_forget(instruction_id: int = Form(...)):
    from .engine import state
    state.remove_instruction(instruction_id)
    store_log("engine_instruction", "removed")
    return RedirectResponse("/engine", status_code=303)


@app.get("/engine/ask-activity", response_class=HTMLResponse)
def ask_activity(request: Request, failed: str = ""):
    """Recent Ask turns from the trace: tool calls, outcomes, errors and the change rows they made. Reads only."""
    from datetime import datetime
    from . import ask_trace, home as summaries
    rows = ask_trace.turns(500 if failed else 60)
    if failed:
        rows = [r for r in rows if r["status"] != "ok" or r["tool_errors"]][:60]
    for r in rows:
        r["when"] = datetime.fromtimestamp(r["started"], summaries.PACIFIC).strftime("%b %d, %H:%M:%S")
        r["seconds"] = f"{r['ended'] - r['started']:.1f} s" if r["ended"] else "still running"
    return render(request, "ask/activity.html", active="engine", turns=rows, only_failed=bool(failed),
                  keep_days=ask_trace.KEEP_DAYS, bad=ask_trace.NOT_DONE)


NOTE_WORDS = {"saved": "Saved. Ask Hub and the overnight review follow it from now on.", "skipped": "Not saved."}


@app.get("/engine/notes", response_class=HTMLResponse)
def engine_notes(request: Request, done: str = "", problem: str = ""):
    """Notes Ask Hub proposed, waiting for the owner's tap, and the notes already saved."""
    from .engine import notes, state
    from .tools.house import zones
    names = {}
    try:
        from .tools.house import read
        names = {d["id"]: d["name"] for d in read.snapshot()["speakers"]}
    except Exception:  # noqa: BLE001 - speaker names are a nicety; the notes stand without them
        pass
    by_note = zones.by_note()
    saved = [{**i, "zone": [names.get(e, e.partition(".")[2]) for e in by_note.get(i["id"], ())]} for i in state.instructions()]
    waiting = [{**p, "zone": [names.get(e, e.partition(".")[2]) for e in p["zone"]]} for p in notes.waiting()]
    return render(request, "notes.html", active="engine", waiting=waiting, saved=saved,
                  said=NOTE_WORDS.get(done, ""), problem=problem[:200], full=len(saved) >= state.MAX_INSTRUCTIONS)


def _signed_out(request, words):
    from . import taps
    return taps.refused(request, words, lambda: HTMLResponse(words, status_code=403), status=403)


@app.post("/engine/notes/{proposal_id}/save")
def engine_note_save(proposal_id: str, request: Request):
    """Only a tap from a signed-in device saves a note: never the chat's model, never a program on this Mac.
    The Notes page and the chat's card both post here."""
    from urllib.parse import quote
    from . import taps
    from .engine import notes
    if request.headers.get("tailscale-user-login") is None:
        return _signed_out(request, "Save notes from your phone or laptop.")
    try:
        notes.save(proposal_id)
    except notes.Refused as error:
        return taps.refused(request, error, lambda: RedirectResponse("/engine/notes?problem=" + quote(str(error)), status_code=303))
    return taps.done(request, "/engine/notes?done=saved", NOTE_WORDS["saved"])


@app.post("/engine/notes/{proposal_id}/skip")
def engine_note_skip(proposal_id: str, request: Request):
    from . import taps
    from .engine import notes
    notes.skip(proposal_id)
    return taps.done(request, "/engine/notes?done=skipped", NOTE_WORDS["skipped"])


@app.post("/engine/friction/{card_id}/{choice}")
def engine_friction_decide(card_id: str, choice: str, request: Request):
    """A fix card on Today: Build files a brief for the next building session; Not now; Never. Signed-in taps only."""
    from urllib.parse import quote
    from .engine import friction
    login = request.headers.get("tailscale-user-login")
    if login is None:
        return HTMLResponse("Decide from your phone or laptop.", status_code=403)
    try:
        friction.decide(card_id, choice, login=login)
    except friction.Refused as error:
        return RedirectResponse("/today?problem=" + quote(str(error)), status_code=303)
    return RedirectResponse("/engine#improvements" if choice == "build" else "/today", status_code=303)


@app.post("/engine/improvements/remove")
def engine_improvement_remove(request_id: str = Form(...)):
    from .engine import improve
    improve.remove(request_id)
    return RedirectResponse("/engine#improvements", status_code=303)


@app.post("/engine/notes/remove")
def engine_note_remove(instruction_id: int = Form(...)):
    from .engine import state
    state.remove_instruction(instruction_id)
    store_log("engine_instruction", "removed")
    return RedirectResponse("/engine/notes", status_code=303)


RECIPE_WORDS = {"saved": "Saved. Name it in Ask Hub to run it.", "skipped": "Not saved.", "renamed": "Renamed.",
                "removed": "Removed.", "trimmed": "Step removed."}


@app.get("/engine/recipes", response_class=HTMLResponse)
def engine_recipes(request: Request, done: str = "", problem: str = ""):
    """Recipes Ask Hub proposed, waiting for the owner's tap, and the recipes already saved."""
    from .engine import recipes

    def shown(r):
        return {**r, "lines": [recipes.describe(s) for s in r["steps"]]}
    return render(request, "recipes.html", active="engine", waiting=[shown(r) for r in recipes.waiting()],
                  saved=[shown(r) for r in recipes.saved()], said=RECIPE_WORDS.get(done, ""), problem=problem[:200])


def _recipe_change(request, change, done):
    from urllib.parse import quote
    from . import taps
    from .engine import recipes
    try:
        change()
    except recipes.Refused as error:
        return taps.refused(request, error, lambda: RedirectResponse("/engine/recipes?problem=" + quote(str(error)), status_code=303))
    return taps.done(request, f"/engine/recipes?done={done}", RECIPE_WORDS[done])


@app.post("/engine/recipes/{proposal_id}/save")
def engine_recipe_save(proposal_id: str, request: Request):
    """Only a tap from a signed-in device saves a recipe: never the chat's model, never a program on this Mac.
    The Recipes page and the chat's card both post here."""
    from .engine import recipes
    if request.headers.get("tailscale-user-login") is None:
        return _signed_out(request, "Save recipes from your phone or laptop.")
    return _recipe_change(request, lambda: recipes.save(proposal_id), "saved")


@app.post("/engine/recipes/{proposal_id}/skip")
def engine_recipe_skip(proposal_id: str, request: Request):
    from .engine import recipes
    return _recipe_change(request, lambda: recipes.skip(proposal_id), "skipped")


@app.post("/engine/recipes/rename")
def engine_recipe_rename(request: Request, recipe_id: int = Form(...), name: str = Form(...)):
    from .engine import recipes
    return _recipe_change(request, lambda: recipes.rename(recipe_id, name), "renamed")


@app.post("/engine/recipes/drop-step")
def engine_recipe_drop_step(request: Request, recipe_id: int = Form(...), index: int = Form(...)):
    from .engine import recipes
    return _recipe_change(request, lambda: recipes.drop_step(recipe_id, index), "trimmed")


@app.post("/engine/recipes/remove")
def engine_recipe_remove(request: Request, recipe_id: int = Form(...)):
    from .engine import recipes
    return _recipe_change(request, lambda: recipes.remove(recipe_id), "removed")


def store_log(action, detail):
    from . import store
    store.log(action, detail)


@app.get("/today/brief", response_class=PlainTextResponse)
def today_brief():
    """Short text for the morning message: recommendation titles only."""
    from . import home as summaries, today
    return today.brief(today.needs_you(summaries.tiles(TOOLS)))


@app.post("/status/jev-test", response_class=HTMLResponse)
def jev_test():
    """Retained owner-initiated synthetic diagnostic; no source-system reads."""
    from preschool import jev
    from preschool.support import luna
    try:
        scores = jev.decide({"subject": "Synthetic preschool event", "text":
            "Synthetic preschool family work party on Saturday, May 15 at 10 AM. Please bring gloves.",
            "recipients": {}, "attachments": []})
        values = [scores[name] for name in ("preschool_relevant", "useful_information")]
        if any(type(value) not in (int, float) or not 0 <= value <= 1 for value in values):
            raise ValueError("Invalid diagnostic scores")
        return HTMLResponse(f"Synthetic relevance: {values[0]:.2f}; usefulness: {values[1]:.2f}")
    except (luna.LunaError, ValueError, KeyError):
        return HTMLResponse("Synthetic relevance check unavailable", status_code=503)

@app.post("/status/preschool-quality-test", response_class=JSONResponse)
def preschool_quality_test(request: Request):
    """Run only the fixed synthetic suite; accept no prompts, models or account data."""
    if (request.query_params or request.headers.get("transfer-encoding") or
            request.headers.get("content-length", "0") != "0"):
        return JSONResponse({"error": "This check accepts no input."}, status_code=400)
    if not _preschool_quality_lock.acquire(blocking=False):
        return JSONResponse({"error": "A synthetic quality check is already running."}, status_code=409)
    try:
        from preschool import quality
        return JSONResponse(quality.run())
    except Exception:
        # Provider responses and exception payloads must not enter HTTP or logs.
        return JSONResponse({"error": "The synthetic check stopped safely."}, status_code=503)
    finally:
        _preschool_quality_lock.release()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)
