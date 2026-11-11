"""Evidence-first network pages; only local reads and Hub-owned preferences."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...templating import render
from . import analytics as a, ha, store

router = APIRouter()
RANGE_OPTIONS = ('6h', '24h', '7d', '30d')
RANGE_LABELS = {'6h': '6 hours', '24h': '24 hours', '7d': '7 days', '30d': '30 days'}
ROLE_LABELS = {'wan': 'Router WAN link', 'reachability': 'External reachability', 'latency': 'External ping latency',
               'loss': 'External packet loss', 'download_rate': 'Gateway download traffic', 'upload_rate': 'Gateway upload traffic',
               'download_total': 'Gateway received bytes', 'upload_total': 'Gateway sent bytes'}


def _history(entity_ids, start, *, include_attributes=False, end=None):
    ids = list(dict.fromkeys(entity_ids))[:ha.MAX_HISTORY_ENTITIES]
    if not ids:
        return {}, None
    try:
        groups = ha.history(ids, start, end, include_attributes=include_attributes)
        mapped = {ident: [] for ident in ids}
        for group in groups:
            # HA may omit or reorder groups; minimal rows inherit their group's ID.
            ident = next((row.get('entity_id') for row in group if row.get('entity_id')), None)
            if ident in mapped and all(not row.get('entity_id') or row['entity_id'] == ident for row in group):
                mapped[ident].extend(group)
        return mapped, None
    except ha.HomeAssistantError:
        return {}, 'Recorder history unavailable. Current readings may still be available.'


def _range(key):
    return key if key in RANGE_OPTIONS else '24h'


def page(request, name, **context):
    return render(request, 'network/' + name + '.html', active='network',
                  range_options=RANGE_OPTIONS, range_labels=RANGE_LABELS, role_labels=ROLE_LABELS, **context)


def _device_rows(states):
    devices, metrics = a.classify(states)
    expected = set(store.get('expected', []))
    now = datetime.now(timezone.utc)
    for device in devices:
        device['expected'] = device['kind'] == 'node' or device['entity_id'] in expected
        device['fresh'] = a.fresh(device, now)
        device['status'] = ('Unknown / old report' if not device['fresh'] or not device['known'] else
                            'Online' if device['online'] else 'Offline')
        device['attention'] = device['expected'] and (not device['online'] or not device['fresh'] or not device['known'])
        device['download_text'] = a.format_rate(a.device_speed(device, 'download')) if device['fresh'] and device['online'] else '—'
        device['upload_text'] = a.format_rate(a.device_speed(device, 'upload')) if device['fresh'] and device['online'] else '—'
        device['updated_text'] = a.local_time(a.parse_time(device.get('last_reported') or device['last_updated']))
    return devices, metrics


def _node_diagnostics(nodes, states):
    """Associate only uniquely matching node sensors, never use them as internet probes."""
    for node in nodes:
        node['diagnostics'] = list(node['diagnostics'])
    for item in states:
        ident = item['entity_id']
        if not ident.startswith('sensor.'):
            continue
        name = a.title(item).casefold()
        matches = [node for node in nodes if ident.startswith('sensor.' + node['entity_id'].split('.', 1)[1] + '_')
                   or name.startswith(node['name'].casefold() + ' ')]
        text = a.entity_text(item).replace('_', ' ')
        if len(matches) != 1 or not any(word in text for word in ('cpu', 'memory', 'backhaul', 'response time', 'timeout', 'connected clients')):
            continue
        raw = a.clean(item.get('state'), 64)
        unit = a.clean((item.get('attributes') or {}).get('unit_of_measurement'), 20)
        matches[0]['diagnostics'].append({'label': a.title(item), 'value': raw + (' ' + unit if unit else '')})


# Up/down and loss sensors only re-report when their value changes, so a steady
# "on" or "0 %" ages past the freshness guard. A fresh reading from the companion
# measurement (the same probe's latency; the router's traffic counters) shows the
# source is still reporting, so the unchanged value is current.
CORROBORATION = {'reachability': ('latency',), 'loss': ('latency',),
                 'wan': ('download_rate', 'upload_rate', 'download_total', 'upload_total')}


def current(role, sources, now=None):
    metric = sources.get(role)
    if not metric:
        return False
    if a.fresh(metric, now):
        return True
    return any(a.fresh(sources.get(other), now) for other in CORROBORATION.get(role, ()))


def _source_status(metric, is_current=None):
    if not metric:
        return 'Not measured'
    if not (a.fresh(metric) if is_current is None else is_current):
        return 'Old / unverified report'
    state = a.connection_state({'state': metric.get('state')})
    return {'up': 'Reported up', 'down': 'Reported down', 'unknown': 'Unavailable'}[state]


def _with_stale_tail(rows, metric, end, is_current=None):
    if metric and not (a.fresh(metric, end) if is_current is None else is_current):
        last = a.parse_time(metric.get('last_reported') or metric.get('last_updated'))
        if last:
            return rows + [{'state': 'unknown', 'last_updated': min(end, last + timedelta(minutes=5)).isoformat(),
                            'entity_id': metric['entity_id']}]
        return []
    return rows


def _chart(rows, metric, start, end):
    if not metric:
        return a.chart([], {'kind': 'value'}, start=start, end=end)
    if metric['kind'].endswith('_total'):
        derived, previous = [], None
        for row in sorted(rows, key=lambda r: a.history_time(r) or start):
            when, val = a.history_time(row), a.bytes_value(row.get('state'), metric.get('unit'))
            if val is not None and val < 0:
                val = None
            if not when:
                continue
            if previous and val is not None and previous[1] is not None:
                delta, elapsed = val - previous[1], (when - previous[0]).total_seconds()
                speed = delta * 8 / elapsed / 1e6 if delta >= 0 and elapsed > 0 else None
                derived.append({'last_updated': when.isoformat(), 'state': speed})
            previous = when, val
        return a.chart(derived, {'kind': 'download_rate', 'unit': 'Mbit/s'}, start=start, end=end)
    if metric['kind'] == 'latency':
        rows = [{**row, 'state': a.value({**metric, 'state': row.get('state')})} for row in rows]
    return a.chart(rows, metric, unit='ms' if metric['kind'] == 'latency' else metric.get('unit') or 'Mbit/s', start=start, end=end)


def _usage(rows, metric, start, end):
    if not metric:
        return 'Not measured'
    ordered = sorted((r for r in rows if a.history_time(r) and a.history_time(r) <= end), key=a.history_time)
    within = [r for r in ordered if a.history_time(r) >= start]
    result = a.counter_usage(within, metric)
    # Counters bracket only observed samples; do not extrapolate through retention loss.
    complete = bool(within and a.history_time(within[0]) <= start + timedelta(minutes=5)
                    and a.history_time(within[-1]) >= end - timedelta(minutes=5) and result['complete'])
    return a.format_bytes(result['bytes']) + (' observed' if complete else ' minimum · partial/reset data') if len(within) >= 2 else 'Insufficient history'


def overview(selected):
    """Everything the overview shows, as plain values. The Network page and the Insights section both render it."""
    if not ha.configured():
        return dict(configured=False, connected=False, error=None, selected_range=selected)
    try:
        states = ha.states()
    except ha.HomeAssistantError as exc:
        return dict(configured=True, connected=False, error=str(exc), selected_range=selected)
    now = datetime.now(timezone.utc)
    days = a.RANGE_DAYS[selected]
    start = now - timedelta(days=days)
    devices, metrics = _device_rows(states)
    choices = store.get('sources', {})
    sources, notes = a.select_sources(states, metrics, choices)
    ids = [m['entity_id'] for m in sources.values()]
    # Read the preceding period for comparison, within the same 30-day API bound.
    fetch_start = start - timedelta(days=days) if days <= 7 else start
    histories, history_error = _history(ids, fetch_start, include_attributes=any(m.get('attribute') for m in sources.values()), end=now)
    def rows(role):
        return histories.get(sources.get(role, {}).get('entity_id'), [])
    live = {role: current(role, sources, now) for role in sources}
    summaries = {role: a.outage_summary(_with_stale_tail(rows(role), sources.get(role), now, live.get(role)), sources.get(role, {}), days, end=now)
                 for role in ('wan', 'reachability')}
    summary_role = 'reachability' if 'reachability' in sources else 'wan'
    summary = summaries[summary_role]
    previous = a.outage_summary(rows(summary_role), sources.get(summary_role, {}), days, end=start) if days <= 7 else None
    comparison = 'Previous-period comparison unavailable: incomplete history.'
    if previous and previous['known'] and summary['known']:
        delta = summary['reliability'] - previous['reliability']
        comparison = f'{delta:+.2f} percentage points availability vs preceding {RANGE_LABELS[selected]}.'
    elif days == 30:
        comparison = '30-day comparisons need more than the permitted Recorder query window.'
    attention = []
    def note(title, evidence, action, level='warn'):
        attention.append({'title': title, 'evidence': evidence, 'action': action, 'level': level})
    wan_status = _source_status(sources.get('wan'), live.get('wan', False))
    reach_status = _source_status(sources.get('reachability'), live.get('reachability', False))
    if wan_status == 'Reported down':
        note('Router reports its WAN link down', 'This is a router report, not an ISP diagnosis.', 'Check the modem/ONT and router WAN cable/status before changing Wi-Fi.', 'bad')
    if reach_status == 'Reported down':
        note('External probe reports unreachable', 'The selected target failed; one target does not prove every internet service is down.', 'Compare another service and router WAN status. Check DNS separately if names fail.', 'bad')
    values = {role: a.value(metric) if live.get(role) else None for role, metric in sources.items()}
    if values.get('latency') is not None and values['latency'] > 100:
        note('Elevated latency to the selected target', f"Current ping is {values['latency']:.1f} ms (fixed 100 ms advisory threshold).", 'Compare traffic at the same time; try a wired device before attributing this to Wi-Fi.')
    if values.get('loss') is not None and values['loss'] > 0:
        note('Packet loss reported', f"Selected probe reports {values['loss']:.1f}% loss.", 'Check whether loss affects multiple devices and wired connections; one probe cannot locate the fault.')
    old = [ROLE_LABELS[role] for role, metric in sources.items() if not live.get(role)]
    if old:
        note('Some source reports are old or unverified', ', '.join(old), 'Check integration polling and availability in Home Assistant. A page refresh does not refresh the router.')
    if 'reachability' not in sources:
        note('Internet usability is not independently measured', 'WAN link status alone cannot establish internet or DNS health.', 'Select an existing external probe under Sources. Hub will not create a probe.')
    if history_error:
        note('History could not be read', history_error, 'Check Recorder availability and reduce the time range; no network restart is needed.')
    elif summary['coverage'] < 99.99:
        note('History includes unobserved periods', f"{summary['coverage']:.1f}% recorded-state coverage; {summary['unknown_text']} unknown.", 'Do not interpret missing monitoring data as uptime. Check Recorder retention and integration availability.')
    for device in devices:
        if device['attention']:
            note(device['name'] + ': ' + device['status'], 'Expected to stay connected.' if device['kind'] == 'client' else 'Mesh node report needs checking.', 'Open its history and compare other clients on the same node. This is not proof of device failure.')
    missing = set(store.get('expected', [])) - {d['entity_id'] for d in devices}
    if missing:
        note('Expected clients missing from the source', f'{len(missing)} expected client entities are no longer returned by Home Assistant.', 'Check disabled/renamed entities and the client integration; absence from the inventory is not a proven disconnect.')
    # Bound drill-down history to mesh nodes and explicitly expected clients.
    tracked = [d for d in devices if d['kind'] == 'node'][:4] + [d for d in devices if d['kind'] == 'client' and d['expected']][:4]
    device_histories, device_history_error = _history([d['entity_id'] for d in tracked], start, include_attributes=False, end=now)
    device_incidents = []
    for device in tracked:
        report = a.outage_summary(_with_stale_tail(device_histories.get(device['entity_id'], []), device, now), {}, days, end=now)
        if report['outages'] >= 3:
            note(device['name'] + ': repeated offline reports', f"{report['outages']} recorded offline intervals in {RANGE_LABELS[selected]}.", 'Compare the timeline with WAN incidents and other clients; sleep/presence policies can still affect these reports.')
        for incident in report['incidents']:
            shared = incident['state'] == 'down' and any(i['state'] == 'down' and i['start'] < incident['end'] and i['end'] > incident['start'] for i in summary['intervals'])
            device_incidents.append({**incident, 'source': device['name'], 'url': '/network/device/' + device['entity_id'],
                                     'shared': shared})
    if device_history_error:
        note('Node/client history unavailable', device_history_error, 'Use current device reports cautiously; no recurring-device diagnosis is available.')
    if not any(d['kind'] == 'client' for d in devices):
        note('Per-device visibility is not connected', 'No network-client trackers detected; WAN totals cannot identify heavy users.', 'See Mesh for the optional Deco integration and its firmware/session limitations.', 'unknown')
    cards = []
    for direction in ('download', 'upload'):
        metric = sources.get(direction + '_rate') or sources.get(direction + '_total')
        graph = _chart(histories.get(metric['entity_id'], []) if metric else [], metric, start, now)
        cards.append({'title': direction.title() + ' traffic', 'graph': graph, 'unit': 'Mbit/s',
                      'current': a.format_rate(values.get(direction + '_rate')), 'usage': _usage(rows(direction + '_total'), sources.get(direction + '_total'), start, now)})
    for role, label, unit in (('latency', 'External latency', 'ms'), ('loss', 'External packet loss', '%')):
        metric = sources.get(role)
        cards.append({'title': label, 'graph': _chart(rows(role), metric, start, now), 'unit': unit,
                      'current': f'{values[role]:.1f} {unit}' if values.get(role) is not None else 'Not measured / unavailable', 'usage': ''})
    source_rows = []
    for role in store.ROLES:
        metric = sources.get(role)
        source_rows.append({'role': role, 'metric': metric, 'note': notes.get(role, ''), 'choice': choices.get(role, ''),
                            'fresh': a.fresh(metric, now), 'steady': not a.fresh(metric, now) and current(role, sources, now), 'updated': a.local_time(a.parse_time((metric or {}).get('last_reported') or (metric or {}).get('last_updated'))),
                            'candidates': [item for item in states if a.source_candidate(item, role)]})
    client_rows = [d for d in devices if d['kind'] == 'client']
    top = sorted((d for d in client_rows if d['online'] and d['fresh'] and (d['download_kb'] is not None or d['upload_kb'] is not None)),
                 key=lambda d: -((d['download_kb'] or 0) + (d['upload_kb'] or 0)))[:5]
    return dict(configured=True, connected=True, error=None, selected_range=selected,
                wan_status=wan_status, reach_status=reach_status, attention=attention, cards=cards, summary=summary,
                summary_label='External reachability' if summary_role == 'reachability' else 'Router WAN report',
                comparison=comparison, incidents=summary['incidents'][:20], sources=source_rows,
                device_incidents=sorted(device_incidents, key=lambda i: i['start'], reverse=True)[:20], tracked_count=len(tracked),
                top=top, client_count=len(client_rows), node_count=sum(d['kind'] == 'node' for d in devices),
                online_count=sum(d['online'] and d['fresh'] and d['known'] for d in client_rows),
                unknown_count=sum(not d['fresh'] or not d['known'] for d in client_rows),
                updated=a.local_time(now), **_verdict(attention, wan_status, reach_status, values, 'reachability' in sources))


def _overview(request, selected):
    return page(request, 'home', **overview(selected))


def summary():
    """The overview's answer for another page: the verdict and the three tiles, as a block of HTML. None when there is none to give."""
    from markupsafe import Markup
    from ...templating import templates
    found = overview('24h')
    if not found['connected']:
        return None
    return Markup(templates.get_template('network/_summary.html').render(full=False, **found))


def _verdict(attention, wan_status, reach_status, values, has_reach):
    """Answer first: is the internet working? Caveats stay in the attention list."""
    primary = reach_status if has_reach else wan_status
    facts = []
    if values.get('latency') is not None:
        facts.append(f"{values['latency']:.0f} ms ping")
    if values.get('loss'):
        facts.append(f"{values['loss']:.0f}% packet loss")
    if has_reach and wan_status == 'Reported up':
        facts.append('router link up')
    worth = [i for i in attention if i['level'] in ('bad', 'warn')]
    if any(i['level'] == 'bad' for i in attention) or primary == 'Reported down':
        tone, headline = 'bad', 'Internet looks down' if primary == 'Reported down' else 'Reported network problems'
    elif primary == 'Reported up':
        tone, headline = ('warn' if worth else 'ok'), 'Internet is up'
    else:
        tone, headline = 'warn', 'Can’t confirm the internet right now'
        facts.append('readings are old or missing')
    return {'headline': headline, 'tone': tone, 'facts': ' · '.join(facts)}


@router.get('', response_class=HTMLResponse)
@router.get('/', response_class=HTMLResponse)
def home(request: Request, range: str = '24h'):
    return _overview(request, _range(range))


@router.post('/source')
def source(role: str = Form(...), entity_id: str = Form(''), external: str = Form('')):
    if role not in store.ROLES:
        raise HTTPException(400, 'Unknown measurement role')
    if entity_id not in ('', 'none'):
        try:
            states = ha.states()
        except ha.HomeAssistantError:
            raise HTTPException(502, 'Cannot verify that source right now; preferences were not changed') from None
        item = next((item for item in states if item['entity_id'] == entity_id), None)
        if not item or not a.source_candidate(item, role):
            raise HTTPException(400, 'Source is not compatible with that measurement')
        if role in ('reachability', 'latency', 'loss') and external != 'yes':
            raise HTTPException(400, 'Confirm this probe targets the external internet, not a LAN device/API')
    choices = store.get('sources', {})
    if entity_id:
        choices[role] = entity_id
    else:
        choices.pop(role, None)
    store.put('sources', choices)
    return RedirectResponse('/network#sources', status_code=303)


@router.post('/device/{entity_id}/expected')
def expected(entity_id: str, enabled: str = Form('0')):
    try:
        devices, _ = a.classify(ha.states())
    except ha.HomeAssistantError:
        raise HTTPException(502, 'Cannot verify that client right now; preferences were not changed') from None
    if not any(d['entity_id'] == entity_id and d['kind'] == 'client' for d in devices):
        raise HTTPException(404, 'Client not found')
    choices = set(store.get('expected', []))
    if enabled == '1':
        choices.add(entity_id)
    else:
        choices.discard(entity_id)
    store.put('expected', sorted(choices))
    return RedirectResponse('/network/device/' + entity_id, status_code=303)


@router.get('/devices', response_class=HTMLResponse)
def devices_page(request: Request, q: str = '', sort: str = 'attention', status: str = 'all'):
    q = q.strip()[:100]
    try:
        devices, _ = _device_rows(ha.states())
    except ha.HomeAssistantError as exc:
        return page(request, 'devices', connected=False, error=str(exc), devices=[], query=q, sort=sort, status=status)
    if q:
        devices = [d for d in devices if q.casefold() in ' '.join((d['name'], d['entity_id'], d['ip'], d['node'], d['connection'])).casefold()]
    if status == 'attention':
        devices = [d for d in devices if d['attention']]
    elif status == 'online':
        devices = [d for d in devices if d['status'] == 'Online']
    if sort == 'activity':
        devices.sort(key=lambda d: (not d['fresh'], not d['online'], -((d['download_kb'] or 0) + (d['upload_kb'] or 0))))
    elif sort == 'node':
        devices.sort(key=lambda d: (d['node'].casefold(), d['name'].casefold()))
    else:
        devices.sort(key=lambda d: (not d['attention'], d['name'].casefold()))
    return page(request, 'devices', connected=True, error=None, devices=devices, query=q, sort=sort, status=status)


@router.get('/mesh', response_class=HTMLResponse)
def mesh_page(request: Request):
    try:
        states = ha.states()
        devices, _ = _device_rows(states)
    except ha.HomeAssistantError as exc:
        return page(request, 'mesh', connected=False, error=str(exc), nodes=[], clients=[])
    nodes = [d for d in devices if d['kind'] == 'node']
    _node_diagnostics(nodes, states)
    clients = [d for d in devices if d['kind'] == 'client']
    for node in nodes:
        aliases = {node['name'].casefold(), node['entity_id'].split('.', 1)[-1].casefold()}
        node['clients'] = [d for d in clients if d['node'].casefold() in aliases]
        node['online_clients'] = sum(d['status'] == 'Online' for d in node['clients'])
    assigned = {d['entity_id'] for node in nodes for d in node['clients']}
    return page(request, 'mesh', connected=True, error=None, nodes=nodes, clients=clients,
                unassigned=[d for d in clients if d['entity_id'] not in assigned])


@router.get('/device/{entity_id}', response_class=HTMLResponse)
def device_page(request: Request, entity_id: str, range: str = '24h'):
    selected = _range(range)
    try:
        states = ha.states()
        devices, _ = _device_rows(states)
        _node_diagnostics([d for d in devices if d['kind'] == 'node'], states)
    except ha.HomeAssistantError as exc:
        return page(request, 'device', device=None, error=str(exc), selected_range=selected)
    device = next((d for d in devices if d['entity_id'] == entity_id), None)
    if not device:
        raise HTTPException(404, 'Network device not found')
    end = datetime.now(timezone.utc)
    start = a.range_start(selected, end)
    histories, error = _history([entity_id], start, end=end, include_attributes=True)
    rows = histories.get(entity_id, [])
    summary = a.outage_summary(_with_stale_tail(rows, device, end), {'kind': 'device'}, a.RANGE_DAYS[selected], end=end)
    charts = [{'title': label, 'graph': a.attribute_chart(rows, attribute, start=start, end=end)}
              for attribute, label in (('down_kilobytes_per_s', 'Download traffic'), ('up_kilobytes_per_s', 'Upload traffic'))]
    return page(request, 'device', device=device, error=error, selected_range=selected, summary=summary, charts=charts,
                incidents=summary['incidents'][:30])
