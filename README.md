# Hub

One private dashboard and one running service: Gmail organizer, Preschool, mobility
and network. Preschool includes the finished class-guide design at `/preschool/`,
with maintenance behind its Manage link. It is not a separately hosted website.

One owner allowlist, loopback listener and private Tailscale origin protect every
tool. School assets live under `/preschool/static` to avoid collisions. The class
catalogs and existing AI opt-outs stay in Hub's explicit private data directory.
No account, credential or data migration is needed for this consolidation.

The package has no Family Site dependency, Vercel publishing, shared website
password or public calendar token. Calendar downloads retain event IDs; cloud
calendar providers cannot fetch private Tailscale addresses. Future portfolio
examples must contain synthetic information only.

Household-specific settings (the school, its web hosts and class, the planner's city
and route anchors) are not in Git. They live in `household.json` in the data directory
(owner-only) and are read at run time through `hub/household.py`. Without that file
the tools still run with neutral wording, and school fetching and city routing stay off.
Tests write a fictional `household.json` (Maple Hill Preschool, Example City). The
morning-brief installer reads its service label prefix from a private `label-prefix`
file in `~/Library/Application Support/Hub`.

Read AGENTS.md and docs/PRIVATE-ADOPTION.md before production changes. Tests use
synthetic data: `python -m pytest`. The source archive and installed wheel contain
both Hub and Preschool and are verified together. Existing background controls,
source-write restrictions, quotas, privacy checks and paused school workers remain.

Design: `hub/static/style.css` holds the shared tokens (colors, tones, radii) and
components (shell, tabs, chips, banners, tiles) every tool uses. Home (`hub/home.py`)
shows one live, read-only tile per tool under a 2.5-second deadline. Gmail samples
500 inbox messages (the bridge maximum), refreshes that overview in the background,
continues the sender queue past the bridge's top 20 by excluding decided senders,
and keeps one local inbox-size snapshot per day (`inbox_history` in `hub.db`, counts
only) for the Today trend.
- **Needs you.** Above the tiles, Home lists up to five things worth acting on (`hub/today.py`): stopped
  30-day rules, planned Gmail changes, class dates today or tomorrow, unchosen trip flights, and any tile
  reporting a fault. Each links to the tool's own preview page; only a provably harmless step gets a
  one-tap button. “Not today” hides an item until tomorrow. `/today/brief` returns the titles as plain
  text for a morning message. No model call and no new access.
- The last inbox overview is saved to `gmail-overview.json` in the data directory (owner-only),
  so a restart shows the previous numbers at once and refreshes them in the background. The
  home Gmail tile says how old its numbers are.
- **Gmail review with Luna.** “Ask Luna” on a sender sends counts, the unread share, how often
  mail arrives and up to 8 recent subject lines (never bodies) through the same zero-retention
  route as custom actions. Luna returns a short read plus up to three options; every option is
  re-validated by `actions.validate` before it is shown, and nothing is planned until the owner
  picks one. Per-sender AI opt-out is honoured. The Review list puts the next 12 senders on one
  page with cautious pre-selected choices.
- **Clean up** plans inbox-wide archives by age or Gmail category, or marks old unread mail read.
  Queries come only from fixed choices (`actions.sweep`), starred mail is never touched, and each
  1,000-message batch has its own undo.
