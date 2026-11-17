---
name: verify
description: Check a Hub change before and after pushing it. Use before every commit you push and before saying a branch is ready to merge.
---

# Verify a Hub change

GitHub runs the full checks on every push and pull request, so a branch is verified by its checks on GitHub, on
the exact commit, not by runs on this machine. Locally, run only what the change touches.

## Before pushing

1. Tests near the change: `.venv/bin/python -m pytest -q tests/<the files for what you changed>`.
   Don't run the whole suite here: CI does that on the installed package.
2. If `web/app` changed: `tools/build-app` (or `npm ci --ignore-scripts && npm run build` in `web/app` with Node 24),
   and commit the built files with the source.
3. If a screen or a flow someone uses changed, add steps for it to `web/app/check/check.cjs` in the same change:
   what the person does and what must be true afterwards, as the steps already there do. Check it as each person
   who sees something different (the demo has the owner and the other person at home).
   Run it once: `.venv/bin/python web/app/check/demo_server.py` in one shell, then
   `TZ=America/Los_Angeles node web/app/check/check.cjs <empty folder>` in another (about 5 minutes).
   Start a fresh demo for each run: its made-up evening is set when it starts, and an older one fails Activity.
4. `python -I tools/privacy_scan.py --all --no-denylist`; the commit hooks run the full scan.

## After pushing

Read both checks for the head commit: "Verify installed package" (the full test suite on the built wheel) and
"Browser check" (the app end to end, with its screenshots kept as the run's artifact for 14 days):

    gh api repos/phinneywood/hub/commits/<sha>/check-runs --jq '.check_runs[] | "\(.name) \(.conclusion)"'

A branch is ready when both are green on its head commit. A rebase makes a new commit, so read them again then
rather than rerunning anything by hand. Link the run and its screenshots in the pull request instead of attaching
screenshots of your own.

## When a check fails

- Fix the cause. Never rerun a failing test to get green, add a sleep, skip it or retry it in a loop.
- A test that fails only sometimes is a bug in the test or the code: make it deterministic. If the cause can't be
  found, delete the test and say why in the commit message.
