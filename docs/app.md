# The app

Hub's new front: one small app instead of a page per tool. It opens on the house, drawn as a plan of rooms that
are lit when their lights are, and changes under a tap. Source: `web/app/` (Preact, TypeScript). Built files,
committed: `hub/static/app/`. The data and taps it uses: `hub/app/`.

It is being built in phases (the plan is in the owner's private review notes). Phase 1 is Home and music;
phase 2 is Ask, inside the app; phase 3 is Today; phase 4 brings Mail and Kitchen in as screens of their own. The
bar has Home, Today, Mail and Kitchen. A Today item that belongs to Mail or Kitchen opens that screen; others (the
trip, the class guide) still open their older pages.

Since 2026-11-15 (owner's request) Ask can change anything in Home Assistant, not only lights and music (see
“Setting up the house”), and Kitchen is part of Ask: the shopping list and the cart are worked from Ask, and the
Kitchen screen is the picture of the list on its way into the cart, with no second conversation (see “Kitchen”).

## The front door

The app is Hub's front door: `/` sends the browser to `/app/`, and the phone's home-screen icon opens it. The
older pages all still answer, listed under System; the old chat is at `/ask`. They wear the app's look (its palette and
typeface in `hub/static/style.css`, and the Evening or Day look chosen in System through `hub/static/look.js`) and carry
the app's tabs and a System link, so an older page is one step from the app rather than a place of its own. To open on the chat again, the owner
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
| `GET /app/api/picks`, `POST /app/api/play/{speaker}` | The library's playlists, and playing one where music is on, or with `"here": true` in that speaker's room only |
| `POST /app/api/undo/{batch}` | Put a change back: lights and speakers, or a change to how the house is set up |
| `GET /app/api/setup/{card}`, `POST /app/api/setup/{card}/confirm`, `.../dismiss` | A change that waits for a tap: what became of it, do it, or put it away (see “Setting up the house”) |
| `GET /app/api/kitchen`, `POST /app/api/kitchen/{do}` | The shopping list with each thing's product and where it stands, and its taps: `add`, `remove`, `pick`, `keep`, `fill`, `read`, `order`, `auto`, `reminders` (see “Kitchen”) |
| `GET /app/api/ask` | Whether Ask can run here, the last half hour of this person's requests, what they often ask around now, and what Hub remembers |
| `POST /app/api/ask` | One request, `{"text": ...}`, answered as it happens (see “Ask”) |
| `POST /app/api/memory/{note}/forget` | Forget one thing Hub remembers |
| `GET /app/api/today` | Today as data: what needs a person, the rest of today, tomorrow, the week at school, the trip |
| `GET /app/api/versions` | Each release of Hub, newest first, in plain words, and which one is running (see “Versions”) |
| `POST /app/api/deploy` | Ask the deployer for the checked version waiting, from a signed-in device (see “Versions”) |
| `GET /app/api/activity` | What happened in the house, newest first; `kinds`, `sources` (comma lists) and `before` (a time, for more) (see “Activity”) |
| `GET /app/api/changes`, `POST /app/api/playlist/{do}` | Everything Hub changed, and playlist taps: `make`, `skip`, `undo` (see “Changes”) |
| `GET /app/api/engine`, `POST /app/api/engine/{do}` | The Engine screen, and its taps: `tell`, `forget`, `request-done`, `improvement-done`, `note-save`, `note-skip`, `recipe-save`, `recipe-skip`, `recipe-remove`, `trust`, `run` (see “Engine”) |
| `GET /app/api/mail-settings`, `POST /app/api/mail-settings/{do}` | Mail settings, and its taps: `level`, `charge`, `sender`, `apply`, `remove`, `undo`, `rule-stop`, `rule-resume`, `rules-check`, `backlog` (see “Mail settings”) |
| `POST /app/api/today/tick` | A need's circle: `{"key": ...}` handles an email or hides an item; with `"back": true`, puts it back |
| `GET /app/api/errands` | The errands Hub got ready, set aside and finished lately, and how the last read went (see “Errands”) |
| `POST /app/api/errands/{id}/{do}` | A tap on an errand: `go` (a step was opened, `{"choice", "step"}`), `list` (`{"choice"}`), `done`, `later`, `not`, `back` |
| `POST /app/api/errands/read`, `POST /app/api/errands/settings` | Look again; where a drafted reply opens, `{"compose": "mail"}` or `"gmail"` |
| `GET /app/api/mail` | Mail as data: what needs the owner, questions nobody answered, records worth a look, each kind Hub sorted |
| `POST /app/api/mail/{do}` | A tap on Mail, `{"key": ...}`: `done`, `back`, `answered`, `fine`, `sort`, `file` (a kind) or `undo` (a filing) |
| `/kitchen/...` | The older page's relay to the kitchen service (`hub/tools/kitchen/pages.py`): what its assistant said, approving a cart plan, placing an order, reading new orders |

The picture is built in `hub/app/model.py` from the same reading of Home Assistant as the rest of Hub
(`hub/tools/house/read.py`), so nothing new leaves Home Assistant for it. Two facts were added to that reading for
the Home screen: the temperature outside, and which door and window sensors are open. They are not part of what
Ask's `home_status` lookup shares with a model.

**Buttons** (2026-11-15). A wall or remote button (a Home Assistant event entity of class button) shows in its room's
sheet under "Buttons": its name, last press and when, and its battery. Hub never presses one. What each press does
comes from the description of the Home Assistant automations that listen to it, read with two fenced, read-only
commands (`broker.ASKS`: `search/related`, `automation/config`) at most every five minutes. A description written as
"Single press: … Double press: … Long press: …" shows one row a press; any other description shows as one line.
Buttons are not part of what Ask's model is given.

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
one in more rooms is. The others are named in `music.also`, their rooms show the playing bars, the sentence and
the playing strip say so, and the music sheet lists every room under “Other rooms”: one playing its own music with
its volume and a pause, a quiet one with **Play here** and **Join**. Play here picks a playlist for that room alone
(`play/{speaker}` with `"here": true`, as Ask plays in a named room: its zone comes along, it leaves a group it
shared, it starts softly from silence) and never touches what plays elsewhere; Join adds it to the main music. A
room's own sheet offers the same. Nothing in the app moves a room that plays its own music into the main music
without the owner tapping Join on it.

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
couldn't place, something it read with `look_up`) does the model get its answer and one more try. A turn is at most six trips.

**What it can do** (`tools.py`): `lights` (rooms, floors, single lights, everywhere indoors, outside; on
restores each lamp's last brightness), `scene` (by its name, or by the words that single it out), `music` (play,
pause, resume, next, previous, volume, a room joining or leaving), `remember`, `groceries` (the shopping list and
the cart; see “Kitchen”), `kitchen` (passes the owner's words to the kitchen assistant, whose answer appears
under the request), `look_up` (reads mail, the calendar, the class guide or today's list; read-only), `errands`
(the asker's errands Hub got ready, drawn as cards under the answer; read-only), `errand` (Done, Tomorrow, Not this,
the list, Undo and where replies open, only as the person's own words ask; see “Errands”), and the
tools that change how the house is set up and work every other device (next section). Lights, scenes and music go
through `hub/tools/house/control.py` like a tap: the same allowlist, the same record and Undo, counted against
Ask's 30 changes a minute. Ask changes nothing for a program without a login, or while the owner has that kind
of change set to “Suggest only” on the Engine page.

### Setting up the house

Owner decision, 2026-11-15: Ask may change anything in Home Assistant, such as moving a light to another room.
Code: `hub/tools/house/admin.py` (reading, the rule for what waits, the record, Undo), `plans.py` (what each
change is, as requests and the requests that put it back) and the setup door in `broker.py`.

| Tool | What it does |
| --- | --- |
| `find`, `details` | Look for things by name, and read one in full: a device's state, a routine's config, a room. Read-only |
| `arrange` | Move things to another room, rename one, hide or show them, disable or enable them, give one an icon. A lamp that is its own device takes the device with it |
| `rooms` | Add, rename, set or remove a room, a floor, or a group of rooms (a Home Assistant label on areas, reached by name like a room) |
| `scene_setup` | Make or change a Home Assistant scene: what it sets, rooms kept as they are now, things left out |
| `routine` | Automations and scripts: save (the whole config, checked by Home Assistant's own `validate_config`), rename, remove, switch on or off, run |
| `device` | Any other device by service: the thermostat, fans, blinds, locks, the garage, helpers' values, a notice to a household phone |
| `setting` | Helpers (switches, numbers, choices, timers) and the house's own name, units and time zone |
| `integration` | Reload an integration whose devices stopped answering, by its title or by what it is (“hue” means every one of that kind). Nothing about how it is set up changes |
| `dashboard` | Home Assistant's own dashboards: make one showing a view per room or a view of named things, add or take out a view, rename, remove. Hub builds the views from the house's rooms and things (a heading and tiles); a model never writes a card. Views the owner made by hand are kept as they are |

**Done at once, with Undo.** A change is planned first: what it is in plain words, the exact requests, and the
requests that put it back, read from Home Assistant before anything is sent. Hub then makes it, says so in a
sentence (“Reading lamp moved from Living room to Bedroom.”), and Undo under the answer, on Activity and on
Changes puts it back for a day. A scene, automation or script is only put back while it is still as Hub left
it: one changed again in Home Assistant is left alone, and Hub says so.

**What waits for a tap.** One fixed rule, in code (`admin.asks`), never a model's judgement:

- anything that removes something (a room, a scene, an automation, a helper), and disabling a thing, which
  takes it out of use until it is enabled again;
- one request that changes more than ten things;
- unlocking, opening a door or the garage, disarming the alarm;
- a scene, automation or script that does any of those, when it is saved, run or set, however that is asked
  for (by name, or through its service): itself, through a scene it sets, through another routine it runs, as a
  device's own action, or in a way Hub can't read (a blueprint, a routine kept in Home Assistant's own files).
  `plans.opens` follows a routine into what it runs, four deep, and asks when it can't tell;
- the whole house's own settings, and restarting Home Assistant.

What this cannot see is a chain that starts somewhere else: an automation the owner made that unlocks when a
helper is switched, and Ask switching that helper. The rule covers what Ask itself saves, runs and sets.

Ask then answers “That one waits for your tap.” with a card: the question, what would happen, and two buttons.
Only the button, from a signed-in device, makes the change; typing “yes” does nothing, and no tool of Ask's
can press it. On the tap Hub plans the change again from the house as it is now and, if it would no longer do
what the card said, does nothing. A card waits an hour. Locking, closing and arming happen at once.

**What a model never moves.** On a turn that has mail, the calendar or school in view (`<data>`, or a `look_up`),
Ask changes nothing about the setup and unlocks nothing: words written by strangers are in the model's view on
those turns. Lights and music still work, as before. Errands are changed on such turns only as the guard in
`hub/app/errands/asked.py` lets the person's own typed words decide.

**What stays in Home Assistant.** The setup door names what may leave Hub (`SETUP_WRITES`, `CONFIGS`,
`SERVICE_DOMAINS`, and `setup_reload` for an integration). Not in it, so refused whoever asks: tokens and users,
backups, add-ons and the system itself, adding, removing or disabling integrations, removing devices, pairing,
updates, Home Assistant's built-in dashboards (the map, the first one) and dashboard resources, entity ids, and
services that run code or reach other machines (shell commands, web requests, MQTT). An automation saved from Ask may only call services behind
the same door; one the owner made in Home Assistant keeps what it already had.

**What the Engine page holds back.** Three settings, each “One tap, with undo” or “Suggest only”: lights, scenes,
speakers and other devices (`house.control`); how the house is set up (`house.setup`); locks, the garage and the
alarm (`house.security`).

Tests: `tests/test_house_setup.py` (the layer, on the made-up house, which follows setup changes too) and
`tests/test_app_ask_setup.py` (whole turns and the card's routes).

**Proved on the real house, without changing it.** Tests run on a made-up Home Assistant and a stand-in for
the model, so two live checks (`hub/live/checks.py`, on the Checks page) do the rest after each release:

- `house.setup` (reads only): everything the setup tools read from the real Home Assistant reads as Hub
  expects, Home Assistant checks a sample routine the way Hub writes one, a kept scene and automation read
  back, and a move is planned from it all and never sent.
- `ask.setup` (a few cents): `hub/app/ask/trial.py` makes up a move, a question about an automation, a new
  automation, a removal, an unlock and a list request from the house as it is and puts them to Ask with the real
  model behind the dry-run layer (`hub/golden/dryrun.py`), which stands in front of the setup door, the everyday
  door, Music Assistant and Kitchen: every change is written down and none is sent. Each request has one right
  outcome, checked in code. It runs once per version. What was asked and what Ask did is kept in Hub's private
  folder (`ask-trial.json`), since it names the house's things; the check itself says only how many.

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
| Rooms, lights, plugs, fans, speakers, scenes, what is playing | Yes | No |
| The thermostat, locks, the garage door and the alarm as they stand; the names of automations and scripts | Yes (since 2026-11-15) | No |
| Any other device, a routine's config, a room's contents | Only what a request needs, read with `find` and `details`: never tokens, addresses, pictures, or where people are | No |
| What Hub remembers | Yes | No |
| Earlier requests and Hub's answers | Yes | No |
| Mail, the calendar, school, today's list | Yes, the bounded lookups; never senders with AI switched off | Yes, the same lookups |
| Kitchen's answers; products, prices and notes on the shopping list | Never. Ask's sentences about the list carry counts and the member's own words; the rest is drawn on cards | No |

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

- **The sentence** is written in the app from the data: the next thing today, then how much needs you, the
  errands ready for a tap included ("Dinner at home is at seven. Nine things need you."). Under it, the
  temperature outside, and how tomorrow starts once today is done.
- **Needs you**, at most five, the most pressing first. Three things differ from the older page, all from the
  owner's "mail is only what needs me":
  - each email that needs the owner is its own line (who it is from, the next step), three at most, the
    longest wait first;
  - Hub's own housekeeping offers (filing sorted mail, archive rules, planned changes) are not asked: they
    are one quiet line under the day, still a tap from their page;
  - what has its own place on the screen isn't listed twice: class dates, two events at once, the trip, and an
    email or a class to-do whose errand is on the screen.
- **Ready for your tap**: the three most pressing errands as cards, finished from there, and the way to the rest
  (see “Errands”).
- **The circle** beside a need marks an email as handled in Hub's own sorting (nothing changes in Gmail) and
  hides anything else until tomorrow, or for a week where the older page did. It shows at once, and Undo
  puts it back. It needs a signed-in device.
- **The day**: what is left of today and all of tomorrow from the calendar, all-day events first; an event
  under way says Now; two busy events at once are marked on the later one.
- **This week at school**: the reviewed class guide's dates for seven days, a cancelled one struck through.
- **The trip**, from four months before it until it ends.

Tests: `tests/test_app_today.py`, on a made-up day (`tests/day_world.py`) that replaces Hub's readers, so no
real calendar, mailbox or service is read. The demo server shows the same made-up day.

## Errands

What the mail and the class guide want of the household, got ready so that one tap finishes each (owner's request,
2026-11-16: a household chief of staff). Code: `hub/app/errands/`. In the app: `web/app/src/errands.tsx`, the Errands
screen (from Today's "All errands"), the cards on Today and under an answer in Ask.

**Hub never sends, pays, books or orders anything.** A tap opens the exact place where the owner finishes it, filled
in: a reply in the phone's mail app (`mailto:`) or in Gmail on the web, the event on Google Calendar's own "new event"
page, the sender's own page to pay or renew on, a call. Hub notes the tap and watches for proof that it was done. The
one thing it does itself is put an errand's things on the household's shopping list, as if the owner had typed them,
with Undo; with "Put an errand’s things on the shopping list" at "Suggest only" on the Engine page, the errand links
to Kitchen instead.

**The read** (`read.py`) runs with the morning job, right after mail is sorted, and on "Look again":
- the emails that need the owner, the bills and renewals sorted as records and the calendar invitations from the
  last 14 days, and the class guide's to-dos for the next 14 (already reviewed for the family);
- each item once. An email's text comes through the read-only bridge for that run only and is never kept, and
  senders with AI switched off are never read;
- Luna, on its zero-retention route, at most 2 calls a run and 6 a day, within the monthly limit every kind of run
  shares. It sees the sender's name (never the address), the subject, the day it came, the text with links and
  addresses taken out, each link's words and site, and a few free times from the calendar for proposing a booking.

**What is kept is checked in code** (`draft.py`, `links.py`): an amount, a due date, an event's day and times, the
quoted sentence and a number to call only when the email itself has them; a link only when it is the email's own,
https, and on the sender's own site, never a mailing service's; a drafted reply only without links, addresses or long
numbers, and only where someone reads replies (not a no-reply or notifications address). A bill or renewal whose
email had no such link is finished on the sender's home page. Saying yes to an invitation also puts it on the
calendar, unless the calendar sent the invitation itself. A bill paid automatically is only told.

**A card**: what to do, one line of why, when it is due, the facts the email gave, the sentence it rests on, and
one to three ways to finish it. A way with one step is that step's button; one with more (a reply, then the
calendar) opens a sheet with each step and the reply as it will be sent. Then Done, Tomorrow (back at 7 the next
morning) and Not this, each with Undo; Done also marks the email handled on the Mail screen. "Replies open in"
picks the mail app or Gmail, for the person's own account. Every tap needs a signed-in device.

**Who sees what** (`docs/people.md`, `hub/people.py`). Each errand keeps the account it was read from, never a login:
`"default"` (the bridge's main Google account) for one from mail, a bill or a calendar invitation, and none for the
class guide's to-dos, which are the household's.
- A person sees, taps and is told about the errands of the accounts `people.sees` gives their login, and the
  household's. That holds on the Errands screen, the cards on Today, Ask's `errands` lookup, what the older questions
  about the day hand the model, and `catalog.errands_lookup`. Hub's "Needs you" list is made for no one, so its line
  counts the household's only.
- The household's are everyone's: one person's Done closes a class to-do for both, and either can put it back.
- A device with no login (this Mac itself), a login `"people"` doesn't name, a person with no account, and a
  `"people"` that can't be read see the household's errands only. Without `"people"` and with one allowed login, that
  login owns `"default"`; with more, nobody does until `"people"` says whose it is.
- A tap or an Undo on an errand the person can’t see is answered exactly as for one that doesn’t exist.
- "Replies open in", the tries a failed reading has, and each item's key are kept per account. A person with no
  account isn't offered "Replies open in".
- The morning read and the proof watch cover only accounts someone who may sign in owns; "Look again" reads the
  asker's own and the class guide. Mail comes only to `"default"` today, but nothing assumes it is the only account.
  The class guide's errands are drafted in their own call to Luna, without anyone's calendar, and a class to-do is
  never closed by one person's mail; Luna's limits stay the household's.

**Asked for in Ask** (`asked.py`, Ask's `errand` tool). "I paid the power bill", "the photo form can wait till
tomorrow", "I don't need the dental cleaning", "the pumpkin goes on the list", "undo the dental cleaning" and "open my
replies in Gmail" do what the same taps do, with Undo under the answer and the errand's card drawn as it now is.
Sending, paying, booking and the calendar stay buttons on the card. An errand's title comes from mail, so the person's
own words decide, checked in code: one sentence of what they typed must both ask for the tap and name the errand in
its own words and order; a question, an "if" or a "not yet" asks for nothing; a name is matched only against the
errand's title, kind and sender (and its things, for the list) and fits one errand at most; if any name fits none or
several, nothing changes. Only the asker's own errands and the household's can be named.

**Closing by itself** (`watch.py`): Hub looks for proof, as little as it takes: something the owner sent to that
address since the email came (sent mail through the bridge, dates only), an event that day on the calendar at the
same time or with a word of the title in common, an email from the same site whose subject says it went through
("Payment received"), or the email marked handled on the Mail screen. It looks when the screen is opened (at most
every 90 seconds), on "Look again" and each morning, at most 10 reads a run; an errand nobody tapped is looked at
every six hours, so a reply sent straight from the mail app closes it too. A call can't be seen, so it waits for
Done. An errand whose event or class day has passed, or that waited 21 days, is let go as missed; a bill keeps
waiting.

**Elsewhere**: Hub's "Needs you" list (the older Today page, the morning brief) has one line with the count of the
household's. Ask's model is given each of the asker's errands' title, why, due date and button words when asked
(`catalog.errands_lookup`, whose login comes from the request and is hidden from the model), never addresses, links or
the email's text, and nothing from a sender with AI switched off.

**Keeping** (`store.py`): one row an errand in Hub's own database, never in Gmail. Its words are blanked 14 days
after it ends and the row is deleted after 60, as sorted mail is.

Tests: `tests/test_app_errands.py`, on a made-up week (`tests/errands_world.py`) with a stand-in for Luna and the
made-up Kitchen, and two made-up people: one whose mail Hub reads and one whose it doesn't. The demo server shows the
same week, counted from the day it runs, to the owner; a request with `tailscale-user-login: casey@example.com` is the
made-up partner, who sees the class guide's two only.

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

The household's shopping list on its way into the shop's cart. Data and taps: `hub/app/kitchen.py`. In the app:
`web/app/src/kitchen.tsx`. Since 2026-11-15 Kitchen is part of Ask, not a second assistant: asking happens in Ask,
and this screen is the picture of the list, as Home is the picture of the house.

**One rule: on the list means in the cart.** The separate kitchen service keeps the list. Whatever is added, from
Ask, from this screen, by its assistant or from the household's Groceries reminders list, is given the exact
product the household buys at once, in code and with no model: its own line for those words, else a product
named for it (its name ends with the words) that it took more than half of the last five times it bought
anything by that name, and at least twice. So a changed brand is followed, and two kinds bought turn about are
asked about. Anything looser is offered and never chosen: something bought once, another of its lines that
merely holds the words (“chicken” for “chicken thighs”; “tortillas” must never mean “tortilla chips”), a
product that only mentions them, a word spelled nearly the same. What waits is picked by a person, once; the
pick is kept, so next time needs nobody. A number written counts single things: a pack of several covers it
(“6 eggs” is one carton of a dozen), and packs named are packs (“2 dozen eggs”). What is
ready goes into the cart in one cart turn: by itself, a minute after the last thing was added, once a member
switches “Fill the cart by itself” on; until then on one tap. Taking a thing off the list takes it out of the
cart. A reminder ticked off on a phone leaves what is already in the cart for a person to decide.

- **The sentence** says the most pressing thing: an order or a cart that waits for a tap, how many things need a
  pick, how many are going in. Under it, how much of the list is in the cart.
- **Add to the list**: commas part several things; a number with a thing is how many (“2 avocados”,
  “avocados x2”), an amount is kept as a note (“2 lbs chicken”), and two known things written as one
  (“milk and eggs”) become two.
- **Needs you**: an order to place, a cart plan the assistant suggested, each thing that needs a pick with its
  choices (what the household bought before, then what the shop's search offers) and why it waits, anything a
  cart turn couldn't confirm (one tap reads the cart and settles it), and anything ticked off the reminders
  while in the cart.
- **The list**: each thing, the product picked for it, and where it stands. A ring fills with lamplight once it
  is in the cart. A thing opens to say why that product (“Your usual.”, “Bought 3 times, last on Oct 20.”), how
  many (one fewer, one more; what is in the cart is set to the new number at the next fill), the other things
  it could be, and the way off the list.
- **The cart**, as its own page showed it when last read, with “Read it again” and “Check out”. Checking out
  reads checkout back as a card; only a tap on Place order, from a signed-in device, places it.
- **Due soon** (staples past their usual gap), **Ideas** (the assistant's latest recipes), **Earlier answers**
  (what it said before, read when opened) and **How it works** (the two switches, and how fresh the purchase
  history is).

**In Ask.** `groceries` adds (“add sponges and two avocados to the list”; said again with another number, a
thing is changed to that), takes off, shows, reads the cart from its own page, and readies checkout, in one
trip, and the things it touched are drawn under the answer with their products, a pick where one is needed,
and the tap that fills the cart. Recipes, meal ideas and questions about food go to the kitchen
assistant word for word (`kitchen`), and its answer is drawn under the request in full: recipes, a cart plan
with Add to cart, an order with Place order.

**What reaches which model.** Nothing of Kitchen's reaches Ask's model: not the list, not a product, a price or
a note about who eats what. Ask's model is given the member's words and calls a tool; Hub writes the sentence
from counts and those same words. The kitchen assistant is Claude Code on the owner's Claude plan (the approved
exception for Kitchen), and sees what it always did.

**Every tap** needs a signed-in device, in Hub and again in the kitchen service. An older kitchen service, from
before it kept the list, still works: the screen says it needs its update and shows its conversation, and
`groceries` passes the words to its assistant instead.

Tests: `tests/test_app_kitchen.py`, on a made-up Kitchen that follows what Hub sends it
(`tests/kitchen_world.py`). The picking itself is tested in the kitchen service's repository.

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
(`web/check_guard.py`). Ask works there on the stand-in model, with a made-up Kitchen (a list, made-up products
and a practice cart, `tests/kitchen_world.py`) and private desk, so the conversation, a setup change and the list
filling a cart can be tried and photographed without a key, a house or a shop. See `web/app/check/README.md`.

## Activity

System's "Activity" opens what happened in the house (`hub/app/activity.py`, `web/app/src/activity.tsx`), newest
first, by day, from three places:

| | What it says | What it can't say |
| --- | --- | --- |
| Home Assistant's logbook | Every state change of its devices, button presses, scenes and automation runs, and often what caused it: a person (by their Home Assistant user), an automation or a script | A wall switch or a device changing by itself has no cause |
| Apple Home, through HomeClaw | Each accessory's change in the selected home, with its old and new value | Who or what did it; anything while HomeClaw is closed or its event log is off |
| Hub's own changes | What Hub changed, who asked (Ask, a tap, a routine) and Undo for a day: lights and speakers, and changes to how the house is set up | |

**How it is read.** Every five minutes from Hub's start (`activity.start`), and when the screen opens if the last
reading is a minute old. Home Assistant: the broker's fenced, read-only `logbook/get_events` (`broker.ASKS`, start
and end only), at most six hours a request, a week back the first time. Apple Home: HomeClaw's command-line tool
(`hub/tools/house/apple.py`), which Hub runs only as `events` and `config`, both reads; HomeClaw's own tool can
change devices and scenes, so nothing else is ever passed to it. HomeClaw records only while its "Enable Event
Logging" setting is on; when it is off, the screen says so. Rows go into Hub's database (`house_activity`) and are
let go after 90 days; Home Assistant keeps about ten.

**One line per happening.** A Home Assistant row for a device Hub changed within 20 seconds is left out: Hub's own
row says who asked and has Undo. An Apple Home row for a device Home Assistant reported within 90 seconds, under the
same name, is left out and the Home Assistant row says "and Apple Home".

**What shows.** Lights and power, doors and locks, buttons, automations, music and setup (things moved, renamed,
rooms and helpers changed through Ask), until Motion or "Everything else" (sensors, a speaker going idle, a device
dropping offline) is tapped; the choice is kept on the device. A
source can be turned off the same way. "Hub's own changes and playlists waiting" opens Changes.

Nothing here changes the house, and none of it goes to a model. Tests: `tests/test_app_activity.py`, on the
made-up house with a stand-in HomeClaw.

## Changes

Activity's "Hub's own changes and playlists waiting" opens Changes (`hub/app/changes.py`, `web/app/src/changes.tsx`): every house,
setup and playlist change Hub made, newest first, from the records the older pages read, with Undo while it can still be
undone, and the playlists Ask proposed, waiting for "Make it" or "Not now". A house change is undone through
`/app/api/undo`; a playlist change, made or skipped through `/app/api/playlist`. The library itself, and deleting a
playlist, are in Music Assistant.

## Mail settings

Mail's "Rules and settings" opens Mail settings (`hub/app/mailrules.py`, `web/app/src/mailrules.tsx`): the plan of
inbox changes with Apply, Remove and, once applied, Undo; the backlog offer; how far Hub files each kind; the charge
alert; senders the owner sorted by hand, with "Let Hub decide"; and the archive-after-30-days rules, with Stop,
Resume and "Check them now". Each tap calls the older Mail page's own handler and reads its redirect, so the pause,
the checks and Undo are the same. While the plan applies or the rules are checked, the screen looks again every
few seconds. Quieting senders one at a time, bulk clean-up and tidying filters still open the older pages.

## Engine

System opens Engine (`hub/app/engine.py`, `web/app/src/engine.tsx`): notes and routines Ask proposed, waiting for a
tap; a box to tell Hub something; what Hub follows, with Remove; routines; what was asked for and not built; how far
Hub may go with each action; and what the models cost, with the overnight review's Run. It reads and changes
exactly what the older Engine page did, through the same functions in `hub/engine/`; every tap needs a signed-in
device. Today's links to the older Engine, Notes and Recipes pages open it, and so does a fix card's Build.
The older pages still answer; Ask activity stays there, for checking turns by hand.

## Versions

System opens Versions: each release of Hub, newest first, with when it went live, what changed in plain words,
and links to the commit and to every change since the release before. The list is `hub/app/versions.json`,
kept by hand; `hub/app/versions.py` reads it. The links go to the repository named in the private settings file,
so no account name is in the source:

```json
"versions": {"repo": "https://github.com/<owner>/hub"}
```

Without it the screen shows short commit names, unlinked.

**Deploy** (2026-11-15). When the deployer has a checked version waiting (`hub/engine/release.py`), Versions leads
with it: its commit subjects and a Deploy button, which posts `/app/api/deploy` and leaves the same request note as
the older Engine page. Only a signed-in device can ask. The card then says Hub is updating and looks again every
few seconds; when a different version answers it reloads, so the new app is what opens. If the deployer put the
earlier version back, it says so. System shows "A new version is ready" while one waits, and Today's "A new
version of Hub is ready" opens Versions too. The older Engine page's Deploy now lands on Versions as well.

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
