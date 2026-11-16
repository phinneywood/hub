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

How to run things here:

- Tests: `<shared checkout>/.venv/bin/python -B -m pytest -q -p no:cacheprovider` from this worktree.
- Kitchen tests, in its worktree: `<kitchen checkout>/.venv/bin/python -B -m unittest test_kitchen_service
  test_groceries test_picks test_push test_dev_kitchen test_learn_history`.
- The app: `tools/build-app`, then the demo (`APP_CHECK_PORT=<free port> .venv/bin/python
  web/app/check/demo_server.py`) and `node web/app/check/check.cjs <folder> http://127.0.0.1:<port>` with
  Playwright on `NODE_PATH` and `APP_CHECK_CHROME` set to an installed Chrome.

## For the owner

The short list of what needs the owner (the yes to merge, the two switches in Kitchen, the Groceries reminders
connection, and three choices made on their behalf) is in the project thread, not here.
