"""Pure trip calculations. Unknowns remain unknown; selections never imply bookings."""
from copy import deepcopy
from datetime import date, datetime
import json
import math
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re

CATEGORIES = ('itinerary', 'reservations', 'people', 'budget', 'decisions', 'documents', 'preparation')
TRIP_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,59}')
# Older saved trips used place-specific names for the two stays' nightly costs.
LEGACY_KEYS = {'nyc_nightly': 'first_nightly', 'nj_nightly': 'second_nightly'}


def upgrade(data):
    """Rename older scenario fields in place; the stored copy changes only when the owner saves."""
    for scenario in data.get('scenarios', []) if isinstance(data, dict) else []:
        if isinstance(scenario, dict):
            for old, new in LEGACY_KEYS.items():
                if old in scenario and new not in scenario:
                    scenario[new] = scenario.pop(old)
    return data


def _local_timestamp(value, zone):
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", value):
        raise ValueError('Flight times need a full local date and time.')
    try:
        parsed = datetime.fromisoformat(value)
        tz = ZoneInfo(zone)
    except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError('Invalid flight timestamp or airport timezone.') from exc
    if parsed.tzinfo is not None and parsed.utcoffset() != parsed.replace(tzinfo=tz).utcoffset():
        raise ValueError('Flight timestamp offset does not match its airport timezone.')
    localized = parsed.replace(tzinfo=tz)
    # Reject nonexistent wall times at a spring-forward transition.
    if datetime.fromtimestamp(localized.timestamp(), tz).replace(tzinfo=None) != localized.replace(tzinfo=None):
        raise ValueError('Flight time does not exist in its airport timezone.')
    return localized


def flight_duration(leg):
    """Elapsed minutes, with airport-local dates and DST-aware offsets."""
    start = _local_timestamp(leg['departure'], leg['origin_timezone'])
    end = _local_timestamp(leg['arrival'], leg['destination_timezone'])
    return int((end.timestamp() - start.timestamp()) / 60)


def validate(data):
    try:
        return _validate(data)
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError('Malformed trip: missing or incorrectly typed fields.') from exc


def _validate(data):
    if not isinstance(data, dict) or not isinstance(data.get('id'), str) or not TRIP_ID.fullmatch(data['id']):
        raise ValueError('This file is not a Hub trip.')
    if len(json.dumps(data, allow_nan=False)) > 1_000_000:
        raise ValueError('Trip is too large (maximum 1 MB).')
    result = upgrade(deepcopy(data))
    data = result
    def text(value, name, required=False):
        if not isinstance(value, str) or len(value) > 20000 or (required and not value.strip()):
            raise ValueError(f'{name} must be valid text.')
    def iso_date(value):
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('Dates must use YYYY-MM-DD.')
        return date.fromisoformat(value)
    def url(value):
        text(value, 'Link')
        if not value:
            return
        parts = urlsplit(value)
        if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password or any(ord(c)<33 for c in value):
            raise ValueError('Links must be ordinary https:// or http:// URLs without embedded credentials.')
    text(data['title'], 'Trip title', True)
    text(data.get('notes', ''), 'Notes')
    for field in ('first_stay', 'second_stay'):
        if field in data and (not isinstance(data[field], str) or not 0 < len(data[field].strip()) <= 60):
            raise ValueError('Stay names must be short text.')
    try:
        ZoneInfo(data['timezone'])
    except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError('Trip local timezone must be a valid time zone name.') from exc
    start, end = iso_date(data['start']), iso_date(data['end'])
    if not 0 < (end-start).days <= 366:
        raise ValueError('Trip duration must be between 1 and 366 days.')
    if 'nonstop_preferred' in data and not isinstance(data['nonstop_preferred'], bool):
        raise ValueError('Nonstop preference must be true or false.')
    def check_items(items, title_required=True):
        if not isinstance(items, list) or len(items) > 500:
            raise ValueError('Each collection must contain at most 500 records.')
        ids = set()
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not 0 < len(item['id']) <= 100 or item['id'] in ids:
                raise ValueError('Records need unique stable IDs.')
            ids.add(item['id'])
            if title_required:
                text(item['title'], 'Title', True)
            for field in ('title', 'label', 'notes', 'note', 'status', 'scope', 'price_kind', 'cabin', 'taxes'):
                if field in item:
                    text(item[field], field)
            for field in ('url', 'source'):
                if field in item:
                    url(item[field])
            for field in ('date', 'observed'):
                if item.get(field) not in (None, ''):
                    iso_date(item[field])
            if item.get('time') not in (None, ''):
                if not isinstance(item['time'], str) or not re.fullmatch(r'\d{2}:\d{2}',item['time']):
                    raise ValueError('Times must use HH:MM or remain unknown.')
                datetime.strptime(item['time'], '%H:%M')
            for field in ('done', 'nonstop'):
                if field in item and not isinstance(item[field], bool):
                    raise ValueError(f'{field} must be true or false.')
            for field in ('amount', 'first_nightly', 'second_nightly', 'car_daily', 'round_trip_per_person', 'party_total'):
                value = item.get(field)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 10_000_000):
                    raise ValueError(f'{field} must be a nonnegative amount up to $10 million, or unknown.')
        return ids
    scenarios = check_items(data['scenarios'])
    if scenarios != {'wed', 'thu'}:
        raise ValueError('Keep both Wednesday and Thursday scenarios.')
    for scenario in data['scenarios']:
        if not start <= iso_date(scenario['transition']) <= end:
            raise ValueError('Transition must be within the trip dates.')
    if data.get('selected_scenario') not in scenarios | {None}:
        raise ValueError('Choose an existing scenario.')
    flights = check_items(data['flights'], title_required=False)
    if data.get('selected_flight') not in flights | {None}:
        raise ValueError('Choose an existing flight.')
    for flight in data['flights']:
        text(flight['label'], 'Flight label', True)
        iso_date(flight['observed'])
        if not isinstance(flight.get('outbound'), dict):
            raise ValueError('Flights need an outbound leg.')
        for key in ('outbound', 'return'):
            leg = flight.get(key)
            if leg is not None:
                if not isinstance(leg, dict):
                    raise ValueError('Flight legs must be objects.')
                for field in ('number','origin','destination','origin_timezone','destination_timezone'):
                    text(leg[field], field, True)
                for time, zone in (('departure','origin_timezone'), ('arrival','destination_timezone')):
                    leg[time] = _local_timestamp(leg[time], leg[zone]).isoformat()
                if not 0 < flight_duration(leg) <= 48*60:
                    raise ValueError('Flight duration must be positive and at most 48 hours.')
    if not isinstance(data['records'], dict):
        raise ValueError('Trip records must be grouped by category.')
    for category in CATEGORIES:
        check_items(data['records'][category])
    return result


def summarize(data, scenario_id):
    scenario = next(s for s in data['scenarios'] if s['id'] == scenario_id)
    start, transition, end = (date.fromisoformat(v) for v in (data['start'], scenario['transition'], data['end']))
    first, second = (transition-start).days, (end-transition).days
    costs = {name: None if scenario.get(key) is None else round(scenario[key] * nights, 2)
             for name, key, nights in [('first', 'first_nightly', first), ('second', 'second_nightly', second),
                                       ('car', 'car_daily', second)]}
    flight = next((f for f in data['flights'] if f['id'] == data.get('selected_flight')), None)
    fare = flight.get('round_trip_per_person') if flight else None
    return {'first_nights': first, 'second_nights': second, 'full_first_days': max(0, first-1), 'transition': scenario['transition'],
            'known_total': round(sum(v for v in costs.values() if v is not None), 2),
            'missing_costs': [key for key, value in costs.items() if value is None], 'costs': costs,
            'complete': all(v is not None for v in costs.values()),
            'regular_fares_total': None if fare is None else round(3*fare, 2),
            'companion_before_tax': None if fare is None else round(2*fare+99, 2)}
