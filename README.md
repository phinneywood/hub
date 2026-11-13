# Hub

Hub is one private, self-hosted dashboard for a household. It runs on a Mac mini at home,
listens only on that machine's loopback address, and is reached only over Tailscale by the
owner's signed-in devices. It is never hosted publicly; this repository is its source.

Hub has three main views. **Ask** is where it opens: a read-only chat over Hub's own lookups.
**Today** has a card for each thing worth acting on ("Needs you", up to five), each linking to a
preview page, and a card per tool. **Insights** charts what Hub already counts: inbox size, sorted
mail, model spend against its limits, and the home network. Behind them are the tools:

| Tool | What it does |
| --- | --- |
| Gmail | Sorts the inbox by kind, plans archives, labels and cleanups, each with a preview and an undo |
| Calendar | The next seven days |
| Preschool | A class guide built from school mail and pages: news, dates, parent duties and resources |
| Bike + bus | Trips that combine a bike or scooter with buses, with honest time comparisons |
| Network | Live home network status, traffic history and clients, from Home Assistant |
| Trip | Plans, decisions and travel reference for one trip |
| Kitchen | Shared grocery list, recipes and a grocery cart, through a separate kitchen assistant |
| Ask Hub | A read-only chat that answers from Hub's own lookups and cites the Hub page each answer came from |

## How it protects privacy

- **Household details stay out of Git.** The school, its web hosts and class, the planner's city
  and similar facts live in an owner-only `household.json` in the data directory, read at run time
  (`hub/household.py`). Without it, Hub runs with neutral wording and anything that needs a real
  value (fetching school pages, city routing) stays off.
- **A privacy check runs on every commit.** `githooks/` runs `tools/privacy_scan.py` on staged
  lines, file names and the commit message, using the owner's private word list kept outside the
  repository. CI runs the same scanner's generic patterns (emails, home paths, phone numbers,
  private hostnames, common secret formats) over every file.
- **Personal data reaches a model only over pinned zero-data-retention routes.** Each model call
  names one provider, with zero data retention, data collection denied and no fallbacks
  (`hub/luna.py`, `preschool/jev.py`). Ask Hub's client refuses any request that is not on that
  route (`hub/agent/model.py`). The one documented exception is Kitchen chat, which the owner
  approved: it goes to the separate kitchen service.
- **Only the owner gets in.** Hub listens on loopback, so another device can reach it only through
  Tailscale Serve, which names the signed-in user. Only allowed logins pass (`hub/auth.py`).
- **Nothing changes without the owner.** Mail is read and organized through a separate local
  bridge with fixed routes; it cannot send, draft, delete or forward. Each action has a preview,
  an undo and a trust level. It runs on the owner's tap, or by itself only when the owner has set
  that action to "Just do it and tell me". No action starts there (`hub/engine/`).
- **Releases are checked and reversible.** A separate deployer, not in this repository, stages
  only a `main` that is already pushed to GitHub and runs the tests against the built package.
  Going live is the owner's tap on Deploy: it backs up and checks the data, switches versions,
  loads the pages, and puts the previous version back if anything fails.

## How it fits together

```
 owner's phone or laptop
        │  Tailscale (allowed logins only)
        ▼
 tailscale serve ──▶ Hub: FastAPI on 127.0.0.1 (hub/main.py)
                       ├─ Today: list, tool cards     hub/today.py, hub/home.py, hub/cards.py
                       ├─ Insights: charts             hub/insights.py
                       ├─ tools                        hub/tools/*, preschool/
                       ├─ engine: sources → judgement → Needs you → actions   hub/engine/
                       ├─ Ask Hub (read-only lookups)  hub/agent/
                       └─ data directory (owner-only): SQLite files, household.json
        │
        ├─▶ local mail and calendar bridge (fixed routes; no send, draft or delete)
        ├─▶ Home Assistant (network) · Google Routes (bike + bus)
        ├─▶ model calls on pinned zero-data-retention routes only
        └─▶ local kitchen service (Kitchen chat)
```

The engine is documented in `docs/engine.md`. Rules for anyone changing the code, people or
agents, are in `AGENTS.md`.

## Run the tests

Every test uses fictional people, places and mail (Alex and Sam, Maple Hill Preschool,
Example City) and mocked services. Nothing reaches a real account. Python 3.12:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pytest
git config core.hooksPath githooks   # turn on the commit-time privacy check
```

To see a change running on real data before it is deployed, the owner runs a dev Hub from this
checkout: `deploy/dev-hub setup`, then `deploy/dev-hub start` (see docs/dev-hub.md).

Golden tests (real requests through Ask, with a dry run for every change) run offline in the suite;
live runs are on demand from Settings → Golden tests (see docs/golden-tests.md).

## Rebuild the Ask chat page

The chat page (`web/ask`, React) is the only part with a build step, and its built files are committed in
`hub/static/ask/`, so the running Hub and the deployer never need Node. To change it, on the Mac mini:

```sh
tools/build-ask-page
```

The first run downloads the official Node release pinned in `.node-version` (24.22.2, the same as the
personal site) into `.tools/` inside this checkout, which Git ignores, and checks its SHA-256. Then it
installs the locked packages and builds. Commit `web/ask/src`, `package-lock.json` and the three files in
`hub/static/ask/` together; `tests/test_ask_page.py` fails if the bundle and the source don't match. The
browser check is in `web/ask/check/README.md`.

## More detail

Preschool includes the class-guide design at `/preschool/`, with maintenance behind its Manage
link; its assets live under `/preschool/static`. The class catalogs and AI opt-outs stay in the
private data directory. The morning-brief installer reads its service label prefix from a
private `label-prefix` file. Read AGENTS.md and docs/PRIVATE-ADOPTION.md before production
changes. The source archive and installed wheel contain both Hub and Preschool and are verified
together. Background controls, source-write restrictions, quotas, privacy checks and paused
school workers remain.

Design: `hub/static/style.css` holds the shared tokens (colors, tones, radii) and
components (shell, tabs, chips, banners, tiles) every tool uses. Today (`hub/home.py`, `hub/cards.py`)
shows one live, read-only card per tool under a 2.5-second deadline; `docs/views.md` describes the
three main views. Gmail samples
500 inbox messages (the bridge maximum), refreshes that overview in the background,
continues the sender queue past the bridge's top 20 by excluding decided senders,
and keeps one local inbox-size snapshot per day (`inbox_history` in `hub.db`, counts
only) for the inbox chart on Insights.
- **Needs you.** Above the tool cards, Today lists up to five things worth acting on (`hub/today.py`): stopped
  30-day rules, planned Gmail changes, class dates today or tomorrow, unchosen trip flights, and any tile
  reporting a fault. Each links to the tool's own preview page; only a provably harmless step gets a
  one-tap button. “Not today” hides an item until tomorrow. `/today/brief` returns the titles as plain
  text for a morning message. No model call and no new access.
- The last inbox overview is saved to `gmail-overview.json` in the data directory (owner-only),
  so a restart shows the previous numbers at once and refreshes them in the background. The
  Gmail card on Today says how old its numbers are.
- **Gmail review with Luna.** “Ask Luna” on a sender sends counts, the unread share, how often
  mail arrives and up to 8 recent subject lines (never bodies) through the same zero-retention
  route as custom actions. Luna returns a short read plus up to three options; every option is
  re-validated by `actions.validate` before it is shown, and nothing is planned until the owner
  picks one. Per-sender AI opt-out is honoured. The Review list puts the next 12 senders on one
  page with cautious pre-selected choices.
- **Clean up** plans inbox-wide archives by age or Gmail category, or marks old unread mail read.
  Queries come only from fixed choices (`actions.sweep`), starred mail is never touched, and each
  1,000-message batch has its own undo.
