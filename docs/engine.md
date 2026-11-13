# The engine

Hub is the control room for a small automation engine. This page is the map and the rules.
Code: `hub/engine/`. Owner's page: `/engine`.

## The five parts

| Part | What it is | Where |
| --- | --- | --- |
| Sources | What Hub may read, and what of it a model may see | `catalog.SOURCES` |
| Judgement | A model on the zero-retention route. No tools: it returns text only | `overnight.py`, `judge.py`, `hub/tools/gmail/triage.py` |
| You | The “Needs you” list: open, do, or Not today | `hub/today.py` |
| Actions | What Hub may do, each with a preview page, an undo and a trust level | `catalog.ACTIONS` |
| Record and limits | Every run with its calls and cost; daily and monthly stops | `state.py` |

Standing instructions (“What you’ve asked for” on the Engine page) are the owner's plain-words
guidance. They steer judgement and are given to Ask Hub on every turn. They cannot add a source or an action.

## Adding something

- **A source**: one `Source(...)` entry in `hub/engine/catalog.py` saying in plain words what it reads
  and what a model may see, with either rule checks (`items`), model evidence (`evidence`), or both.
  If the overnight review may point at it, add its page to `PAGES`. A source may also have a
  `sort` step (see “Sorting mail”) and a `prepare` step (see “Getting ready each morning”).
- **An action**: one `Action(...)` entry with what it does, how it is undone and its preview page.
  It starts at “suggest”. Raise `ceiling` only when the code has a reversible one-tap route (`undo`):
  a `post` route for Today, or an Ask `Control` that names the action and records each run with Undo.
  `auto` needs an `auto` function as well, and stays a separate owner decision per action.
- **A standing instruction**: typed by the owner in “Ask Hub” on the Engine page, or saved with a tap from
  a note Ask proposed (see “Remember by tap”). No code.
- **A hand-off**: one `Handoff(...)` in `HANDOFFS`, for another assistant Ask Hub may pass the owner's
  message to (see “Hand-offs” under Ask Hub). Adding one is an owner decision: it sends chat text
  somewhere new.
- **Golden scenarios** for anything Ask can newly do or should newly refuse: real requests with checks on
  what Hub would change and say, run offline in the suite and live on demand (`docs/golden-tests.md`).
  A new control or hand-off must also be covered by the dry-run layer (`hub/golden/dryrun.py`); a test fails
  until it is.

## The overnight review

The morning job (`POST /today/prepare`, this Mac only) runs four steps in order: sort
(`sorting.py`), trusted actions (`auto.py`), getting ready (`prep.py`), then the review. “Run” on the
Engine page runs the review alone.

1. Each source with `evidence` hands over a small, bounded summary. The review reads no message
   itself: for Gmail it gets what sorting already worked out (counts, and the sender's name,
   subject and next step of up to 5 emails that need the owner, and up to 3 the owner is waiting
   on). No email address is passed on; Hub keeps it for its own “Find the message” link.
2. One Luna call returns at most 5 suggestions in a strict format.
3. Hub checks the reply, picks every link itself from a fixed list, and saves the suggestions.
   They show in “Needs you” marked “Suggestion”. Nothing else happens.

Limits, enforced in Hub: 2 review calls a day, and $5 of recorded model cost a month across
everything, sorting included (the chat has its own limit; see “Ask Hub”). A failed night or a reached limit shows up as its own item in
“Needs you”.

## Sorting mail

`hub/tools/gmail/triage.py` sorts each new inbox message once, into one of six kinds: needs you,
calendar notice, record, code or sign-in link, reading, promotion or notification. It runs first in
the morning job and on “Sort now” on the Gmail page (`/gmail`, `hub/tools/gmail/inbox.py`).
Sorting changes nothing in Gmail.

- **What it reads**: the last 7 days of inbox mail and 14 days of sent mail, as sender, subject
  line and labels (`/gmail/search`). Mail that needs the owner is followed for 14 days.
- **Fixed rules first**, no model: the owner's own “always” choices, conversations the owner is
  part of, calendar notices, security notices, codes, Gmail's Promotions and Social tabs, and
  subject-line patterns for records, policy notices and newsletters.
- **Jev** reads the text of a message only when its subject suggests it may need the owner, and
  answers yes or no. A “no” falls back to what the rules would otherwise have said.
- **Luna** places what the rules couldn't, from the sender's name and domain, the subject line and
  Gmail's category: never an address, never the text. A second Luna call words a one-line next step
  for mail that needs the owner; a step with a link, an address or a long number is dropped.
  Text given to either model has its links and email addresses taken out first (`judge.scrub`).
- A model's answer never overrules the owner: a message the owner moved or finished while a sort
  was running keeps the owner's choice. If a sort stops unexpectedly, the calls already made are
  still counted against the limits below.
- **Loose ends**: mail that needs the owner and has waited 2 days is flagged. A message the owner
  sent 3 to 12 days ago to one person, with no reply in the thread (`/gmail/thread`), is read once
  by Jev (“does this expect an answer?”) and then watched until someone replies.
- **Records worth a look**: a security notice, or a charge at or above the owner's limit ($1,000
  unless changed on the Gmail page), is shown instead of sitting quietly with the other records.
- **AI off**: a sender the owner switched AI off for is never read for a model or sent to one.
  Their mail stays “Not sorted” unless a fixed rule places it.
- **Limits**, enforced in Hub: 6 Luna calls and 40 message checks a day, under run kind `sort`,
  and nothing once the month's $5 is reached. Without the model key, the rules still sort.
- **What is kept** (`triage_*` tables in Hub's database): sender, sender name, subject line, kind
  and Hub's one-line step. Never message text or the snippet. Names, subjects and steps are
  blanked after 14 days; rows go after 60.
- **Corrections**: the owner can move one message, or say “always” for a sender. That beats every
  rule and the model.

The home list gets `today.gmail_triage`: emails that need the owner, records worth a look, unanswered
questions, an unconfirmed filing change, and one line for what Hub filed (left out of the morning
message). The overnight review gets the same picture as evidence.

### Filing

`hub/tools/gmail/filing.py` takes sorted mail out of the inbox and puts it under a Hub label
(`Hub/Records`, `Hub/Reading`, `Hub/Noise`, `Hub/Expired`, `Hub/Calendar`; each is created the first
time it is needed). One action per kind in the catalog (`gmail.file_*`), each starting at
“Leave in the inbox”. At “File when I tap” the Gmail page has the button; at “File each morning
and tell me” the morning job does it.

- Never filed: mail that needs the owner, starred mail, a record worth a look until the owner says
  it looks fine, unsorted mail, and anything Hub did not see in the inbox on its latest look. Codes
  and calendar notices wait 2 days.
- Through the helper's `/gmail/labels/apply`, at most 50 messages per call and 200 per run. Nothing
  is deleted or marked read. A batch, or one message, can be put back for 14 days; mail put back
  (by Hub or by hand) is left alone afterwards.
- A refusal changes nothing. A result Hub can't confirm stops all filing until the owner taps
  “I checked, carry on”; it is never retried by itself. A pause on Gmail changes stops filing.
  After “I checked”, mail Hub had already filed once (an unconfirmed put-back) counts as filed, so
  it is never filed a second time. Each message is re-checked at the moment it is sent, so one the
  owner moved meanwhile stays in the inbox.

## Owner decisions (2026-11-09)

- Personal data reaches a model only over a zero-data-retention route, with a pinned provider and no
  fallback. This is the default for anything new.
- Exception (2026-11-10): the Kitchen tab relays household chat to the separate kitchen service,
  whose assistant is Claude Code on the owner's Claude plan, because only it can drive the signed-in
  Chrome. Hub itself makes no model call for Kitchen. Only a member's tap on Approve (a signed-in
  device, never a program on this Mac) lets that assistant add to the Fred Meyer cart.
  Since 2026-11-11 Ask Hub can pass a cooking or grocery message to Kitchen too (see “Hand-offs”).
  The message goes word for word; Kitchen's reply is shown on the page, and a short text of it is given
  to Ask's model on later turns, on Luna's zero-retention route.
- Subject lines and message bodies may always be sent over such a route.
- Approved models: Luna (`hub/luna.py`) for proposals and Jev (`preschool/jev.py`) for yes/no
  judgements. Another model needs the owner's yes.
- Senders with AI switched off are never read for a model.
- Agents build and test with made-up data. Real mail stays out of coding sessions.
- Deploying, restarting, new schedules, pushing and merging to main need the owner's yes each time.
- One repo for the engine (this one). Machine setup and public sites stay in their own repos.

## Doing things without asking

An action with an `auto` function in the catalog can run unasked, but only once the owner sets it
to “Just do it and tell me” on the Engine page. Nothing starts there. Trusted actions run with the
morning job (`POST /today/prepare`), before the overnight review, and each leaves a note in
“Needs you” saying what it did and how to undo it. A failure leaves a note too. A pause on Gmail
changes stops them.

The first one (owner decision, 2026-11-09): turning stopped archive rules back on, only those
that provably changed nothing before they stopped.

The second can be switched on but starts at one tap: running the archive-after-30-days rules that
are due (`gmail.run_rules`). Hub's own background jobs are off by the owner's choice, so without it
the rules run only on “Check now”; rules more than a day late show as an item. The morning run
stops starting new rules after two minutes; the rest stay due.

Applying the plan stays a link to the Plan page (`gmail.apply_plan` has no one-tap route). The
review list's “Apply these now” is the owner's tap on the page that shows every change.

## Getting ready each morning

`hub/engine/prep.py` runs each source's `prepare` with the morning job, after the trusted actions
and before the overnight review. It is read-only housekeeping with no model, so pages and items
have nothing to wait for. One that fails is skipped and logged by name.

- Gmail (`hub/tools/gmail/nightly.py`): a fresh inbox sample and the day's inbox count, the next
  12 loud senders with name, counts and a cautious pre-selection, how much inbox mail is over a
  year old, and how much the backlog step would archive (inbox mail over 30 days old, never
  starred). Saved in `gmail-prepared.json` in the data directory: addresses, sender names and
  counts, never subjects. Used for 36 hours, then pages work things out themselves as before.
- Hub itself: the reminder to delete the older Keychain copy of the model key (see “The model key”).

`today.gmail_next` offers at most one Gmail step a day in “Needs you”: sorted mail that is ready
to file, or the choice to let Hub file once 10 sorted emails sit in the inbox, or the backlog
(`/gmail/backlog`: a preview, then one step through the Plan with its own undo; never one tap from
the list). “Not this week” hides that offer for seven days and leaves the Gmail slot empty for the
day. The sender review, bulk clean-ups, rules and filters are still there under “Clean up”
(`/gmail/tools`); they are no longer offered on the home list.

## Calendar

`hub/tools/calendar/` reads the owner's main Google calendar for the next 7 days through the
helper's event list (`/calendar/events`), and nothing else: no single-event details, guest lists
or descriptions, and no route that creates or changes events, although the helper has some.
The page is `/calendar`; overlapping events today or tomorrow become an item; the overnight
review sees titles, times and places of up to 40 events. A lapsed Google sign-in shows as a
problem tile, which is also a “Needs you” item; only the owner can renew it
(`./phone-setup --calendar-auth` in `~/Developer/mac-mini`, from Termius).

## Asking in Hub

The box at the top of the Engine page takes one line. Luna (one small call, at most 20 a day) only
picks the drawer: guidance the overnight review can follow, or something that needs building. The
owner's own words are saved either way. If sorting fails, the line is kept as a build request.

Build requests show under “Asked for, not built yet” and at `GET /engine/requests.json`. They are
the owner's wish list for the next building session. They do nothing by themselves and are not
permission to deploy, restart, schedule or connect anything.

## Ask Hub (the chat)

`/ask` (also Hub's landing page, `/`) is a chat over the catalog: it reads, and hands kitchen requests to Kitchen. Cards and charts hand it a
question through “Ask about this”; see `views.md`. Code: `hub/agent/`; page source: `web/ask/`.

- **What it can do**: only the catalog's lookups (`Lookup` entries on a `Source`): today's list, sorted
  mail, the inbox's size by day, the calendar week, the trip, the network's one-line status and Hub's
  own AI spend. A lookup returns a small bounded dict and changes nothing. Apart from the Kitchen
  hand-off below there is no tool that does anything, so a request to change something gets a plain
  "read-only for now".
  Adding a lookup is one `Lookup(...)` on its source, with `reads` and `sends` in plain words; the chat
  shows those words on the lookup's card.
- **What it can change** (`Control` entries in `CONTROLS`, docs/home-control.md): lights, plugs and
  scenes (`home_control`), the speakers (`play_music`) and playlists (`edit_playlist`), only when the
  owner's latest message asks, only for a signed-in owner, and never when the control's action is set to
  Suggest only. Each change is recorded with Undo for a day. Making or deleting a playlist never happens in
  the chat: the owner taps Make playlist or Delete on the Playlists page. A control can be given the owner's
  message exactly as typed (`said`, from the turn, never from the model) to check that the owner asked:
  making, renaming or deleting a playlist needs the message to ask for one, and "as well" in a play request
  joins the playing room.
- **Remember by tap** (`remember`, `hub/engine/notes.py`): when the owner asks Hub to remember something or
  to do something from now on, Ask proposes one short note (at most 160 characters). Hub refuses it unless
  the owner's typed message asks to remember and most of the note's words come from that message, so text
  from mail, pages or device names can't become a note. It waits a day on the Notes page (`/engine/notes`),
  with the owner's message beside it; only a tap on Save from a signed-in device puts it into the standing
  instructions. A note about speakers playing together keeps them as a zone (docs/home-control.md). Saved
  notes are listed and removed on the Notes page and the Engine page. The chat page shows it as a
  card under the answer (see “Cards in the chat”); the Notes page is the other way to save.
- **Recipes** (`propose_recipe`, `run_recipe`, `hub/engine/recipes.py`): named routines made only of steps
  Ask can already take, each one a `home_control` or `play_music` call with that control's own arguments
  ("great room music": the kitchen speaker joins the living room's, both at 15%, a calm pick). A recipe can't
  add a capability: its steps are checked against the catalog (the tool, its argument types, nothing extra,
  never who asked or the owner's words, and that each device exists) when it is proposed, when it is saved
  and every time it runs. Ask proposes one only when the owner's typed message asks for a recipe or routine,
  under a name taken from that message. It waits a day on the Recipes page (`/engine/recipes`, linked from
  Settings); only a signed-in tap saves it, replacing a recipe of the same name. Saved recipes are named to
  Ask on every turn and run when the owner's message names one: the whole recipe is checked first, then each
  step runs through its control like a typed request (trust level, limits, record and Undo), stopping at the
  first step that isn't done. The Recipes page lists, renames, trims and removes them. Golden scenarios:
  `recipe-is-proposed-not-run`, `recipe-runs-by-name`.
- **Cards in the chat** (`hub/engine/waiting.py`): a control that leaves something waiting for the owner's tap
  (a note, a recipe, a playlist to make, a playlist to delete) puts a small `card` in its answer, and the chat
  page draws it under the answer with its buttons: Save / Not now, Make playlist / Not now, or Delete. The
  buttons post to the same signed-in routes the Notes, Recipes and Playlists pages use, asking for JSON
  (`hub/taps.py`), so the same checks apply: a signed-in device, same origin, and the proposal still
  waiting. A reopened chat asks `/ask/card/<kind>/<id>` what became of each card.
- **Improve this** (`hub/engine/improve.py`): a button under every answer. One tap files a one-paragraph
  brief for the next building session: which tools Hub used on that turn, how each control or hand-off
  answered (in Hub's own words for anything not done), whether the answer said Hub couldn't, and what Ask
  can change. It never holds the owner's message, the answer, mail or anything a lookup returned. Briefs
  go to `improvement-requests.json` in the private data folder (owner-only), where the Manager reads them;
  each shows on Today for a day and on the Engine page until removed. Hub never edits or deploys its own
  code.
- **What the model sees**: what each lookup's `sends` says and no more. Mail is sender names and subject
  lines with links and addresses taken out, one kind at a time, at most 10, never from a sender with AI
  switched off. Today's list passes a mail item's title only, because its second line names senders.
- **The route**: `hub/agent/model.py` builds the model from `luna.URL`, `luna.MODEL` and `luna.PROVIDER`,
  and a guard on its HTTP client refuses any request that differs. It is the only place a chat model is made.
- **Whose conversation**: Hub's. Threads live in `ask_threads` in Hub's database. From the browser the
  server takes the new message only. Links under an answer are the lookups' own Hub pages; links and
  images the model writes are not shown.
- **Limits**, enforced in Hub: run kind `chat` has its own $10 a month, apart from the $5 everything else
  shares; 300 model calls a day as a backstop; 6 model calls per question; the last 12 turns of a thread
  are shown to the model. Each run is recorded with its calls, tokens and cost, never its text.
- **What is kept**: chat text for 30 days. Older turns are cut from a thread, and a thread untouched for
  30 days is removed. The owner can delete a chat at any time.
- **Sign-in**: the same rule as every Hub page.
- **The page** is the one Hub page with a build step (React, assistant-ui, Vite). The built files are
  committed in `hub/static/ask/`; Hub and the deployer need no Node. `tests/test_ask_page.py` fails if
  they do not match `web/ask/`. See `web/ask/README.md`, and `web/ask/check/` for the browser check.

### Hand-offs

Cooking, recipes, groceries, the shopping list and the Fred Meyer cart belong to Kitchen. For those the
model calls the `kitchen` tool (`catalog.HANDOFFS`). The tool takes no arguments: it sends the owner's new
message exactly as typed, with their Tailscale login, to the kitchen service, the same request the
Kitchen tab makes, once per turn. The model gets back only that Kitchen was asked and the message's
number, and replies "On it." (an empty reply would end the turn in an error). The page hides the model's words for that turn: Kitchen's reply is the answer, with no Kitchen label or link, so Ask reads as one assistant.

The page then reads Kitchen's answer straight from `/kitchen/messages`: every Kitchen message after that
one, up to the next member message. It shows the reply, recipe cards, the cart plan with Add to cart and
Not now, and the cart result, and keeps checking while Kitchen works or a plan is waiting. Add to cart
is Kitchen's own route, so it still needs a tap on a signed-in device. The conversation is Kitchen's, so
it also shows in the Kitchen tab.

On each later turn, Hub asks Kitchen again for its answers to the thread's last 3 hand-offs (`recall`)
and gives the model a short plain text of each (at most 1,500 characters): Kitchen's words, recipe
titles, cart plans and changes with their state, and an order's total and time. It goes in that turn's
instructions, marked as data that may come from web pages, so "how long does the first one take?" makes
sense. It is fetched fresh each turn, so a cart result or a placed order shows up as it is now.

Not built yet: approval cards for locks, the garage and the alarm, saving a reply that was cut off mid-stream, notifications.

## Versions and the Deploy button

Hub never installs anything. A fixed deployer in the machine-setup repository
(`home-ops/hub-deployer/hub_release.py`) owns `~/Developer/hub/releases`:

- `stage` (an agent may run it): refuses unless committed `main` is already pushed to GitHub, then builds it, runs the tests against the built package,
  sets the result in `releases/<commit>` and marks it in `pending.json`. Nothing live changes.
- The owner taps **Deploy** in “Needs you” or on the Engine page. Hub writes `queue/request.json`,
  and only for a signed-in owner device, never for a program on this Mac.
- The deployer backs up the data, repoints `releases/current`, restarts Hub, loads its pages and
  goes back to the previous version if any check fails. The result is in `last.json`; a failed
  update shows as its own item.

`hub/engine/release.py` reads those notes. It is off unless `HUB_RELEASES_DIR` is set, which the
deployer does. This is a guardrail against accidents, not a wall: everything runs as one user.

## Faults Hub reports about itself

A night that didn't finish, a morning that never ran (only once mornings have been running), the
monthly limit reached, and an update that didn't take. Each is an item in “Needs you”.

## The model key

`hub/model_key.py` reads the OpenRouter key from 1Password (`op://Hub/OpenRouter/credential`) with a
read-only service account limited to that vault. The service account's token is the one secret
kept in the Keychain (`hub-1password-token`; owner-approved exception, 2026-11-09), set by the
owner with `deploy/set-1password-token`. If 1Password doesn't answer, the older Keychain item
`hub-openrouter` is used until the owner removes it. `/status` says which one is in use.

The older item holds the same key, so nothing needs revoking. From 2026-11-13, while the key comes
from 1Password and the older item still exists (checked without reading it), the morning job leaves
a “Needs you” reminder. The owner deletes it with `deploy/remove-old-model-key`, which first checks
that 1Password answers. An agent never runs that script.

## Next

1. More sources through the catalog, as the owner asks for them in “Ask Hub”.
2. Gmail: a weekly line on how well sorting did (how often the owner moved something); put calendar
   notices next to the event they are about; fewer tabs on a phone.
