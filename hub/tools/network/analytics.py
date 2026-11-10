"""Normalize Home Assistant network entities and build bounded chart data."""
import math
import ipaddress
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

RANGE_DAYS = {"6h": 0.25, "24h": 1, "7d": 7, "30d": 30}
RATE_UNITS = {
    "bit/s": 1e-6, "bits/s": 1e-6, "bps": 1e-6,
    "kbit/s": 1e-3, "kbps": 1e-3, "mbit/s": 1, "mbps": 1,
    "gbit/s": 1e3, "gbps": 1e3,
    "b/s": 8e-6, "byte/s": 8e-6, "bytes/s": 8e-6,
    "kb/s": 0.008, "kb/s (decimal)": 0.008, "kbyte/s": 0.008,
    "kbytes/s": 0.008, "kib/s": 8192 / 1e6, "kibibyte/s": 8192 / 1e6,
    "mb/s": 8, "mbyte/s": 8, "mbytes/s": 8,
    "mib/s": 8 * 1024 * 1024 / 1e6, "gib/s": 8 * 1024 * 1024 * 1024 / 1e6,
}
BYTE_UNITS = {"b": 1, "byte": 1, "bytes": 1,
              "kb": 1000, "kbyte": 1000, "kbytes": 1000,
              "kib": 1024, "kibibyte": 1024,
              "mb": 1_000_000, "mbyte": 1_000_000, "mbytes": 1_000_000,
              "mib": 1024 ** 2, "mebibyte": 1024 ** 2,
              "gb": 1_000_000_000, "gbyte": 1_000_000_000, "gbytes": 1_000_000_000,
              "gib": 1024 ** 3, "teb": 1024 ** 4, "tb": 1_000_000_000_000}
TEXT_LIMIT = 180


def clean(value, limit=TEXT_LIMIT):
    return "".join(ch for ch in str(value if value is not None else "") if ord(ch) >= 32 and ord(ch) != 127)[:limit]


def number(value):
    if isinstance(value, bool):
        return None
    try:
        found = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return found if math.isfinite(found) else None


def state_value(item):
    return number(item.get("state"))


def title(item):
    attrs = item.get("attributes") or {}
    return clean(attrs.get("friendly_name") or item.get("entity_id"))


def entity_text(item):
    return (str(item.get("entity_id", "")) + " " + str((item.get("attributes") or {}).get("friendly_name", ""))).lower()


def _network_rank(item):
    text = entity_text(item)
    if str(item.get("entity_id", "")).startswith("device_tracker."):
        return 2
    if any(word in text for word in ("upnp", "igd", "wan", "internet", "router", "deco", "be11000")):
        return 0
    return 1


def client_or_node(item):
    if str(item.get("entity_id", "")).split(".", 1)[0] != "device_tracker":
        return None
    attrs = item.get("attributes") or {}
    dtype = str(attrs.get("device_type", "")).casefold()
    connection = str(attrs.get("connection_type", "")).casefold()
    source = str(attrs.get("source_type", "")).casefold()
    if source == 'gps' or any(key in attrs for key in ('latitude', 'longitude', 'gps_accuracy')):
        return None
    has_router_markers = any(k in attrs for k in ("down_kilobytes_per_s", "up_kilobytes_per_s", "connected_ap", "interface"))
    has_network_identity = bool(attrs.get("ip_address") and (attrs.get("mac") or attrs.get("mac_address")))
    if dtype in ("client", "device") or dtype in ("deco", "node", "router"):
        kind = "node" if dtype in ("deco", "node", "router") else "client"
    elif connection in ("wired", "ethernet", "2.4 ghz", "5 ghz", "6 ghz", "band2_4", "band5", "band6",
                        "band5_1", "band5_2", "band6_1", "wifi", "wi-fi") and has_network_identity:
        kind = "client"
    elif source == "router" and has_network_identity:
        kind = "client"
    elif has_router_markers:
        kind = "node" if "deco" in entity_text(item) else "client"
    else:
        return None
    state = str(item.get("state", "")).casefold()
    if state not in ("home", "not_home", "on", "off", "unavailable", "unknown"):
        return None
    ident = str(item.get("entity_id", ""))
    name = title(item)
    mac = clean(attrs.get("mac") or attrs.get("mac_address") or "", 32)
    ip = clean(attrs.get("ip_address") or attrs.get("ip") or "", 64)
    visible_mac = ("••:••:••:" + mac[-8:]) if mac else ""
    rx = number(attrs.get('down_kilobytes_per_s'))
    tx = number(attrs.get('up_kilobytes_per_s'))
    rx = rx if rx is not None and rx >= 0 else None
    tx = tx if tx is not None and tx >= 0 else None
    last = item.get("last_updated") or item.get("last_changed") or ""
    diagnostics = []
    for key, label in (("internet_online", "Internet"), ("master", "Mesh role"),
                       ("signal_band2_4", "2.4 GHz backhaul signal"), ("signal_band5", "5 GHz backhaul signal"),
                       ("signal_band5_1", "5 GHz-1 backhaul signal"), ("signal_band5_2", "5 GHz-2 backhaul signal"),
                       ("cpu_usage", "CPU"), ("memory_usage", "Memory"), ("device_model", "Model"),
                       ("hw_version", "Hardware"), ("sw_version", "Firmware")):
        if key not in attrs or attrs[key] is None:
            continue
        val = attrs[key]
        if isinstance(val, (int, float)) and not isinstance(val, bool) and not math.isfinite(float(val)):
            continue
        if isinstance(val, (str, int, float, bool)):
            diagnostics.append({"label": label, "value": clean("Primary" if key == "master" and val is True else "Satellite" if key == "master" and val is False else val, 100)})
    return {"entity_id": ident, "name": name, "kind": kind, "state": state,
            "online": state in ("home", "on"), "known": state not in ("unavailable", "unknown"),
            "ip": ip, "mac": visible_mac, "connection": connection_label(attrs.get("connection_type") or attrs.get("interface") or ""),
            "node": clean(attrs.get("connected_ap") or attrs.get("ap_name") or attrs.get("connected_to") or attrs.get("deco_device") or ""),
            "download_kb": rx, "upload_kb": tx, "diagnostics": diagnostics,
            "last_updated": clean(last, 50), "last_reported": clean(item.get('last_reported'), 50)}


def _belongs_to_client(item, devices):
    ident = str(item.get("entity_id", "")).casefold()
    name = title(item).casefold()
    for device in devices:
        base = device["entity_id"].split(".", 1)[1].casefold()
        device_name = device["name"].casefold()
        if ident.split('.', 1)[-1].startswith(base + "_") or (len(device_name) >= 3 and name.startswith(device_name + ' ')):
            return True
    return False


def _metric_kind(item, devices):
    domain = str(item.get("entity_id", "")).split(".", 1)[0]
    text = entity_text(item).replace('_', ' ')
    attrs = item.get("attributes") or {}
    unit = clean(attrs.get("unit_of_measurement", ""), 32)
    lower_unit = unit.casefold()
    gateway = any(word in text for word in ('upnp', 'igd', 'wan', 'router', 'deco', 'be11000', 'internet'))
    external = external_target(attrs)
    if domain == "binary_sensor":
        if external and any(word in text for word in ('ping', 'reachab', 'connect')):
            return 'reachability'
        if re.search(r'\bwan\b', text) or ('internet' in text and any(word in text for word in ('upnp', 'igd', 'router', 'deco', 'be11000'))):
            return 'wan'
        return None
    if domain != "sensor":
        return None
    if any(x in text for x in ('api', 'coordinator', 'speedtest', 'speed test')):
        return None
    if (external or 'internet' in text) and any(x in text for x in ('packet loss', 'loss')) and unit == '%':
        return 'loss'
    if (external or 'internet' in text) and any(x in text for x in ("latency", "ping", "round trip")) and lower_unit in ('ms', 's'):
        return "latency"
    if any(x in text for x in ("external ip", "public ip", "wan ip")):
        return "public_ip"
    if "uptime" in text:
        return "uptime"
    if _belongs_to_client(item, devices):
        return None
    if not gateway:
        return None
    direction = None
    if any(x in text for x in ("download", "received", "inbound", "bytes in")):
        direction = "download"
    elif any(x in text for x in ("upload", "sent", "outbound", "bytes out")):
        direction = "upload"
    if direction is None:
        return None
    if any(x in text for x in ("total", "counter", "cumulative", "bytes received", "bytes sent")) and lower_unit in BYTE_UNITS:
        return direction + "_total"
    is_rate = any(x in text for x in ("speed", "rate", "throughput", "bandwidth")) or "/s" in lower_unit or lower_unit.endswith("bps")
    if is_rate and lower_unit in RATE_UNITS:
        return direction + "_rate"
    # HA's data_rate unit can be represented with the unit itself even if the
    # integration uses only a short entity name such as "download".
    if is_rate and any(x in lower_unit for x in ("bit/s", "byte/s", "bytes/s", "b/s")):
        return direction + "_rate"
    return None


def classify(states):
    devices = [found for item in states if (found := client_or_node(item))]
    metrics = []
    for item in states:
        kind = _metric_kind(item, devices)
        if kind:
            value = state_value(item)
            if kind == "wan":
                value = str(item.get("state", "")).casefold()
            elif kind == "public_ip":
                value = clean(item.get("state", ""), 64)
            if value is not None or kind in ('download_rate', 'upload_rate', 'download_total', 'upload_total', 'latency', 'loss'):
                metrics.append({"entity_id": item["entity_id"], "name": title(item), "kind": kind,
                                "state": value, "display_state": clean(item.get("state", "unavailable"), 64),
                                "unit": clean((item.get("attributes") or {}).get("unit_of_measurement", ""), 32),
                                "device_class": clean((item.get("attributes") or {}).get("device_class", ""), 32),
                                "state_class": clean((item.get("attributes") or {}).get("state_class", ""), 32),
                                "last_updated": clean(item.get("last_updated") or item.get("last_changed") or "", 50),
                                "last_reported": clean(item.get('last_reported'), 50),
                                "basis": 'Unambiguous gateway signal' if kind == 'wan' else 'External target' if external_target(item.get('attributes') or {}) else 'Named network signal',
                                "rank": _network_rank(item)})
        attrs = item.get("attributes") or {}
        if (item.get("entity_id", "").startswith("device_tracker.")
                and attrs.get("device_type") in ("deco", "node", "router") and "internet_online" in attrs
                and (attrs.get("master") is True or "master" not in attrs)):
            internet = attrs.get("internet_online")
            if isinstance(internet, bool):
                state = ('on' if internet else 'off') if str(item.get('state')).casefold() not in ('unknown', 'unavailable') else 'unknown'
                metrics.append({"entity_id": item["entity_id"], "name": title(item) + " internet", "kind": "wan",
                                "state": state, "display_state": state, "unit": "", "device_class": "connectivity",
                                "state_class": "", "last_updated": clean(item.get("last_updated") or item.get("last_changed") or "", 50),
                                "last_reported": clean(item.get('last_reported'), 50), "basis": 'Primary Deco internet attribute',
                                "attribute": 'internet_online',
                                "rank": _network_rank(item)})
    devices.sort(key=lambda device: (device["kind"] != "node", not device["online"], device["name"].casefold()))
    metrics.sort(key=lambda metric: (metric["rank"], metric["kind"], metric["name"].casefold()))
    return devices, metrics


def best_metric(metrics, kind):
    matches = [item for item in metrics if item['kind'] == kind]
    return matches[0] if len(matches) == 1 else None


def external_target(attrs):
    for key in ('host', 'target', 'ip_address'):
        try:
            if ipaddress.ip_address(str(attrs.get(key, ''))).is_global:
                return True
        except ValueError:
            pass
    return False


def fresh(metric, now=None):
    if not metric:
        return False
    age = metric_age(metric, now)
    return age is not None and age <= 300


def display_rate(value, unit):
    found = number(value)
    factor = RATE_UNITS.get(str(unit or "").strip().casefold())
    return found * factor if found is not None and factor is not None else None


def bytes_value(value, unit):
    found = number(value)
    factor = BYTE_UNITS.get(str(unit or "").strip().casefold())
    return found * factor if found is not None and factor is not None else None


def format_rate(value):
    if value is None:
        return "Not reported"
    if value >= 1000:
        return f"{value / 1000:.2f} Gbit/s"
    if value >= 1:
        return f"{value:.2f} Mbit/s"
    return f"{value * 1000:.0f} kbit/s"


def format_bytes(value):
    if value is None or value < 0:
        return "Not reported"
    for unit, scale in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if value >= scale:
            return f"{value / scale:.2f} {unit}"
    return f"{value:.0f} B"


def parse_time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def history_time(entry):
    """Use updated time for changing attributes; minimal HA history only has changed time."""
    return parse_time(entry.get("last_updated") or entry.get("last_changed"))


def chart(group, metric, unit="Mbit/s", *, limit=192, start=None, end=None):
    values = []
    for entry in group or []:
        when = history_time(entry)
        if when is None:
            continue
        value = display_rate(entry.get("state"), metric.get("unit")) if metric["kind"].endswith("_rate") else number(entry.get("state"))
        if value is not None and value < 0:
            value = None
        values.append((when, value))
    values.sort(key=lambda pair: pair[0])
    start = start or (values[0][0] if values else datetime.now(timezone.utc))
    end = end or (values[-1][0] if values else start + timedelta(seconds=1))
    # Clip to the actual window; never stretch a partial range to look complete.
    values = [(t, v) for t, v in values if start <= t <= end]
    valid = [v for _, v in values if v is not None]
    result = {'points': '', 'segments': [], 'unit': unit, 'min': min(valid) if valid else None,
              'max': max(valid) if valid else None, 'count': len(valid), 'average': None,
              'start_label': local_time(start), 'end_label': local_time(end)}
    if not valid:
        return result
    span = max(valid) or 1
    seconds = max(1, (end - start).total_seconds())
    segments, segment = [], []
    for when, value in values:
        if value is None:
            if segment:
                segments.append(segment)
                segment = []
            continue
        segment.append((when, value))
    if segment:
        segments.append(segment)
    # Bucket by time, retaining min/max peaks rather than every nth sample.
    for segment in segments:
        buckets = {}
        for when, value in segment:
            key = min(limit - 1, int((when - start).total_seconds() / seconds * limit))
            buckets.setdefault(key, []).append((when, value))
        retained = []
        for bucket in buckets.values():
            retained.extend(sorted(set((bucket[0], min(bucket, key=lambda p: p[1]),
                                        max(bucket, key=lambda p: p[1]), bucket[-1]))))
        points = ' '.join(f'{2 + 716 * (t - start).total_seconds() / seconds:.1f},{176 - 168 * v / span:.1f}' for t, v in retained)
        result['segments'].append(points)
    result['points'] = result['segments'][0] if result['segments'] else ''
    return result


def usage(group, metric):
    result = counter_usage(group, metric)
    return result['bytes'] if result['complete'] else None


def counter_usage(group, metric):
    rows = sorted(((history_time(row), bytes_value(row.get('state'), metric.get('unit')))
                   for row in group or [] if history_time(row)), key=lambda p: p[0])
    total, previous, resets = 0, None, 0
    complete = len(rows) >= 2
    for _, value in rows:
        if value is None or value < 0:
            complete, previous = False, None
            continue
        if previous is not None:
            delta = value - previous
            if delta < 0:
                # Reset vs rollover cannot safely be distinguished from values alone.
                resets += 1
                complete = False
            else:
                total += delta
        previous = value
    return {'bytes': total, 'complete': complete, 'resets': resets}


def outage_summary(group, metric, period_days, *, end=None):
    end = end or datetime.now(timezone.utc)
    start = end - timedelta(days=period_days)
    events = sorted((history_time(row), connection_state(row, metric)) for row in group or []
                    if history_time(row) and history_time(row) <= end)
    state, cursor = 'unknown', start
    intervals = []
    for when, new_state in events:
        if when <= start:
            state = new_state
            continue
        if when > cursor:
            intervals.append({'start': cursor, 'end': when, 'state': state})
        cursor, state = when, new_state
    intervals.append({'start': cursor, 'end': end, 'state': state})
    merged = []
    for interval in intervals:
        if merged and merged[-1]['state'] == interval['state']:
            merged[-1]['end'] = interval['end']
        else:
            merged.append(interval)
    totals = {key: sum((i['end'] - i['start']).total_seconds() for i in merged if i['state'] == key)
              for key in ('up', 'down', 'unknown')}
    incidents = []
    for i in merged:
        if i['state'] == 'up':
            continue
        duration = i['end'] - i['start']
        incidents.append({**i, 'when': local_time(i['start']), 'duration': format_duration(duration),
                          'label': 'Monitoring gap' if i['state'] == 'unknown' else 'Reported offline',
                          'ongoing': i['end'] == end})
    seconds = (end - start).total_seconds()
    complete = totals['unknown'] < 0.01
    downs = [i for i in merged if i['state'] == 'down']
    return {'known': complete, 'downtime': timedelta(seconds=totals['down']),
            'downtime_text': format_duration(timedelta(seconds=totals['down'])),
            'unknown_text': format_duration(timedelta(seconds=totals['unknown'])),
            'coverage': 100 * (1 - totals['unknown'] / seconds),
            'reliability': 100 * totals['up'] / seconds if complete else None,
            'outages': len(downs), 'last_outage': downs[-1]['start'] if downs else None,
            'incidents': list(reversed(incidents)), 'intervals': merged}


def connection_state(row, metric=None):
    state = str(row.get('state', '')).casefold()
    if state in ('unknown', 'unavailable', 'none', ''):
        return 'unknown'
    attrs = row.get('attributes') or {}
    if metric and metric.get('attribute') == 'internet_online':
        state = str(attrs.get('internet_online', 'unknown')).casefold()
    if state in ('on', 'home', 'connected', 'online', 'true'):
        return 'up'
    if _is_down(state):
        return 'down'
    return 'unknown'


def _is_down(state):
    return state in ("off", "not_home", "disconnected", "offline", "false")


def format_duration(delta):
    if delta is None:
        return "Not reported"
    if 0 < delta.total_seconds() < 60:
        return f'{int(delta.total_seconds())}s'
    minutes = max(0, int(delta.total_seconds() // 60))
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def range_start(key, now=None):
    now = now or datetime.now(timezone.utc)
    return now - timedelta(days=RANGE_DAYS.get(key, 1))


def device_speed(device, direction):
    value = device.get(direction + "_kb")
    # ha-tplink-deco exposes decimal kilobytes per second.
    return value * 0.008 if value is not None else None


def connection_label(value):
    found = clean(value, 80)
    return {"band2_4": "2.4 GHz", "band5": "5 GHz", "band6": "6 GHz",
            "band5_1": "5 GHz-1", "band5_2": "5 GHz-2", "band6_1": "6 GHz-1",
            "wired": "Wired", "ethernet": "Wired", "main": "Main", "guest": "Guest",
            "iot": "IoT"}.get(found.casefold(), found)


def attribute_chart(group, attribute, *, limit=192, start=None, end=None):
    points = []
    for entry in group or []:
        attrs = entry.get("attributes")
        when = history_time(entry)
        value = number(attrs.get(attribute)) if isinstance(attrs, dict) else None
        if when:
            points.append({"last_changed": when.isoformat(), "state": str(value * 0.008) if value is not None else 'unknown'})
    return chart(points, {"kind": "download_rate", "unit": "Mbit/s"}, limit=limit, start=start, end=end)


def entity_duration(metric):
    value = number(metric.get("state"))
    unit = str(metric.get("unit", "")).casefold()
    if value is None:
        return "Not reported"
    factors = {"s": 1, "sec": 1, "seconds": 1, "min": 60, "minutes": 60, "h": 3600, "hours": 3600, "d": 86400, "days": 86400}
    seconds = value * factors.get(unit, 1)
    return format_duration(timedelta(seconds=seconds))


def metric_age(metric, now=None):
    updated = parse_time(metric.get('last_reported') or metric.get("last_updated"))
    if not updated:
        return None
    return max(0, int(((now or datetime.now(timezone.utc)) - updated).total_seconds()))


def local_time(value):
    return value.astimezone(ZoneInfo('America/Los_Angeles')).strftime('%b %-d, %-I:%M %p') if value else '—'


def source_candidate(item, role):
    ident = str(item.get('entity_id', ''))
    attrs = item.get('attributes') or {}
    unit = str(attrs.get('unit_of_measurement', '')).casefold()
    if role in ('wan', 'reachability'):
        return ident.startswith('binary_sensor.') and (attrs.get('device_class') == 'connectivity'
                or any(word in entity_text(item) for word in ('wan', 'internet', 'ping')))
    if not ident.startswith('sensor.'):
        return False
    if role == 'latency':
        return unit in ('ms', 's') and not any(word in entity_text(item) for word in ('api', 'coordinator')) and any(word in entity_text(item) for word in ('ping', 'latency', 'round_trip'))
    if role == 'loss':
        return unit == '%' and 'loss' in entity_text(item)
    if role.endswith('_rate'):
        return unit in RATE_UNITS
    return role.endswith('_total') and unit in BYTE_UNITS


def select_sources(states, metrics, choices):
    selected, notes = {}, {}
    current = {item['entity_id']: item for item in states}
    for role in ('wan', 'reachability', 'latency', 'loss', 'download_rate', 'upload_rate', 'download_total', 'upload_total'):
        configured = choices.get(role)
        candidates = [m for m in metrics if m['kind'] == role]
        if configured:
            item = current.get(configured)
            if item and source_candidate(item, role):
                metric = {'entity_id': configured, 'kind': role, 'name': title(item), 'state': item.get('state'),
                          'display_state': clean(item.get('state'), 64), 'unit': clean((item.get('attributes') or {}).get('unit_of_measurement'), 32),
                          'last_updated': item.get('last_updated'), 'last_reported': item.get('last_reported'), 'basis': 'Owner-selected source'}
                selected[role] = metric
            else:
                notes[role] = 'Selected source missing or disabled' if configured != 'none' else 'Disabled by owner'
        else:
            # Names suggest candidates; only an external IP target establishes probe scope automatically.
            if role in ('latency', 'loss'):
                candidates = [m for m in candidates if external_target(current[m['entity_id']].get('attributes') or {})]
            if role == 'wan':
                primary = [m for m in candidates if m.get('rank') == 0]
                candidates = primary or candidates
            if len(candidates) == 1:
                selected[role] = candidates[0]
            else:
                notes[role] = 'Multiple candidates; select a source' if candidates else 'Not measured'
    return selected, notes


def value(metric):
    if not metric:
        return None
    found = number(metric.get('state'))
    if found is None or found < 0:
        return None
    if metric['kind'] == 'loss' and found > 100:
        return None
    if metric['kind'] == 'latency' and str(metric.get('unit')).casefold() == 's':
        return found * 1000
    if metric['kind'].endswith('_rate'):
        return display_rate(found, metric.get('unit'))
    return found
