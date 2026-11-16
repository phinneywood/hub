# Progress note: Ask changes the house's setup, and Kitchen joins Ask

A working note for whoever picks this branch up next. It is deleted before the branch is merged; what stays is
written into `docs/app.md`, `docs/home-control.md` and `docs/engine.md`.

Branches: `ask-house-kitchen` here, and `list-to-cart` in the kitchen service's repository. Nothing is merged,
staged or deployed, and no running service was touched. Tests use the made-up house and a made-up shop only.

## What the owner asked for (2026-11-15)

1. Ask can change anything in Home Assistant: any scene, automation or setting, such as moving a light to another
   room. Changes are recorded on Activity and can be undone; a card asks first before anything is deleted, before
   one request changes many things, and before a door is unlocked or opened.
2. Kitchen is part of Ask, not a second assistant, and is finished: an item added to the household's Groceries
   reminders list lands in the shop's cart as the exact product the household buys, chosen from past orders.

## Design in one page

**House setup** (`hub/tools/house/admin.py` and `plans.py`, fence in `broker.py`, tools in `hub/app/ask/tools.py`):

- A second, wider door in the broker client, used only by `admin.py`: named Home Assistant commands for the
  registries (entities, devices, rooms, floors, labels), scene, automation and script configs, helpers, and service
  calls in a list of domains. Still no tokens, users, backups, add-ons, system shutdown or removing integrations.
- Every change is planned first: what it is in plain words, the exact requests, and the requests that put it back
  (read from Home Assistant before the change). A plan either runs at once or waits on a card for a tap.
- The rule for the card is fixed and in code: deletes; more than ten things in one request; unlock, open and
  disarm; an automation or script that touches a lock, the garage or the alarm; whole-house settings and restart.
- A turn that read mail, the calendar or school changes no setup and unlocks nothing: text written by strangers
  is in the model's view on those turns.
- Records go in `house_setup`; Activity and Changes show them with Undo for a day.

**Kitchen** (service repository, then `hub/app/kitchen.py` and the Kitchen screen):

- The service owns a structured list. Each item gets a product picked in code: the household's usual product for
  it, else a product named for it that was bought in two or more orders (most often, ties to the most recent).
  Bought once, or only mentioning it, is offered and never chosen. No history means the item waits for a pick.
- Items come from Ask, the Kitchen screen and the Groceries reminders list (a read-only source behind one
  interface; a file source stands in for tests and the demo).
- "On the list means in the cart": picked items are added to the cart by the service's own cart turn, at a tap,
  or by themselves once a member switches that on in Kitchen. A practice cart stands in wherever the real shop
  must not be touched.
- Ask gets a `groceries` tool that works the list and cart directly; recipes and questions still go to the kitchen
  assistant, and their cards (recipes, cart plan, order) draw inside Ask. The Kitchen screen becomes the picture
  of the list, the cart and the order, with no second conversation.

## State (updated as each part lands)

- [x] Broker: the setup door and its checks (`hub/tools/house/broker.py`)
- [x] `admin.py` and `plans.py`: reading, planning, running, recording, Undo, cards that wait
- [x] Ask tools for setup and devices; instructions; the house lines (`hub/app/ask/tools.py`, `brain.py`)
- [x] Routes, Activity and Changes rows, Engine trust entries
- [x] App: confirm card, setup lines, Activity's Setup filter
- [x] Kitchen service (its own repository, branch `list-to-cart`): `picks.py`, `groceries.py`, `reminders.py`,
      `drycart.py`, the list's API, the assistant's `list` block, tests
- [x] Hub: `hub/app/kitchen.py`, the Kitchen routes, the `groceries` tool, Today's Kitchen need, a made-up Kitchen
- [x] App: the Kitchen screen, the item sheet, Ask's list card and the assistant's cards drawn in Ask
- [x] Docs (`docs/app.md`, `AGENTS.md`, `docs/home-control.md`), the release entry in `hub/app/versions.json`
- [x] The browser check passing at all three sizes (`web/app/check/check.cjs`), the installed-package check
      (`tools/verify_build.py`), screenshots for the owner, both branches pushed

## Second pass (2026-11-16)

A fresh-reader review, read-only checks against the real Home Assistant (through the maintenance tool: its
version, the registries' shape, `validate_config`, a kept scene and automation), and more of the same work:

- [x] A routine or scene that opens the house through something else waits for a tap too (`plans.opens`): a
      wrapper automation, a script called by name, a scene that holds a lock, a device's own action, a scene made
      on the spot, a blueprint or a routine kept in files. The same however it is run: by name, or by its service.
- [x] Newer Home Assistant has no `has_entity_name` in the registry's list: read from the name itself
      (`admin._world`). A fan and its own light under one name are one device.
- [x] Disable and enable, icons, reloading an integration, Home Assistant's own dashboards.
- [x] Live checks `house.setup` (reads, and a move planned and not sent) and `ask.setup` (the real model behind the
      dry-run layer, which now stands in front of the setup door): `hub/live/checks.py`, `hub/app/ask/trial.py`.
- [x] Kitchen service: the picker follows the habit of the last five purchases and never picks from a line that
      merely holds the words; a number counts single things; messy entries; the cart turn answers by number.
- [x] Kitchen in the app: how many, a pack that covers a number, things to check, why a thing waits, the cart in Ask.

How to run things here:

- Tests: `<shared checkout>/.venv/bin/python -B -m pytest -q -p no:cacheprovider` from this worktree.
- Kitchen tests, in its worktree: `<kitchen checkout>/.venv/bin/python -B -m unittest test_kitchen_service
  test_groceries test_picks test_push test_dev_kitchen test_learn_history`.
- The app: `tools/build-app`, then the demo (`APP_CHECK_PORT=<free port> .venv/bin/python
  web/app/check/demo_server.py`) and `node web/app/check/check.cjs <folder> http://127.0.0.1:<port>` with
  Playwright on `NODE_PATH` and `APP_CHECK_CHROME` set to an installed Chrome.

## Third pass (2026-11-16)

The rule became one: whatever Ask saves, runs or sets that Hub can't read as safe waits for the owner's tap.
The first half (a read as an attacker, commit "what belongs to a lock or the alarm waits for a tap like them")
closed guards beside the locks and the alarm, helpers that an automation unlocks on, routines that lock or arm,
and strangers' words in later requests. A fresh reader, who hadn't seen that review, then found these, each now
closed with a test on the made-up house (`tests/test_house_setup.py`, `tests/test_app_ask_setup.py`):

- [x] A routine that unlocks through the older `service_template`: read like any call. A call worked out at run
      time, an event another routine may act on, a step of a kind Hub doesn't know and a code sent through a
      remote are unreadable, so they wait.
- [x] A device's own action (pressing the garage opener's button): read by its device.
- [x] A helper read by an opening automation in a condition or a template, or by one made from a blueprint, one
      kept in Home Assistant's files, or when Home Assistant doesn't answer; changing a helper's settings (a
      choice taken away): all looked into, and the same check now covers anything the setup door works, lights
      and speakers included.
- [x] A lock, door or garage moved into another room, or a room holding one put in a group or on a floor that
      a routine works by: waits.
- [x] An old-style group, or a group of covers: read by what it holds.
- [x] A scene that doesn't list what it holds (an integration's own): the careful way, unless its integration's
      scenes only set lights (Hue, LIFX, deCONZ, TRÅDFRI).
- [x] A card re-aimed by renaming rooms between the card and the tap: the tap does only the exact requests the
      card showed.
- [x] Undo: putting back what would unlock, open or disarm, or bring back a routine or scene that does, brings
      up the card instead (Ask, Activity and Changes).
- [x] An independent read of these fixes found that Home Assistant's own search only knows entities under
      entity_id, not one read in a template or a time trigger's "at": every automation is now read, in one
      request, Home Assistant's own files too, and the made-up house's search answers as the real one does. Also
      from that read: a scene that switches an automation on or off is read like switching it; switching off what
      watches a way in waits inside a routine too; and homeassistant.* on "all" is more than Hub can read.
- [x] Smaller: a cover that doesn't say it is a blind waits like the garage; a thing made from a template waits;
      a new name for a scene or routine that opens waits; switching off what tells the owner a way in is open
      waits; a routine's card names what opens first; a notice is its title and its message; a model reading a
      thing isn't shown song titles or update notes; brackets that look like the prompt's own are folded.
- Left as it was, on purpose: the song playing still shows in `<house>`, marked as a title from the music
  service; find() still shows states. The everyday doors (lights, lights-only scenes, music) don't yet ask what
  an automation would do when a light or a speaker changes; that is the next step if it is wanted.
- [x] Kitchen: the adversarial picker run, finished and reviewed (peanut butter is not butter), squashed into
      the kitchen service's main.
- [ ] The first-time walk-through.

## For the owner

The short list of what needs the owner (the yes to merge, the two switches in Kitchen, the Groceries reminders
connection, and three choices made on their behalf) is in the project thread, not here.
