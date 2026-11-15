# The app

Hub's new front: one small app instead of a page per tool. It opens on the house, drawn as a plan of rooms that
are lit when their lights are, and changes under a tap. Source: `web/app/` (Preact, TypeScript). Built files,
committed: `hub/static/app/`. The data and taps it uses: `hub/app/`.

It is being built in phases (the plan is in the owner's private review notes). Phase 1 is Home and music;
phase 2 is Ask, inside the app. Until a later phase replaces them, Today and Kitchen in the app's bar open the
older pages.

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
| `GET /app/api/ask` | Whether Ask can run here, the last half hour of this person's requests, what they often ask around now, and what Hub remembers |
| `POST /app/api/ask` | One request, `{"text": ...}`, answered as it happens (see “Ask”) |
| `POST /app/api/memory/{note}/forget` | Forget one thing Hub remembers |

The picture is built in `hub/app/model.py` from the same reading of Home Assistant as the rest of Hub
(`hub/tools/house/read.py`), so nothing new leaves Home Assistant for it. Two facts were added to that reading for
the Home screen: the temperature outside, and which door and window sensors are open. They are not part of what
Ask's `home_status` lookup shares with a model.

## Changing things

Every tap goes through `hub/tools/house/control.py`, like a request typed to Ask: the broker's allowlist
(lights, plugs, fans, scenes, speakers; never locks, the garage, the alarm or the thermostat), a record of each
change with what was there before, and Undo for a day. A change needs a signed-in device: a program on this Mac
has no Tailscale login and gets a 403.

Four things differ from Ask's path:

- **A room changes in one request.** Lights, plugs and fans that get the same change go to Home Assistant
  together (`control.change(..., at_once=True)`; the broker client accepts up to 40 of one kind at once, and still
  one at a time for everything else). Ask's path is unchanged.
- **Taps have their own limit.** 120 a minute, one per tap, kept apart from Ask's 30 changes a minute, so an
  evening of switching rooms by hand never locks Ask out.
- **A room comes back as it was.** Hub notes each lit lamp's brightness whenever it reads the house
  (`hub/app/levels.py`, kept in its database). A room's tap asks for that again; a lamp Hub has never seen lit is
  simply switched on, at whatever Home Assistant chooses.
- **Outdoors is not the house.** "All off", and a kept scene's "turn every other light off", leave outdoor lights
  alone: they belong to whatever switches them at dusk and dawn. A room is outdoors when its floor in Home
  Assistant is called Outside (or Outdoors, Exterior, Garden, Yard), or when the owner lists it in the private
  settings file: `"app": {"outside": ["Porch"]}`. The sentence at the top counts indoor rooms only.

When two rooms play different things (music downstairs, a lullaby in one bedroom), the picture still has one
main music, the one the playing strip works: it stays the main one for as long as it plays, and otherwise the
one in more rooms is. The others are named in `music.also`, their rooms show the playing bars, the sentence says
so, and the music sheet lists them under “Also playing” with a pause each.

## Scenes

The row of words above the plan. Three kinds, run the same way:

- **Scenes Hub keeps** (`hub/app/scenes.py`). The owner sets the house, taps the plus, picks the rooms and names
  it: Hub remembers each of those rooms' lights as they are, and optionally "turn every other light off". Running
  one asks only the lights that differ, as one batch with one Undo, and the row shows which scene the house is in
  right now. Kept in Hub's own database (`app_scenes`), at most 12.
- **Home Assistant's scenes**, when it has any. Hub can run them but not undo them or tell whether one is on.
- **All off**: every light indoors that is on, with Undo.

## Ask

Say what you want, and Hub does it or answers. Code: `hub/app/ask/`. In the app: `web/app/src/ask.tsx` and
`asking.ts`. It replaces the older chat (`/ask`, `hub/agent/`), which stays until Ask runs on the live Hub.

**Three ways a request goes** (`brain.py`), chosen by what was asked:

1. **A few letters** find a room, a scene or a playlist in the app itself and work it in the list. Nothing is
   asked of anyone. What this person often asks around this time of day (`turns.usual`, counted from their own
   earlier requests, no model) is offered as one tap.
2. **A question about mail, the calendar, school or today's list** is answered by the private desk
   (`private.py`): Luna, on the pinned zero-retention route in `hub/luna.py`, from the same bounded lookups the
   older chat used. It is recognised by its words before anything else happens, so the house model is given
   neither the question, the data nor the answer, on that turn or a later one.
3. **Anything else** goes once to the house model with the house beside it: rooms, lights, speakers, what is
   playing, the scenes and what Hub remembers, as a few plain lines (`brain.house`), plus the last few
   requests and what Hub did. So the model can act at once instead of looking first.

**One trip to the model.** A tool that did the job says so itself, in a sentence Hub writes
(“The kitchen and the living room are at 40%.”), and the turn ends there: no second trip for the model to
describe what happened. Only when a tool could not do it as asked (nothing matched a search, a room it
couldn't place) does the model get its answer and one more try. A turn is at most four trips.

**What it can do** (`tools.py`): `lights` (rooms, floors, single lights, everywhere indoors, outside; on
restores each lamp's last brightness), `scene`, `music` (play, pause, resume, next, previous, volume, a room
joining or leaving), `remember`, `kitchen` (passes the owner's words to Kitchen, whose answer appears under the
request) and `private_desk` (for a private question the words missed: the model passes it on and never sees
the answer). Every change goes through `hub/tools/house/control.py` like a tap: the same allowlist, the same
record and Undo, counted against Ask's 30 changes a minute. Never locks, the garage, the alarm or the
thermostat. Ask changes nothing for a program without a login, or while the owner has Home control set to
“Suggest only” on the Engine page.

**What Hub remembers.** One list: the engine's standing notes (`hub/engine/state.py`), the same list the older
chat saved to. Ask keeps a note at once when asked, shows it with Forget, and lists them under System. The
engine's own test still decides what may become a note (`notes.grounded`): the owner's latest message must ask
for something to be remembered, and the note must be made of that message's words, so a song title, a device
name or a model's invention never becomes one. A note that says some rooms play together keeps them as a zone.

**The model** (`route.py`). Owner decision, 2026-11-14: Claude Sonnet (`claude-sonnet-5-5`) on the owner's own
Anthropic API key, with adaptive thinking at high effort. `"ask": {"effort": "medium"}` in `household.json`
changes the effort. The key is read from 1Password by reference (`op://Hub/Anthropic/credential`, or
`HUB_ANTHROPIC_REF`), kept in memory for a few minutes and never logged or returned. A guard on the one HTTP
client refuses any request that isn't this model at `https://api.anthropic.com/v1/messages`, so nothing else
can send Ask's words another way. This route is not zero-retention, which is why the private desk exists.
Instructions and tool definitions are cached between requests. Each turn's tokens and cost go in the engine's
record under the chat's own $10 a month limit; the words do not.

**What reaches which model.**

| | House model (Claude Sonnet, the owner's key) | Private desk (Luna, zero retention) |
| --- | --- | --- |
| The owner's typed request | Yes, unless it is plainly a private question | Private questions only |
| Rooms, lights, speakers, scenes, what is playing | Yes | No |
| What Hub remembers | Yes | No |
| Earlier requests and Hub's answers | Yes, except private ones | No |
| Mail, the calendar, school, today's list | Never | Yes, bounded as before |
| Kitchen's answers | Never | No |

**Until it is set up.** Ask needs two things a Hub may not have yet: the Anthropic SDK among the reviewed
wheels (`anthropic`, an extra of `pydantic-ai-slim`), and the key. Without either, `GET /app/api/ask` says
`"ready": false`, the app's list still works, and whatever else is typed is handed to the older chat, as in
phase 1. Nothing fails.

**From a Shortcut, for Siri.** `POST /app/api/ask` without `Accept: text/event-stream` answers once, at the
end, with `{"ok": true, "said": "..."}`. A Shortcut on a signed-in phone (“Get contents of URL”, method POST,
JSON body `text`, and the header `Sec-Fetch-Site: none`) can ask and speak the answer. The phone's own
Tailscale login still decides who is asking, and the same-origin rule still refuses any web page.

**Tests.** `tests/test_app_ask.py` runs whole turns on the made-up house with a stand-in for the model
(`tests/ask_script.py`) that picks a tool from the words; every tool, check and change underneath is the real
one. Two tests run the real model adapter with only the network replaced, to pin the request that would leave
and the cost that is recorded. How well the real model understands is not something these can show: that
needs the live comparison on real requests.

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
(`web/check_guard.py`). Ask works there on the stand-in model, with a made-up Kitchen and private desk, so the
conversation can be tried and photographed without a key. See `web/app/check/README.md`.
