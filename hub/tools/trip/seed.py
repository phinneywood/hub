"""Fictional starter trip used when the trip database is empty.

The real trip lives only in the private trip database (`trip.db` in the data directory). This seed is
example data for new installs and tests: dated observations and preferences, never live inventory or
reservations. All venues, links, people and prices are invented.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

EWR = 'https://airline.example/search?from=SEA&to=EWR&depart=2027-05-22&return=2027-05-29&adults=2&children=1'
JFK = EWR.replace('to=EWR', 'to=JFK')
VENUE = 'https://venue.example/orchard-barn'


def leg(number, origin, destination, departure, arrival):
    origin_zone = 'America/Los_Angeles' if origin == 'SEA' else 'America/New_York'
    destination_zone = 'America/Los_Angeles' if destination == 'SEA' else 'America/New_York'
    departure = datetime.fromisoformat(departure).replace(tzinfo=ZoneInfo(origin_zone)).isoformat()
    arrival = datetime.fromisoformat(arrival).replace(tzinfo=ZoneInfo(destination_zone)).isoformat()
    return dict(number=number, origin=origin, destination=destination, departure=departure, arrival=arrival,
                origin_timezone='America/Los_Angeles' if origin == 'SEA' else 'America/New_York',
                destination_timezone='America/Los_Angeles' if destination == 'SEA' else 'America/New_York')


def record(id, title, **extra):
    return dict(id=id, title=title, status='open', notes='', **extra)


def seed_trip():
    flights = []
    for id, number, dest, dep, arr, price, note in [
        ('as477', 'XA477', 'EWR', '09:05', '2027-05-22T17:26', 612, 'Complete XA477 / XA370 pairing observed. Recommended candidate, not selected.'),
        ('as356', 'XA356', 'EWR', '07:35', '2027-05-22T16:01', 557, 'From-price; complete return pairing not finalized.'),
        ('as1362', 'XA1362', 'EWR', '15:35', '2027-05-22T23:55', 507, 'From-price; late arrival with a small child. Return pairing not finalized.'),
        ('as314', 'XA314', 'EWR', '21:40', '2027-05-23T06:04', None, 'Overnight: arrives Sunday May 23, not May 22. Fare unknown.'),
        ('as26', 'XA26', 'JFK', '08:05', '2027-05-22T16:31', 627, 'JFK from-price, complete return pairing not finalized.'),
        ('as34', 'XA34', 'JFK', '11:06', '2027-05-22T19:32', 627, 'JFK from-price, complete return pairing not finalized.'),
    ]:
        flights.append(dict(id=id, label=number+' · SEA → '+dest, outbound=leg(number,'SEA',dest,'2027-05-22T'+dep,arr),
            **{'return': leg('XA370','EWR','SEA','2027-05-29T15:20','2027-05-29T18:38') if id=='as477' else None},
            round_trip_per_person=price, price_kind='observed complete round trip' if id=='as477' else 'round-trip from-price; pairing unverified',
            status='candidate', cabin='Main', taxes='Displayed taxes and fees included', nonstop=True, source=EWR if dest=='EWR' else JFK,
            observed='2026-11-08', note=note))
    flights[0]['note'] += ' Older historical snapshot; a later XA356 / XA370 party quote is retained separately.'
    flights.append(dict(id='as356-as370-new', label='XA356 / XA370 · SEA ↔ EWR',
        outbound=leg('XA356','SEA','EWR','2027-05-22T07:35','2027-05-22T16:01'),
        **{'return':leg('XA370','EWR','SEA','2027-05-29T15:20','2027-05-29T18:38')},
        party_total=1670.43, round_trip_per_person=None, price_kind='observed party total',
        status='candidate', cabin='Main', taxes='Displayed party total; no companion discount', nonstop=True,
        source=EWR, observed='2026-11-08',
        note='Example full itinerary quote for two adults and one child, before any companion fare. '
             'A transient observation, not current inventory or a booking. Do not divide it into per-person fares.'))
    return dict(id='nyc-nj-2027',title='New York & New Jersey · Example wedding trip',start='2027-05-22',end='2027-05-29',
        timezone='America/New_York',nonstop_preferred=True,revision=1,selected_scenario='wed',selected_flight=None,
        notes='Example trip. Wednesday May 26 is the preferred NYC → NJ move. Nonstop flights are preferred. '
              'Travel dates remain proposed; nothing is booked.',
        history=[],scenarios=[dict(id=id,title=title,transition=day,nyc_nightly=None,nj_nightly=None,car_daily=None) for id,title,day in [
            ('wed','Wednesday · more settling-in time','2027-05-26'),('thu','Thursday · one more NYC day','2027-05-27')]],flights=flights,
        records={
        'itinerary':[
            dict(id='DATE-TRIP-20270522',title='Travel to New York',date='2027-05-22',time=None,status='tentative',notes='Two adults and one child; flights not selected.'),
            dict(id='DATE-WELCOME-20270527',title='Welcome dinner',date='2027-05-27',time=None,status='confirmed date',notes='Venue and start time unknown.'),
            dict(id='DATE-WEDDING-20270528',title='Wedding · Orchard Barn',date='2027-05-28',time=None,status='confirmed date',notes='Fictional venue. Ceremony/reception times unknown.',url=VENUE),
            dict(id='return-home',title='Return home',date='2027-05-29',time=None,status='tentative',notes='Flights not selected.')],
        'reservations':[
            dict(id='nyc-stay',title='NYC hotel · not booked',status='candidate',notes='One ordinary room. Verify two-adults-plus-child occupancy.'),
            dict(id='nj-stay',title='NJ shared rental · not booked',status='candidate',notes='Shared with extended family. Room-sharing and access needs unknown.'),
            dict(id='flight-reservation',title='Flights · not booked',status='candidate',notes='Two adults and one child, each with own ticket and seat. No flight selected.')],
        'people':[
            dict(id='family',title='Immediate family · two adults and a child',status='proposed',notes='NYC May 22–26 preferred; NJ May 26–29. Child-seat and luggage needs to determine.'),
            dict(id='extended-family',title='Extended family · NJ segment',status='dates unknown',notes='Joining date, transport and access needs unknown. Optional group ground transport to explore.')],
        'budget':[
            dict(id='regular-flights',title='XA477 / XA370 · three regular fares',amount=1836,scope='immediate family',status='observed 2026-11-08',notes='$612 round trip per passenger in Main, including displayed taxes/fees. Example, not live inventory.'),
            dict(id='companion-model',title='Companion fare alternative · not additive',amount=None,scope='immediate family',status='unverified estimate',notes='Two regular fares + $99 + companion taxes/fees. At $612: $1,323 before companion taxes.'),
            dict(id='stay-costs',title='Lodging, rental car and other costs',amount=None,scope='allocation undecided',status='unknown',notes='Budget not set. An extra NJ night replaces one NYC night and may add a rental day.')],
        'decisions':[
            dict(id='transition-preference',title='Prefer Wednesday May 26 transition',status='selected preference',notes='Thursday remains a comparison, not the chosen plan. Not a booking.'),
            record('welcome-details','Find welcome dinner venue and start time'),
            record('wedding-time','Confirm wedding ceremony / reception times'),
            record('family-arrivals','Clarify extended family joining date and optional shared ground transport'),
            record('house-access','Confirm NJ check-in time and any wedding-week obligations'),
            record('companion','Verify companion fare eligibility and full itinerary quote'),
            record('flight-choice','Choose airport and flights after comparing day-of-travel needs')],
        'documents':[
            dict(id='venue',title='Orchard Barn · wedding venue',url=VENUE,status='reference',notes='Fictional venue. Times unknown.'),
            dict(id='directions',title='Orchard Barn · directions',url=VENUE+'/directions',status='reference',notes='Advertised drive times are not door-to-door guarantees; allow traffic, stops and check-in.'),
            dict(id='dates-sheet',title='Shared dates sheet · owning date records',status='reference',notes='A shared sheet owns the dated records. Hub does not deliver reminders or silently synchronize changes.'),
            dict(id='airline',title='Airline EWR flight search',url=EWR,status='historical source',notes='Example observation. EWR times Eastern, SEA Pacific.'),
            dict(id='discount-rules',title='Airline discount-code rules',url='https://airline.example/discount-codes',status='verify before booking',notes='Companion certificate unverified. No raw certificate codes in this workspace.'),
            dict(id='lodging-a',title='Archived lodging · Downtown hotel king room',status='research paused',notes='Example quote for two adults only; family occupancy unresolved. No reservation.'),
            dict(id='lodging-b',title='Archived lodging · Lakeside cabin',url='https://rentals.example/lakeside-cabin',status='research paused',notes='Example snapshot: 4 beds / 3 baths. Many stairs. Not priced for Wednesday arrival.'),
            dict(id='lodging-c',title='Archived lodging · Village inn',url='https://rentals.example/village-inn',status='research paused',notes='Example snapshot: 4 bedrooms / 2.5 baths near the venue. Access details unknown.')],
        'preparation':[dict(id=id,title=title,status='open',done=False,notes='') for id,title in [
            ('child-kit','Child travel kit, snacks and sleep essentials'),('car-seat','Decide car seat and ground-transport arrangement'),
            ('wedding-clothes','Wedding outfits and shoes'),('travel-docs','Check travel documents and airline child requirements'),
            ('confirmations','Save confirmed addresses and reservation links'),('packing','Build final packing checklist')]]})
