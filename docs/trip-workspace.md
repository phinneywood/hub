# NYC / NJ trip workspace

## Scope and integration

Private Hub tool at `/trip`, registered in `hub/tools/__init__.py`. Uses Hub's existing owner authorization, same-origin writes, CSP and no-store headers. No network fetches, new credentials, booking, messaging, reminders or external synchronization. Hotel research remains paused. Wednesday May 26, 2027 is the preferred transition; Thursday remains an exploratory alternative. Flight selection never creates a reservation.

The latest observation is the AS356/AS370 Main round-trip quote of $1,670.43 for two adults and a child, observed November 8, 2026 before companion fare. Older observations remain separately labeled. Nonstop flights preferred. Neither quote nor selected option implies live inventory.

## Storage and portability

`HUB_DATA_DIR/trip.db` is a separate owner-readable SQLite store. One stable trip ID, optimistic revision checks, previous full snapshots in `trip_versions`. No browser-only persistence. Seed runs only when trip does not exist; adding seed facts later requires a deliberate stable-ID merge, never overwriting user edits. Save validation preserves unknown amounts/times as null and validates airport timezone offsets. All values rendered as text; external links accept only HTTP(S).

Use **Backup & history → Download trip JSON** for a portable export. **Restore exported trip** accepts that JSON and replaces this same trip by stable ID, retaining the previous version. Repeating import does not duplicate records. Keep exports private. SQLite snapshots can be recovered by a developer by reading `trip_versions.document` for the desired revision and importing through the same validated workflow; never replace the live DB while a writer is active.

## Run and verification

Normal Hub route is `/trip`. Production runs a separately installed immutable release and is not automatically updated by checkout changes. Deployment/restart and LaunchAgent changes require explicit approval under repository instructions. Do not modify Tailscale or launch configuration simply to preview.

A loopback-only development preview can run from the checkout on 8782 with isolated `HUB_DATA_DIR=.../var/trip-preview`. Disable inherited Hub background startup handlers for this preview to avoid mailbox or other background work:

```
HUB_DATA_DIR="$PWD/var/trip-preview" .venv/bin/python -c 'from hub.main import app; app.router.on_startup.clear(); import uvicorn; uvicorn.run(app,host="127.0.0.1",port=8782)'
```

This is a Mac-local URL, not verified phone access. No offline cache is implemented. The browser can read and edit the saved trip whenever this private service is reachable. Production adoption should port only this module and registry change to the current release source, preserve all existing Hub features/configuration and data, run tests, stage a distinct release, then obtain approval for switching the running service.

Focused tests: `.venv/bin/python -m pytest tests/test_trip_domain.py tests/test_trip_routes.py -q`.
Aggregate tests: `.venv/bin/python -m pytest -q`. This executor injects SOCKS proxy variables without socksio; run offline tests with HTTP_PROXY/HTTPS_PROXY/ALL_PROXY (including lowercase forms) unset. This does not bypass a network restriction: tests use fake/mocked data, not external services.

## Workflow and limits

Plan compares both transitions with unknown cost inputs and a separate exploration state. Explicit choices require a rationale and create decision records. Itinerary and Travel view show unallocated days, selected flights, fixed wedding dates and manually entered items. People, reservations, budget, decisions, preparation and links are editable records. Budget preserves candidate/selected/booked/paid status and scope; scenario estimates and reservation amounts are not automatically double-counted. Actual shared allocation is manually entered rather than assumed equal. Flight observations can be added/updated, retaining prior versions; outbound and optional return legs have editable airport-local timestamps, timezones and stop status.

File uploads, live airline integrations, automatic Sheet synchronization, true offline mode and full accounting/allocation automation are deferred. Dates & Reminders Sheet remains the owner of personal date reminders. Changes here do not update it; record any necessary sync as an open decision. Do not store companion codes or raw confirmation secrets.

## Verification receipt (November 8, 2026)

The final repository aggregate suite passed **653 tests** (three existing deprecation warnings). The initial run inherited an executor SOCKS proxy without the optional `socksio` dependency and failed unrelated mocked HTTP paths; the offline rerun with proxy variables unset passed. Focused domain and HTTP tests cover timezone normalization, night counts, unknown amounts/times, fixed-ID imports, stale revision conflicts, same-origin enforcement and owner authorization.

Real browser QA uses the installed official Chrome in headless mode, an isolated temporary profile and private CDP pipes (no debugging port or unlocked desktop required). Evidence and the repeatable script are in ignored `var/trip-qa/`. Tested desktop and 390px/320px layouts, edit/save/reload, Cancel, selecting a scenario without marking it booked, flight party totals, preparation toggles, Back navigation and repeated import without duplicates. A shared Hub loading-indicator conflict discovered visually was fixed in Trip's event handling without modifying shared JavaScript.

Final real-browser checks additionally verified actual preview service restart persistence, scenario subtotal calculation without overwriting the alternative, optional return/stop flight editing, Cancel, actual JSON export download, invalid-fragment fallback, Hub → Trip navigation and the loading-indicator fix. The final screenshots were refreshed after restoring clean real trip values; synthetic QA changes remain only in the private preview audit trail. The preview database is `var/trip-preview/trip.db`, separate from production `var/trip.db`.
