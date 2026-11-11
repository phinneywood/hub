"""Invented starter trip, used only when the trip database is empty (a fresh install or tests).

A saved trip lives only in the private trip database (`trip.db` in the data directory); this seed
never replaces or edits it. Every place, airport, flight, date, link and price here is made up.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

HOME, HOME_ZONE, AWAY_ZONE = 'HOM', 'America/Denver', 'America/Chicago'
SEARCH = 'https://airline.example/search?from=HOM&to=PEL&depart=2031-10-04&return=2031-10-11&adults=2&children=1'
NORTH = SEARCH.replace('to=PEL', 'to=PEN')
VENUE = 'https://venue.example/lakeside-hall'


def zone(airport):
    return HOME_ZONE if airport == HOME else AWAY_ZONE


def leg(number, origin, destination, departure, arrival):
    departure = datetime.fromisoformat(departure).replace(tzinfo=ZoneInfo(zone(origin))).isoformat()
    arrival = datetime.fromisoformat(arrival).replace(tzinfo=ZoneInfo(zone(destination))).isoformat()
    return dict(number=number, origin=origin, destination=destination, departure=departure, arrival=arrival,
                origin_timezone=zone(origin), destination_timezone=zone(destination))


def record(id, title, **extra):
    return dict(id=id, title=title, status='open', notes='', **extra)


def seed_trip():
    flights = []
    for id, number, dest, dep, arr, price, note in [
        ('f1', 'XA101', 'PEL', '09:05', '2031-10-04T12:26', 412, 'Complete XA101 / XA110 pairing observed. Recommended candidate, not selected.'),
        ('f2', 'XA103', 'PEL', '07:35', '2031-10-04T11:01', 357, 'From-price; complete return pairing not finalized.'),
        ('f3', 'XA105', 'PEL', '15:35', '2031-10-04T18:55', 307, 'From-price; late arrival with a small child. Return pairing not finalized.'),
        ('f4', 'XA107', 'PEL', '23:40', '2031-10-05T03:04', None, 'Overnight: arrives Sunday October 5, not October 4. Fare unknown.'),
        ('f5', 'XA201', 'PEN', '08:05', '2031-10-04T11:31', 427, 'North airport from-price, complete return pairing not finalized.'),
        ('f6', 'XA203', 'PEN', '11:06', '2031-10-04T14:32', 427, 'North airport from-price, complete return pairing not finalized.'),
    ]:
        flights.append(dict(id=id, label=number+' · HOM → '+dest, outbound=leg(number, HOME, dest, '2031-10-04T'+dep, arr),
            **{'return': leg('XA110', 'PEL', HOME, '2031-10-11T15:20', '2031-10-11T16:38') if id == 'f1' else None},
            round_trip_per_person=price, price_kind='observed complete round trip' if id == 'f1' else 'round-trip from-price; pairing unverified',
            status='candidate', cabin='Main', taxes='Displayed taxes and fees included', nonstop=True,
            source=SEARCH if dest == 'PEL' else NORTH, observed='2031-06-01', note=note))
    flights[0]['note'] += ' Older snapshot; a later XA103 / XA110 party quote is kept separately.'
    flights.append(dict(id='f2-party', label='XA103 / XA110 · HOM ↔ PEL',
        outbound=leg('XA103', HOME, 'PEL', '2031-10-04T07:35', '2031-10-04T11:01'),
        **{'return': leg('XA110', 'PEL', HOME, '2031-10-11T15:20', '2031-10-11T16:38')},
        party_total=1170.43, round_trip_per_person=None, price_kind='observed party total',
        status='candidate', cabin='Main', taxes='Displayed party total; no companion discount', nonstop=True,
        source=SEARCH, observed='2031-06-01',
        note='Full itinerary quote for two adults and one child, before any companion fare. '
             'An observation, not current inventory or a booking. Do not divide it into per-person fares.'))
    return dict(id='example-trip', title='Port Ellery & Larch Valley · Example family trip',
        first_stay='Port Ellery', second_stay='Larch Valley',
        start='2031-10-04', end='2031-10-11', timezone=AWAY_ZONE, nonstop_preferred=True, revision=1,
        selected_scenario='wed', selected_flight=None,
        notes='Invented example. Wednesday is the preferred move from the first stay to the second. '
              'Nonstop flights are preferred. Travel dates remain proposed; nothing is booked.',
        history=[], scenarios=[dict(id=id, title=title, transition=day, first_nightly=None, second_nightly=None, car_daily=None)
                               for id, title, day in [('wed', 'Wednesday · more settling-in time', '2031-10-08'),
                                                      ('thu', 'Thursday · one more day at the first stay', '2031-10-09')]],
        flights=flights,
        records={
        'itinerary': [
            dict(id='DATE-TRAVEL-20311004', title='Travel to Port Ellery', date='2031-10-04', time=None, status='tentative', notes='Two adults and one child; flights not selected.'),
            dict(id='DATE-WELCOME-20311009', title='Welcome dinner', date='2031-10-09', time=None, status='confirmed date', notes='Venue and start time unknown.'),
            dict(id='DATE-WEDDING-20311010', title='Wedding · Lakeside Hall', date='2031-10-10', time=None, status='confirmed date', notes='Ceremony and reception times unknown.', url=VENUE),
            dict(id='return-home', title='Return home', date='2031-10-11', time=None, status='tentative', notes='Flights not selected.')],
        'reservations': [
            dict(id='first-stay', title='Port Ellery hotel · not booked', status='candidate', notes='One ordinary room. Verify two-adults-plus-child occupancy.'),
            dict(id='second-stay', title='Larch Valley shared rental · not booked', status='candidate', notes='Shared with extended family. Room-sharing and access needs unknown.'),
            dict(id='flight-reservation', title='Flights · not booked', status='candidate', notes='Two adults and one child, each with own ticket and seat. No flight selected.')],
        'people': [
            dict(id='family', title='Immediate family · two adults and a child', status='proposed', notes='Port Ellery first, then Larch Valley. Child-seat and luggage needs to determine.'),
            dict(id='extended-family', title='Extended family · second stay', status='dates unknown', notes='Joining date, transport and access needs unknown. Optional shared ground transport to explore.')],
        'budget': [
            dict(id='regular-flights', title='XA101 / XA110 · three regular fares', amount=1236, scope='immediate family', status='observed 2031-06-01', notes='$412 round trip per passenger in Main, including displayed taxes/fees. Not live inventory.'),
            dict(id='companion-model', title='Companion fare alternative · not additive', amount=None, scope='immediate family', status='unverified estimate', notes='Two regular fares + $99 + companion taxes/fees. At $412: $923 before companion taxes.'),
            dict(id='stay-costs', title='Lodging, rental car and other costs', amount=None, scope='allocation undecided', status='unknown', notes='Budget not set. An extra night at the second stay replaces one at the first and may add a rental day.')],
        'decisions': [
            dict(id='transition-preference', title='Prefer the Wednesday move', status='selected preference', notes='Thursday remains a comparison, not the chosen plan. Not a booking.'),
            record('welcome-details', 'Find welcome dinner venue and start time'),
            record('wedding-time', 'Confirm wedding ceremony / reception times'),
            record('family-arrivals', 'Clarify extended family joining date and optional shared ground transport'),
            record('house-access', 'Confirm the second stay check-in time and any wedding-week obligations'),
            record('companion', 'Verify companion fare eligibility and full itinerary quote'),
            record('flight-choice', 'Choose airport and flights after comparing day-of-travel needs')],
        'documents': [
            dict(id='venue', title='Lakeside Hall · wedding venue', url=VENUE, status='reference', notes='Invented venue. Times unknown.'),
            dict(id='directions', title='Lakeside Hall · directions', url=VENUE+'/directions', status='reference', notes='Advertised drive times are not door-to-door guarantees; allow traffic, stops and check-in.'),
            dict(id='dates-sheet', title='Shared dates sheet · owning date records', status='reference', notes='A shared sheet owns the dated records. Hub does not deliver reminders or silently synchronize changes.'),
            dict(id='airline', title='Airline flight search', url=SEARCH, status='historical source', notes='Example observation. Away airports on Central time, home on Mountain time.'),
            dict(id='discount-rules', title='Airline discount-code rules', url='https://airline.example/discount-codes', status='verify before booking', notes='Companion certificate unverified. No raw certificate codes in this workspace.'),
            dict(id='lodging-a', title='Archived lodging · Harbor hotel king room', status='research paused', notes='Example quote for two adults only; family occupancy unresolved. No reservation.'),
            dict(id='lodging-b', title='Archived lodging · Lakeside cabin', url='https://rentals.example/lakeside-cabin', status='research paused', notes='Example snapshot: 4 beds / 3 baths. Many stairs. Not priced for a Wednesday arrival.'),
            dict(id='lodging-c', title='Archived lodging · Village inn', url='https://rentals.example/village-inn', status='research paused', notes='Example snapshot: 4 bedrooms / 2.5 baths near the venue. Access details unknown.')],
        'preparation': [dict(id=id, title=title, status='open', done=False, notes='') for id, title in [
            ('child-kit', 'Child travel kit, snacks and sleep essentials'), ('car-seat', 'Decide car seat and ground-transport arrangement'),
            ('wedding-clothes', 'Wedding outfits and shoes'), ('travel-docs', 'Check travel documents and airline child requirements'),
            ('confirmations', 'Save confirmed addresses and reservation links'), ('packing', 'Build final packing checklist')]]})
