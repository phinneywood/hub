"""Errands: what Hub gets ready for the owner to finish with one tap.

Each morning, after the mail is sorted (hub/tools/gmail/triage.py), Hub reads what wants something of the household:
the emails that need the owner, bills and invitations from the last two weeks, and the class guide's to-dos. Luna, on
its pinned zero-retention route, reads each one and drafts the errand: the reply written, the event filled in, the
amount and the page to pay it on, the things to buy. Hub checks the draft against the email itself before keeping
any of it: an amount, a date or a sentence it can't find there is left out, and a link is offered only when it goes
to the sender's own site.

Hub never sends, pays, books or orders anything. A tap on an errand either opens the exact place to finish it, with
everything filled in (a reply in the owner's mail app, an event in Google Calendar, the sender's own page), or, for
the one thing Hub does itself, puts things on the shopping list, with Undo. Hub then watches for proof that it
happened (the reply in sent mail, the event on the calendar, a confirmation from the same sender) and closes the
errand by itself.

links.py builds every address a tap opens; draft.py is what Luna is asked and how its answer is checked; read.py
reads and drafts within its limits; watch.py closes what is done; taps.py is what a tap changes; picture.py is what
the app, Today and Ask see; routes.py is the app's door to all of it. Kept in store.py.
"""
