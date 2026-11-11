# Browser check for the three main views

Drives Ask, Today, Insights, “Ask about this”, All tools and Settings in headless Chromium at phone
width (390 px, light and dark) and at 1280 px, on made-up data with a scripted model. It checks the
navigation, the cards, that charts are drawn, that a card's question lands in Ask's box unsent, that
nothing scrolls sideways, that there are no console errors (so nothing breaks Hub's content policy)
and that the pages talk to no other host. It saves a screenshot of each view.

It is not part of the test suite: it needs Node and Playwright, which Hub's tests and deployer do not.

1. Start the demo (from the repository root; it uses a throwaway data folder, no key, and looks in
   no real key store, mailbox, calendar or Home Assistant):

       .venv/bin/python web/views/check/demo_server.py

2. In another shell, with Playwright available to Node:

       node web/views/check/shots.cjs /path/to/an/empty/folder

It prints `All checks passed.` and exits 0. Keep the screenshots out of the repository.
