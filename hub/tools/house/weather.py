"""The weather at home for the family brief: now, the next hours, today, tomorrow and the days after, and the sun.

From Home Assistant's own weather entity (where the house is stays in Home Assistant, never here), read through
the broker's everyday door: the entity as it stands comes with the house snapshot (read.py), and its forecasts
from weather.get_forecasts, a service that only answers (broker.forecast). Read-only, and the forecast is kept
for a quarter of an hour. Units are the entity's own: °F and inches, or °C and millimetres.

Rain is judged from the forecast's amount and its condition, since not every forecast gives a chance of rain.
"""
import threading
import time
from datetime import datetime, timedelta

from ... import home, household
from . import broker, read

FORECAST_SECONDS = 15 * 60
HOURS = 12                  # hours in the strip
DAYS = 4                    # days after today
WAKING = (6, 21)            # the hours a family is out and about: rain outside them is "tonight" or "overnight"
WET = {'rainy', 'pouring', 'lightning-rainy', 'snowy-rainy', 'snowy', 'hail'}
SNOW = {'snowy', 'snowy-rainy'}
# Home Assistant's conditions: (words, icon). The app draws each icon (web/app/src/ui.tsx).
CONDITIONS = {
    'clear-night': ('Clear', 'moon'), 'cloudy': ('Cloudy', 'cloud'), 'exceptional': ('Unusual weather', 'alert'),
    'fog': ('Fog', 'fog'), 'hail': ('Hail', 'hail'), 'lightning': ('Thunder', 'storm'), 'lightning-rainy': ('Thunderstorms', 'storm'),
    'partlycloudy': ('Partly cloudy', 'partly'), 'pouring': ('Heavy rain', 'pour'), 'rainy': ('Rain', 'rain'),
    'snowy': ('Snow', 'snow'), 'snowy-rainy': ('Sleet', 'sleet'), 'sunny': ('Sunny', 'sun'), 'windy': ('Windy', 'wind'),
    'windy-variant': ('Windy and cloudy', 'wind'),
}
NIGHT_ICONS = {'sun': 'moon', 'partly': 'partly-night'}
# How a condition is said after the temperature: "45° and partly cloudy", "52° and raining".
NOW_WORDS = {'clear-night': 'clear', 'cloudy': 'cloudy', 'exceptional': 'wild out', 'fog': 'foggy', 'hail': 'hailing',
             'lightning': 'thundery', 'lightning-rainy': 'stormy', 'partlycloudy': 'partly cloudy', 'pouring': 'pouring',
             'rainy': 'raining', 'snowy': 'snowing', 'snowy-rainy': 'sleeting', 'sunny': 'sunny', 'windy': 'windy',
             'windy-variant': 'windy'}
WET_AMOUNT = {'in': 0.01, 'mm': 0.25}       # an hour with at least this much is a wet one
_lock = threading.Lock()
STALE_SECONDS = 3 * 3600
RETRY_SECONDS = 60
_kept = {'at': float('-inf'), 'entity': None, 'found': None, 'retry': float('-inf')}


def _entity(found):
    """The weather entity to use: the one named in household.json ("brief": {"weather": "weather.x"}), else
    Home Assistant's own forecast for home, else the first there is."""
    entities = found.get('weather') or []
    try:
        named = household.section('brief').get('weather')
    except ValueError:
        named = None
    return next((w for w in entities if w['id'] == named), None) or next(
        (w for w in entities if w['id'] in ('weather.forecast_home', 'weather.home')), None) or (entities[0] if entities else None)


def forecasts(entity_id, fresh=False):
    """(hourly, daily) rows for the entity, kept for a quarter of an hour. When Home Assistant can't give one, the
    last one is used for up to three hours and it is asked again after a minute. Raises broker.HomeDown."""
    with _lock:
        age = time.monotonic() - _kept['at']
        if not fresh and _kept['entity'] == entity_id and (age < FORECAST_SECONDS or time.monotonic() < _kept['retry']):
            if _kept['found'] is None or age > STALE_SECONDS:
                raise broker.HomeDown('Home Assistant gave no forecast just now.')
            return _kept['found']
    try:
        try:
            found = tuple(broker.forecast(entity_id, 'hourly', 'daily'))
        except broker.Rejected:     # an entity that gives only one of the two: a daily one, else an hourly one
            try:
                found = ([], broker.forecast(entity_id, 'daily')[0])
            except broker.Rejected:
                found = (broker.forecast(entity_id, 'hourly')[0], [])
    except broker.HomeDown:
        with _lock:
            _kept['retry'] = time.monotonic() + RETRY_SECONDS
            if _kept['entity'] != entity_id:
                _kept.update(entity=entity_id, found=None, at=float('-inf'))
        raise
    with _lock:
        _kept.update(at=time.monotonic(), entity=entity_id, found=found, retry=float('-inf'))
    return found


def forget():
    with _lock:
        _kept.update(at=float('-inf'), entity=None, found=None, retry=float('-inf'))


def _when(text):
    """A forecast's time, in the house's own time zone, or None."""
    try:
        moment = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    except ValueError:
        return None
    return moment.astimezone(home.PACIFIC) if moment.tzinfo else moment.replace(tzinfo=home.PACIFIC)


def _date_of(text):
    """The day a daily forecast row is for. A row stamped at midnight UTC is a date, not a moment: its UTC date."""
    try:
        moment = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.utcoffset() == timedelta(0) and (moment.hour, moment.minute, moment.second) == (0, 0, 0):
        return moment.date()
    return (moment.astimezone(home.PACIFIC) if moment.tzinfo else moment).date()


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) < 1e5 else None


def _degrees(value):
    number = _number(value)
    return f'{number:.0f}°' if number is not None else ''


def hour_label(moment):
    """"3 pm", "7:05 am", "noon"."""
    if moment.minute == 0 and moment.hour in (0, 12):
        return 'noon' if moment.hour == 12 else 'midnight'
    return moment.strftime('%-I %p' if moment.minute == 0 else '%-I:%M %p').lower()


def _sun(found, now):
    """(sunrise today, sunset today), from when Home Assistant says the sun next rises and sets, or Nones."""
    sun = found.get('sun')
    if not sun:
        return None, None
    rises, sets = _when(sun['rising']), _when(sun['setting'])
    if not rises or not sets:
        return None, None
    while rises.date() > now.date():        # it next rises tomorrow: today's was a day earlier, near enough
        rises -= timedelta(days=1)
    while sets.date() > now.date():
        sets -= timedelta(days=1)
    return rises, sets


def _dark(moment, rises, sets):
    if rises and sets:
        clock = moment.hour * 60 + moment.minute
        return not (rises.hour * 60 + rises.minute <= clock < sets.hour * 60 + sets.minute)
    return not 7 <= moment.hour < 17


def _look(condition, dark=False):
    words, icon = CONDITIONS.get(condition, ('', 'cloud'))
    return words, NIGHT_ICONS.get(icon, icon) if dark else icon


def _wet(row, unit):
    amount = _number(row.get('precipitation'))
    return row.get('condition') in WET or (amount is not None and amount >= WET_AMOUNT.get(unit or 'mm', 0.25))


def _span(start, end, snow):
    """"Rain from 3 pm to 7 pm", "Rain until 10 am", "Snow from 9 pm"."""
    what = 'Snow' if snow else 'Rain'
    if start is None:
        return f'{what} until {hour_label(end)}' if end else f'{what} all day'
    return f'{what} from {hour_label(start)}' + (f' to {hour_label(end)}' if end else '')


def rain(hours, now, unit, end):
    """What the rain does from now until `end`, in a few words, and whether any falls: ('Dry all day', False)."""
    window = [h for h in hours if now - timedelta(hours=1) < h['at'] <= end]
    wet = [h for h in window if _wet(h, unit)]
    if not window:
        return '', False
    if not wet:                 # dry, as far as the forecast reaches: said only when it reaches the end
        return ('Dry all day' if now.hour < 17 else 'Dry tonight') if window[-1]['at'] >= end - timedelta(hours=1) else '', False
    snow = sum(h.get('condition') in SNOW for h in wet) * 2 > len(wet)
    stretches, current = [], None
    for h in window:
        if _wet(h, unit):
            current = current or [h['at'], None]
        elif current:
            current[1] = h['at']
            stretches.append(current)
            current = None
    if current:
        stretches.append(current)
    if len(stretches) > 2 or (len(stretches) == 2 and stretches[1][0] - stretches[0][1] < timedelta(hours=3)):
        return f'{"Snow" if snow else "Showers"} on and off from {hour_label(stretches[0][0])}' if stretches[0][0] > now \
            else f'{"Snow" if snow else "Showers"} on and off', True
    first, second = stretches[0], stretches[1] if len(stretches) > 1 else None
    started = None if first[0] <= now else first[0]
    if started is None and first[1] is None:
        return ('Snow' if snow else 'Rain') + (' all day' if now.hour < 12 else ' through the evening' if now.hour < 18 else ' tonight'), True
    said = _span(started, first[1], snow)
    if second:
        said += f', then again from {hour_label(second[0])}'
    return said, True


def _wear(high, low, wet, snow, uv, unit):
    """What to wear out, from the day's high and low in the entity's own unit: "Coats and umbrellas"."""
    if high is None:
        return ''
    high_f = high * 9 / 5 + 32 if unit == '°C' else high
    low_f = (low * 9 / 5 + 32 if unit == '°C' else low) if low is not None else high_f
    layer = 'Warm coats, hats and gloves' if high_f < 40 else 'Warm coats' if high_f < 50 else 'Jackets' if high_f < 60 \
        else 'A light layer' if high_f < 70 or low_f < 52 else 'Short sleeves'
    extra = 'snow boots' if snow else 'umbrellas' if wet else 'sunscreen' if (uv or 0) >= 6 else ''
    return f'{layer} and {extra}' if extra else layer


def _day(row, unit):
    words, icon = _look(row.get('condition'))
    amount = _number(row.get('precipitation'))
    return {'high': _number(row.get('temperature')), 'low': _number(row.get('templow')), 'words': words, 'icon': icon,
            'wet': _wet(row, unit) or bool(amount and amount >= WET_AMOUNT.get(unit or 'mm', 0.25) * 10),
            'snow': row.get('condition') in SNOW, 'uv': _number(row.get('uv_index'))}


def picture(now=None, found=None):
    """The weather for the brief, or None when Home Assistant has no weather. `found`: a house snapshot already
    read. Raises broker.HomeDown."""
    now = now or datetime.now(home.PACIFIC)
    found = found if found is not None else read.snapshot()
    entity = _entity(found)
    if not entity:
        return None
    hourly, daily = forecasts(entity['id'])
    unit, rain_unit = entity['unit'] or '°F', entity['rain_unit'] or ('in' if (entity['unit'] or '°F') == '°F' else 'mm')
    rises, sets = _sun(found, now)
    hours = sorted((dict(r, at=_when(r.get('datetime'))) for r in hourly if _when(r.get('datetime'))), key=lambda r: r['at'])
    days = {}
    for r in daily:
        on = _date_of(r.get('datetime'))
        if on and on not in days:
            days[on] = _day(r, rain_unit)
    today, tomorrow = days.get(now.date()), days.get(now.date() + timedelta(days=1))
    words, icon = _look(entity['condition'], _dark(now, rises, sets))
    bedtime = now.replace(hour=WAKING[1], minute=0, second=0, microsecond=0)
    rain_now, wet_now = rain(hours, now, rain_unit, bedtime if now < bedtime - timedelta(hours=2) else now + timedelta(hours=HOURS))
    morning_of = (now + timedelta(days=1)).replace(hour=WAKING[0], minute=0, second=0, microsecond=0)
    rain_next, wet_next = rain([h for h in hours if h['at'] >= morning_of], morning_of, rain_unit, morning_of.replace(hour=WAKING[1]))
    strip = []
    for h in [h for h in hours if h['at'] > now - timedelta(minutes=59)][:HOURS]:
        hour_words, hour_icon = _look(h.get('condition'), _dark(h['at'] if strip else now, rises, sets))
        strip.append({'at': 'Now' if not strip else hour_label(h['at']).capitalize() if h['at'].hour == 12 and h['at'].minute == 0
                      else hour_label(h['at']), 'temp': _degrees(h.get('temperature')), 'icon': hour_icon, 'words': hour_words,
                      'wet': _wet(h, rain_unit)})
    ahead = [{'day': on.strftime('%a'), 'date': on.isoformat(), 'high': _degrees(d['high']), 'low': _degrees(d['low']), 'icon': d['icon'],
              'words': d['words'], 'wet': d['wet']} for on, d in sorted(days.items()) if now.date() < on <= now.date() + timedelta(days=DAYS)]

    def summary(d, said, wet):
        if not d:
            return None
        if d['wet'] and not said:     # the day's own forecast says rain where the hours don't reach (or there are none)
            said, wet = ('Snow likely' if d['snow'] else 'Rain likely'), True
        return {'high': _degrees(d['high']), 'low': _degrees(d['low']), 'words': d['words'], 'icon': d['icon'], 'rain': said, 'wet': wet,
                'wear': _wear(d['high'], d['low'], wet, d['snow'], d['uv'], unit)}
    return {'now': {'temp': _degrees(entity['temperature']), 'words': words, 'icon': icon, 'said': NOW_WORDS.get(entity['condition'], '')},
            'today': summary(today, rain_now, wet_now), 'tomorrow': summary(tomorrow, rain_next, wet_next),
            'hours': strip, 'days': ahead,
            'sun': {'rise': hour_label(rises) if rises else '', 'set': hour_label(sets) if sets else '',
                    'up': not _dark(now, rises, sets)}}


def says(weather, part):
    """One sentence for the brief: "Partly cloudy and 45°, up to 51°. Rain from 3 pm to 7 pm." In the evening and
    at night it is about tomorrow: "Tomorrow: cloudy, high 48°, low 42°. Dry all day." """
    if not weather:
        return ''
    if part in ('evening', 'night') and weather['tomorrow']:
        day = weather['tomorrow']
        temps = ', '.join(p for p in (f'high {day["high"]}' if day['high'] else '', f'low {day["low"]}' if day['low'] else '') if p)
        said = 'Tomorrow: ' + ', '.join(p for p in (day['words'].lower(), temps) if p) + '.'
        return f'{said} {day["rain"]}.' if day['rain'] else said
    now, day = weather['now'], weather['today']
    said = ' and '.join(p for p in (now['temp'], now['said']) if p)
    if said and day and day['high'] and part in ('morning', 'day') and day['high'] != now['temp']:
        said += f', up to {day["high"]}'
    said = said[:1].upper() + said[1:] + '.' if said else ''
    if day and day['rain']:
        said += f' {day["rain"]}.'
    return said.strip()
