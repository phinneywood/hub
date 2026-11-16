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
