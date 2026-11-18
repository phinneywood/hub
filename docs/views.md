# The three main views

Hub's main navigation is **Ask**, **Today** and **Insights**. Every tool keeps its own pages; they are
the detail screens behind these three.

| View | Address | What it is | Code |
| --- | --- | --- | --- |
| Ask | `/` and `/ask` | The read-only chat over the catalog's lookups (see “Ask Hub” in `engine.md`) | `hub/agent/`, `web/ask/` |
| Today | `/today` | One card per decision from “Needs you”, then one card per tool | `hub/cards.py`, `hub/today.py`, `hub/home.py` |
| Insights | `/insights` | Retired 2026-11-18: opens the AI spend board in the app (“Dashboards” in `app.md`) | `hub/app/boards/spend.py` |

With the door set up (docs/door.md) the bar is **Ask**, **Today**, **Home** and **Music**: Home and Music are Home
Assistant and Music Assistant themselves (`/house/assistant`, `/house/player`), and Insights is listed under All tools.

All tools (`/tools`) and Settings (`/settings`) were retired on 2026-11-18 (owner's ask to move every older page
into the app): both open the app's System, which lists the older pages, Home Assistant and Music Assistant, and
“How Hub checks itself” (Status, Checks, Golden tests, Ask activity). `/` still answers 200, now with Ask.

## Today's cards

A card adds nothing Hub couldn't already do. `cards.decisions` takes the list exactly as
`today.collect` settled it, so a card has a one-tap button only where the list had one: a catalog
action with a `post` route, at a trust level the owner chose. Otherwise the button is a link to the
tool's preview page. “Not today” is the existing `/today/snooze`. The signed-in-device rule for
Deploy is enforced by its own route, as before.

Each tool has a card built from its home tile. A tool that already has a decision card above is not
shown a second time; its card returns when the decision is done or waved away. Either way Kitchen is
one tap from Today.

## “Ask about this”

A card or chart links to `/ask?about=<reference>`: `need:<key>` for a list item, `tool:<slug>` or
`insight:<name>` for a card or chart. The page asks `GET /ask/about?ref=…`, and Hub answers with its
own question for that reference and the lookups that can answer it (`cards.TOPICS`, `cards.about`).

- The link carries a reference, never text. A link from outside Hub can only fill the box with one
  of Hub's own questions.
- For a list item the question quotes its title, with links and addresses taken out (`judge.scrub`),
  the same text the `today_list` lookup already gives the model. A mail item's second line names
  senders and is never used.
- The question waits in the box. Nothing is sent, and no model is called, until the owner sends it.
- A card only shows “Ask about this” when a lookup stands behind its topic. Kitchen, Preschool and
  Bike + bus have none today.

## Insights charts

Retired 2026-11-18 (owner's ask to move every older page into the app). `/insights` opens the AI spend board;
the inbox charts were already on the Mail board and the network summary on the Network board. `hub/insights.py`
keeps only the spend figures the board and Ask's `ai_spend` lookup read.

## Checking the views in a browser

`web/views/check/` has a demo server on made-up data and a Playwright script that checks the views
and saves screenshots. See its README.
