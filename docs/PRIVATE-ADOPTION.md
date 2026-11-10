# Adopt one private Hub

This supersedes earlier plans for separate Hub/Preschool services and private URLs.
The candidate combines the two modules in one wheel, keeps the existing loopback
port and Tailscale route, and reuses Hub's existing explicit private data directory,
owner allowlist and bridge-token reference. There is no data or AI opt-out migration.

Before switching, record exact artifact/interpreter and existing launcher. Tell the
owner what changes, the short outage and recovery plan. Stop the old Hub only, keep
its source/interpreter intact, snapshot all Hub SQLite databases and local state
with no writers, verify SQLite integrity and a scratch restore, then start the new
artifact with background jobs disabled. Do not resume School processing/backfill,
organizer writes, model calls or uncertain operations as acceptance tests.

Check process readiness, owner-denial, dashboard navigation, exact frozen school
edition and calendar bytes, scoped asset URLs and existing private HTTPS access.
Do not read mailbox content or actuate devices to test a source deployment.

If acceptance fails, stop the candidate and restore only the retained launcher/code.
Keep current databases and journals; do not restore an old snapshot over newer state.
The original software remains a rollback option, but any incompatible schema change
must stop adoption before it starts. Never restore retired public hosting or jobs.

The public website stays paused. School calendar is an explicitly labelled download,
not a promised cloud subscription. Browser/iPhone appearance is a separate acceptance
step; synthetic tests alone do not establish real-device or live integration health.
