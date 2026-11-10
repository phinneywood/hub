"""Versioned, owner-agreed preschool inclusion policy.

School, class and neighborhood names are `{SHORT}`-style tokens filled from the private
household settings when a rule is read (see `preschool.school`).
"""
from . import school

VERSION = '1.4'
_TITLE = 'What belongs in the {SHORT} class handbook'

DISCOVERY = (
    'Possible messages are found by searching for the preschool name or acronym, the word “tuition” as a possible '
    'class-bill hint, and mail addressed to the family’s saved class mailing list. These are search clues only; a match is not '
    'proof that the message belongs in the handbook.'
)
_INCLUDE = (
    'Keep the practical picture a family needs for this specific class: class/teacher updates; schedules, closures '
    'and events; what to bring or prepare; parent meetings and participation duties; tasks, forms and deadlines; '
    'and {SHORT}-specific resources.',
    'Include class/co-op financial obligations as To do reminders: tuition, class fees or contributions, with the '
    'due date and stated amount when provided. Preserve only details needed to know what is due and when; never '
    'infer an amount, assume a payment is outstanding, or make/facilitate a payment.',
    'Messages addressed to the configured class group qualify even without the {SHORT} acronym when their current '
    'content is clearly about that class or its families.',
    'For mixed newsletters from the {ORG} ({ORG_SHORT}) or {NEWS} local-news blog, keep only a directly '
    '{SHORT}-specific class item; ignore unrelated neighborhood articles, events, promotions and general {ORG_SHORT} material.',
)
_EXCLUDE = (
    'A stale {SHORT} mention found only in quoted history or a signature, with no useful current {SHORT} information.',
    '{NEWS} blog/news items, generic neighborhood announcements, promotions, unrelated tuition/billing '
    'messages, automated notices or personal conversation without a concrete {SHORT} class-useful item. A sender or '
    'class-group match by itself is not enough.',
    'Do not infer an event time, date, assignment or audience that the source does not provide. Preserve TBD '
    'and flag ambiguity for review.',
)
PRIVACY = (
    'Omit children’s and other families’ names unless a specific assignment cannot be understood without the name. '
    'Prefer a short source quote that does not contain a child’s name.',
    'For class bills, retain only the class obligation, explicitly stated amount and due date needed for the reminder. '
    'Never retain bank/card credentials or unrelated personal billing details.',
    'Editing a handbook entry changes only that entry. It does not change the inclusion rules. Rule changes are '
    'versioned and tested separately.',
)
_EVIDENCE = (
    'A saved summary must quote its own supporting source. Numeric currency amounts and concrete dates are '
    'checked against that quote, not an unrelated fact elsewhere in the email. These checks do not verify every '
    'paraphrase or assignment. Unsupported formats or unresolved changes require source review.',
    'A yearless date can use the email’s sent year only within six months. Weekday-only dates, ambiguous '
    'year rollovers, cancellations and rescheduled events require review rather than a guessed calendar date.',
    'The reader can safely inspect a small number of PDF, plain-text and calendar attachments. Other file types, or '
    'files that cannot be read reliably, remain linked to the email and need direct review.',
    'Only public pages from the approved {SHORT}, {ORG_SHORT}, {NEWS} and Google Docs/Sheets/Drive sources may be read. '
    'The reader does not sign in, submit forms, RSVP, pay, unsubscribe or follow links outside that list. Private or '
    'unavailable sources stay linked to the original email and require direct review.',
)


def __getattr__(name):
    if name in ('TITLE', 'INCLUDE', 'EXCLUDE', 'EVIDENCE'):
        return school.fill(globals()['_' + name])
    raise AttributeError(name)


def current_text(text):
    """Remove recognizable quoted history so it cannot alone create a candidate decision."""
    import re
    match = re.search(r'(?im)^\s*(?:On .{0,250}wrote:|[- ]{2,}Original Message[- ]{2,}|[- ]{2,}Forwarded message[- ]{2,}|>\s)', text or '')
    return (text or '')[:match.start()] if match else (text or '')
