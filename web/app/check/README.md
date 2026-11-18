# Browser check for the app

Drives the real app in headless Chromium at phone width (390 px, evening and day looks) and laptop width
(1280 px), on a made-up house that follows what it is sent. It checks that:

- Hub's front door leads to the app, which opens on Ask as a conversation: a header naming Hub, Hub's first bubble
  with the day's sentence, what needs you, the house beside it or on a card, a “Message Hub” field with its send
  button at the bottom, a bar of Ask and House, and nothing in the conversation wider than the screen; the plan shows every room floor by floor,
  and the sentences are right;
- a tap shows within 0.4 s while Hub is still answering, holds once the house agrees, and Undo puts it back;
- a tap that didn't take is put right and says why;
- a second open app hears about a change without asking;
- holding a room opens it without switching it; the dimmer, a plug, scenes, All off, keeping the house as a
  scene and running it, play and pause, a room joining the music, a playlist, a quiet room playing music of its own, a card for each music with one volume for its rooms, a room taken out and joining the music it picks, the list in Ask and the look
  all work;
- Ask offers what is often asked around now as a suggested message, does what is typed and says so in a sentence, draws the rooms
  or speakers it changed, puts a change back from under the answer, shows Kitchen's reply when it arrives,
  answers a question about the day from what Hub read, searches the owner's mail and draws the emails found
  under the answer (each opening in Gmail), reads the first when told "open the first one" and marks it, puts
  "archive the first one" on a card naming the email that changes nothing until it is tapped, archives it on the
  tap and puts it back with the card's Undo, and remembers and forgets;
- Ask moves a lamp to another room and puts it back, makes an automation, leaves unlocking a door, removing a
  room and disabling a thing on a card until it is tapped, does nothing when the card is put away, reloads an
  integration, and asks which one when a name fits several lamps;
- Ask adds to the shopping list, draws the product Hub picked under the answer, takes a pick there, reads the cart
  from its own page, and changes a number when a thing is said again;
- on a Hub where Ask isn't set up, what is typed still reaches the older chat;
- the dashboards open, are arranged by each person, and the older Calendar and Insights addresses open the Calendar
  and AI spend boards, and the Trip and School boards lead to the trip workspace and the class guide;
- Today says the right sentence and the weather in words, lists what needs you (a flat lock battery first,
  then the longest wait), shows the evening, tomorrow and its weather drawn, the house's locks, the week at
  school and the trip, shows the note from a stand-in for Ask's model, marks two things at once, takes a tick
  and puts it back, puts a lock's batteries on the shopping list in one tap, and says so when nothing needs you;
- Errands shows what Hub got ready, the most pressing first, with what finished by itself; puts the class guide's
  pumpkin on the shopping list and back with Undo; opens one errand's ways, steps and drafted reply; notes a reply
  opened in the mail app, and closes that errand by itself once the reply is in sent mail; Today shows the three
  most pressing and counts them in its sentence, and Ask draws them under its answer; Ask marks one done when told
  "I paid the power bill", draws it so under the answer and puts it back with Undo; and the other person at home,
  whose mail Hub doesn't read, sees only the class guide's two on Errands and Today, is told on Mail and Mail
  settings that their mail isn't connected yet with none of the owner's shown, isn't offered the older Gmail and
  Calendar pages, finds the older Gmail page saying it isn't theirs, and is told in Ask that the owner's mail
  isn't theirs to search;
- Today, Mail and Kitchen open from Ask by a few letters of their name;
- the wall (the brief on a kitchen iPad, at iPad size) shows the household's needs and only counts the rest,
  keeps mail off, follows the sun from the day look to dusk, never opens the house's live line, keeps the day
  and says so when Hub can't be reached and comes back by itself, and at night is a dim clock and how
  tomorrow starts;
- System opens Versions, which lists the releases and leads back;
- Ask answers "how's the house?" from House health with Open House health under it, plays the Hub drill under its
  answer until it finishes, and leaves "restart Home Assistant" on the caretaker's card, which Not now puts away
  without asking for anything;
- the top button turns amber while the caretaker hands you something; House health shows what needs you with
  Restart Home Assistant where it is asked for, every system with seven days of history, the last seven days, the
  drill Ask ran, a line saying Ask steers the caretaker, one Pause button and no drill controls, and the top button
  going back once the last thing is sorted;
- Mail leads with the emails that need you and the unanswered question, takes a tick and puts it back, and
  files a kind; Kitchen shows the list with each thing's product and where it stands, what needs a pick, a cart
  plan and ideas, opens a thing and sets how many, adds what is typed, takes a pick, fills the cart, reads it,
  asks for a reading when a thing couldn't be confirmed, fills it by itself once switched on, reads checkout
  back for a tap that places the order, shows a recipe's cart plan under the recipe once Kitchen has made it,
  says so under the recipe and offers its button again when Kitchen couldn't answer, and says so when the
  kitchen service is away or an older version;
- everything meant to be tapped is at least 44 px on its short side;
- the app says so when the house stops answering and when Hub can't be reached, still opens without Hub,
  and reconnects by itself;
- nothing scrolls sideways, the typeface loads, and the app talks to no other host.

It saves screenshots of each state and prints how long a tap took to show.

It is not part of the test suite: it needs Node and Playwright, which Hub's tests and deployer do not.
GitHub runs it on every push and pull request (`.github/workflows/browser-check.yml`) and keeps the
screenshots with the run for 14 days. Run it here when you change the app, and add steps for the change.

1. Start the demo (from the repository root; a throwaway data folder, no key, no real house):

       .venv/bin/python web/app/check/demo_server.py

   It listens on `http://127.0.0.1:8912/`. Open that in a browser to try the app by hand.
   Today there shows a made-up day (`tests/day_world.py`) seen from ten past six this evening, with a made-up
   forecast and locks (`tests/app_world.py`). The wall is at `http://127.0.0.1:8912/app/#wall`; to see the
   morning or the night, `curl -X POST -H 'Origin: http://127.0.0.1:8912' http://127.0.0.1:8912/demo/morning`
   (or `night`, or `evening`), then reload with the browser's clock at that hour.
   Ask there runs on a stand-in for its model (`tests/ask_script.py`): a script that knows the made-up rooms
   and a few verbs, such as “dim the kitchen”, “everything off”, “some jazz in the bedroom, softly”,
   “play it in the dining room too”, “pause”, “movie night”, “remember to start music softly”,
   “what should we cook this week?”, “what needs me today?” and “what errands are ready?”. It shows how Ask
   looks and feels, not how well the real model understands. Errands there show a made-up week of mail and two
   class to-dos (`tests/errands_world.py`), read by Hub's real reading and drafting on a stand-in for its model.

2. In another shell, with Playwright available to Node:

       node web/app/check/check.cjs /path/to/an/empty/folder

To use an installed Chrome instead of Playwright's own browser, set `APP_CHECK_CHROME` to its executable.

It prints `"ok": true` and exits 0 when every check passes. Keep the screenshots out of the repository.
The made-up day is set in Pacific time, so on a machine in another time zone run it with `TZ=America/Los_Angeles`.

To try the demo from a phone, set `APP_CHECK_HOST` to this machine's private tailnet address before
starting it. The demo has no sign-in of its own, so anyone who can reach that address can tap the made-up
house; stop it when done.
