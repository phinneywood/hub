# The app

Hub's new front: one small app instead of a page per tool. It opens on the house, drawn as a plan of rooms that
are lit when their lights are, and changes under a tap. Source: `web/app/` (Preact, TypeScript). Built files,
committed: `hub/static/app/`. The data and taps it uses: `hub/app/`.

It is being built in phases (the plan is in the owner's private review notes). Phase 1 is Home and music;
phase 2 is Ask, inside the app; phase 3 is Today; phase 4 brings Mail and Kitchen in as screens of their own. The
bar has Home, Today, Mail and Kitchen. A Today item that belongs to Mail or Kitchen opens that screen; others (the
trip, the class guide) still open their older pages.

## The front door

The app is Hub's front door: `/` sends the browser to `/app/`, and the phone's home-screen icon opens it. The
older pages all still answer, listed under System; the old chat is at `/ask`. To open on the chat again, the owner
sets this in the private settings file (`household.json`):

```json
"app": {"front": false}
```

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
| `GET /app/api/today` | Today as data: what needs a person, the rest of today, tomorrow, the week at school, the trip |
| `GET /app/api/versions` | Each release of Hub, newest first, in plain words, and which one is running (see “Versions”) |
| `POST /app/api/today/tick` | A need's circle: `{"key": ...}` handles an email or hides an item; with `"back": true`, puts it back |
| `GET /app/api/mail` | Mail as data: what needs the owner, questions nobody answered, records worth a look, each kind Hub sorted |
| `POST /app/api/mail/{do}` | A tap on Mail, `{"key": ...}`: `done`, `back`, `answered`, `fine`, `sort`, `file` (a kind) or `undo` (a filing) |
| `/kitchen/...` | Kitchen's screen uses the same relay to the kitchen service as the older page (`hub/tools/kitchen/pages.py`) |

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
2. **Anything typed** goes once to the model with the house beside it: rooms, lights, speakers, what is
   playing, the scenes and what Hub remembers, as a few plain lines (`brain.house`), plus the last few
   requests and what Hub did. So the model can act at once instead of looking first.
3. **A question about mail, the calendar, school or today's list** (owner decision, 2026-11-15, reversing the
   14th) goes the same way, with what Hub knows about it beside it in `<data>`: the same bounded lookups the
   older chat used (`private.data`), recognised by the question's words (`private.about`), so it is answered in
   one trip. One the words missed, the model reads with `look_up`. On a Hub without Ask's model yet, Luna
   answers these on its zero-retention route (`private.answer`), as before.

**One trip to the model.** A tool that did the job says so itself, in a sentence Hub writes
(“The kitchen and the living room are at 40%.”), and the turn ends there: no second trip for the model to
describe what happened. Only when a tool could not do it as asked (nothing matched a search, a room it
couldn't place, something it read with `look_up`) does the model get its answer and one more try. A turn is at most four trips.

**What it can do** (`tools.py`): `lights` (rooms, floors, single lights, everywhere indoors, outside; on
restores each lamp's last brightness), `scene`, `music` (play, pause, resume, next, previous, volume, a room
joining or leaving), `remember`, `kitchen` (passes the owner's words to Kitchen, whose answer appears under the
request) and `look_up` (reads mail, the calendar, the class guide or today's list; read-only). Every change goes through `hub/tools/house/control.py` like a tap: the same allowlist, the same
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
can send Ask's words another way. This route is not zero-retention; the owner chose it for mail, the calendar and
school too, knowing that (2026-11-15).
Instructions and tool definitions are cached between requests. Each turn's tokens and cost go in the engine's
record under the chat's own $10 a month limit; the words do not.

**What reaches which model.**

| | Ask's model (Claude Sonnet, the owner's key) | Luna (zero retention), only on a Hub without Ask's model |
| --- | --- | --- |
| The owner's typed request | Yes | Questions about the day only |
| Rooms, lights, speakers, scenes, what is playing | Yes | No |
| What Hub remembers | Yes | No |
| Earlier requests and Hub's answers | Yes | No |
| Mail, the calendar, school, today's list | Yes, the bounded lookups; never senders with AI switched off | Yes, the same lookups |
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

## Today

What needs a person, then the day. Data: `hub/app/today.py`. In the app: `web/app/src/today.tsx` and `day.ts`.
It reads what Hub already keeps and nothing else: the "Needs you" list of the older Today page
(`hub/today.py`: the sources' own checks, faults, the overnight review's suggestions, what was hidden), the
calendar week, the reviewed class guide, the trip, and whether Kitchen has a cart waiting. No model is called,
nothing is changed by reading it, and every part fails soft. The app asks for it when the screen is looked at
and once a minute while it is, and keeps none of it on the device.

- **The sentence** is written in the app from the data: the next thing today, then how much needs you
  ("Dinner at home is at seven. Three things need you."). Under it, the temperature outside, and how tomorrow
  starts once today is done.
- **Needs you**, at most five, the most pressing first. Three things differ from the older page, all from the
  owner's "mail is only what needs me":
  - each email that needs the owner is its own line (who it is from, the next step), three at most, the
    longest wait first;
  - Hub's own housekeeping offers (filing sorted mail, archive rules, planned changes) are not asked: they
    are one quiet line under the day, still a tap from their page;
  - what has its own place on the screen isn't listed twice: class dates, two events at once, the trip.
- **The circle** beside a need marks an email as handled in Hub's own sorting (nothing changes in Gmail) and
  hides anything else until tomorrow, or for a week where the older page did. It shows at once, and Undo
  puts it back. It needs a signed-in device.
- **The day**: what is left of today and all of tomorrow from the calendar, all-day events first; an event
  under way says Now; two busy events at once are marked on the later one.
- **This week at school**: the reviewed class guide's dates for seven days, a cancelled one struck through.
- **The trip**, from four months before it until it ends.

Tests: `tests/test_app_today.py`, on a made-up day (`tests/day_world.py`) that replaces Hub's readers, so no
real calendar, mailbox or service is read. The demo server shows the same made-up day.

## Mail

What needs the owner, then what Hub sorted. Data and taps: `hub/app/mail.py`. In the app: `web/app/src/mail.tsx`.
It is drawn from Hub's own sorting tables (`hub/tools/gmail/triage.py`), never a Gmail call, so it opens at once.

- **The sentence** counts the emails that need the owner, and under it when Hub last sorted and what it sorted today.
- **Needs you**: each email, the longest wait first, with the next step and a Find link (the older page's read-only
  search). The circle marks it handled in Hub's own sorting, with Undo; nothing changes in Gmail.
- **You asked, no answer yet**: the owner's own questions that nobody answered; the circle stops watching one.
- **Worth a look**: records Hub kept in the inbox (a large amount, something unusual); the circle says it is fine.
- **Sorted**: one line per kind Hub may file, with how many are in the inbox and the owner's level for it. At
  "File when I tap" a File button files what is ready, and each filing from the last two weeks can be put back.
  Filing and putting back move mail in Gmail through the bridge, exactly as the older page's buttons do.
- **Sort now** asks for a sort; **Rules and settings** opens the older page, which keeps levels, sender rules, the
  charge limit, the backlog and the plan.

Every tap needs a signed-in device. Tests: `tests/test_app_mail.py`, on made-up rows in Hub's own tables.

## Kitchen

The household's conversation with the kitchen service. In the app: `web/app/src/kitchen.tsx`. It uses the same
`/kitchen/...` relay as the older page, so nothing new reaches the kitchen service and nothing about it is kept
on the device: the conversation, recipe cards with "Add what's missing to the cart", cart plans with Add to cart,
the cart report after an add, orders as checkout showed them with Place order, the shopping list, and "Update from
orders". Approving a plan and placing an order still take a tap on a signed-in device (`pages.py`). Kitchen's
words are drawn as text, never as HTML. Turning on notifications stays on the older page, whose worker covers
`/kitchen/`. Ask's Kitchen reply opens this screen.

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

## Versions

System opens Versions: each release of Hub, newest first, with when it went live, what changed in plain words,
and links to the commit and to every change since the release before. The list is `hub/app/versions.json`,
kept by hand; `hub/app/versions.py` reads it. The links go to the repository named in the private settings file,
so no account name is in the source:

```json
"versions": {"repo": "https://github.com/<owner>/hub"}
```

Without it the screen shows short commit names, unlinked.

### Adding a release

Every release adds its entry, in the same branch as the work, before it is merged:

1. Fill in the entry at the top that has `"sha": null`: its `sha` and `live` come from the deployer's
   `last.json` for the release now running (`live` in UTC, `2026-11-15T15:34:06Z`). If that release was rolled
   back instead, leave it and fold its lines into the new entry.
2. Add a new entry above it with `"sha": null`, `"live": null`, `"from"` set to the sha just filled in, a short
   `title`, and `changes`: a few sentences the owner would say, not commit subjects. No names or private
   details; the file is public like the rest of the source.

A release can't name its own commit, so the newest entry stays `null` until the next one; Hub shows the running
version's commit for it. `tests/test_app_versions.py` checks the list holds together.
