# The app

Hub's new front: one small app instead of a page per tool. It opens on Ask, a conversation with Hub, which speaks
first with what needs you and the house right now, over a field to say what you want (see “Ask at the center”). The house is one tap away, drawn
as a plan of rooms that are lit when their lights are, and changes under a tap. Source: `web/app/` (Preact,
TypeScript). Built files, committed: `hub/static/app/`. The data and taps it uses: `hub/app/`.

It was built in phases (the plan is in the owner's private review notes): Home and music, then Ask, Today, and
Mail and Kitchen as screens of their own. Since 2026-11-16 (owner's choice, to keep Hub from sprawling) the bar has
two places, Ask and House. Today, Mail and Kitchen open from Ask: from its cards, from under an answer, or by a few
letters typed in its field. A Today item that belongs to Mail or Kitchen opens that screen; others (the trip, the
class guide) still open their older pages.

Since 2026-11-15 (owner's request) Ask can change anything in Home Assistant, not only lights and music (see
“Setting up the house”), and Kitchen is part of Ask: the shopping list and the cart are worked from Ask, and the
Kitchen screen is the picture of the list on its way into the cart, with no second conversation (see “Kitchen”).

## The look: Lamplight

Since 2026-11-19 the app is drawn as the house seen from the
street after dark (`app.css`). A warm night (Evening) and warm paper (Day); Hub speaks in a serif of its own (Fraunces,
SIL Open Font License, its soft axis set in the file) wherever it speaks, Jost for everything you tap and read at a
glance. A lit room glows like a window, from gold to burnt orange, as bright as its lamps, and warms up and cools down
slowly. Behind the top of Ask, and along the foot of the wall, a band of warm night colours moves left to
right every two minutes and breathes; it is warmer the more rooms are lit (`Glow` in `ui.tsx`). It moves only by
transform and opacity and stands still where motion is turned down. Nothing else moves by itself but the bars of
music playing.

Colour keeps its meaning: lamplight is light and the one main action on a screen, sea-glass is sound, coral a problem.

The bar at the bottom is one row: Ask, House (and Dashboards on a phone), with the record playing and its pause at the
right; a tap on the record opens the music. A page Ask opened (Today, Mail, Kitchen, System and their pages) keeps Ask
marked in the bar and has its own way back at its top left, named after where it was opened from. Going into a page
is a step in the browser's history, so the phone's back gesture and Back come out the way you went in. A sheet can be
pulled down to close it.

## The front door

The app is Hub's front door: `/` sends the browser to `/app/`, which opens on Ask, and the phone's home-screen icon
opens it. `/app/#house` opens on the house. The
older pages all still answer, listed under System; the old chat is at `/ask`. Since 2026-11-18 the older pages are
moving in (owner's ask): Calendar, Insights (as AI spend), Trip and School are dashboards (see “Dashboards”), and All
tools and Settings open System, whose “How Hub checks itself” lists Status, Checks, Practice requests (the golden tests) and Ask activity. They wear the app's look (its palette and
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
| `POST /app/api/show/end`, `POST /app/api/show/timing` | The light show's card: end the show, or set its lights `{"value": 150}` ms earlier (below 0, later), on the timing's own steps and within its range (`hub/tools/house/lightshow.py`) |
| `POST /app/api/show/start` | A tap on one of Ask's choices for a light show, `{"handle": "#1a2b3c"}`, or on a playlist the light show's sheet offers, `{"uri": ...}` (one `/app/api/picks` gave): only something a lookup found lately, never words |
| `POST /app/api/undo/{batch}` | Put a change back: lights and speakers, or a change to how the house is set up |
| `GET /app/api/setup/{card}`, `POST /app/api/setup/{card}/confirm`, `.../dismiss` | A change that waits for a tap: what became of it, do it, or put it away (see “Setting up the house”) |
| `GET /app/api/kitchen`, `POST /app/api/kitchen/{do}` | The shopping list with each thing's product and where it stands, and its taps: `add`, `remove`, `pick`, `keep`, `fill`, `read`, `order`, `auto`, `reminders` (see “Kitchen”) |
| `GET /app/api/ask` | Whether Ask can run here, the last twelve hours of this person's requests (the model is given only the last half hour), what they often ask around now, and what Hub remembers |
| `POST /app/api/ask` | One request, `{"text": ...}`, answered as it happens (see “Ask”) |
| `POST /app/api/memory/{note}/forget` | Forget one thing Hub remembers |
| `GET /app/api/today` | Today as data, for the person asking: what needs them, the rest of today, tomorrow, the week at school, the trip, and the family brief's weather, house, night and note; `?wall=1` is the wall's version, the household's only, whoever asks |
| `GET /app/api/brief` | The brief in words, as plain text, for a Shortcut or a speaker to read out: the asker's own; `?wall=1` the household's |
| `GET /app/api/versions` | Each release of Hub, newest first, in plain words, and which one is running (see “Versions”) |
| `POST /app/api/deploy` | Ask the deployer for the checked version waiting, from a signed-in device (see “Versions”) |
| `POST /app/api/deploy/undo` | Ask the deployer for the version from before the last update, within a day of it (see “Versions”) |
| `GET /app/api/activity` | What happened in the house, newest first; `kinds`, `sources` (comma lists) and `before` (a time, for more) (see “Activity”) |
| `GET /app/api/changes`, `POST /app/api/playlist/{do}` | Everything Hub changed, and playlist taps: `make`, `skip`, `undo` (see “Changes”) |
| `GET /app/api/engine`, `POST /app/api/engine/{do}` | The Engine screen, and its taps: `tell`, `forget`, `request-done`, `improvement-done`, `note-save`, `note-skip`, `recipe-save`, `recipe-skip`, `recipe-remove`, `trust`, `run` (see “Engine”) |
| `GET /app/api/mail-settings`, `POST /app/api/mail-settings/{do}` | Mail settings, and its taps: `level`, `charge`, `sender`, `apply`, `remove`, `undo`, `rule-stop`, `rule-resume`, `rules-check`, `backlog`, `quiet` (see “Mail settings”); `GET /app/api/mail-settings/quiet`, the next loud sender |
| `POST /app/api/today/tick` | A need's circle: `{"key": ...}` handles an email or hides an item; with `"back": true`, puts it back |
| `GET /app/api/errands` | The errands Hub got ready, set aside and finished lately, and how the last read went (see “Errands”) |
| `POST /app/api/errands/{id}/{do}` | A tap on an errand: `go` (a step was opened, `{"choice", "step"}`), `list` (`{"choice"}`), `done`, `later`, `not`, `back` |
| `POST /app/api/errands/read`, `POST /app/api/errands/settings` | Look again; where a drafted reply opens, `{"compose": "mail"}` or `"gmail"` |
| `GET /app/api/mail` | Mail as data: what needs the owner, questions nobody answered, records worth a look, each kind Hub sorted |
| `POST /app/api/mail/{do}` | A tap on Mail, `{"key": ...}`: `done`, `back`, `answered`, `fine`, `sort`, `file` (a kind) or `undo` (a filing) |
| `GET /app/api/archive`, `POST /app/api/archive/search` | The Archive before a search, and what a search found, source by source, `{"q": ...}` (see “Archive”). Signed-in devices only, like every archive route |
| `POST /app/api/archive/mail` | Mail that matches, live from Gmail, `{"q": ...}`. Searches are POSTs, so the words are never in Hub's request log |
| `GET /app/api/archive/item/{id}`, `GET /app/api/archive/attachment/{id}/{part}` | One result in full; the text of a PDF attached to an email |
| `GET /app/api/archive/thumb/{id}`, `/photo/{id}?sharp=1`, `/page/{id}/{n}`, `/file/{id}` | A preview, a photo (sharp when Photos has exported it), a document's page, a document's own bytes |
| `POST /app/api/archive/kid`, `POST /app/api/archive/switch` | Who counts as one of the kids; what Ask may read (the owner's login only) |
| `GET /app/api/photos`, `GET /app/api/photos/thumb/{photo}`, `POST /app/api/photos/{do}` | A month the photo curator chose, a preview, and a mark, `{"key": photo}`: `keep`, `print`, `skip` or `clear` (see “Archive”). Signed-in devices only |
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
the playing strip say so. The music sheet then becomes “Music in the house”: one card for each music, the main one
first, each with what it is and where, play or pause and next, one volume for all its rooms (`group_volume`: each
room moves by the same step, so their balance stays), and its rooms opened up, each with its own volume and a way
out (not the room that leads: Music Assistant can't take it out while others follow). **Add a room** on a card
brings in a quiet room, or a room from another music, which then leaves it (never one that leads a music others
follow); **Change music** picks a playlist for that card's rooms only. With one music the sheet looks as before:
the track, the big buttons and the rooms where it plays. Under the cards, each quiet room has **Play here** and
**Join**; with two musics playing Join asks which. Play here picks a playlist for that room alone
(`play/{speaker}` with `"here": true`, as Ask plays in a named room: its zone comes along, it leaves a group it
shared, it starts softly from silence) and never touches what plays elsewhere. A room's own sheet offers the same.
Nothing in the app moves a room that plays its own music into another music without the owner tapping on it.

## Scenes

The row of words above the plan. Three kinds, run the same way:

- **Scenes Hub keeps** (`hub/app/scenes.py`). The owner sets the house, taps the plus, picks the rooms and names
  it: Hub remembers each of those rooms' lights as they are, and optionally "turn every other light off". Running
  one asks only the lights that differ, as one batch with one Undo, and the row shows which scene the house is in
  right now. Kept in Hub's own database (`app_scenes`), at most 12.
- **Home Assistant's scenes**, when it has any. Hub can run them but not undo them or tell whether one is on.
- **All off**: every light indoors that is on, with Undo.

## Ask

Say what you want, and Hub does it or answers. Code: `hub/app/ask/`. In the app: `web/app/src/ask.tsx`,
`asking.ts` and `now.tsx`. It replaces the older chat (`/ask`, `hub/agent/`), which stays until Ask runs on the live Hub.

### Ask at the center

Since 2026-11-16 (owner's choice, after finding Ask hard to start on a phone) Ask is the screen Hub opens to, and the
one way into everything else. It reads as a chat (owner's ask, after trying it: “not obvious that you are in a chat
interface”): a header naming Hub, Hub's words in bubbles on the left, yours in the lamp's colour on the right, and
a message field with its send button. From the top:

- **The header** (`ChatHead` in `now.tsx`): Hub, with its ember (which glows while Hub works on something), and the
  part of the day, read from the same clock as the greeting; or that Hub or the house can't be reached.
- **Earlier** (above, scrolled past on opening). The last twelve hours of this person's requests are kept, so they
  are still there when the app opens again. A conversation ends at a pause of half an hour; earlier ones sit
  above what Hub says now, each request and the start of its answer as two small bubbles (with its Undo), the
  last two shown and the rest under “Show N earlier messages”. A tap opens one. The model still sees only the
  last half hour (`brain.earlier`).
- **Hub speaks first** (`Greeting` in `now.tsx`). The day's sentence in Hub's own voice, on the band of light (“Good
  evening. Dinner at home is at seven. Three things need you.”), then at most three cards under it: what needs you (Today's rows, three
  at most, each with its circle and its button, then “Open Today”), and the house in a line with a tiny plan and
  its scenes (a tap opens House). When Home Assistant has a light show and none is on, “Light show” is the first
  chip (after the scenes on House): its sheet offers the owner's playlists, and one tap starts the show, whose card
  then leads Ask (`ShowSheet` in `sheets.tsx`). The third place is for a feature that needs a person now, such as an errand
  coming due or the house unwell. Never more than three: Ask stays a conversation, not a feed. On a wide screen
  the house card gives way to the plan beside Ask.
- **The conversation going on now**, under it, in full. What is newest is read from its start: the last request
  (or Hub's first bubble, on opening) stays at the top when what follows is taller than the screen.
- **Suggested messages** at the end of what Hub says first, while no conversation is going (beside another screen on a
  wide one, over the field): what this person often asks around now (marked with a lamp dot), then a few questions
  anyone can ask. One tap sends it.
- **The field** (“Message Hub”), pinned above the bar. It grows to a few lines, Return sends, and the keyboard's own microphone
  dictates. A request made while another is going waits under it and goes as soon as that one ends (Hub takes
  one at a time per person); while one is going, an empty field offers Stop, which stops waiting for it: what
  Hub already did stays done, with its Undo. On a phone, while the keyboard is up, the screen is sized to what it
  leaves (`viewport.ts`, from `visualViewport`), so the field rides on the keyboard and the bar steps aside meanwhile.
  Without the keyboard, Ask is pinned between the top of the screen and the top of the bar, the page under it never
  scrolls, and a page an iPhone left scrolled when the keyboard went is put back. While a finger is on the
  conversation (and as it coasts after), nothing scrolls it but the finger. The browser check holds Ask to this,
  including with the visible area reported moved as the iPhone did (owner's report, 2026-11-17).
- **A few letters** find a room, a scene, a playlist or a page (Today, Mail, Kitchen, House and System's pages)
  at once, without the model.

**Answers end in something to tap.** A question about mail, the calendar, school or what needs you ends in the
same rows Today draws, with their circles and buttons, and a link to Today or Mail. The card names only the topics
(`{"kind": "day", "show": ["mail"]}`, `tools.shown`); the app reads the rows from Hub for whoever is looking, so
nothing from the model or the mail is in the card, and each person sees their own.

**A new feature** joins Ask rather than the bar: a tool (and, for reading, a `look_up` topic), a card kind drawn
under the answer (`CardView` in `ask.tsx`), and, only if it needs a person right now, the third card at the top.
Its screen, if it has one, is a page Ask opens and the few letters find.

On a wide screen Ask is the middle column with the house beside it; on any other screen Ask sits beside it
instead. Cmd-K (Ctrl-K) puts the cursor in the field from anywhere.

**Three ways a request goes** (`brain.py`), chosen by what was asked:

1. **A few letters** find a room, a scene or a playlist in the app itself and work it in the list. Nothing is
   asked of anyone. What this person often asks around this time of day (`turns.usual`, counted from their own
   earlier requests, no model) is offered as one tap.
2. **Anything typed** goes once to the model with the house beside it: rooms, lights, speakers, what is
   playing, the scenes, the owner's playlists by name (read from Music Assistant at most every ten minutes,
   `brain.playlists`) and what Hub remembers, as a few plain lines (`brain.house`), plus the last few
   requests and what Hub did. So the model can act at once instead of looking first.
3. **A question about mail, the calendar, school or today's list** (owner decision, 2026-11-15, reversing the
   14th) goes the same way, with what Hub knows about it beside it in `<data>`: the same bounded lookups the
   older chat used (`private.data`), recognised by the question's words (`private.about`), so it is answered in
   one trip. One the words missed, the model reads with `look_up`. On a Hub without Ask's model yet, Luna
   answers these on its zero-retention route (`private.answer`), as before.

**One conversation per person** (owner's request, 2026-11-17: one chat that keeps going and manages its own context;
no side chats). Code: `hub/app/ask/thread.py`. Each person has exactly one conversation with Hub; two logins
`people` gives one member are the same one. It never resets by itself. The app shows it as one scroll, marked by day,
and reads back through it 40 turns at a time (`GET /app/api/ask?before=<id>`); turns are kept 90 days.

- **What the model is given of it** (`brain.earlier`): the running summary, then the last 20 turns word for word (at
  most about 9,000 characters), however long ago they were. An answer made from mail, the calendar or school goes in
  as a placeholder, never its words, as before.
- **The running summary.** Once 30 turns sit before the recent ones and outside the summary, they are folded into
  it after a turn, on a thread of its own, by one Luna call on its zero-retention route (`ask_summary` in the
  engine's record, under the shared monthly limit, at most 3 a day per person). Luna gets the summary so far and
  those turns, with the same placeholder for private answers; what it writes is cut to about 350 words, with links,
  addresses and long numbers taken out. It is a record beside the house, never instructions: it can't save a note
  or allow anything. A failed or empty fold leaves the summary as it was.
- **Asked in Ask** (`conversation`): "what do you remember about our conversation?" reads the summary back;
  "start fresh" (only when the person's own words ask to start over, start fresh or clear the conversation)
  starts the context over from now. The messages stay on screen and notes stay; nothing opens a second
  conversation. `POST /app/api/ask/fresh` does the same from a tap. The dry run holds it back.
- **No side chats.** The Engine screen no longer has its own box: guidance is told in Ask ("remember that …"), and
  a wish ("Hub should …", "I wish Hub …") becomes a line for the morning's fix cards, as the older chat's did. System
  no longer lists the older chat once Ask runs; `/ask` stays for a Hub where Ask isn't set up, and for the deployer.

**The morning message** (owner's decision, 2026-11-17: one voice in the morning, replacing the separate lists in
Ask). Code: `hub/app/ask/morning.py`. The first time a person opens Ask from 5 am, Hub adds one turn to their
conversation, written by code (no model): at most three numbered things, ranked house safety and money first, then
what is overdue or due within a day, then the soonest due. The pool is the errands that person may see and Today's
"Needs you" list (mail, the overnight review's suggestions, fix cards, the house, the weekly house review's ideas,
Kitchen's cart, Hub's own health); cut as the brief cuts Today for that person, so nobody hears of another's things.
An idea for the house is said on one morning only (docs/engine.md, “The weekly house review”). Under the greeting, Ask then shows
only the day's next thing and Open Today (only Open Today when the greeting already names that thing): the morning
message takes the Needs-you rows' place, and Today keeps the full list. The turn is kept as private (it holds errand titles and senders), so the model and the summary see only
that it was given.

- **Typed answers, in code before the model.** "done 2", "paid", "not this 1", "Thursday 3", "later", "build it"
  (a fix card) are matched to today's numbered things (`morning.reply`); with several open and no number, Hub asks
  which. An errand goes through its own tap (`taps.tap`: done, not, later; a named day sets when it comes back), a
  Today need through its tick, a suggestion through the engine's dismissal, a fix card through its decision, an idea
  for the house through the review's (“fix it” to an idea goes to the model, which has it in `<house>`). A tap on
  a thing's button counts as acted on (`POST /app/api/ask/morning/opened`).
- **Quieting.** Two "not this" to a low-stakes kind in 30 days leave that kind out for 30 days, said once. Money,
  the house and errands (which carry deadlines) never quiet that way. "What are you watching?" lists the kinds in
  plain words with the last two weeks' counts; "stop watching …" and "watch … again" switch one (`morning.watching`).
- **Counts, per person**, for the 14-day test: shown, done, not, later, opened, build (`morning.counts`), in Hub's
  own database. Code reads them; no model sees them. The dry run holds back `morning.act` and `morning.switch`.

**One trip to the model.** A tool that did the job says so itself, in a sentence Hub writes
(“The kitchen and the living room are at 40%.”), and the turn ends there: no second trip for the model to
describe what happened. Only when a tool could not do it as asked (nothing matched a search, a room it
couldn't place) or gave something to answer from (`look_up`, `music_library`, `hub_info`, `bike_bus`, `conversation`'s show,
`find`, `details`) does the model get its answer and go on. A tool that reads never ends a turn with a sentence of
its own: on 2026-11-18 "what's that playlist you made for me?" reached `conversation`, whose stock line about the
summary became the whole answer. A turn is at most six trips.

**How each turn ended is kept** (since 2026-11-18, after a diagnosis found every bad answer marked "ok"). Each turn
in `app_ask` keeps the tools it used with one word for how each came out (`"music_library:read"`, `"lights:not_done"`)
and, for a failure, its kind (`rate_limit`, `overloaded`, `timeout`, `too_many_steps`, or the error's class name).
Its status is `ok`, `stuck` (tools were used and none did the job or gave anything to answer from), `unanswered`
(the model neither spoke nor acted), `failed` (an error ended it) or `refused` (no key, a limit). Hub's log gets one
line a turn with the status, time and tools, never words. `turns.health()` counts them for a week. A rate limit or
a busy model is told as such ("Hub's model is busy just now"), not as "Hub couldn't finish that". Turns that got
stuck or failed stay in the context, so "try again" knows what to try.

**What it can do** (`tools.py`): `lights` (rooms, floors, single lights, everywhere indoors, outside; on
restores each lamp's last brightness), `scene` (by its name, or by the words that single it out), `music` (play,
pause, resume, next, previous, volume, a room joining or leaving), `remember`, `groceries` (the shopping list and
the cart; see “Kitchen”), `kitchen` (passes the owner's words to the kitchen assistant, whose answer appears
under the request), `look_up` (reads mail, the calendar, the class guide or today's list; read-only), `music_library` (the owner's
playlists, a playlist's songs, search with handles `music` plays exactly, and what played lately; read-only),
`playlist` (make, add to, take from, reorder, rename or delete a playlist, only when the person's own words ask;
making and deleting wait for their tap on Hub's Playlists page, the rest have Undo there for a day: the older chat's
`edit_playlist`, `catalog.edit_playlist`), `hub_info` (the trip, what Hub's AI costs and the inbox's counts, those
two for the owner only, and the devices on the home network; read-only), `bike_bus` (since 2026-11-18, the older
Bike + bus page's planner: their bike or a Lime with the bus, against the bus alone; the saved trip when no place is
said; the page's own limit of one search at a time and six an hour, `mobility/pages.limited_search`; the model gets
times, lines and stop names, never the saved addresses; read-only), `mail_search` and `mail_read`
(see “Ask reads mail”), `errands`
(the asker's errands Hub got ready, drawn as cards under the answer; read-only), `errand` (Done, Tomorrow, Not this,
the list, Undo and where replies open, only as the person's own words ask; see “Errands”), `house_health` and
`caretaker` (see “House health”), `search` and `read` (the family archive; see “Archive”), and the
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

**Ask reads mail** (`mailbox.py`; owner's choice 2026-11-17, week 1 of “one pattern for every category”). `look_up`
gives only what the last sort found; for a question about particular mail the model searches the person's own Gmail
with `mail_search` (Gmail's search words; never spam or the bin; up to 8 emails, newest first, twice a request at
most) and reads one with `mail_read` (by its number, from this request's search or the person's mail card of the
last half hour; up to 3,000 characters, three a request at most). The model never sees Gmail's ids or anyone's
address, and nothing from a sender with AI switched off: the From (and for sent mail the To and Cc) of the message
itself is checked again before its text goes. The emails found are drawn under the answer as one mail card (who,
the subject, when, and Open, which opens it in Gmail in the right account), kept with the turn so it draws without
reading Gmail again; it takes the day's mail rows' place. Only a login that may see the bridge's main Google account
(`hub/people.py`) is read for: anyone else is told it isn't theirs and nothing is read. The answer is private, so it
isn't carried into later requests; only that N emails were shown, by number. Nothing here changes Gmail, and a
request that read mail changes nothing in the house or on the list either (`tools.READ_MAIL`): ask for that by itself.

**Ask changes mail, after a tap** (`mailchange.py`; owner's choice 2026-11-18, week 2). `mail_change` archives emails,
puts them back in the inbox, adds or takes off a label (a new one is made on the tap), or marks them read or unread,
by their numbers from this request's search or the person's last mail card. The tool changes nothing: it keeps the
proposal (`ask_mail_waiting`) and puts a card under the answer listing each email (who and the subject) and what will
happen. Only the tap (`POST /app/api/mail/change/{id}/confirm`, from a signed-in device of the login that asked, which
must still see the main Google account) sends it, through the bridge's `/gmail/labels/apply`, at most 8 emails a card.
Undo on the card puts it back through `/gmail/undo` for a day. A card waits an hour, and a newer one replaces it. The
person's own words must ask for a change (`tools.MAIL_VERBS`); an email asking for one is data. A refusal changes
nothing; a result Gmail doesn't confirm is logged and the card says to check Gmail, never retried. The bridge has no
delete, trash, send or forward path, so Ask can't do any of those: they stay in Gmail.

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
- `ask.golden` (a cent or two, after each Deploy): the same trial's `core` set, on the Ask the owner uses: "What
  playlists do I have?" (the answer must name their real playlists), playing one of them in a room with a speaker
  (by its own address), a room's lights off, and a question about the weather. Requests are spaced a couple of
  seconds apart; one the model's rate limit stopped is left out, not failed, and a run the rate limit stopped
  entirely is skipped as "the model was busy". Kept in `ask-trial-core.json`. Until 2026-11-18 this check ran the
  older chat's golden scenarios (`hub/golden/`), which said nothing about Ask in the app.

**What Hub remembers.** One list per person: the engine's standing notes (`hub/engine/state.py`), the same list the
older chat saved to, each kept with the login that saved it, so a note steers only its own person's turns. Ask keeps a note at once when asked, shows it with Forget, and lists them under System. The
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
record under the chat's own $100 a month limit; the words do not.

**What reaches which model.**

| | Ask's model (Claude Sonnet, the owner's key) | Luna (zero retention), only on a Hub without Ask's model |
| --- | --- | --- |
| The owner's typed request | Yes | Questions about the day only |
| Rooms, lights, plugs, fans, speakers, scenes, what is playing | Yes | No |
| The thermostat, locks, the garage door and the alarm as they stand; the names of automations and scripts | Yes (since 2026-11-15) | No |
| Any other device, a routine's config, a room's contents | Only what a request needs, read with `find` and `details`: never tokens, addresses, pictures, or where people are | No |
| What Hub remembers | Yes | No |
| Earlier requests and Hub's answers, and the running summary of them | Yes | Writes the summary, from the same record (`thread.py`) |
| Mail, the calendar, school, today's list | Yes, the bounded lookups, mail and the calendar only the asker's own; never senders with AI switched off | Yes, the same lookups |
| Kitchen's answers; products, prices and notes on the shopping list | Never. Ask's sentences about the list carry counts and the member's own words; the rest is drawn on cards | No |
| The family brief, for its note: the day's weather, the owner's calendar titles and times for today and tomorrow, the class guide's dates, the locks, alarm, garage and doors as they stand, and the titles of the house's and the trip's needs | Yes (since 2026-11-16), and only that: never mail, never Kitchen's list. See “Today” | No |
| The family brief's household part, beside a question about the weather, the locks' batteries or “brief me” (`catalog.brief_lookup`): the forecast, the locks, alarm and doors with battery levels, the overnight note | Yes: nobody's mail or calendar, nothing of Kitchen's | Yes, the same lookup |
| The archive's mail and calendar results (sender's name, subject, date, Gmail's snippet; an event's title, date and place), and one email or event read in full | Yes (since 2026-11-16); never senders with AI switched off | No |
| The passages of documents and ChatGPT history that match what was asked, with card, account and ID-like numbers masked | Yes (owner decision, 2026-11-16), unless switched off on the Archive screen | No |
| Anything about a photo: the picture, a label, a person, a place, a date, a count, whether any were found | Never. Hub draws the card and writes its sentence itself | No |
| Someone else's mail, calendar, documents or ChatGPT history | Never: Ask reads for the person asking, by their login, only what that login may see | No |

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

## Today: the family brief

What needs a person, then the day, as one brief for the household: the calendar, school, the weather, the locks and
anything that needs attention. Each person sees their own (the owner's decision, 2026-11-16, `docs/people.md`): the
household's things (the weather, the house, the night, school, the trip, Kitchen's cart, the house's needs) are
everyone's; mail, the calendar, the note and Hub's own upkeep only the person whose account they come from, by the
Tailscale login the phone carries. A login Hub lets in but can't place is told so in a quiet line, so an empty day
isn't mistaken for a quiet one. A household need hidden with its circle is hidden for everyone ("Hidden for everyone
until tomorrow"), and a need from someone else's account can't be ticked. Data: `hub/app/today.py` and `hub/app/brief.py`. In the app: `web/app/src/today.tsx`,
`brief.tsx` and `day.ts`. It reads what Hub already keeps, plus the house's weather and locks, and changes nothing
by being read. The app asks for it when the screen is looked at and once a minute while it is, and keeps none of
it on the device. Every part fails soft: the weather, the house and the night are each read on a thread of their
own and left out if they haven't answered in two seconds, so a slow Home Assistant never holds Today up.

- **The sentence** is written in the app from the data: the next thing today, then how much needs you, the
  errands ready for a tap included ("Dinner at home is at seven. Nine things need you."). Under it, the weather in
  words ("45° and partly cloudy, up to 53°. Rain from 3 pm to 6 pm."; from five in the evening, tomorrow's), and how
  tomorrow starts once today is done.
- **The note**: one or two sentences that join the brief's parts ("Alex is away tomorrow, so the swim lesson at
  9:30 and the grocery pickup at 11 fall to one person."), written by Ask's model (`hub/app/ask/note.py`, Ask's
  own route and key, counted in Ask's spending and budget). It is written from the owner's calendar, so it is the
  owner's, on the owner's phone only. It is given only what is listed in “What reaches which model” above: never
  mail, never Kitchen's list and never Hub's own upkeep. It is written when someone looks and
  what it would be given has changed, at most once every half hour and eight times a day, never on a schedule
  and never at night; until a new one is ready the last one stays, if it was written for the same part of the day.
  When nothing joins up the model says so and there is no note. `household.json` `"brief": {"note": false}` turns
  it off; without Ask's key there is simply none.
- **Overnight**: the caretaker's word on the night (`hub.caretaker.note()`, when it is installed): until noon its
  headline and lines, then only the headline, unless something still needs someone.
- **Needs you**, at most five, the most pressing first. Three things differ from the older page, all from the
  owner's "mail is only what needs me":
  - each email that needs the owner is its own line (who it is from, the next step), three at most, the
    longest wait first;
  - Hub's own housekeeping offers (filing sorted mail, archive rules, planned changes) are not asked: they
    are one quiet line under the day, still a tap from their page;
  - what has its own place on the screen isn't listed twice: class dates, two events at once, the trip, and an
    email or a class to-do whose errand is on the screen.
  A lock's battery that is low or flat is one line, the flattest first with the rest named after it, and an
  **Add to list** button that puts what it takes on the shopping list in one tap ("AA batteries", unless
  `household.json` `"brief": {"batteries": {"lock": "..."}}` says otherwise). A lock that stopped responding
  for another reason, and an Apple Home lock left unlocked at night, are lines of their own.
- **Ready for your tap**: the three most pressing errands as cards, finished from there, and the way to the rest
  (see “Errands”).
- **The circle** beside a need marks an email as handled in Hub's own sorting (nothing changes in Gmail) and
  hides anything else until tomorrow, or for a week where the older page did. It shows at once, and Undo
  puts it back. It needs a signed-in device.
- **The day**: what is left of today and all of tomorrow from the calendar, all-day events first; an event
  under way says Now; two busy events at once are marked on the later one.
- **The weather**, drawn: now and the day's high and low, when rain comes, what to wear, the next twelve hours
  (the wet ones marked), the next four days, and sunrise or sunset. From Home Assistant's forecast
  (`weather.get_forecasts`, the one service call the everyday door in `broker.py` makes, which only answers),
  read at most every fifteen minutes; when it can't be read the last one is used for up to three hours. Which
  weather entity: `household.json` `"brief": {"weather": "weather.…"}`, else the forecast for home.
- **The house**: every lock, from Home Assistant and from Apple Home (HomeClaw's `list`, read-only, merged by
  name), with its battery; the alarm; the garage; doors left open. A sentence on top says what matters.
- **This week at school**: the reviewed class guide's dates for seven days, a cancelled one struck through,
  something to bring marked.
- **The trip**, from four months before it until it ends.

In the morning the order is what needs you, the rest of today, the weather, school, the house, tomorrow, the
trip; from five in the evening it is what needs you, the rest of the evening, tomorrow, tomorrow's weather, the
house, school, the trip. A wide screen shows two columns.

**Read aloud.** `GET /app/api/brief` is the same brief as a few plain sentences, for a Shortcut or a speaker
(the 4:30 nudge reads the older `/today/brief`).

Tests: `tests/test_app_today.py` and `tests/test_app_brief.py`, on a made-up day (`tests/day_world.py`) and
house (`tests/app_world.py`: a forecast, a sun, Apple Home locks, one flat and one low) that replace Hub's
readers, so no real calendar, mailbox, house or model is reached. The demo server shows the same made-up day.

## The wall

The brief on the kitchen iPad, all day: `/app/#wall` (System, The wall). Open it there in Safari, then Share,
Add to Home Screen, and open it from the Home Screen so it fills the screen. In the iPad's settings, Display &
Brightness, Auto-Lock: Never, unless the page's own request to keep the screen on is honoured; Guided Access
keeps it from being swiped away. Code: `web/app/src/wall.tsx`.

- **What it shows**: a large clock and the date; the weather now, today's high and low (from five, tomorrow's),
  when rain comes, what to wear, the next eight hours and four days; the overnight note in the morning; what needs
  the household; the house as tiles. It is laid out for a landscape iPad in two columns (the time and the weather;
  the rest), and in portrait with the time and the weather above the rest.
- **Only the household's** (the owner's decision, 2026-11-16): anyone in the kitchen sees it, so calendars,
  school, mail and the note stay on each person's phone. `?wall=1` is read as nobody's, whoever asks, and keeps
  the needs that are the household's (the house, Kitchen's cart, the trip), only counting any other household
  need ("One more thing is on the phone."). The wall's picture carries only the parts the wall draws (`brief.WALL`
  in `hub/app/brief.py`), so a part added to Today later stays off the wall until it is added there.
- **By itself**: it reads the day once a minute (a quarter hour at night), checks for a new version of the app
  every ten minutes and reloads into it, and says when Hub can't be reached while it keeps showing the day as it
  was. It never opens the house's live line and starts no Ask, so a wall left on all day costs Hub one reading of
  the day a minute.
- **The look follows the sun**: the day look while the sun is up and dusk after it sets, unless the iPad was
  given a look of its own in System.
- **At night**, from ten to half past five, it is a dim clock on black with the next day's weather, named by the
  day so it reads the same before and after midnight.
- **Burn-in**: everything moves a few pixels each minute, and the night clock further.
- **Add to list** works on the wall only when the iPad is signed in to Hub (its Tailscale login); otherwise
  the button isn't shown.

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
own words decide, checked in code, and when in doubt nothing changes (the card's buttons are always there):
- each name the model passes must be in what the person typed, and counts as their whole phrase there ("water bill"
  when the model passed "bill"); every word of it must fit the errand's title, kind or sender (for the list, its
  things too), never the email's text, and it may fit one errand only;
- a name must stand right after the words asking for that tap ("I paid the power bill", "skip the dentist"), right
  before them ("the power bill is paid", "the photo form can wait") or between their halves ("mark the permission form
  done"), with only small words between; another name joins only through "and" or a comma right after one already
  tied, said exactly as the model passed it, where the sentence ends ("... and the water bill");
- in that part of the sentence, a question ("is the power bill paid", "who sent the photo form"), a condition ("if",
  "when", "once", "wish") or a "not" outside the asking words ("don't skip the dentist") asks for nothing;
- "Replies open in" changes only when a part of a sentence starts by telling Hub to ("open replies in Gmail", "use the
  mail app for replies") or names replies and where;
- if any name fails, nothing changes. Only the asker's own errands and the household's can be named, and a "which
  one?" lists their titles as the `errands` lookup gives them (none from a sender with AI switched off).

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
after it ends and the row is deleted after 60, as sorted mail is. A database from before errands kept their account is
rebuilt once, in one transaction: the class guide's rows become the household's, every other row the main account's,
and the old "Replies open in" and mailbox settings the main account's.

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

**Whose it is.** The mail is one person's: the bridge's main Google account (`"default"`, `hub/people.py`). For
anyone else, Mail and Mail settings answer `{"ok": true, "connected": false}` and draw a calm screen saying their
mail isn't connected yet, and their taps change nothing; see “What follows the rule” in `docs/people.md`.

## Dashboards

Owner's rule (2026-11-17): every category in Hub works the same way. Ask knows it and answers about it; what needs a
person shows in Ask or Today; and the category has one page of its own that the person can arrange. A dashboard is
that page, written once for every category. Code: `hub/app/boards/` (one module per board), in the app
`web/app/src/boards.tsx`. Address: `/app/#board/<id>`; `/app/#boards` lists the ones this person may open. They are
found by a few letters in Ask, listed in System, and Today ends with one line for each. On a laptop or iPad the left
column lists each one under Ask and House; on a phone (owner's ask, 2026-11-18) the bar gets a third place, Dashboards,
once the person has one, and it opens the list.

- **Blocks.** A board is a list of blocks of a few kinds the app draws: `hero` (a big line, with an optional progress
  bar), `tiles` (numbers), `rows`, `checks` (a checklist), `line` (a chart, points in a 720 by 180 box), `bars` and
  `note`. A kind the app doesn't know is left out.
- **Arrange** puts the blocks in the person's order and hides the ones they don't want: kept by login in Hub's
  database (`app_board_layout`), theirs alone. A block a board adds later comes last. A checklist's ticks and a
  board's settings (such as the due date) are the household's (`app_board_ticks`, `app_board_settings`). A board with
  settings also has a Settings button beside Arrange, which opens them in one tap.
- **Ask.** A question in a board's words (`private.TOPICS`: `pregnancy`, `network`) is answered with the board's
  `facts` beside it, and the board's headline and first numbers drawn under the answer (`{"kind": "board"}`), read
  from Hub for whoever is looking. Mail's questions keep Mail's own lookups.
- **Who sees which.** Each board says (`sees`): pregnancy, school, trip, network and AI spend, the household's members; mail and
  calendar, the mailbox owner. Never on the wall. The morning message's pool is unchanged during the assistant's 14-day test.

| Board | Reads | Shows |
| --- | --- | --- |
| Pregnancy | the due date (`"pregnancy": {"due": "YYYY-MM-DD", "scan": false}` in `household.json`, or set on the board; called an estimate until a dating ultrasound sets it, the board's "Dated by ultrasound" switch or `"scan": true`) | the week, size, trimester bar, days to go, the average chance of a live birth from here and its curve from week 4 to 40 (sources in `pregnancy.LIVE`), when babies are born by date (CDC 2023 single births) and a day-by-day bell for labour around the due date, what usually comes next (US schedule of visits, screenings, vaccines), a shared getting-ready list, visit cadence, month by month |
| Network | the older Network page's overview (`hub/tools/network/pages.overview`), at most once a minute | up or down, speed and ping now, the day's uptime and drops, download and ping charts, what needs a look, busiest devices |
| Mail | Hub's own sorting tables, the daily inbox size, the plan of inbox changes | counts, what needs you, the inbox over 30 days, by kind, Hub's cleanup, changes that need a check |
| Calendar | the older Calendar page's reader (`hub/tools/calendar/read.week`, through the helper) | what is on today, the week's counts and clashes, the next seven days. Replaces that page: `/calendar` opens it |
| School | the class guide as last published for the family (`preschool/publication.latest`) | the next 30 days of class dates (cancelled ones marked), the guide's topic pages and links; the class guide's own pages stay for reviewing it |
| Trip | the saved trip (`hub/tools/trip/store.load`), read-only | the dates, nights and known cost of the chosen plan, the itinerary, what is still to decide, bookings, getting ready and links; changing the trip stays in its workspace at `/trip` |
| AI spend | Hub's record of runs (`hub/insights.spend`) and the newest live check (`hub/golden/runner.latest`) | each monthly limit, cost and runs by kind over 30 days, Ask's last live check. Replaces the older Insights page: `/insights` opens it (its inbox charts were already on Mail, its network summary on Network) |
| Money | Money's own tables (`hub/app/money/`), never a bank or a model on opening | what needs you, the month against the budget, checking now and its six-week low, spending by category, bills and paydays in the next two weeks, unsorted charges, goals, accounts and net worth, the long run, recent changes |

| Address | What |
| --- | --- |
| `GET /app/api/boards` | The dashboards this person may open, with their line for today |
| `GET /app/api/board/{id}` | One, in this person's order; 404 when it isn't theirs |
| `POST /app/api/board/{id}/layout`, `/tick`, `/set` | `{"order", "hidden"}`; `{"item", "done"}`; `{"key", "value"}` |

**Money** (`hub/app/money/`, board `hub/app/boards/money.py`). The household's bank, card, loan and investment
accounts, read through SimpleFIN Bridge (read-only: Hub's client refuses anything but the token claim and
`GET /accounts`, so Hub can never pay, transfer or trade) or from statement files (OFX, QFX, CSV). Owner's decisions
(2026-11-16): shared, with private accounts (each member sees every shared account and the household's budget and
plans; an account its owner marks private shows to the other only as its month-start total rounded to $100, and stays
out of the household budget); and Ask's model gets what a money question needs from an allow-list (names, kinds,
balances, the month, bills, cash ahead, goals, matching transactions with payees cleaned), never an account or card
number, and nothing of a member who turned "Ask may use my money" off. Its settings are each member's own and are on
the board only, never in Ask: the SimpleFIN setup token, which of their own accounts are private, and the Ask switch.
Ask's tools: `money` and `what_if` read; `money_budget`, `money_sort`, `money_goal`, `money_bill`, `money_account` and
`money_plan` change Hub's own record (with Undo, batch `m…`), refused on a request that had payees in view. Data:
`money.db` and the access URLs in `money-access.json`, both owner-only in Hub's data folder. Readings: twice a day,
at most 8 a day and one tap an hour. The wall and programs on the Mac (no login) see nothing; money is never in the
morning push. Tests: `tests/test_app_money.py` on a made-up household (`tests/money_world.py`), which the demo uses too.

A new board: a module in `hub/app/boards/` with `ID`, `NAME`, `WHAT`, `sees`, `picture`, `line` and `facts`, added to
`_boards()`; a topic in `private.TOPICS` if Ask should hear it; tests and browser-check steps. Tests:
`tests/test_app_boards.py`.

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

## Archive

The family's photos and videos, documents, mail, calendar history and the owner's ChatGPT history, in one search
(`hub/app/archive.py`, `web/app/src/archive.tsx`). It is the fifth place in the laptop's rail; on a phone it opens
from the search button in the top bar, from System, and from Ask's cards ("See all"). Owner request, 2026-11-16.

**Where each part comes from.**

| | Read by | Kept where | Fresh |
| --- | --- | --- | --- |
| Photos and videos (the owner's library and the shared one) | The archive program, with osxphotos, from the library's own database | The archive's index and 400-pixel previews | Nightly |
| Documents in Documents, Desktop, Downloads and iCloud Drive | The archive program: PDFKit, Vision for scans and pictures with text, textutil | The archive's index: text in pieces of about 1,000 characters, page by page | Nightly |
| ChatGPT history (the export in Documents), the group chat, attached documents | The archive program, from the export's own files | The archive's index, message by message | Nightly |
| Mail | Hub, live in Gmail through the bridge: each phrase its own query, newest first, ten at most | Nowhere | As asked |
| Calendar history | Hub, through the bridge, month by month as far back as it answers | Hub's database (`archive_events`, with full-text search) | Hourly for the weeks around now, nightly for a year either side |

**The archive program** (`deploy/archive`, installed by `deploy/install-archive`) is the only thing on the Mac with
Full Disk Access; Hub never opens the Photos library, the documents or the export. It reads them and never changes
them, and keeps what it derives in one owner-only folder (`~/Library/Application Support/HubArchive`, folders 0700,
files 0600): `archive.db` (SQLite with FTS5: porter stemming, accents ignored), the previews, a cache of sharp photos,
and the curator's files. It answers Hub on a Unix socket in that folder, on fixed read-only routes (`serve.py`): every
item is named by an id the index already holds, so no request can name a file; a photo is sent as a picture made from
what Photos keeps on the Mac, never its original; a document's own bytes only when it is on the Mac. Nothing is logged
about what was asked. Its LaunchAgents: `hub-archive` (the service, always), `hub-archive-index` (3:30 every night),
`hub-archive-curate` and `hub-archive-apply` (the curator, below), and `hub-archive-access` (run once, by hand).

- **Photos**: every photo and video except hidden ones, those in Recently Deleted and the extra shots of a burst: when
  and where, the people Photos knows by name, Photos' own labels and the text it read in a photo (never for a photo
  Photos marks as an ID document), titles, captions, keywords, albums, favorite, its score. Most photos keep only a
  small preview on the Mac (the originals are in iCloud): the viewer shows it at once and puts a 2048-pixel picture
  over it when Photos has exported one, bringing the original from iCloud (`sharp.py`; a 2 GB cache, the least
  recently opened going first). That needs the owner's one yes to the archive working Photos, the same one the
  curator's albums need; without it the viewer keeps the Mac's own picture.
- **Documents**: documents only (PDFs, Word and other text documents, spreadsheets, slides, and pictures that hold
  text), never source code or anything inside a software project, a library or a bundle; nothing over 100 MB.
  Numbers, Keynote, Pages and Office spreadsheets and slides are found by name and folder, with a preview. iCloud
  Drive keeps many files only in iCloud: the archive asks iCloud for the documents among them (25 MB each at most,
  pictures 10 MB, 3 GB a run) and reads each once it is on the Mac. It never evicts, moves or changes a file.
- **ChatGPT**: each conversation along the branch it ended on, voice included; never ChatGPT's thinking or the
  summaries of it. Attached PDFs, Word documents, text and Markdown are read into their conversation.

**The kids.** A person Photos knows by name is one of the kids when their faces over the last two years they were
photographed are mostly a baby's or a child's (Photos' own face age types, which match its Baby and Child groups),
and they were photographed in the last two years: someone who was a child in old photos is not. "The kids" in a search
means those people, and children Photos sees but hasn't named. Anyone signed in can change who counts on the Archive
screen (`archive_kids` in Hub's database).

**A search** is read in Hub with no model (`understand`): years, months, seasons, "last summer", "two years ago", the
people Photos knows (and "Alex's"), "the kids", "last" or "first", and words that name a source ("pdf", "email",
"chat", "video"). The rest are the words to look for, matched in the archive with all words in the same page or
message, and with any of them when none has them all (the screen says so). The sentence on top is Hub's own: "Kids at
the beach in 2024: 48 photos from 6 days." Photos come as a grid that opens full screen; documents with their first
page, the passage that matched and Get the file; mail as it arrives from Gmail; events; conversations opened at the
message that matched. Before a search: photos from this day in earlier years, the month's best (below), what the
archive holds, the kids, and what Ask may read.

**Who sees what** (owner decisions, 2026-11-16). The owner's login sees everything in the archive. Any other signed-in
login sees the photos in the iCloud Shared Library and nothing else of it: not the owner's own library, documents or
ChatGPT history (“Shared Library photos only”, `archive.may_see`); its counts, On this day, the month's best and every
answer by id are cut the same way, by the archive program itself. Mail and the calendar are each person's own: a
login sees the Google accounts that are its own (`hub/people.py`, by Tailscale login), so until another person's
account is added, they see neither, and the screen says so. Another login opening the owner's email, event or
document by id is told it isn't there. The owner's login is the one `hub/people.py` names, else the one in the private
settings file (`"archive": {"owner": ...}`), else the login this Mac's Tailscale is signed in with. A program on the
Mac with no login gets nothing.

**Ask** searches it with `search` and opens one result with `read` (see "Ask"). Photos: Hub searches, draws a card of
up to 12 and writes the sentence; the model is told nothing about them, not even whether any were found, and that
never decides whether it goes again; what is kept for later requests says only that Hub answered from the archive.
Mail and calendar results go to the model (a sender's name, subject, date and Gmail's snippet; an event's title, date
and place), never senders with AI switched off. Documents and ChatGPT history: the passages that match what was asked,
with card, account and ID-like numbers masked (owner decision, 2026-11-16); either can be switched off on the Archive
screen, and then the model hears only that there are matches for the owner to open. A request that read anything
written by others changes nothing in the house.

**Best of the month.** The curator (`deploy/archive/curator.py`) chooses each month's best photos on the 1st: last
month, the owner's library and the shared one, without screenshots, hidden photos or burst extras, the best of each
moment by the scores Photos computed on the Mac, raised for favorites and people, 25 picks with at most four a day,
and 25 runners-up. Hub reads them from the curator's folder (`hub/app/photos.py`) and writes back one file, the
marks. The curator watches that file: Keep and Print add the photo to the month's album in a "Photo picks" folder in
Photos, and Print also saves a full-size copy to a prints folder. It never edits, hides, deletes or un-albums a photo.
`#photos` and System's older Photos link open the Archive.

**Files from the archive** are sent with Hub's no-store rule and a sandbox, and a document a browser would run (a web
page, an SVG, XML, a script) is sent as plain bytes, to be saved.

Tests: `tests/test_archive_program.py` and `tests/test_archive_serve.py` (the archive program, on a made-up archive:
`tests/archive_world.py`), `tests/test_app_archive.py` and `tests/test_app_ask_archive.py` (Hub and Ask, on a
stand-in archive over a real socket: `tests/archive_standin.py`), `tests/test_app_photos.py`,
`tests/test_photo_curator.py`.

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

## House health

System's first row, "House health", opens what the house's caretaker sees (`hub/caretaker.py`,
`web/app/src/health.tsx`). The caretaker is a program of its own on the Mac (home-ops `caretaker/`, its design in
that repository's `docs/CARETAKER.md`). It watches Home Assistant, the devices, Hue, Apple Home, the network, Hub, its
deployer and helpers, routines, backups and the Mac; fixes a fenced few things by itself (an integration reload,
two apps, the four services, opening HomeClaw); and keeps a folder, `HUB_CARETAKER_DIR`
(`~/Library/Application Support/HomeOps/caretaker` by default):

| | What Hub does with it |
| --- | --- |
| `status.json`, rewritten every minute | Reads it: each system's state and line, what needs you, the advice, batteries, what isn't watched, the drills |
| `caretaker.db`, its journal | Reads it, read-only (`mode=ro`): what needs you, the night's lines, seven days of history, a drill's steps, the answers to your requests. SQLite may add the journal's own `-wal` and `-shm` files beside it when the caretaker isn't running; Hub writes no rows |
| `queue/` | The only thing Hub writes: one request note per tap |

**The screen.** A headline (what needs you, all quiet, paused, or the caretaker hasn't looked since when), then:
- Needs you, with Restart Home Assistant where the caretaker asks for it, Try again where a step can help, Done
  and Ignore;
- Last night, from 9 PM: what it fixed, what came back by itself, and other lines worth a glance;
- Watching: a row per system with its state, each opening seven days as dots and what happened;
- The last seven days, day by day;
- Make it sturdier, with I did this and Not for us;
- Batteries;
- a drill's steps, only while one is running or just ran;
- one button, Pause until I resume (Resume while paused), and a line saying Ask steers the rest. Pausing keeps it
  watching and telling, but it fixes nothing.

**Steered from Ask** (owner's rule, 2026-11-18: Ask is the one way in, so the screen has no drill or pause-length
controls of its own). Every request carries House health's headline and what needs you in a line or two
(`caretaker.brief`); `house_health` reads the caretaker's whole account (`caretaker.lines`), and a question that names
what broke, what was fixed or whether everything works gets it in `<health>` beside the house (`brain.health`), so
it is answered in one trip. It is data from the house, never instructions. `caretaker` pauses (for a length, or until
resumed) or resumes it, runs one of its drills (played under the answer until it finishes), tries a step again, marks
a problem sorted, or says the owner did one of the sturdier things or doesn't want that suggestion. It goes through
`caretaker.plan`, which finds the item from the person's words, then `caretaker.ask`, as a tap does. The person's own
message must ask for it (`tools.CARE_ASKED`: a word for pausing, trying again, a drill and so on, or a plain yes to
Hub's own question), so text in House health, such as a device or problem named to read as an instruction, can't
steer the caretaker however the model takes it. Restart Home Assistant and Ignore (leave a problem alone from now
on) are never done from words: Ask answers with the caretaker's card and its button, and only the tap asks. Not
wanting a sturdier suggestion is not Ignore; it is a note from words like the others. Try again and restart are
refused while Ask is set to Suggest only; nothing else it asks reaches the house. A tap is the owner's own, so Suggest
only doesn't hold one back. An answer about House health ends with Open House health (`{"kind": "health"}`).

**Taps.** Each needs a signed-in device. Hub checks the request first (`caretaker.ask`): the kind of request (`fix`,
`restart`, `done`, `ignore`, `pause`, `resume`, `drill`), that the item is open and not a drill's, that a Try again
has a step to try and isn't a restart (`restart` is its own request, only for the item that asks for it), that a
drill is one the caretaker lists, a pause's length, and that the caretaker looked in the last five minutes: a request
it wouldn't see is refused in words rather than left waiting. Then it leaves `queue/hub-<id>.json`
(`{id, do, target, by, at}`), written aside and moved in. The caretaker checks it again, does it or refuses, answers
it in its journal within a pass, and deletes it. Restart Home Assistant runs only from such a tap.

**Today and the morning.** Each needs-you item the caretaker marks for Today is a Today item keyed `care-<id>`, with
the caretaker's words and Open to House health. Battery items stay on this screen. Items raised at night wait for
the morning, except Hub, the broker, the kitchen service or Home Assistant still down half an hour after a failed
fix. `caretaker.note()` gives the night in a headline and lines, 9 PM to now until noon, for the morning brief
(`{"at", "headline", "lines": [{"tone", "text"}], "needs"}` or None), and `GET /app/api/health/note` serves it. The
top button and System's tab turn amber while something needs you or the caretaker has stopped looking.

**When it stops.** A heartbeat older than five minutes is said plainly on the screen and in System ("The caretaker
hasn't looked since 3:12 AM"), and the live check `caretaker.alive` fails. A journal Hub can't read (damaged, or written
by a caretaker with tables Hub doesn't know) is said the same way, never as "all quiet": the top button turns amber,
House health and Ask say the journal can't be read, Today and the morning go on without it, and nothing is asked of
the caretaker. A row without a time is left off the week rather than failing the page.

Nothing here reaches the house; Hub never runs the caretaker or a fix. Tests: `tests/test_app_health.py`, on a
made-up caretaker folder (`tests/caretaker_world.py`), which the demo also uses with a stand-in caretaker that
answers taps and plays drills.

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

Nothing here changes the house, and none of it goes to a model as it is. Once a week the house review reads these
rows for ideas (docs/engine.md, “The weekly house review”), and only those ideas, in Hub's words, reach Ask's model,
for the owner. Tests: `tests/test_app_activity.py`, on the made-up house with a stand-in HomeClaw.

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
few seconds.

**Quiet your loudest senders** (since 2026-11-18, moved in from the older Quiet page): one sender at a time, as that
page's card showed it (how many are in the inbox, how much is unread, the last three subjects, whether a filter
already skips the inbox), read from the Gmail helper by `GET /app/api/mail-settings/quiet` for the mailbox owner
only. Skip the inbox and archive, new mail only, Keep or Later goes into the plan above through the older page's own
`decide`; nothing changes in Gmail until the plan is applied. Labels, 30-day rules and Luna's advice still open the
older page, as do bulk clean-up and tidying filters. The older Inbox overview and Labels pages open the Mail board.

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

**Automatic deploys and Undo** (2026-11-18). Once the owner switches it on, the deployer deploys `main` by itself when
its checks on GitHub are green, and goes back if a check after the switch breaks (home-ops `docs/HUB-DEPLOYER.md`).
Then it runs `python -m hub.engine.release tell` with the newest code: a notice to the owner's phone through Home
Assistant (`notify.<phone>` named in the private settings as `"deploy": {"notify": "mobile_app_..."}`; without it,
none). Today shows the same for a day from the deployer's `auto.json` ("Hub updated itself", with Undo, or why
nothing went live). For a day after any update that wasn't itself an Undo, Versions shows it with **Undo**, which
posts `/app/api/deploy/undo` and leaves a request note with `"undo": true` for the version from before; the card
follows the restart as Deploy does. Undo waits while another request is queued.

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
