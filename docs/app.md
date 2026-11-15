# The app

Hub's new front: one small app instead of a page per tool. It opens on the house, drawn as a plan of rooms that
are lit when their lights are, and changes under a tap. Source: `web/app/` (Preact, TypeScript). Built files,
committed: `hub/static/app/`. The data and taps it uses: `hub/app/`.

It is being built in phases (the plan is in the owner's private review notes). Phase 1 is Home and music. Until
a later phase replaces them, Today and Kitchen in the app's bar open the older pages.

## Switching it on

The app always answers at `/app` once a version with it is deployed, so it can be tried beside the old front.
Hub opens on it only when the owner says so in the private settings file (`household.json`):

```json
"app": {"front": true}
```

With it, `/` sends the browser to `/app/`. Without it, `/` is the Ask chat as before. Nothing else changes either way: no address was removed.

## What it asks Hub for

| Address | What |
| --- | --- |
| `GET /app/` | The page: one HTML shell that loads `app.css` and `app.js` by version |
| `GET /app/sw.js` | A small worker that keeps the app's own files on the phone, so it opens at once; it covers `/app/` only |
| `GET /app/api/home` | The house as data: floors, rooms and their lights, scenes, what is playing, a few facts |
| `GET /app/api/live` | The same picture again each time it changes, for as long as the app is open |
| `POST /app/api/room/{room}` | A room's lights together: `{"on": true}`, `{"on": false}` or `{"level": 40}` |
| `POST /app/api/device/{entity}` | One light, plug or fan |
| `POST /app/api/scene/{scene}` | A scene Hub keeps (`hub:3`), a Home Assistant scene, or `all-off` |
| `POST /app/api/scenes`, `POST /app/api/scenes/{scene}/forget` | Keep the house as it stands as a scene; forget one |
| `POST /app/api/music/{speaker}` | Play, pause, next, previous, a volume, a room in or out |
| `GET /app/api/picks`, `POST /app/api/play/{speaker}` | The library's playlists, and playing one where music is on |
| `POST /app/api/undo/{batch}` | Put a change back |

The picture is built in `hub/app/model.py` from the same reading of Home Assistant as the rest of Hub
(`hub/tools/house/read.py`), so nothing new leaves Home Assistant for it. Two facts were added to that reading for
the Home screen: the temperature outside, and which door and window sensors are open. They are not part of what
Ask's `home_status` lookup shares with a model.

## Changing things

Every tap goes through `hub/tools/house/control.py`, like a request typed to Ask: the broker's allowlist
(lights, plugs, fans, scenes, speakers; never locks, the garage, the alarm or the thermostat), a record of each
change with what was there before, and Undo for a day. A change needs a signed-in device: a program on this Mac
has no Tailscale login and gets a 403.

Two things differ from Ask's path:

- **A room changes in one request.** Lights, plugs and fans that get the same change go to Home Assistant
  together (`control.change(..., at_once=True)`; the broker client accepts up to 40 of one kind at once, and still
  one at a time for everything else). Ask's path is unchanged.
- **Taps have their own limit.** 120 a minute, one per tap, kept apart from Ask's 30 changes a minute, so an
  evening of switching rooms by hand never locks Ask out.

## Scenes

The row of words above the plan. Three kinds, run the same way:

- **Scenes Hub keeps** (`hub/app/scenes.py`). The owner sets the house, taps the plus, picks the rooms and names
  it: Hub remembers each of those rooms' lights as they are, and optionally "turn every other light off". Running
  one asks only the lights that differ, as one batch with one Undo, and the row shows which scene the house is in
  right now. Kept in Hub's own database (`app_scenes`), at most 12.
- **Home Assistant's scenes**, when it has any. Hub can run them but not undo them or tell whether one is on.
- **All off**: every light that is on, with Undo.

## The live feed

`hub/app/feed.py`. While at least one app is open, Hub keeps the picture of the house current and sends it to
every open app when it differs from the last one. Nobody open, nothing read. An app that nobody is looking at
stops listening after 20 seconds and picks up again the moment it is looked at.

When Hub reads the house:

- **When the house says it changed.** Hub listens to the broker (`broker.listen`, home-ops' `listen` operation),
  which says which light, speaker or other house device changed the moment it does: the device, never its state.
  Hub then reads the house as usual. A wall switch reaches an open app at once. While this line is up, Hub
  otherwise reads only every 30 seconds, as a safety net.
- **On a timer, when it can't listen.** With an older broker that has no `listen`, or the broker away, Hub reads
  every 3 seconds instead and asks again each minute whether it may listen. Nothing else differs.
- **After a change Hub made**: at once, and twice more as speakers settle.

The feed is a plain event stream (`text/event-stream`) with a `beat` every 5 seconds when nothing has changed. A
phone that leaves the home network is told nothing, the line just goes quiet, so an app that hears no beat for
12 seconds says it can't reach Hub and keeps trying to reconnect by itself.

## The built files

`tools/build-app` builds `web/app` into `hub/static/app/app.js` and `app.css` and records their hashes in
`build.json`; `tests/test_app_page.py` fails when they don't match the source (see `web/app/README.md`).
The page names both files by version (`?v=<hash>`), and files asked for that way may be kept by the phone for
good, as may the typeface (`fonts/Jost.ttf`, SIL Open Font License). Everything else Hub serves stays
`no-store`. Files under `/static` are compressed; streamed answers are not.

## Trying it on a made-up house

`web/app/check/demo_server.py` runs the real app and the real change code against a fictional house that
follows what it is sent (`tests/app_world.py`). It reaches no other machine and no service on this one
(`web/check_guard.py`). See `web/app/check/README.md`.
