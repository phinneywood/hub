"""The pregnancy dashboard: where the pregnancy is, what usually comes next and what to get ready, from the due date.

Everything here is worked out from one date, the due date, the way a clinic counts: 280 days from the first day of
the last period, so week 1 starts 280 days before the due date. The due date is the household's, never in Git: it
is read from the private settings file,

    "pregnancy": {"due": "YYYY-MM-DD"}

and can be changed on the dashboard itself (after a dating scan, say), which keeps the new one in Hub's database.
Until a dating ultrasound sets it, a due date is an estimate (from the last period or from ovulation), and the page
says so; the dashboard's "Dated by ultrasound" switch (or `"scan": true` in the settings file) marks it as the one
to keep.

What it says about tests and visits is the usual schedule in the US (ACOG and CDC guidance), written as what is
usual, not as advice: the household's own doctor or midwife decides. It is for the household's members (the
people `hub/people.py` names), on their own phones. It is never on the kitchen wall, where guests can read it.
"""
from datetime import date, timedelta

from ... import household, people
from . import Refused, setting, ticks

ID = 'pregnancy'
NAME = 'Pregnancy'
WHAT = 'The week, what usually comes next and what to get ready'
TERM = 280                          # days from week 1, day 1 to the due date
T2, T3 = 14 * 7, 28 * 7             # the second and third trimesters start at 14w0d and 28w0d

# About the size of, by week (the usual comparisons; a picture of size, not a measurement).
SIZES = {4: 'a poppy seed', 5: 'a sesame seed', 6: 'a lentil', 7: 'a blueberry', 8: 'a raspberry', 9: 'a cherry', 10: 'a strawberry',
         11: 'a fig', 12: 'a lime', 13: 'a lemon', 14: 'a peach', 15: 'an apple', 16: 'an avocado', 17: 'a pear', 18: 'a bell pepper',
         19: 'a mango', 20: 'a banana', 21: 'a carrot', 22: 'a papaya', 23: 'a grapefruit', 24: 'an ear of corn', 25: 'a cauliflower',
         26: 'a head of lettuce', 27: 'a cabbage', 28: 'an eggplant', 29: 'a butternut squash', 30: 'a cucumber', 31: 'a coconut',
         32: 'a squash', 33: 'a pineapple', 34: 'a cantaloupe', 35: 'a honeydew', 36: 'a romaine lettuce', 37: 'a bunch of chard',
         38: 'a leek', 39: 'a mini watermelon', 40: 'a small pumpkin'}

# What usually happens around this week, in a sentence (general, from the usual week-by-week guides).
WEEKS = [(4, 'Implantation is done and the pregnancy hormone is rising: a home test can turn positive around now.'),
         (5, 'The neural tube, which becomes the brain and spine, is forming. Folic acid matters most in these weeks.'),
         (6, 'A heartbeat can often be seen on an early ultrasound from about now.'),
         (8, 'Arms, legs, fingers and toes are forming. Nausea is often at its strongest in weeks 8 to 10.'),
         (10, 'The embryo becomes a fetus: the major organs are in place and now grow and mature.'),
         (12, 'Reflexes begin. For many, nausea starts easing toward the end of the first trimester.'),
         (14, 'The second trimester: energy often comes back, and the risk of miscarriage is much lower.'),
         (16, 'The baby can make facial expressions. Some feel first movements from about 16 to 22 weeks.'),
         (18, 'Hearing is developing: the baby may start to hear voices and the heartbeat.'),
         (20, 'Halfway. The anatomy scan looks at the baby’s organs and growth.'),
         (22, 'Movements get stronger and more regular. The partner may feel kicks from outside.'),
         (24, 'Lungs are developing quickly. This is the week usually counted as viability.'),
         (27, 'The last week of the second trimester. Eyes can open and close.'),
         (28, 'The third trimester. Visits usually come every two weeks from here.'),
         (30, 'The baby is gaining about half a pound a week. Sleep can get harder.'),
         (32, 'Most babies have turned head down or will soon. Practice contractions are common.'),
         (34, 'Lungs and the nervous system are nearly mature.'),
         (36, 'Visits are weekly now. The group B strep test is usually in weeks 36 to 37.'),
         (37, 'Early term. The baby is practising breathing and gaining fat.'),
         (39, 'Full term.'),
         (40, 'The due date week. Only about one in twenty babies arrive on the day itself.'),
         (41, 'Past the due date: the doctor or midwife usually checks in more often now.')]

# The usual schedule: (first day, last day, key, title, what). Days counted from week 1, day 1.
MILESTONES = [
    (8 * 7, 11 * 7 - 1, 'first-visit', 'First prenatal visit', 'Usually weeks 8 to 10, often with a dating scan.'),
    (10 * 7, 14 * 7 - 1, 'nipt', 'Cell-free DNA screening (NIPT)', 'An optional blood test, available from week 10.'),
    (11 * 7, 14 * 7 - 1, 'nt-scan', 'First-trimester screening', 'The nuchal translucency scan, weeks 11 to 13.'),
    (T2, T2, 'second-trimester', 'Second trimester begins', ''),
    (15 * 7, 23 * 7 - 1, 'quad', 'Quad screen', 'An optional blood test, weeks 15 to 22, if no NIPT.'),
    (16 * 7, 23 * 7 - 1, 'movement', 'First movements', 'Often felt between weeks 16 and 22; later in a first pregnancy.'),
    (18 * 7, 23 * 7 - 1, 'anatomy', 'Anatomy scan', 'The detailed ultrasound, weeks 18 to 22.'),
    (24 * 7, 24 * 7, 'viability', 'Viability milestone', 'Week 24.'),
    (24 * 7, 29 * 7 - 1, 'glucose', 'Glucose screening', 'The gestational diabetes test, weeks 24 to 28.'),
    (27 * 7, 37 * 7 - 1, 'tdap', 'Whooping cough vaccine (Tdap)', 'Recommended in every pregnancy, weeks 27 to 36, ideally early in that window.'),
    (T3, T3, 'third-trimester', 'Third trimester begins', 'Visits usually every two weeks from here.'),
    (28 * 7, 28 * 7 + 6, 'rh', 'Rh shot, if needed', 'Only for an Rh-negative mother, around week 28.'),
    (36 * 7, 38 * 7 - 1, 'gbs', 'Group B strep test', 'A swab, weeks 36 to 37.'),
    (36 * 7, 36 * 7, 'weekly', 'Weekly visits begin', 'Week 36 to the birth.'),
    (37 * 7, 37 * 7, 'early-term', 'Early term', 'Week 37.'),
    (39 * 7, 39 * 7, 'full-term', 'Full term', 'Week 39.'),
    (TERM, TERM, 'due', 'Due date', ''),
]

# The chance a pregnancy still going at a given week ends in a live birth, on average: (completed weeks, chance).
# Population averages, read from published studies, never a prediction for one pregnancy:
#   week 4 (a positive test): about a quarter of pregnancies found by a test end early (Wilcox et al., NEJM 1988);
#   week 5: miscarriage risk from week 5 to 20 is 11 to 22 in 100 (Ammon Avalos et al., Birth Defects Res A 2012);
#   weeks 6 to 10, after a normal visit with a heartbeat seen: 9.4, 4.2, 1.5, 0.5 and 0.7 in 100 (Tong et al.,
#   Obstet Gynecol 2008); from week 20, stillbirth is about 6 in 1,000, and about 3 in 1,000 from week 28 (CDC, NCHS
#   fetal death data). Weeks between two of these are drawn as a straight line.
LIVE = [(4, .75), (5, .83), (6, .90), (7, .952), (8, .979), (9, .989), (10, .989), (12, .991), (14, .992), (20, .994),
        (28, .997), (37, .998), (40, .999)]
LIVE_SOURCES = ('Wilcox et al., N Engl J Med 1988; Ammon Avalos et al., Birth Defects Res A 2012; '
                'Tong et al., Obstet Gynecol 2008; CDC/NCHS fetal death statistics')

# When babies are born, single births in the US in 2023 (CDC, NCHS Health E-Stat 104): (first day, last day, share).
# Days counted from week 1, day 1, so 259 is 37 weeks 0 days and the due date is 280.
BORN = [(None, 258, 8.71, 'Before 37 weeks', 'Preterm'), (259, 265, 12.05, 'At 37 weeks', 'Early term'),
        (266, 272, 17.58, 'At 38 weeks', 'Early term'), (273, 286, 56.68, 'At 39 or 40 weeks', 'Full term, around the due date'),
        (287, None, 4.97, 'At 41 weeks or later', 'Late term')]
BORN_SOURCE = 'CDC, NCHS Health E-Stat 104 (US single births, 2023)'
SPREAD = 9.5        # days: labour that starts by itself, spread around the due date (about 4 in 100 on the day itself,
                    # about 70 in 100 within ten days of it; Jukic et al., Hum Reprod 2013, and ESHRE's summary)


def live_chance(day):
    """The average chance of a live birth for a pregnancy still going `day` days from week 1, day 1."""
    weeks = max(day, 0) / 7
    if weeks <= LIVE[0][0]:
        return LIVE[0][1]
    for (w1, p1), (w2, p2) in zip(LIVE, LIVE[1:]):
        if weeks <= w2:
            return p1 + (p2 - p1) * (weeks - w1) / (w2 - w1)
    return LIVE[-1][1]


def in_words(chance):
    """A chance the way a person says it: about 90 in 100, more than 99 in 100."""
    if chance >= .995:
        return 'More than 99 in 100'
    return f'About {int(chance * 100 + .5)} in 100'


def _odds_curve(day):
    """The live-birth chance from week 4 to 40, as points in a 720 by 180 box from 70% (bottom) to 100% (top)."""
    point = lambda w: f'{2 + 716 * (w - 4) / 36:.1f},{170 - 160 * (live_chance(w * 7) - .7) / .3:.1f}'
    marks = [w / 2 for w in range(8, 81)]
    return [' '.join(point(w) for w in marks)], min(max((max(day, 0) / 7 - 4) / 36, 0), 1)


def _births(start, today):
    """When babies are born, as rows of dates: the share each stretch has and the share born by its end."""
    rows, by = [], 0.0
    for first, last, share, title, what in BORN:
        by += share
        a = start + timedelta(days=first) if first is not None else None
        b = start + timedelta(days=last) if last is not None else None
        when = (f'Before {a_short(start + timedelta(days=259))}' if a is None else f'{a_short(a)} or later' if b is None
                else f'{a_short(a)} to {a_short(b)}')
        now = (a is None or a <= today) and (b is None or today <= b) and today >= start + timedelta(days=154)
        rows.append({'title': when, 'sub': f'{title}. {what}.', 'right': f'{round(share)}%' + ('' if a is None or b is None else f' · {round(by)}% by then'),
                     'tone': 'lit' if now else ''})
    return rows


def a_short(d):
    return f'{d:%b} {d.day}'


def _labour_curve(due_date):
    """Labour that starts by itself, day by day from four weeks before the due date to two after: a bell around the
    due date, in a 720 by 180 box. Returns the points, where the due date sits (0 to 1) and the most likely week."""
    import math
    days = range(-28, 15)
    peak = 1 / (SPREAD * math.sqrt(2 * math.pi))
    point = lambda d: f'{2 + 716 * (d + 28) / 42:.1f},{170 - 160 * math.exp(-d * d / (2 * SPREAD * SPREAD)):.1f}'
    return [' '.join(point(d) for d in days)], 28 / 42, peak


# Getting ready, the household's list: (key, by week, title, why). Ticks are shared by everyone who sees the board.
PREP = [
    ('vitamins', 4, 'Take a prenatal vitamin with folic acid', 'It matters most in the first weeks.'),
    ('provider', 6, 'Choose a doctor or midwife and book the first visit', 'First visits usually fall in weeks 8 to 10; book early.'),
    ('insurance', 10, 'Check the insurance: maternity cover, the hospital in network, the deductible', ''),
    ('flu', 12, 'Ask about the flu shot this season', 'Recommended in any trimester during flu season.'),
    ('tell', 13, 'Decide when and how to tell family, friends and work', ''),
    ('leave', 16, 'Work out parental leave for both of you', 'Employer policies and the state’s paid leave programme; some need notice.'),
    ('childcare', 20, 'Start looking at childcare', 'Waitlists for infant care can run for months.'),
    ('registry', 22, 'Start a baby registry', ''),
    ('class', 24, 'Sign up for a birth and newborn class', 'They fill up; most are taken in the third trimester.'),
    ('budget', 26, 'Plan the money: the birth bill, childcare, leave pay', ''),
    ('tour', 28, 'Tour the hospital or birth centre and pre-register', ''),
    ('pediatrician', 32, 'Choose a pediatrician', 'The baby’s first check is usually a few days after birth.'),
    ('nursery', 34, 'Set up a safe sleep space', 'A firm, flat crib or bassinet, nothing else in it.'),
    ('carseat', 35, 'Install the car seat and have it checked', 'Hospitals usually check it before you leave.'),
    ('bag', 36, 'Pack the hospital bag', ''),
    ('meals', 37, 'Fill the freezer with easy meals', ''),
    ('add-baby', 40, 'After the birth: add the baby to health insurance', 'Usually within 30 days of the birth.'),
]


def TICKABLE():
    return {key for key, *_ in PREP}


def _household_due():
    try:
        found = household.section('pregnancy').get('due')
        return date.fromisoformat(found) if isinstance(found, str) else None
    except (OSError, ValueError):
        return None


def due():
    """The due date: the one set on the dashboard, else the private settings file's. None when there is neither."""
    kept = setting(ID, 'due')
    try:
        return date.fromisoformat(kept) if kept else _household_due()
    except ValueError:
        return _household_due()


def scanned():
    """Whether a dating ultrasound set the due date; until then it is an estimate."""
    kept = setting(ID, 'scan')
    if kept is not None:
        return kept == 'on'
    try:
        return household.section('pregnancy').get('scan') is True
    except (OSError, ValueError):
        return False


def sees(login):
    """The household's members, on their own devices, once a due date is known or the settings file names one."""
    if people.who(login) is None:
        return False
    try:
        return due() is not None or 'pregnancy' in household.load()
    except (OSError, ValueError):
        return False


def _short(d):
    return f'{d:%a}, {d:%b} {d.day}'


def _long(d):
    return f'{d:%A}, {d:%B} {d.day}'


def _in(days):
    if days == 0:
        return 'today'
    if days == 1:
        return 'tomorrow'
    if days < 0:
        return f'{-days} days ago'
    if days < 14:
        return f'in {days} days'
    if days < 63:
        return f'in {round(days / 7)} weeks'
    return f'in about {round(days / 30.4)} months'


def where(due_date, today):
    """Where the pregnancy is on `today`, as plain values."""
    start = due_date - timedelta(days=TERM)
    day = (today - start).days
    weeks, days = divmod(max(day, 0), 7)
    trimester = 1 if day < T2 else 2 if day < T3 else 3
    return {'start': start, 'day': day, 'weeks': weeks, 'days': days, 'week': weeks + 1, 'trimester': trimester,
            'left': (due_date - today).days, 'size': SIZES.get(min(max(weeks + 1, 4), 40), ''),
            'this_week': next((said for week, said in reversed(WEEKS) if week <= weeks + 1), WEEKS[0][1])}


def _along(at):
    weeks = f"{at['weeks']} week{'s' * (at['weeks'] != 1)}"
    return weeks + (f", {at['days']} day{'s' * (at['days'] != 1)}" if at['days'] else '')


TRIMESTER = {1: 'First trimester', 2: 'Second trimester', 3: 'Third trimester'}


def _coming(start, today, most=6):
    found = []
    for first, last, key, title, what in MILESTONES:
        if start + timedelta(days=last) < today:
            continue
        a, b = start + timedelta(days=first), start + timedelta(days=last)
        when = _short(a) if a == b else f'{_short(a)} to {_short(b)}'
        now = a <= today <= b
        found.append({'key': key, 'title': title, 'sub': f'{when}. {what}'.strip(), 'right': 'Now' if now else _in((a - today).days),
                      'tone': 'lit' if now else ''})
    return found[:most]


def _prep(start, today, at):
    done = ticks(ID)
    items = []
    for key, by, title, why in PREP:
        by_day = start + timedelta(days=by * 7)
        late = key not in done and by_day < today and key != 'add-baby'
        when = 'After the birth' if key == 'add-baby' else f'By week {by}, {_short(by_day)}'
        items.append({'id': key, 'title': title, 'sub': (when + ('. ' + why if why else '')), 'done': key in done,
                      'tone': 'warn' if late else '', 'by': by})
    return items


def _months(start, due_date, today):
    rows, first = [], date(start.year, start.month, 1)
    while first <= due_date:
        nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
        a, b = max(first, start), min(nxt - timedelta(days=1), due_date - timedelta(days=1))
        wa, wb = (a - start).days // 7 + 1, (b - start).days // 7 + 1
        tri = sorted({1 if d < T2 else 2 if d < T3 else 3 for d in ((a - start).days, (b - start).days)})
        rows.append({'title': f'{first:%B %Y}', 'sub': f'Weeks {wa} to {wb}' if wa != wb else f'Week {wa}',
                     'right': ' and '.join(TRIMESTER[t].split()[0] for t in tri), 'tone': 'lit' if first <= today < nxt else ''})
        first = nxt
    return rows


def _visits(start, today):
    phases = [(0, T3, 'Every 4 weeks', 'Until week 28'), (T3, 36 * 7, 'Every 2 weeks', 'Weeks 28 to 36'), (36 * 7, TERM + 14, 'Every week', 'From week 36')]
    rows = []
    for a, b, how, span in phases:
        first, end = start + timedelta(days=a), start + timedelta(days=b)
        now = first <= today < end
        when = f'{span} to the birth, from {_short(first)}' if b > TERM else f'{span}, until {_short(end - timedelta(days=1))}' if not a \
            else f'{span}, {_short(first)} to {_short(end - timedelta(days=1))}'
        rows.append({'title': how, 'sub': when, 'right': 'Now' if now else '', 'tone': 'lit' if now else ''})
    return rows


def picture(login, today):
    due_date = due()
    scan = scanned()
    settings = [{'key': 'due', 'label': 'Due date', 'type': 'date', 'value': due_date.isoformat() if due_date else ''},
                {'key': 'scan', 'label': 'Dated by ultrasound', 'type': 'switch', 'value': 'on' if scan else ''}]
    if due_date is None:
        return {'say': 'Set the due date', 'sub': 'Everything here is worked out from it.', 'settings': settings,
                'blocks': [{'id': 'how', 'title': 'How it works', 'kind': 'note',
                            'text': 'Put in the due date below. Hub counts the weeks the way a clinic does, and keeps it for the household.'}]}
    at = where(due_date, today)
    start = at['start']
    if at['left'] >= 0:
        say = f"Week {at['week']}" + (f": about the size of {at['size']}" if at['size'] else '')
        sub = f"Due {_long(due_date)}{'' if scan else ', an estimate'}. " + ('Today.' if at['left'] == 0 else f"{at['left']} day{'s' * (at['left'] != 1)} to go.")
    else:
        say, sub = 'Past the due date', f"Due {_long(due_date)}, {-at['left']} day{'s' * (at['left'] != -1)} ago."
    next_tri = start + timedelta(days=T2 if at['trimester'] == 1 else T3) if at['trimester'] < 3 else None
    prep = _prep(start, today, at)
    blocks = [
        {'id': 'progress', 'title': 'Where we are', 'kind': 'hero', 'big': f"{_along(at)}",
         'small': f"{TRIMESTER[at['trimester']]} · day {max(at['day'], 0) + 1} of {TERM}",
         'progress': min(max(at['day'], 0) / TERM, 1), 'marks': [{'at': T2 / TERM, 'label': '2nd'}, {'at': T3 / TERM, 'label': '3rd'}]},
        {'id': 'numbers', 'title': 'In numbers', 'kind': 'tiles', 'tiles': [
            {'value': str(max(at['left'], 0)), 'label': 'days to go'},
            {'value': _short(due_date), 'label': 'due date' if scan else 'due date, estimated'},
            {'value': f"{at['trimester']}", 'label': f"trimester · next {_short(next_tri)}" if next_tri else 'trimester, the last'},
            {'value': f"{min(round(100 * max(at['day'], 0) / TERM), 100)}%", 'label': 'of the way'}]},
        *([] if scan or at['week'] > 14 else [{'id': 'dating', 'title': 'An estimated due date', 'kind': 'note',
            'text': 'This due date is worked out from the cycle, so it can move by a week or so. The dating ultrasound, '
                    'usually between weeks 8 and 13, gives the date to keep: open Settings, put its date in and turn on '
                    'Dated by ultrasound.'}]),
        *_odds_blocks(at, due_date, today, start),
        {'id': 'week', 'title': f"This week, week {at['week']}", 'kind': 'note', 'text': at['this_week']},
        {'id': 'coming', 'title': 'Coming up', 'kind': 'rows', 'rows': _coming(start, today),
         'empty': 'Nothing left on the usual schedule.'},
        {'id': 'prep', 'title': 'Getting ready', 'kind': 'checks', 'items': prep,
         'sub': f"{sum(i['done'] for i in prep)} of {len(prep)} done. Shared with everyone who sees this page."},
        {'id': 'visits', 'title': 'Visits to expect', 'kind': 'rows', 'rows': _visits(start, today)},
        {'id': 'months', 'title': 'Month by month', 'kind': 'rows', 'rows': _months(start, due_date, today)},
        {'id': 'about', 'title': 'About these dates', 'kind': 'note',
         'text': 'Worked out from the due date the way clinics count, with the usual US schedule for visits and tests. '
                 'Your own doctor or midwife sets the real plan.'},
    ]
    return {'say': say, 'sub': sub, 'blocks': blocks, 'settings': settings}


def _odds_blocks(at, due_date, today, start):
    """The chance of a live birth from here, how it changes week by week, and when babies are usually born."""
    chance = live_chance(at['day'])
    segments, here = _odds_curve(at['day'])
    labour, due_at, peak = _labour_curve(due_date)
    early = at['day'] < 7 * 7
    return [
        {'id': 'odds', 'title': 'Chance of a live birth', 'kind': 'hero', 'big': in_words(chance),
         'small': (f"On average, for a pregnancy still going at {_along(at).lower()}. "
                   + ('It rises quickly once a heartbeat is seen on a scan.' if early else 'It keeps rising a little each week.')),
         'progress': chance},
        {'id': 'odds-curve', 'title': 'How the chance changes, week 4 to 40', 'kind': 'line', 'segments': segments, 'view': [720, 180],
         'start': 'Week 4', 'end': 'Week 40', 'now': f'{chance * 100:.0f}%', 'top': 'From 70% at the bottom to 100% at the top',
         'mark': {'at': here, 'label': 'You are here'}},
        {'id': 'born', 'title': 'When babies are born', 'kind': 'rows', 'rows': _births(start, today),
         'sub': f'US single births in 2023, by the dates for a due date of {a_short(due_date)}.'},
        {'id': 'labour-curve', 'title': 'Labour on its own, day by day', 'kind': 'line', 'segments': labour, 'view': [720, 180],
         'start': a_short(due_date - timedelta(days=28)), 'end': a_short(due_date + timedelta(days=14)),
         'now': f'About {round(peak * 100)}% on the day', 'top': 'About 70% within ten days of it',
         'mark': {'at': due_at, 'label': f'Due {a_short(due_date)}'}},
        {'id': 'odds-about', 'title': 'About these numbers', 'kind': 'note',
         'text': ('These are averages over many pregnancies, not a prediction for this one: age, health, history and '
                  'what the scans show all change them, and your doctor or midwife can say more. Chance of a live birth: '
                  f'{LIVE_SOURCES}. When babies are born: {BORN_SOURCE}. Labour on its own: a bell with a spread of '
                  f'{SPREAD:g} days around the due date, after Jukic et al., Hum Reprod 2013.')},
    ]


def line(login, today):
    due_date = due()
    if due_date is None:
        return None
    at = where(due_date, today)
    if at['left'] < -21:
        return None
    text = f"Week {at['week']}, {at['left']} days to go" if at['left'] >= 0 else f"{-at['left']} days past the due date"
    soon = next((i for i in _prep(at['start'], today, at) if not i['done'] and i['id'] != 'add-baby' and i['by'] <= at['week'] + 2), None)
    if soon:
        return {'text': text, 'next': soon['title'], 'tone': soon['tone']}
    coming = _coming(at['start'], today, 1)
    return {'text': text, 'next': f"{coming[0]['title']}, {coming[0]['right'].lower()}" if coming else '', 'tone': ''}


def facts(login, today):
    due_date = due()
    if due_date is None:
        return {'note': 'No due date is set on the pregnancy dashboard yet.'}
    at = where(due_date, today)
    prep = _prep(at['start'], today, at)
    return {'today': today.isoformat(), 'due_date': due_date.isoformat(),
            'due_date_is': 'set by the dating ultrasound' if scanned() else 'an estimate from the cycle, until the dating ultrasound',
            'along': _along(at), 'week': at['week'],
            'trimester': at['trimester'], 'days_to_go': at['left'], 'size_this_week': at['size'], 'this_week': at['this_week'],
            'chance_of_a_live_birth_on_average': f"{in_words(live_chance(at['day']))} ({live_chance(at['day']) * 100:.1f}%), "
                                                 'an average for a pregnancy still going at this point, not a prediction',
            'when_babies_are_born': [f"{r['title']}: {r['right']}" for r in _births(at['start'], today)],
            'numbers_from': f'{LIVE_SOURCES}; {BORN_SOURCE}',
            'coming_up_on_the_usual_schedule': [f"{c['title']}: {c['sub']} ({c['right']})" for c in _coming(at['start'], today)],
            'getting_ready_not_done': [f"{i['title']} ({i['sub'].split('.')[0]})" for i in prep if not i['done']][:6],
            'note': 'Dates are the usual schedule worked out from the due date; the household’s doctor or midwife sets the real plan.'}


def set_value(key, value):
    """The due date, from the dashboard: within the next ten months, or empty to go back to the settings file's.
    `scan`: whether a dating ultrasound set it."""
    if key == 'scan':
        return ('on', 'Marked as the date from the dating ultrasound.') if value else ('off', 'Marked as an estimate.')
    if key != 'due':
        raise Refused('Hub couldn’t read that request.')
    if not value:
        return None, 'Back to the due date in Hub’s settings.'
    try:
        found = date.fromisoformat(str(value)[:10])
    except ValueError as error:
        raise Refused('Choose a date.') from error
    from ... import home
    today = home.today()
    if not today - timedelta(days=60) <= found <= today + timedelta(days=TERM + 14):
        raise Refused('Choose a due date within the next ten months.')
    return found.isoformat(), f'Due date set to {_long(found)}.'
