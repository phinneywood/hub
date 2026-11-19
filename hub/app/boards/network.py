"""The network dashboard: is the internet working, how fast, what dropped and what needs a look.

It reads exactly what the older Network page reads (`hub/tools/network/pages.overview`): Home Assistant's current
states and its recorder history for the sources chosen there, read-only. Nothing about devices goes to a model
except the few numbers in `facts`, and only when someone asks Ask about the network.

The network is the household's, so every member sees it.
"""
import time

from ... import people

ID = 'network'
NAME = 'Network'
WHAT = 'Whether the internet is up, its speed, drops and devices'
FRESH = 60                      # seconds one reading of Home Assistant is used for, between Today, Ask and the board
_kept = {'at': float('-inf'), 'found': None}


def sees(login):
    return people.who(login) is not None


def read():
    """The older page's overview for the last 24 hours, read at most once a minute."""
    from ...tools.network import pages
    if time.monotonic() - _kept['at'] > FRESH or _kept['found'] is None:
        _kept['found'] = pages.overview('24h')
        _kept['at'] = time.monotonic()
    return _kept['found']


def _tone(tone):
    return {'bad': 'bad', 'warn': 'warn'}.get(tone, '')


def _line_block(block_id, title, card):
    graph = card.get('graph') or {}
    return {'id': block_id, 'title': title, 'kind': 'line', 'segments': graph.get('segments') or [], 'view': [720, 180],
            'start': graph.get('start_label') or '', 'end': graph.get('end_label') or '', 'now': card.get('current') or '',
            'top': f"{graph['max']:.0f} {graph.get('unit', '')}".strip() if graph.get('max') is not None else '',
            'empty': 'No history for this in the last day.'}


# How "Right now" was decided, in plain words (tools/network/pages.py names the source).
CHECKED = {'External reachability': 'Checked by reaching the internet from home', 'Router WAN report': 'As the router reports it'}


def picture(login, today):
    found = read()
    if not found.get('configured'):
        return {'say': 'The network isn’t connected to Hub', 'sub': 'Hub reads the network from Home Assistant, which isn’t set up here.',
                'blocks': [{'id': 'older', 'title': 'Network', 'kind': 'rows', 'rows': [{'title': 'The older Network page', 'href': '/network'}]}]}
    if not found.get('connected'):
        return {'say': 'Can’t read the network right now', 'sub': 'Home Assistant didn’t answer. The internet itself may be fine.',
                'blocks': [{'id': 'older', 'title': 'Network', 'kind': 'rows', 'rows': [{'title': 'The older Network page', 'href': '/network'}]}]}
    cards = {c['title']: c for c in found['cards']}
    summary = found['summary']
    up = summary.get('reliability')
    blocks = [
        {'id': 'verdict', 'title': 'Right now', 'kind': 'hero', 'big': found['facts'] or found['headline'],
         'small': CHECKED.get(found['summary_label'], ''), 'tone': _tone(found['tone'])},
        # What the house is moving now, not how fast the line can go: it rises and falls with use.
        {'id': 'now', 'title': 'Traffic now', 'kind': 'tiles', 'tiles': [
            {'value': cards.get('Download traffic', {}).get('current') or '—', 'label': 'coming in'},
            {'value': cards.get('Upload traffic', {}).get('current') or '—', 'label': 'going out'},
            {'value': (cards.get('External latency', {}).get('current') or '—').replace('Not measured / unavailable', '—'), 'label': 'ping'},
            {'value': (cards.get('External packet loss', {}).get('current') or '—').replace('Not measured / unavailable', '—'), 'label': 'packet loss'}]},
        {'id': 'day', 'title': 'The last 24 hours', 'kind': 'tiles', 'tiles': [
            {'value': f'{up:.2f}%' if up is not None else '—', 'label': 'up' if up is not None else 'up (history incomplete)'},
            {'value': str(summary.get('outages', 0)), 'label': 'drops', 'tone': 'warn' if summary.get('outages') else ''},
            {'value': summary.get('downtime_text') or '0', 'label': 'offline in all'},
            {'value': f"{found['online_count']} of {found['client_count']}" if found['client_count'] else '—', 'label': 'devices online'}]},
        {'id': 'attention', 'title': 'Worth a look', 'kind': 'rows',
         'rows': [{'title': n['title'], 'sub': n['evidence'], 'tone': _tone(n['level'])} for n in found['attention'][:6]],
         'empty': 'Nothing needs a look.'},
        _line_block('download', 'Download, last 24 hours', cards.get('Download traffic', {})),
        _line_block('latency', 'Ping, last 24 hours', cards.get('External latency', {})),
        {'id': 'drops', 'title': 'Drops', 'kind': 'rows',
         'rows': [{'title': i['label'], 'sub': f"{i['when']}, {i['duration']}" + (' (still going)' if i.get('ongoing') else ''),
                   'tone': 'bad' if i['label'] == 'Reported offline' else ''} for i in found['incidents'][:6]]
         + [{'title': f"{i['source']}: {i['label'].lower()}", 'sub': f"{i['when']}, {i['duration']}", 'href': i['url']}
            for i in found['device_incidents'][:4]],
         'empty': 'No drops in the last 24 hours.'},
        {'id': 'busiest', 'title': 'Busiest devices now', 'kind': 'rows',
         'rows': [{'title': d['name'], 'sub': f"Down {d['download_text']} · up {d['upload_text']}"} for d in found['top']],
         'empty': 'Hub can’t see per-device traffic.'},
        {'id': 'more', 'title': 'More', 'kind': 'rows', 'rows': [
            {'title': 'The full Network page', 'sub': 'Charts for 7 and 30 days, the mesh, every device and where readings come from', 'href': '/network'}]},
    ]
    sub = f"{found['node_count']} mesh points, {found['client_count']} devices. Read at {found['updated']}." if found['node_count'] else f"Read at {found['updated']}."
    return {'say': found['headline'], 'sub': sub, 'blocks': blocks}


def line(login, today):
    found = read()
    if not found.get('connected'):
        return None
    if found['tone'] == 'ok':
        return {'text': found['headline'], 'next': found['facts'], 'tone': ''}
    worst = found['attention'][0]['title'] if found['attention'] else ''
    return {'text': found['headline'], 'next': worst, 'tone': _tone(found['tone'])}


def facts(login, today):
    found = read()
    if not found.get('connected'):
        return {'note': 'Hub can’t read the network right now.' if found.get('configured') else 'The network isn’t connected to Hub.'}
    cards = {c['title']: c['current'] for c in found['cards']}
    summary = found['summary']
    return {'verdict': found['headline'], 'details': found['facts'], 'download_now': cards.get('Download traffic'),
            'upload_now': cards.get('Upload traffic'), 'ping': cards.get('External latency'), 'packet_loss': cards.get('External packet loss'),
            'up_last_24h_percent': round(summary['reliability'], 2) if summary.get('reliability') is not None else None,
            'drops_last_24h': summary.get('outages', 0), 'offline_last_24h': summary.get('downtime_text'),
            'devices_online': found['online_count'], 'devices_seen': found['client_count'],
            'worth_a_look': [n['title'] for n in found['attention'][:5]]}
