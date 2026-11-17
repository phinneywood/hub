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

Where it is applied so far (the family brief, 2026-11-16):

- Today's list (`hub/today.py`): every item carries its source's account; `gather`, `collect` and `needs_you` take
  the asker's `accounts` and leave out anyone else's. A home tile counts as its source's (Gmail's and the
  calendar's are `"default"`'s). The overnight review's suggestions, Hub's notes on what it did by itself and Hub's
  own upkeep (its runs, a new version and Deploy) are the owner's: the review reads the owner's mail. A key that
  arrives without its item (a tap that hides or ticks it) is placed by `today.account_of(key)`.
- The app's Today and `/app/api/brief`, the older `/today` page and its cards, and "Ask about this" on a list item
  are the asker's. The kitchen wall (`?wall=1`) is no one's: the household's things only.
- One exception: `/today/brief` without a login, which the morning job on this Mac reads to send the owner's
  phone its count, is the owner's list (`"default"`'s). Through Tailscale it is the asker's.
- Still to come (a separate change): Mail and Mail settings in the app, the older Gmail, Calendar and Insights
  pages, and Ask's mail and calendar lookups.

**Before a version with this goes live**, a Hub that allows more than one login needs `"people"` in
`household.json`, or nobody sees the owner's mail and calendar on Today (each such login is told why in a quiet
line). `household.json` is read on every request, so adding it takes effect at once, with no restart.
