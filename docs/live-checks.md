# Live checks

Status: built (2026-11-14), off until the owner switches the daily run on. Code: `hub/live/`. Page: Settings → Checks (`/checks`).

## The idea

Unit tests and offline golden tests use a made-up household, so they can't see the real system break: a key that
ran out, a Music Assistant update that changed its addresses, a helper that stopped answering. A live check asks
the **real running system** one question and answers in one sentence. Every answer is kept, so a failure can be
dated to the day and the Hub version it started in.

The first check written from a real failure is `music.search`: on 2026-11-14 Hub refused to play every Apple Music
item it had just found. No test on made-up data could see it. This check searches the real Music Assistant and
fails if Hub would refuse what comes back.

## The checks

`python -m hub.live list` prints them. Each has one effect, and the runner only runs the effects it was allowed.

| Check | Effect | Passes when |
| --- | --- | --- |
| `hub.pages` | read | every main page and tool page of the running Hub answers, none slower than 8 s |
| `house.read` | read | Home Assistant answers through the broker with its devices and rooms |
| `house.speakers` | read | Home Assistant lists speakers and at least one is reachable |
| `music.search` | read | Music Assistant finds music, and every item found has an address Hub would play |
| `music.key` | read | Hub's Music Assistant key is accepted and has 30 days or more left |
| `mail.read` | read | the mail helper answers, Gmail is signed in, and the helper still reports it can't send |
| `calendar.read` | read | the calendar week can be read |
| `kitchen.answers` | read | the Kitchen service answers |
| `house.light` | undo | one named light switches, and is put straight back |
| `model.route` | spend | one call with invented data comes back over the pinned private route, in the right form |
| `ask.golden` | spend | a few golden scenarios pass through the real Ask with the real model, as a dry run |

- **read** looks and changes nothing.
- **spend** makes model calls: `model.route` a fraction of a cent (recorded as run kind `checks`), `ask.golden` at
  most $0.10 a run inside the golden tests' own monthly limit (docs/golden-tests.md). Nothing reaches the house.
- **undo** is the only check that changes anything. It switches the one light the owner named, through
  `control.change` (the same allowlist, limit, record and Undo as a typed request), and undoes it at once. Home
  changes shows both rows as a test's (`via` = `check`). It never runs from 9 PM to 8 AM, never from the page's
  button, and not at all until the owner switches it on. No check ever plays anything on a speaker.

A sentence a check returns holds counts and times only: no names, mail or song titles. A check that breaks is
recorded by its exception type, never its message.

## History

`live-checks.db` in Hub's private data folder (owner-only), kept 180 days: one row per run (when, what started
it, the Hub version running) and one per check (pass, fail or skip, how long, one sentence).

- The Checks page shows each check with its last 30 results as dots, and for a failing one: since when and which
  version, and when and on which version it last passed.
- A check that fails now is one item on Today ("2 live checks failing") until it passes again. A skipped result
  never hides a failure.
- `python -m hub.live failing` prints the same from the shell.

## When they run

| How | What runs | Who switches it on |
| --- | --- | --- |
| Run on the Checks page | read and spend | any signed-in tap |
| With the morning job | read and spend, and undo if switched on | the owner, in the private settings |
| `python -m hub.live run` | read and spend; `--no-spend`, `--with-undo`, `--only`, `--skip` | whoever runs it |
| The deployer, before and after a deploy | `run --gate` exits 1 on any failure | the owner (a change in home-ops; not built here) |

The morning run adds no schedule of its own: the existing morning job starts it, in a process of its own.

Private settings (`household.json` in the data folder; nothing here is in Git):

```json
"checks": {
  "daily": true,
  "undo": true,
  "undo_light": "light.some_lamp"
}
```

`daily` switches the morning run on. `undo` and `undo_light` together switch the put-back check on; leave them out
and nothing in the house is ever changed for a check.

From the shell (Termius on the Mac mini, with the live Hub's environment):

```sh
HUB_DATA_DIR=<the live Hub's data folder> python -m hub.live run --no-spend
```

Before a new version is live its pages aren't being served yet, so a deployer gates with
`run --trigger predeploy --gate --skip hub.pages`, and after the switch with `run --trigger deploy --gate`.

## Adding a check

1. Write a function in `hub/live/checks.py` that asks one question through the connector Hub already uses. Return
   one sentence; raise `Failed` with the reason, or `Skipped` when the connection isn't set up.
2. Add a `Check(...)` to `CHECKS` with its effect. If it comes from a real failure, say so in `seed`.
3. In `tests/test_live.py`, show it passing on the made-up household and failing for the reason it exists.

A new connection or a new kind of thing Hub can do gets a live check, as it gets golden scenarios.
