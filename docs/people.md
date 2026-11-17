# Who sees what

Each person in the household sees only what Hub read from their own accounts (their Google account's mail and
calendar and what comes of them, and any other account of their own Hub reads); the household's own things (the
weather, the house, Kitchen, the house's needs) are everyone's. `hub/people.py` decides it from the Tailscale
login a request carries:

- Everything read from an account carries that account's name, never a login. `people.sees(login, account)` is
  the one check, and `people.accounts(login)` filters a list. `"default"` is the bridge's main Google account (its
  `/gmail` and `/calendar` reads); the shared mailbox (`/gmail/shared`) is kept apart and is no one's account.
- A source in the engine's catalog says whose account it reads in `account` (`hub/engine/registry.py`): Gmail and
  Calendar read `"default"`. A source without one (the class guide, the trip, the network, the house, Hub itself)
  is the household's.
- `household.json` in the private data folder says who is who:
  `"people": [{"name": "…", "logins": ["…"], "accounts": ["default"]}]`.
- Without `"people"`, the one allowed login (`HUB_ALLOWED_LOGINS`) owns `"default"`. With more than one allowed
  login, nobody sees an account until `"people"` names whose it is.
- No login (this Mac itself), or a login `"people"` doesn't name, sees the household's things only. So does a
  shared screen such as the kitchen wall. A `"people"` that can't be read counts as no one.
- The owner is whoever owns `"default"`: `people.owner(login)`. Everyone else `"people"` names is a member.

## What follows the rule

- **Mail and Mail settings** in the app read only for a login that may see `"default"`. Anyone else gets
  `{"ok": true, "connected": false}` from `/app/api/mail` and `/app/api/mail-settings`, and a calm screen saying their
  mail isn't connected yet; their taps there are refused (403) and change nothing. `/app/api/home` says `"mail"`, and
  System's list of older pages leaves out Gmail and Calendar when it is false.
- **The older pages** of a tool whose `TOOL` names an `"account"` (Gmail, Calendar) open only for that person
  (`auth.account_owner`, mounted in `hub/main.py`); `/tools` lists them only for them. Anyone else, this Mac included,
  gets a plain page titled "Not yours to open", and nothing of the mailbox is read. It is a 200 on purpose: after
  every deploy the deployer loads the Gmail pages on this Mac with no login, as do the live checks, and takes anything
  else for a release to go back from. A change there is a 403.
- **Insights** draws its two inbox charts only for that person.
- **Ask** reads mail and the calendar only for that person; anyone else's model is told they aren't theirs
  (`catalog.NOT_YOURS`), and nothing is read. School is everyone's. Today's list mixes the household's lines with the
  owner's mail and calendar ones, so it goes only to that person until it can be cut to whoever asks.
- **The older chat**: a lookup whose source reads an account (`catalog.account_of`) answers anyone else the same
  `NOT_YOURS` without reading. A lookup that cuts its answer to the asker takes `login` or `accounts`, which Hub fills
  in from the request and hides from the model. A chat is the person's who started it ('' for this Mac): nobody
  else can list, read, continue or delete it. Chats kept from before have no login and are the owner's. Today's list
  counts as the owner's there too (`catalog.MAIN_UNTIL_CUT`), and the 200 kept chats are counted per person.
- **Golden runs** ask as a made-up login but read as the owner (`runner.READS`), so their mail and calendar scenarios
  still read. Starting one (`/golden/run`), and the overnight review (`/engine/prepare`, Engine's Run), is the owner's:
  anyone else gets a 404, as if there were nothing there (`auth.main_owner`).
- **Preschool's personal mailbox** is the owner's Google account (`preschool/mail.py` reads `/gmail/*`): finding
  senders and previewing them, a candidate, its review, a saved record from it, the maintenance page, scanning,
  backfilling, automatic mode and the class group open only for the owner (`OWNERS`; the same "Not yours to open"
  page, a 403 for a change). The class guide, the family preview, `calendar.ics` and the shared mailbox's pages
  (`/preschool/shared/*`) are the household's; a scan or automatic mode started from a shared page reaches the shared
  mailbox only.
- **The Engine**: how far Hub goes with an action on a person's account (filing their mail, `catalog.action_account`)
  is theirs to set; to anyone else the action isn't there. Household actions are anyone's.
- **Errands** keep the account each was read from: see "Errands" in `docs/app.md`.
- `/status` says only whether the Gmail profile read worked, with no address, so it stays open to everyone.

Tests: `tests/test_people.py`, and `tests/test_per_person.py` with two made-up people, one whose mail Hub reads and
one whose it doesn't.
