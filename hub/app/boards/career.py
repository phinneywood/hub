"""The career dashboard: one person's career desk (hub/app/career/desk.py) as their page.

Each member has their own desk, and this board shows each person only theirs: the other member never sees it, and
neither do the wall or programs on the Mac. What is on it is read from the desk's Markdown files, in code; opening
the board calls no model. Ask steers the desk (the `career` tool: note, tick, research, draft, status); here a
person can tick the plan's steps and decide whether Hub's model may read the desk at all.
"""
from ..career import desk

ID = 'career'
NAME = 'Career'
WHAT = 'Your thesis, your plan, what you are writing and the desk’s field notes'
ROWS = 6
STATUS = {'idea': 'Idea', 'draft': 'Draft', 'ready': 'Ready', 'published': 'Published'}
JOB = {'waiting': 'Waiting', 'working': 'Working on it', 'done': 'Done', 'failed': 'Didn’t finish', 'refused': 'Not run'}


class Refused(Exception):
    """A tap that can't be done as asked; the words are for the person."""


def sees(login):
    return login is not None and desk.account(login) is not None


def picture(login, today):
    found = desk.picture(login)
    if not found.get('ok'):
        return {'say': 'No desk here', 'sub': found.get('said', ''), 'blocks': []}
    blocks = []
    if found['thesis'] or found['headline']:
        blocks.append({'id': 'thesis', 'title': 'Thesis', 'kind': 'hero', 'big': found['headline'] or found['thesis'],
                       'small': found['thesis'] if found['headline'] else ''})
    for n, section in enumerate(found['plan']):
        if not section['steps']:
            continue
        blocks.append({'id': f'plan-{n}', 'title': section['title'] or 'The plan', 'kind': 'checks', 'sub': section['about'],
                       'items': [{'id': s['key'], 'title': s['plain'], 'sub': '', 'done': s['done'], 'tone': ''} for s in section['steps']]})
    writing = found.get('writing') or []
    blocks.append({'id': 'writing', 'title': 'Writing', 'kind': 'rows', 'empty': 'Nothing in progress. Ask Hub to draft a piece about a topic.',
                   'rows': [{'title': w['title'], 'sub': ' · '.join(x for x in (w.get('form', ''), w.get('date') or '') if x),
                             'right': STATUS.get(w.get('status', 'draft'), 'Draft'),
                             'tone': 'warn' if w.get('flags') else ''} for w in writing[:ROWS]]})
    field = found.get('field') or []
    blocks.append({'id': 'field', 'title': 'Field notes', 'kind': 'rows', 'empty': 'None yet. Ask Hub to look into a topic for your career.',
                   'rows': [{'title': f['title'], 'sub': f['summary'][:140], 'right': f.get('date') or ''} for f in field[:ROWS]]})
    if found['jobs']:
        blocks.append({'id': 'jobs', 'title': 'The desk is working on', 'kind': 'rows',
                       'rows': [{'title': j['topic'], 'sub': 'Research' if j['kind'] == 'research' else 'A first draft',
                                 'right': JOB.get(j['status'], j['status'])} for j in found['jobs']]})
    if found['notes']:
        blocks.append({'id': 'notes', 'title': 'Your notes', 'kind': 'rows',
                       'rows': [{'title': n['plain'][:160], 'sub': n['at']} for n in found['notes']]})
    work = found.get('work') or []
    if work:
        blocks.append({'id': 'work', 'title': 'Evidence from your work', 'kind': 'rows',
                       'rows': [{'title': w['title'], 'right': w.get('date') or ''} for w in work[:ROWS]]})
    spent = found['budget']
    blocks.append({'id': 'about', 'title': 'About the desk', 'kind': 'note',
                   'text': f'Your desk is yours alone: the files are in {found["where"]}. Hub never publishes, posts, applies or '
                           f'contacts anyone. Its research and drafts this month: ${spent["spent"]:.2f} of ${spent["cap"]:.2f}.'})
    say = (f'{found["open"]} step{"s" * (found["open"] != 1)} of your plan open' if found['open']
           else 'Your desk is empty so far' if found['empty'] else 'Every step of your plan is done')
    sub = 'Tell Ask what to note, look into or draft.' if found['empty'] else (found['next'][0]['plain'] if found['next'] else '')
    return {'say': say, 'sub': sub, 'blocks': blocks,
            'settings': [{'key': 'model', 'label': 'Hub’s model may read my desk', 'type': 'switch', 'value': 'on' if found['model'] else ''}]}


def tick_for(login, item, done):
    try:
        desk.tick(login, item, done)
    except (desk.NoDesk, desk.Refused) as problem:
        raise Refused(str(problem)) from problem
    return 'Ticked' if done else 'Put back'


def set_for(login, key, value):
    if key != 'model':
        raise Refused('Hub couldn’t read that request.')
    try:
        return desk.set_model_reads(login, str(value or '').strip().lower() in ('on', 'true', '1', 'yes'))
    except desk.NoDesk as problem:
        raise Refused(str(problem)) from problem


def line(login, today):
    found = desk.picture(login)
    if not found.get('ok') or found['empty'] or not found['next']:
        return None
    return {'text': f'Next: {found["next"][0]["plain"][:90]}', 'next': f'{found["open"]} open', 'tone': ''}


def facts(login, today):
    """What Ask's model may be given about the desk: the desk's own reading, which honours the person's switch."""
    return desk.for_model(login, '')
