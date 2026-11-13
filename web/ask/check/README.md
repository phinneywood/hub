# Browser check for the Ask page

Drives the real page in headless Chromium at phone width (390 px, light and dark), on made-up data with
a scripted model. It checks streaming, lookup chips, citations, the read-only refusal, a reload, a note card saved by tap, Improve this, and the
saved-chat list, that sending does not dim the page, that nothing scrolls sideways and that the page
talks to no other host. It saves screenshots of each state.

It is not part of the test suite: it needs Node and Playwright, which Hub's tests and deployer do not.
Run it after changing the page or updating its packages.

1. Start the demo (from the repository root; it uses a throwaway data folder and no key):

       .venv/bin/python web/ask/check/demo_server.py

2. In another shell, with Playwright available to Node:

       node web/ask/check/check.cjs /path/to/an/empty/folder

To use an installed Chrome instead of Playwright's own browser, set `ASK_CHECK_CHROME` to its executable.

It prints `"ok": true` and exits 0 when every check passes. Keep the screenshots out of the repository.
