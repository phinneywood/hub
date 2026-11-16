# Browser check for the app

Drives the real app in headless Chromium at phone width (390 px, evening and day looks) and laptop width
(1280 px), on a made-up house that follows what it is sent. It checks that:

- Hub's front door leads to the app, the plan shows every room floor by floor, and the sentences are right;
- a tap shows within 0.4 s while Hub is still answering, holds once the house agrees, and Undo puts it back;
- a tap that didn't take is put right and says why;
- a second open app hears about a change without asking;
- holding a room opens it without switching it; the dimmer, a plug, scenes, All off, keeping the house as a
  scene and running it, play and pause, a room joining the music, a playlist, a quiet room playing music of its own, the list in Ask and the look
  all work;
- Ask offers what is often asked around now, does what is typed and says so in a sentence, draws the rooms
  or speakers it changed, puts a change back from under the answer, shows Kitchen's reply when it arrives,
  answers a question about the day from what Hub read, and remembers and forgets;
- Ask moves a lamp to another room and puts it back, makes an automation, leaves unlocking a door, removing a
  room and disabling a thing on a card until it is tapped, does nothing when the card is put away, reloads an
  integration, and asks which one when a name fits several lamps;
- Ask adds to the shopping list, draws the product Hub picked under the answer, takes a pick there, reads the cart
  from its own page, and changes a number when a thing is said again;
- on a Hub where Ask isn't set up, what is typed still reaches the older chat;
- Today says the right sentence, lists what needs you with the longest wait first, shows the evening,
  tomorrow, the week at school and the trip, marks two things at once, takes a tick and puts it back, and
  says so when nothing needs you;
- System opens Versions, which lists the releases and leads back;
- Mail leads with the emails that need you and the unanswered question, takes a tick and puts it back, and
  files a kind; Kitchen shows the list with each thing's product and where it stands, what needs a pick, a cart
  plan and ideas, opens a thing and sets how many, adds what is typed, takes a pick, fills the cart, reads it,
  asks for a reading when a thing couldn't be confirmed, fills it by itself once switched on, reads checkout
  back for a tap that places the order, and says so when the kitchen service is away or an older version;
- everything meant to be tapped is at least 44 px on its short side;
- the app says so when the house stops answering and when Hub can't be reached, still opens without Hub,
  and reconnects by itself;
- nothing scrolls sideways, the typeface loads, and the app talks to no other host.

It saves screenshots of each state and prints how long a tap took to show.

It is not part of the test suite: it needs Node and Playwright, which Hub's tests and deployer do not.
Run it after changing the app or updating its packages.

1. Start the demo (from the repository root; a throwaway data folder, no key, no real house):

       .venv/bin/python web/app/check/demo_server.py

   It listens on `http://127.0.0.1:8912/`. Open that in a browser to try the app by hand.
   Today there shows a made-up day (`tests/day_world.py`) seen from ten past six this evening.
   Ask there runs on a stand-in for its model (`tests/ask_script.py`): a script that knows the made-up rooms
   and a few verbs, such as “dim the kitchen”, “everything off”, “some jazz in the bedroom, softly”,
   “play it in the dining room too”, “pause”, “movie night”, “remember to start music softly”,
   “what should we cook this week?” and “what needs me today?”. It shows how Ask looks and feels, not how
   well the real model understands.

2. In another shell, with Playwright available to Node:

       node web/app/check/check.cjs /path/to/an/empty/folder

To use an installed Chrome instead of Playwright's own browser, set `APP_CHECK_CHROME` to its executable.

It prints `"ok": true` and exits 0 when every check passes. Keep the screenshots out of the repository.

To try the demo from a phone, set `APP_CHECK_HOST` to this machine's private tailnet address before
starting it. The demo has no sign-in of its own, so anyone who can reach that address can tap the made-up
house; stop it when done.
