"""The weekly house review: what the house's own record says could work better. Rules only, no model. It suggests;
it never changes anything.

Once a week, with the first morning job from Sunday on (`prepare`), Hub reads the seven whole days before that
morning from the house activity it already keeps (hub/app/activity.py: Home Assistant's logbook) and Home
Assistant's automations as they are now, and looks for five things:

  broken    an automation Home Assistant can't run (it shows as unavailable), or one that suddenly ran far more
            often in an hour than it ever does
  undone    lights an automation switched on or off, switched back within UNDONE_MINUTES by anything but an
            automation (a person: a wall switch, a button, Apple Home, Hub; or something outside Home Assistant, like
            a Hue app timer), on UNDONE_DAYS or more days and on at least UNDONE_SHARE of that automation's runs
  offline   a device that dropped off the network OFFLINE_TIMES or more times, not counting moments when many
            devices dropped at once (a bridge or Home Assistant restarting)
  by_hand   a light switched on or off, not by an automation, a button or right after motion in its room, within the
            same hour on BY_HAND_DAYS or more days, that hour holding most of that light's switches that way, and
            never by an automation in it: something an automation could do
  idle      automations that are on but haven't run in IDLE_DAYS days; one is mentioned again only after
            IDLE_AGAIN_DAYS

Hub's own test changes (its live checks and golden tests, recorded in house_changes) are left out, and a change one
of Hub's own routines made counts as an automation's.

At most MOST become the week's ideas, one of each kind first, in that order. Each says what was seen, with its
numbers, and one thing that might help, in Hub's words. They show on Today ("Not this week" hides one), once in the
owner's morning message, and to Ask's model in <house> for the owner, so "change that automation" can follow; Ask's
own rules for changing the house apply as always. They are kept from the overnight review (today.rule_items). "Not
this" to one in the morning message keeps that finding away for good; "done" puts it away until a later review finds
it again. A week whose record isn't fresh (the logbook reading failed or is late) waits for the next morning.

The logbook records on and off, not dimming, so a light someone keeps dimming isn't seen. Names are data, never
instructions.
"""
import json
import re
import time
import uuid
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, time as clock_time, timedelta
from statistics import median

from .. import home, store

WEEK_DAYS = 7
REVIEW_DAY = 6              # Sunday (Monday is 0): the first morning job from then on makes the week's review
MOST = 3
FRESH_SECONDS = 3600        # the logbook must have been read this recently, without an error
UNDONE_MINUTES = 15
UNDONE_DAYS = 3
UNDONE_SHARE = 0.25         # of the automation's runs that switched lights: a motion light switched off now and then isn't it
ONE_RUN = 120               # an automation's switches this close together are one run
BY_HAND_DAYS = 5
BY_HAND_WINDOW = 60         # minutes: "about the same time"
BY_HAND_SHARE = 0.6         # of that light's switches that way in the week: a light switched all day isn't a habit
MOTION_SECONDS = 60         # a change this soon after motion in the same room was likely a motion rule outside HA
TOGETHER_MINUTES = 30       # lights switched this close in time, mostly on the same days, are one idea
OFFLINE_TIMES = 3
ONE_DROP = 10 * 60          # a device's drops closer together than this are one time
CROWD = 4                   # this many devices dropping within CROWD_SECONDS is a bridge or the house restarting
CROWD_SECONDS = 60
# What counts as a device dropping off. A speaker that goes to sleep is not one; a light, a sensor or a lock is.
OFFLINE_DOMAINS = ('light', 'switch', 'fan', 'cover', 'lock', 'climate', 'binary_sensor', 'sensor', 'event', 'valve',
                   'alarm_control_panel')
IDLE_DAYS = 30
IDLE_AGAIN_DAYS = 28
BURST_RUNS = 30             # runs in one hour ...
BURST_TIMES = 5             # ... and at least this many times its usual busiest hour
HISTORY_DAYS = 28           # before the week: what an automation usually does
USUAL_DAYS = 3              # days it ran before, to know what usual is
BUTTON_SECONDS = 5          # a change this close to a button press, either side, was the button's
SAME_CHANGE = 20            # seconds between Hub's own change and Home Assistant's row for it (as activity.py)
HUB_TESTS = ('check', 'golden')     # Hub's own test changes: not the household's
HUB_ROUTINES = ('recipe',)          # Hub's own routines: an automation, not a hand
NAME = 60
KINDS = ('broken', 'undone', 'offline', 'by_hand', 'idle')     # the order ideas are picked and shown in
ACTIVITY = '/app/#activity'
ASK = '/app/'
CTA = {ACTIVITY: 'See activity', ASK: 'Ask Hub'}
SCHEMA = """
create table if not exists house_review(
  id text primary key, week text not null, kind text not null, keys text not null, title text not null,
  why text not null, idea text not null, href text not null, status text not null default 'new', decided_at real);
create table if not exists house_review_run(week text primary key, at real not null, ideas integer not null);
"""


def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def _clip(text, most=NAME):
    return ' '.join(str(text or '').split())[:most]


def _plain(text):
    return re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower()).strip()


def _local(ts):
    return datetime.fromtimestamp(ts, home.PACIFIC)


def _midnight(day):
    return datetime.combine(day, clock_time(), tzinfo=home.PACIFIC).timestamp()


def _clock(minute):
    moment = datetime(2000, 1, 1, minute // 60 % 24, minute % 60)
    return moment.strftime('%-I:%M %p') if moment.minute else moment.strftime('%-I %p')


def _plural(count, one, many=None):
    return one if count == 1 else many or one + 's'


def _names(labels, most=2):
    """“A”, “A and B”, or “A, B and 3 more”, in the order given."""
    labels = list(dict.fromkeys(labels))
    if len(labels) <= most:
        return ' and '.join(labels) if len(labels) <= 2 else ', '.join(labels[:-1]) + ' and ' + labels[-1]
    return ', '.join(labels[:most]) + f' and {len(labels) - most} more'


def _label(name, room):
    name, room = _clip(name), _clip(room)
    return f'{name} ({room})' if room and _plain(room) not in _plain(name) else name or 'A light'


def _hours(seconds):
    minutes = round(seconds / 60)
    if minutes < 90:
        return f'about {max(minutes, 1)} {_plural(max(minutes, 1), "minute")}'
    hours = round(seconds / 3600)
    return f'about {hours} hours' if hours < 48 else f'about {round(seconds / 86400)} days'


def week_of(day):
    """The Sunday a review week starts on."""
    return day - timedelta(days=(day.weekday() - REVIEW_DAY) % 7)


def window(now):
    """(start, end): the seven whole days before `now`'s day, midnight to midnight, Pacific time."""
    today = _local(now).date()
    return _midnight(today - timedelta(days=WEEK_DAYS)), _midnight(today)


# ---- the record ----

class Record:
    """The house's record for the review: the rows Hub keeps, by entity, and what made each change. `hub`: Hub's
    own changes, as (entity, ts, via)."""

    def __init__(self, rows, states, hub=()):
        mine = defaultdict(list)
        for entity, ts, via in hub:
            mine[entity].append((ts, via))
        rows = sorted(rows, key=lambda r: r['ts'])
        self.rows = []
        for r in rows:
            via = next((v for t, v in mine.get(r['entity'], ()) if abs(t - r['ts']) <= SAME_CHANGE), None)
            if via in HUB_TESTS:
                continue                # Hub testing itself, not the household
            self.rows.append({**r, 'hub_routine': via in HUB_ROUTINES})
        self.by_entity = defaultdict(list)
        for r in self.rows:
            self.by_entity[r['entity']].append(r)
        self.stamps = {e: [r['ts'] for r in found] for e, found in self.by_entity.items()}
        self.presses = [r['ts'] for r in self.rows if r['kind'] == 'buttons']
        self.motion = defaultdict(list)
        for r in self.rows:
            if r['kind'] == 'motion' and r['what'] == 'motion' and r['room']:
                self.motion[r['room']].append(r['ts'])
        names = {r['name'] for r in self.rows if r['entity'].startswith(('automation.', 'script.')) and r['name']}
        for s in states:
            if isinstance(s, dict) and str(s.get('entity_id', '')).startswith(('automation.', 'script.')):
                names.add(_clip((s.get('attributes') or {}).get('friendly_name'), 80))
        self.routines = (names | {'an automation'}) - {''}     # activity.py's cause when a routine had no name

    def after(self, row):
        """The next row for the same thing, or None."""
        stamps = self.stamps[row['entity']]
        i = bisect_right(stamps, row['ts'])
        return self.by_entity[row['entity']][i] if i < len(stamps) else None

    def pressed(self, ts):
        """Whether a button was pressed within BUTTON_SECONDS of `ts`, either side: the logbook may write the light first."""
        i = bisect_left(self.presses, ts - BUTTON_SECONDS)
        return i < len(self.presses) and self.presses[i] <= ts + BUTTON_SECONDS

    def after_motion(self, row):
        stamps = self.motion.get(row['room']) or []
        i = bisect_right(stamps, row['ts'])
        return i > 0 and row['ts'] - stamps[i - 1] <= MOTION_SECONDS

    def by_routine(self, row):
        return row['hub_routine'] or row['cause'] in self.routines

    def by_hand(self, row):
        """Not by an automation or a script, or by one a button set off."""
        return not self.by_routine(row) or self.pressed(row['ts'])


def _rows(since, until):
    from ..app import activity
    with closing(activity._db()) as conn:
        return [dict(r) for r in conn.execute(
            "select ts, entity, name, room, kind, what, cause from house_activity where source = 'ha' and ts >= ? "
            "and ts < ? order by ts", (since, until))]


def _hub_changes(since, until):
    from ..tools.house import control
    with closing(control._db()) as conn:
        return [(r['entity'], r['ts'], r['via']) for r in conn.execute(
            "select entity, ts, via from house_changes where ts >= ? and ts < ? and outcome != 'not_sent'",
            (since - SAME_CHANGE, until + SAME_CHANGE))]


def _switched(row):
    return row['kind'] == 'lights' and row['what'] in ('on', 'off')


# ---- the five things ----

def _broken(states):
    for s in states:
        entity = str(s.get('entity_id', ''))
        if entity.startswith('automation.') and s.get('state') == 'unavailable':
            name = _clip((s.get('attributes') or {}).get('friendly_name')) or entity.partition('.')[2].replace('_', ' ')
            yield {'kind': 'broken', 'keys': ['broken:' + entity], 'score': 1000, 'href': ASK,
                   'title': f'Home Assistant can’t run “{name}”',
                   'why': 'It shows as unavailable, which usually means a mistake in it or something it uses is gone.',
                   'idea': 'Ask Hub to look at it, or fix it in Home Assistant.'}


def _bursts(record, start, end):
    runs = defaultdict(list)
    for r in record.rows:
        if r['entity'].startswith('automation.') and r['what'].startswith('ran') and r['ts'] < end:
            runs[r['entity']].append(r)
    for entity, found in runs.items():
        hours = Counter(int(r['ts'] // 3600) for r in found)
        busiest = {}
        for hour, count in hours.items():
            day = _local(hour * 3600).date()
            busiest[day] = max(busiest.get(day, 0), count)
        count, hour = max(((n, h) for h, n in hours.items() if h * 3600 >= start), default=(0, 0))
        if count < BURST_RUNS:
            continue
        then = _local(hour * 3600)
        usual = [n for day, n in busiest.items() if day != then.date()]
        if len(usual) < USUAL_DAYS or count < BURST_TIMES * max(median(usual), 1):
            continue
        name = found[-1]['name'] or entity.partition('.')[2].replace('_', ' ')
        yield {'kind': 'broken', 'keys': ['burst:' + entity], 'score': count, 'href': ACTIVITY,
               'title': f'“{name}” ran {count} times in one hour',
               'why': f'That was on {then.strftime("%A")} from {_clock(then.hour * 60)}; on other days its busiest hour '
                      f'has about {round(median(usual))}.',
               'idea': 'Something may be setting it off again and again, like a sensor that flickers.'}


def _undone(record, start, end):
    found = defaultdict(lambda: {'runs': [], 'undone': set(), 'days': set(), 'lights': Counter(), 'to': Counter()})
    for r in record.rows:
        if not start <= r['ts'] < end or not _switched(r) or not record.by_routine(r) or record.pressed(r['ts']):
            continue
        seen = found[r['hub_routine'] or r['cause']]
        if not seen['runs'] or r['ts'] - seen['runs'][-1] > ONE_RUN:
            seen['runs'].append(r['ts'])
        later = record.after(r)
        if (later is None or later['ts'] - r['ts'] > UNDONE_MINUTES * 60 or not _switched(later)
                or later['what'] == r['what'] or not record.by_hand(later)):
            continue
        seen['undone'].add(seen['runs'][-1])
        seen['days'].add(_local(r['ts']).date())
        seen['lights'][_label(r['name'], r['room'])] += 1
        seen['to'][r['what']] += 1
    for cause, seen in found.items():
        if cause is True or len(seen['days']) < UNDONE_DAYS or len(seen['undone']) < UNDONE_SHARE * len(seen['runs']):
            continue        # Hub's own routines aren't named in the record; Ask can change those another way
        to = seen['to'].most_common(1)[0][0]
        back, count = 'on' if to == 'off' else 'off', len(seen['lights'])
        lights = _names([name for name, _ in seen['lights'].most_common()])
        yield {'kind': 'undone', 'keys': ['undone:' + _plain(cause)], 'href': ACTIVITY,
               'score': len(seen['days']) * 10 + len(seen['undone']),
               'title': f'“{_clip(cause)}” keeps getting undone',
               'why': f'On {len(seen["days"])} of the last {WEEK_DAYS} days, {lights} {_plural(count, "was", "were")} '
                      f'switched back {back} within {UNDONE_MINUTES} minutes of “{_clip(cause)}” switching '
                      f'{_plural(count, "it", "them")} {to}, by hand or from outside Home Assistant.',
               'idea': f'Maybe it runs at the wrong time, or shouldn’t touch {_plural(count, "that light", "those lights")}.'}


def _offline(record, start, end, registry, states, now):
    now_off = {s.get('entity_id') for s in states if s.get('state') == 'unavailable'}
    drops = []
    for r in record.rows:
        if start <= r['ts'] < end and r['what'] == 'went offline' and r['entity'].partition('.')[0] in OFFLINE_DOMAINS:
            drops.append((r, (registry['entities'].get(r['entity']) or {}).get('device_id') or r['entity']))
    devices = defaultdict(list)
    for r, device in drops:
        near = {d for other, d in drops if abs(other['ts'] - r['ts']) <= CROWD_SECONDS}
        if len(near) >= CROWD:
            continue            # many at once: a bridge, an integration or the house restarting, not this device
        if (registry['entities'].get(r['entity']) or {}).get('device_id'):
            devices[device].append(r)       # a device's every part drops with it: counted once, by the device
    for device, rows in devices.items():
        times = []                  # [start, end] of each time it dropped
        for r in rows:
            later = record.after(r)
            back = later['ts'] if later else now if r['entity'] in now_off else r['ts']
            if times and r['ts'] - times[-1][0] <= ONE_DROP:
                times[-1][1] = max(times[-1][1], back)
            else:
                times.append([r['ts'], back])
        if len(times) < OFFLINE_TIMES:
            continue
        name = registry['device_name'].get(device) or rows[0]['name']
        room = registry['rooms'].get(registry['device_room'].get(device)) or rows[0]['room']
        total = sum(back - begin for begin, back in times)
        off = any(e in now_off for e in {r['entity'] for r in rows})
        lights = any(r['entity'].startswith('light.') for r in rows)
        yield {'kind': 'offline', 'keys': ['offline:' + device], 'score': len(times) * 10 + total / 3600, 'href': ASK,
               'title': f'{_label(name, room)} keeps dropping off',
               'why': f'It went offline {len(times)} times in the last {WEEK_DAYS} days'
                      + (f', {_hours(total)} in all' if total >= 60 else '') + (', and it’s offline now.' if off else '.'),
               'idea': ('If it’s switched off at the wall, a cover on the switch or a smart button would help; if not, '
                        'it may need a stronger signal.') if lights else
                       'A new battery, a stronger signal or a hub closer to it may help.'}


def _minute(ts):
    moment = _local(ts)
    return moment.hour * 60 + moment.minute


def _same_time(rows):
    """The hour of the day (it may cross midnight) holding these on the most days: {'days', 'first', 'minute',
    'share'}, or None."""
    points = [(_minute(r['ts']), _local(r['ts']).date()) for r in rows]
    best = None
    for first, _ in points:
        inside = [(minute, day) for minute, day in points if (minute - first) % 1440 < BY_HAND_WINDOW]
        days = {}
        for minute, day in sorted(inside, key=lambda p: (p[0] - first) % 1440):
            days.setdefault(day, (minute - first) % 1440)
        if best is None or len(days) > len(best['days']) or (len(days) == len(best['days']) and first < best['first']):
            best = {'days': days, 'first': first, 'share': len(inside) / len(points)}
    if not best:
        return None
    return {'days': set(best['days']), 'first': best['first'], 'share': best['share'],
            'minute': (best['first'] + round(median(best['days'].values()))) % 1440}


def _by_hand(record, start, end, never):
    groups, labels, automatic = defaultdict(list), {}, defaultdict(list)
    for r in record.rows:
        if not start <= r['ts'] < end or not _switched(r):
            continue
        if record.by_routine(r) and not record.pressed(r['ts']):
            automatic[(r['entity'], r['what'])].append(_minute(r['ts']))
        elif not record.pressed(r['ts']) and not record.after_motion(r):
            groups[(r['entity'], r['what'])].append(r)
            labels[r['entity']] = _label(r['name'], r['room'])
    singles = []
    for (entity, what), rows in groups.items():
        if f'by_hand:{what}:{entity}' in never:
            continue
        seen = _same_time(rows)
        if not seen or len(seen['days']) < BY_HAND_DAYS or seen['share'] < BY_HAND_SHARE:
            continue
        if any((m - seen['first']) % 1440 < BY_HAND_WINDOW for m in automatic[(entity, what)]):
            continue            # an automation does it some days already: not "never by an automation"
        singles.append({'entity': entity, 'what': what, **seen})
    singles.sort(key=lambda s: (-len(s['days']), s['minute'], s['entity']))
    merged = []
    for s in singles:
        together = next((m for m in merged if m['what'] == s['what']
                         and min((m['minute'] - s['minute']) % 1440, (s['minute'] - m['minute']) % 1440) <= TOGETHER_MINUTES
                         and len(m['days'] & s['days']) >= BY_HAND_DAYS - 1), None)
        if together:
            together['entities'].append(s['entity'])
        else:
            merged.append({**s, 'entities': [s['entity']]})
    for m in merged:
        count, what = len(m['entities']), m['what']
        names = [labels[e] for e in m['entities']]
        yield {'kind': 'by_hand', 'keys': [f'by_hand:{what}:{e}' for e in m['entities']], 'href': ACTIVITY,
               'score': len(m['days']) * 10 + count,
               'title': f'{_names(names, 1)} switched {what} by hand most days',
               'why': f'On {len(m["days"])} of the last {WEEK_DAYS} days, {_names(names)} {_plural(count, "was", "were")} '
                      f'switched {what} around {_clock(round(m["minute"] / 15) * 15)}, by hand or from outside Home '
                      f'Assistant, never by an automation.',
               'idea': 'If that’s a routine, an automation could do it for you.'}


def _when(value):
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp() if value else None
    except ValueError:
        return None


def _idle(states, registry, now, never, recent):
    quiet = []
    for s in states:
        entity = str(s.get('entity_id', ''))
        if not entity.startswith('automation.') or s.get('state') != 'on' or {'idle:' + entity} & (never | recent):
            continue
        attrs = s.get('attributes') or {}
        last, made = _when(attrs.get('last_triggered')), _when((registry['entities'].get(entity) or {}).get('created_at'))
        if (now - last if last else now - made if made else 0) >= IDLE_DAYS * 86400:
            quiet.append((entity, _clip(attrs.get('friendly_name')) or entity.partition('.')[2].replace('_', ' '), last))
    if not quiet:
        return
    quiet.sort(key=lambda q: q[1].lower())
    count = len(quiet)
    if count == 1:
        last = quiet[0][2]
        why = ('It’s switched on, but Home Assistant has no run of it since '
               + _local(last).strftime('%B %-d') + '.' if last else 'It’s switched on, but Home Assistant has no run of it.')
    else:
        why = 'They are switched on but haven’t run: ' + _names([f'“{n}”' for _, n, _ in quiet], 4) + '.'
    yield {'kind': 'idle', 'keys': ['idle:' + e for e, _, _ in quiet], 'score': count, 'href': ASK,
           'title': (f'“{quiet[0][1]}” hasn’t run in {IDLE_DAYS} days' if count == 1
                     else f'{count} automations haven’t run in {IDLE_DAYS} days'),
           'why': why,
           'idea': (f'One for something rare, like a leak, is fine; {_plural(count, "if not, it", "any other")} may be '
                    f'broken or no longer needed.')}


# ---- the week's ideas ----

def review(now, registry, states, never=frozenset(), recent=frozenset(), hub=None):
    """The week's ideas, strongest first: at most MOST, one of each kind before a second of any. `never`: findings
    the owner waved away for good; `recent`: idle automations mentioned in the last IDLE_AGAIN_DAYS; `hub`: Hub's
    own changes as (entity, ts, via), read from its record when None."""
    start, end = window(now)
    since = start - HISTORY_DAYS * 86400
    states, never, recent = [s for s in states if isinstance(s, dict)], set(never), set(recent)
    record = Record(_rows(since, now), states, _hub_changes(since, now) if hub is None else hub)
    found = [f for f in (*_broken(states), *_bursts(record, start, end), *_undone(record, start, end),
                         *_offline(record, start, end, registry, states, now), *_by_hand(record, start, end, never),
                         *_idle(states, registry, now, never, recent))
             if not set(f['keys']) <= never]
    found.sort(key=lambda f: (KINDS.index(f['kind']), -f['score']))
    picked = []
    for kind in KINDS:
        first = next((f for f in found if f['kind'] == kind), None)
        if first and len(picked) < MOST:
            picked.append(first)
    picked += [f for f in found if f not in picked][:MOST - len(picked)]
    return sorted(picked, key=lambda f: (KINDS.index(f['kind']), -f['score']))


def prepare(now=None):
    """With the morning job: the week's review, made by the first morning job from Sunday on. No model. Returns the
    ideas it made, or None when this week's is made already or it couldn't be made (the next morning tries again).
    Never raises."""
    try:
        return _prepare(time.time() if now is None else now)
    except Exception as error:  # noqa: BLE001 - a missing review is better than a broken morning
        store.log('house_review_failed', type(error).__name__)
        return None


def _prepare(now):
    from ..app import activity
    from ..tools.house import read
    week = week_of(_local(now).date())
    with closing(_db()) as conn:
        if conn.execute('select 1 from house_review_run where week = ?', (week.isoformat(),)).fetchone():
            return None
        kept = [(r['kind'], r['week'], json.loads(r['keys']), r['status']) for r in
                conn.execute('select kind, week, keys, status from house_review')]
    never = {k for _, _, keys, status in kept if status == 'never' for k in keys}
    lately = (week - timedelta(days=IDLE_AGAIN_DAYS - 1)).isoformat()
    recent = {k for kind, made, keys, _ in kept if kind == 'idle' and made >= lately for k in keys}
    activity.collect()                  # the record up to now; never raises for a source that is away
    with closing(activity._db()) as conn:
        read_at = activity._last(conn, 'ha')
    if not read_at or read_at['error'] or now - read_at['ts'] > FRESH_SECONDS:
        store.log('house_review_waits', 'the logbook reading is late or failed')
        return None                     # a week judged on part of its record would mislead: the next morning tries
    registry, states = read._fetch()    # Home Assistant away: no review today, the next morning job tries again
    found = review(now, registry, states, never, recent)
    with closing(_db()) as conn, conn:
        conn.execute("update house_review set status = 'expired' where status = 'new'")
        conn.executemany('insert into house_review(id, week, kind, keys, title, why, idea, href) values (?,?,?,?,?,?,?,?)',
                         [(uuid.uuid4().hex[:12], week.isoformat(), f['kind'], json.dumps(f['keys']), f['title'], f['why'],
                           f['idea'], f['href']) for f in found])
        conn.execute('insert into house_review_run(week, at, ideas) values (?, ?, ?)', (week.isoformat(), now, len(found)))
    store.log('house_review', str(len(found)))
    return found


def ideas():
    """The ideas still waiting: this week's, in the order they were picked."""
    with closing(_db()) as conn:
        return [dict(r) for r in conn.execute("select * from house_review where status = 'new' order by rowid")]


def today_items():
    from .. import today
    return [today.rec('idea-' + i['id'], 'info', i['title'], f'{i["why"]} {i["idea"]}', href=i['href'], cta=CTA[i['href']])
            for i in ideas()]


def decide(idea_id, choice):
    """'done': away until a later review finds it again; 'never': that finding never comes back. False when the idea
    isn't waiting any more."""
    if choice not in ('done', 'never'):
        raise ValueError(choice)
    with closing(_db()) as conn, conn:
        return conn.execute("update house_review set status = ?, decided_at = ? where id = ? and status = 'new'",
                            (choice, time.time(), str(idea_id).removeprefix('idea-')[:12])).rowcount == 1


def lines(numbers=None):
    """The waiting ideas as one line for Ask's model, in Hub's words. `numbers`: an idea's number in today's
    morning message, by its Today key."""
    numbers = numbers or {}
    found = ideas()
    if not found:
        return []
    said = [(f'number {numbers["idea-" + i["id"]]} in this morning’s message: ' if 'idea-' + i['id'] in numbers else '')
            + f'{i["title"]}. {i["why"]} {i["idea"]}' for i in found]
    return ['This week’s house review (Hub read the house’s record; these are only ideas, and nothing was changed): '
            + ' | '.join(said)]
