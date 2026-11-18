"""The AI spend dashboard: what Hub's models cost this month against each limit, how the runs went, and Ask's last
live check.

It replaces the older Insights page (`/insights` opens this board). Its inbox charts were already on the Mail board,
and its network summary on the Network board; what was left is here. It reads Hub's own record of runs
(`hub/insights.spend`) and the newest live check (`hub/golden/runner.latest`): counts and dollars only, never text.

The household's: every member sees it, as they saw Insights.
"""
from ... import people

ID = 'spend'
NAME = 'AI spend'
WHAT = 'What Hub’s AI costs against its limits, and Ask’s last check'


def sees(login):
    return people.who(login) is not None


def _spend():
    from ... import insights
    try:
        return insights.spend()
    except Exception:  # noqa: BLE001 - the board still opens and says so
        return None


def _checked():
    from ...golden import runner
    try:
        return runner.latest()
    except Exception:  # noqa: BLE001
        return None


def _tone(state):
    return {'bad': 'bad', 'warn': 'warn'}.get(state, '')


def picture(login, today):
    found = _spend()
    if found is None:
        return {'say': 'Can’t read Hub’s record of runs', 'sub': 'Try again in a minute.',
                'blocks': [{'id': 'how', 'title': 'AI spend', 'kind': 'note', 'text': 'Hub couldn’t read its record of runs just now.'}]}
    meters = found['meters']
    worst = max(meters, key=lambda m: ('ok', 'warn', 'bad').index(m['state']))
    say = (f"{worst['label']}: {worst['words'].rstrip('.').lower()}" if worst['state'] != 'ok'
           else f"Within limits in {found['month']}")
    checked = _checked()
    check_rows = []
    if checked:
        passed = f"{checked['passed']} of {checked['total']} passed"
        check_rows.append({'title': f"Last live check, {checked['when']}", 'sub': passed + (f", failed: {', '.join(checked['failed'][:5])}" if checked['failed'] else ''),
                           'tone': 'warn' if checked['failed'] else ''})
    blocks = [
        {'id': 'month', 'title': f"This month, {found['month']}", 'kind': 'tiles', 'tiles': [
            {'value': m['spent'], 'label': f"{m['label'].lower()}, of {m['cap']}", 'tone': _tone(m['state'])} for m in meters]},
        {'id': 'kinds', 'title': 'Cost by kind, last 30 days', 'kind': 'bars',
         'bars': [{'label': r['name'], 'value': r['dollars'], 'text': r['cost']} for r in found['rows']],
         'empty': 'No model runs recorded in the last 30 days.'},
        {'id': 'runs', 'title': 'Runs, last 30 days', 'kind': 'rows',
         'rows': [{'title': r['name'], 'sub': f"{r['runs']} runs, {r['calls']} model calls" + (f", {r['failed']} didn’t finish" if r['failed'] else ''),
                   'tone': 'warn' if r['failed'] else ''} for r in found['rows']],
         'empty': 'No model runs recorded in the last 30 days.'},
        {'id': 'checks', 'title': 'Ask’s live check', 'kind': 'rows', 'rows': check_rows, 'empty': 'No live check has run yet.'},
        {'id': 'more', 'title': 'More', 'kind': 'rows', 'rows': [
            {'title': 'Engine', 'sub': 'Limits, each run, and what Hub may do without asking', 'href': '/app/#engine'}]},
    ]
    return {'say': say, 'sub': ' · '.join(f"{m['label']} {m['spent']} of {m['cap']}" for m in meters), 'blocks': blocks}


def line(login, today):
    found = _spend()
    if not found:
        return None
    near = [m for m in found['meters'] if m['state'] != 'ok']
    if not near:
        return None
    worst = near[-1] if near[-1]['state'] == 'bad' else near[0]
    return {'text': f"AI spend: {worst['label']} {worst['words'].rstrip('.').lower()}", 'next': f"{worst['spent']} of {worst['cap']}", 'tone': _tone(worst['state'])}


def facts(login, today):
    found = _spend()
    if not found:
        return {'note': 'Hub can’t read its record of runs right now.'}
    return {'month': found['month'], 'limits': [{'what': m['label'], 'spent': m['spent'], 'limit': m['cap'], 'percent': m['percent']} for m in found['meters']]}
