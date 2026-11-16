"""A made-up caretaker folder, for the House health tests and the demo. Every name and time is invented.

The journal's tables match what the caretaker writes (home-ops caretaker/store.py); Hub only reads them.
`night()` fills a folder with a made-up night on the made-up house of app_world: Hub stopped answering and was
started again, the reading lamp dropped off the Hue bridge and came back by itself, and Sonos keeps failing
until someone taps Restart Home Assistant. Six earlier days carry a few happenings of their own.
`Caretaker` stands in for the caretaker itself in the demo: it answers request notes and plays drills.
"""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hub import home

SCHEMA = """
create table if not exists meta(key text primary key, value text not null);
create table if not exists looks(
  id integer primary key, at text not null, ms integer not null, systems text not null,
  conditions integer not null, errors text not null default '[]');
create table if not exists conditions(
  subject text primary key, kind text not null, system text not null, title text not null,
  since text not null, last_seen text not null, detail text not null);
create table if not exists incidents(
  id integer primary key, subject text not null, kind text not null, system text not null,
  title text not null, cause text not null, plan text not null, status text not null, outcome text,
  first_seen text not null, opened_at text not null, updated_at text not null, closed_at text,
  tries integer not null default 0, last_try_at text, verify_at text,
  needs text, ask text, today integer not null default 1, drill text, detail text not null default '{}',
  today_from text, urgent integer not null default 0);
create table if not exists steps(
  id integer primary key, incident integer not null, at text not null, action text not null, target text not null,
  status text not null, words text not null, before text, after text, request text, drill text);
create table if not exists events(
  id integer primary key, at text not null, system text not null, tone text not null, text text not null,
  incident integer, drill text);
create table if not exists requests(
  id text primary key, received_at text not null, do text not null, target text, by text,
  status text not null, words text not null);
create table if not exists spells(subject text not null, started text not null, ended text, primary key (subject, started));
create table if not exists sturdier(
  key text primary key, title text not null, words text not null, evidence text not null,
  first_seen text not null, last_seen text not null, done_at text, baseline text, gone_at text, verdict text);
create table if not exists ignored(subject text primary key, at text not null, by text);
"""
SYSTEMS = [('hub', 'Hub', 'Answering'), ('updates', 'Hub updates', 'Up to date'), ('broker', 'House connection', 'Answering'),
           ('home_assistant', 'Home Assistant', '1 of 24 integrations not working: Sonos'),
           ('devices', 'Devices', 'All 61 devices answering'), ('hue', 'Hue lights', '3 lamps answering; 4 short drops in 24 hours'),
           ('apple_home', 'Apple Home', '31 accessories responding'), ('network', 'Internet and Wi-Fi', 'Internet up; router answering'),
           ('kitchen', 'Kitchen', 'Answering'), ('mail', 'Mail and calendar', 'Answering'),
           ('routines', 'Routines', '6 of 6 on; timed ones ran on time'), ('backups', 'Backups', 'Last backups: Home Assistant 5 hours ago'),
           ('mac', 'The Mac', '210 GB free, up 12 days, on Ethernet'), ('checks', 'Live checks', 'Nothing failing; last run 4:00 AM')]
DRILLS = [('hub-down', 'An update leaves Hub stopped.'), ('hue-flapping', 'A Hue lamp flaps, then another stays out.'),
          ('lock-battery', 'A lock stops responding with its battery at 1%.'), ('broker-hung', 'The house connection stops answering.')]
STURDIER = [('hue-zigbee', 'Hue lamps keep dropping off',
             'Hue lamps dropped off 41 times this week (Reading lamp 30, Porch lantern 11). The Wi-Fi on 2.4 GHz channel 6 '
             'covers Zigbee channels 15 to 20. Move the Hue bridge to Zigbee channel 11 in the Hue app.'),
            ('ha-backups-local', 'Home Assistant’s backups stay on one disk',
             'Home Assistant keeps its backups only on its own disk; add a second place in Settings, System, Backups.')]


def iso(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec='seconds')


def local(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=home.PACIFIC)


class Folder:
    """A caretaker folder with its journal open for writing (the caretaker's side, not Hub's)."""

    def __init__(self, path):
        self.path = Path(path)
        (self.path / 'queue').mkdir(parents=True, exist_ok=True)
        os.chmod(self.path, 0o700)
        self.conn = sqlite3.connect(str(self.path / 'caretaker.db'), isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('pragma journal_mode=wal')
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def run(self, sql, *args):
        with self.lock:
            return self.conn.execute(sql, args).lastrowid

    def incident(self, kind, system, title, start, end=None, outcome=None, needs=None, ask=None, today=1, drill=None,
                 today_from=None, steps=(), cause='', detail=None):
        status = 'closed' if end else ('needs_you' if needs else 'open')
        iid = self.run('insert into incidents(subject, kind, system, title, cause, plan, status, outcome, first_seen, opened_at, '
                       'updated_at, closed_at, needs, ask, today, drill, detail, today_from) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       f'{kind}:{title}', kind, system, title, cause or title, '[]', status, outcome, iso(start), iso(start),
                       iso(end or start), iso(end) if end else None, needs, ask, today, drill, json.dumps(detail or {}),
                       iso(today_from) if today_from else None)
        for at, action, step_status, words in steps:
            self.run('insert into steps(incident, at, action, target, status, words, drill) values(?,?,?,?,?,?,?)',
                     iid, iso(at), action, '', step_status, words, drill)
        return iid

    def event(self, at, system, tone, text, incident=None, drill=None):
        self.run('insert into events(at, system, tone, text, incident, drill) values(?,?,?,?,?,?)',
                 iso(at), system, tone, text, incident, drill)

    def look(self, at):
        self.run("insert into looks(at, ms, systems, conditions) values(?, 900, '{}', 0)", iso(at))

    def status(self, now, **over):
        found = self.read_status() or {}
        data = {'schema_version': 1, 'version': '1.0', 'at': iso(now), 'heartbeat': iso(now), 'pass_ms': 900,
                'mode': 'fix', 'dry_run': False, 'paused': False, 'paused_until': None,
                'systems': [{'id': i, 'name': n, 'state': 'needs_you' if i == 'home_assistant' else 'ok', 'line': line}
                            for i, n, line in SYSTEMS],
                'open': [], 'needs': [], 'batteries': [{'name': 'Side door lock', 'room': 'Entry', 'level': 18, 'reachable': True}],
                'devices_out': [], 'drills': [{'name': n, 'words': w} for n, w in DRILLS],
                'sturdier': [{'key': k, 'title': t, 'words': w, 'since': iso(now - timedelta(days=3)), 'done_at': None,
                              'verdict': None} for k, t, w in STURDIER],
                'ignoring': [{'name': 'Travel speaker', 'why': 'set aside in settings'}], 'errors': []}
        with self.lock:
            open_needs = self.conn.execute("select * from incidents where status = 'needs_you' and drill is null").fetchall()
        data['needs'] = [{'id': r['id'], 'fixable': False, 'ask': r['ask']} for r in open_needs]
        data.update({k: v for k, v in found.items() if k == 'drill'})
        data.update(over)
        scratch = self.path / 'status.json.tmp'
        scratch.write_text(json.dumps(data))
        os.replace(scratch, self.path / 'status.json')
        return data

    def read_status(self):
        try:
            return json.loads((self.path / 'status.json').read_text())
        except (OSError, ValueError):
            return None


def night(path, now=None, released=False):
    """A folder holding a made-up night and week, seen from `now` (default: now). Returns the Folder. `released`:
    the item that waits for the morning is on Today already, whatever the hour (for the demo)."""
    now = now or datetime.now(timezone.utc)
    f = Folder(path)
    today = now.astimezone(home.PACIFIC).date()
    for back in range(7, -1, -1):           # a look an hour for a week, so every day counts as watched
        for hour in range(24):
            at = local(today - timedelta(days=back), hour, 7)
            if at <= now:
                f.look(at)
    tonight = today - timedelta(days=1) if now.astimezone(home.PACIFIC).hour < 21 else today
    hub = f.incident('hub_down', 'hub', 'Hub isn’t answering', local(tonight, 23, 12), local(tonight, 23, 15), 'fixed',
                     steps=[(local(tonight, 23, 14), 'mac.kickstart', 'done', 'restart Hub')],
                     detail={'loaded': True})
    f.event(local(tonight, 23, 15), 'hub', 'fixed', 'Hub stopped answering at 11:12 PM. The caretaker restarted it; it was back at 11:15 PM.', hub)
    after = tonight + timedelta(days=1)
    lamp = f.incident('hue_lamp_out', 'hue', 'Reading lamp is off the Hue network', local(after, 2, 10), local(after, 5, 40),
                      'by_itself', detail={'name': 'Reading lamp'})
    f.event(local(after, 5, 40), 'hue', 'by_itself', 'Reading lamp was off the Hue bridge from 2:10 to 5:40 AM; it came back by itself.', lamp)
    sonos = f.incident('ha_unsteady', 'home_assistant', 'Sonos keeps failing', local(after, 1, 5), needs=(
        'Sonos still fails after two reloads. Restarting Home Assistant may clear it.'), ask='ha.restart',
        today_from=(now - timedelta(minutes=1)) if released else local(after, 4, 15), cause='Sonos failed to start (Timed out connecting).',
        steps=[(local(after, 1, 8), 'ha.reload', 'done', 'reload Sonos'), (local(after, 1, 18), 'ha.reload', 'done', 'reload Sonos')])
    f.event(local(after, 1, 20), 'home_assistant', 'needs_you', 'Sonos still fails after two reloads. Restarting Home Assistant may clear it.', sonos)
    for back, kind, system, title, tone, text in (
            (5, 'internet_down', 'network', 'The internet dropped', 'by_itself', 'The internet dropped at 2:41 AM and came back by itself after four minutes.'),
            (3, 'service_down', 'kitchen', 'The kitchen service isn’t answering', 'fixed', 'The kitchen service stopped answering at 9:20 AM. The caretaker restarted it; it was back at 9:22 AM.'),
            (2, 'hue_lamp_out', 'hue', 'Porch lantern is off the Hue network', 'by_itself', 'Porch lantern was off the Hue bridge from 7:05 to 7:48 PM; it came back by itself.')):
        day = today - timedelta(days=back)
        start = local(day, 2 if back == 5 else 9 if back == 3 else 19, 41 if back == 5 else 20 if back == 3 else 5)
        iid = f.incident(kind, system, title, start, start + timedelta(minutes=4 if back != 2 else 43),
                         'fixed' if tone == 'fixed' else 'by_itself')
        f.event(start + timedelta(minutes=4), system, tone, text, iid)
    for key, title, words in STURDIER:
        f.run('insert into sturdier(key, title, words, evidence, first_seen, last_seen) values(?,?,?,?,?,?)',
              key, title, words, '{}', iso(now - timedelta(days=3)), iso(now))
    f.status(now)
    return f


class Caretaker:
    """For the demo: answers Hub's request notes the way the caretaker would, and plays a drill when one is asked
    for, writing it into the journal step by step so the screen can follow. Touches nothing but the folder."""

    def __init__(self, folder, pace=1.5, released=True):
        self.f, self.pace, self.released = folder, pace, released
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def loop(self):
        while True:
            try:
                for note_path in sorted((self.f.path / 'queue').glob('*.json')):
                    try:
                        note = json.loads(note_path.read_text())
                    except (OSError, ValueError):
                        note = None
                    note_path.unlink(missing_ok=True)
                    if isinstance(note, dict):
                        self.answer(note)
                self.f.status(datetime.now(timezone.utc))
            except (OSError, sqlite3.Error):
                pass                # a reset in the middle of a pass; the next one picks up
            time.sleep(1.0)

    def quiet(self):
        """Nothing open: for the demo's quiet day."""
        self.f.run("update incidents set status = 'closed', outcome = 'done', closed_at = ? where status != 'closed'",
                   iso(datetime.now(timezone.utc)))

    def reset(self):
        """The made-up night as it started, for the browser check's next round."""
        path, old = self.f.path, self.f
        old.conn.close()
        for name in ('caretaker.db', 'caretaker.db-wal', 'caretaker.db-shm', 'status.json'):
            (path / name).unlink(missing_ok=True)
        for note in (path / 'queue').glob('*'):
            note.unlink(missing_ok=True)
        self.f = night(path, released=self.released)

    def answer(self, note):
        now = datetime.now(timezone.utc)
        do, target = note.get('do'), str(note.get('target', ''))
        words = 'Done.'
        if do == 'drill':
            threading.Thread(target=self.drill, args=(target,), daemon=True).start()
            words = f'Running the {target} drill.'
        elif do == 'pause':
            self.f.status(now, paused=True, paused_until='forever' if target == 'forever' else iso(now + timedelta(minutes=int(target or 60))))
            words = 'Paused.'
        elif do == 'resume':
            self.f.status(now, paused=False, paused_until=None)
            words = 'Watching and fixing again.'
        elif target.startswith('sturdier:'):
            key = target.split(':', 1)[1]
            self.f.run('update sturdier set done_at = ? where key = ?' if do == 'done' else 'update sturdier set gone_at = ? where key = ?',
                       iso(now), key)
            words = 'Noted.'
        else:
            iid = int(target.split('-')[-1])
            if do == 'fix':
                self.f.run("insert into steps(incident, at, action, target, status, words, request) values(?,?,?,?,?,?,?)",
                           iid, iso(now), 'ha.restart', '', 'done', 'restart Home Assistant', note.get('id'))
                self.f.run("update incidents set status = 'closed', outcome = 'fixed', closed_at = ? where id = ?", iso(now), iid)
                self.f.event(now, 'home_assistant', 'fixed', 'Sonos kept failing. The caretaker restarted Home Assistant at your tap; it came back.', iid)
                words = 'Restarted Home Assistant.'
            else:
                self.f.run("update incidents set status = 'closed', outcome = ? where id = ?", 'done' if do == 'done' else 'ignored', iid)
                words = 'Marked as sorted.' if do == 'done' else 'Left alone from now on.'
        self.f.run('insert or ignore into requests(id, received_at, do, target, by, status, words) values(?,?,?,?,?,?,?)',
                   note.get('id'), iso(now), do, target, note.get('by'), 'accepted', words)

    def drill(self, name):
        words = dict(DRILLS).get(name, '')
        start = datetime.now(timezone.utc)
        script = [(0, None, 'Hub isn’t answering'), (1, ('wait', 'waiting', 'An update just ended; waiting for it before touching Hub.'), None),
                  (2, ('mac.bootstrap', 'drill', 'start Hub again'), None), (3, ('check', 'checked', 'Looked again: it is working.'), None)]
        iid = None
        for minute, step, title in script:
            now = datetime.now(timezone.utc)
            if title:
                iid = self.f.incident('hub_down', 'hub', title, now, drill=name)
            elif step:
                action, status, said = step
                self.f.run('insert into steps(incident, at, action, target, status, words, drill) values(?,?,?,?,?,?,?)',
                           iid, iso(now), action, 'hub', status, said, name)
            self.f.status(now, drill={'scenario': name, 'words': words, 'minute': minute, 'of': len(script) - 1, 'done': False})
            time.sleep(self.pace)
        now = datetime.now(timezone.utc)
        self.f.run("update incidents set status = 'closed', outcome = 'fixed', closed_at = ? where id = ?", iso(now), iid)
        self.f.status(now, drill={'scenario': name, 'words': words, 'minute': len(script) - 1, 'of': len(script) - 1, 'done': True,
                                  'started': iso(start)})
