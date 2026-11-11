# The three main views

Hub's main navigation is **Ask**, **Today** and **Insights**. Every tool keeps its own pages; they are
the detail screens behind these three.

| View | Address | What it is | Code |
| --- | --- | --- | --- |
| Ask | `/` and `/ask` | The read-only chat over the catalog's lookups (see “Ask Hub” in `engine.md`) | `hub/agent/`, `web/ask/` |
| Today | `/today` | One card per decision from “Needs you”, then one card per tool | `hub/cards.py`, `hub/today.py`, `hub/home.py` |
| Insights | `/insights` | Charts from numbers Hub already keeps, and the Network overview | `hub/insights.py`, `hub/tools/network/` |

Behind two icons in the header: **All tools** (`/tools`, every tool's own page) and **Settings**
(`/settings`, which leads to Engine and Status). A tool page marks All tools as the current place;
Engine and Status mark Settings. No address was removed: `/` still answers 200, now with Ask.

## Today's cards

A card adds nothing Hub couldn't already do. `cards.decisions` takes the list exactly as
`today.collect` settled it, so a card has a one-tap button only where the list had one: a catalog
action with a `post` route, at a trust level the owner chose. Otherwise the button is a link to the
tool's preview page. “Not today” is the existing `/today/snooze`. The signed-in-device rule for
Deploy is enforced by its own route, as before.

Each tool has a card built from its home tile, so Kitchen is always one tap from Today.

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

Charts are worked out in `hub/insights.py` as plain numbers and drawn by the template as inline SVG:
no chart library, no inline styles or scripts (Hub's content policy forbids them), nothing from
another site. `hub/static/insights.js` only adds a tooltip; every value it shows is also on the page
as a label or a table.

- **Inbox size**: `inbox_history`, one reading per day Hub checked. A line on a true date axis.
- **What's in the inbox**: the last sort's count of each kind. Bars, one colour.
- **AI spend**: the engine's record of runs (`state.daily`): a meter per monthly limit, cost per day
  stacked by kind of run, and a table of runs, runs that didn't finish, model calls and cost.
- **Network**: the Network overview itself (`network.pages.section`), without its source settings.

Series colours are in `hub/static/insights.css` (`--viz-*`), chosen for both light and dark and
checked for colour-blind separation. A kind of run keeps its colour whatever else is on the chart.

## Checking the views in a browser

`web/views/check/` has a demo server on made-up data and a Playwright script that checks the views
and saves screenshots. See its README.
