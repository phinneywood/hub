from copy import deepcopy
import json
import sqlite3
import pytest
from hub.tools.trip import domain, seed, store


def test_scenarios_and_unknowns():
    data = seed.seed_trip()
    a, b = domain.summarize(data, 'wed'), domain.summarize(data, 'thu')
    assert (a['first_nights'], a['second_nights'], a['full_first_days']) == (4,3,3)
    assert (b['first_nights'], b['second_nights'], b['full_first_days']) == (5,2,4)
    assert a['costs'] == {'first':None,'second':None,'car':None}
    assert not a['complete'] and a['missing_costs'] == ['first','second','car']
    assert data['records']['itinerary'][1]['time'] is None
    assert data['selected_scenario']=='wed' and data['selected_flight'] is None
    assert all(f['status']=='candidate' for f in data['flights'])
    data['scenarios'][0]['first_nightly'] = 100
    assert domain.summarize(data,'wed')['costs']['first'] == 400
    assert domain.summarize(data,'thu')['costs']['first'] is None


def test_flight_dates_duration_and_fares():
    data = seed.seed_trip()
    overnight=next(f for f in data['flights'] if f['id']=='f4')
    assert overnight['outbound']['arrival'].startswith('2031-10-05')
    assert domain.flight_duration(overnight['outbound']) == 144
    assert domain.flight_duration(data['flights'][0]['outbound']) == 141
    data['selected_flight']='f1'
    totals=domain.summarize(data,'wed')
    assert totals['regular_fares_total']==1236
    assert totals['companion_before_tax']==923
    assert data['flights'][0]['status']=='candidate'


def test_validation_duplicate_negative_and_unsafe_links():
    data = seed.seed_trip()
    assert domain.validate(data) == data
    for mutate in [
        lambda d:d['records']['preparation'].append(deepcopy(d['records']['preparation'][0])),
        lambda d:d['scenarios'][0].update(first_nightly=-1),
        lambda d:d['records']['documents'][0].update(url='javascript:alert(1)'),
        lambda d:d.update(selected_flight='missing'),
        lambda d:d['records']['itinerary'][0].update(time='25:10'),
    ]:
        damaged=deepcopy(data);mutate(damaged)
        with pytest.raises(ValueError):domain.validate(damaged)


def test_store_repeated_seed_restart_import_and_revision(tmp_path,monkeypatch):
    monkeypatch.setattr(store.config,'DATA_DIR',tmp_path)
    first=store.load();second=store.load()
    assert first==second and first['revision']==1
    edited=deepcopy(first);edited['notes']='Synthetic test note';edited['records']['preparation'][0]['done']=True
    edited['selected_scenario']='thu'
    saved=store.save(edited,1)
    assert saved['revision']==2
    assert store.load()['notes']=='Synthetic test note'
    assert store.load()['records']['preparation'][0]['done'] is True
    assert store.load()['selected_scenario']=='thu'
    with pytest.raises(ValueError,match='another tab'):store.save(first,1)
    # Portable import replaces same ID, no duplicated records, and retains history.
    exported=json.loads(json.dumps(saved)); imported=store.save(exported,2)
    assert len(imported['records']['preparation'])==len(first['records']['preparation'])
    assert imported['revision']==3
    assert imported['history'][0]['selected_scenario']=='wed'
    with sqlite3.connect(tmp_path/'trip.db') as conn:
        assert conn.execute('select count(*) from trip').fetchone()[0]==1
        assert conn.execute('select count(*) from trip_versions').fetchone()[0]==2
    assert (tmp_path/'trip.db').stat().st_mode & 0o777 == 0o600


def test_airport_offsets_dst_and_invalid_numeric_inputs():
    data=seed.seed_trip()
    assert data['flights'][0]['outbound']['departure'].endswith('-06:00')
    assert data['flights'][0]['outbound']['arrival'].endswith('-05:00')
    nonexistent=deepcopy(data)
    nonexistent['flights'][0]['outbound']['departure']='2031-03-09T02:30:00'
    with pytest.raises(ValueError,match='does not exist'):
        domain.validate(nonexistent)
    for value in (float('nan'),float('inf'),-float('inf')):
        invalid=deepcopy(data);invalid['scenarios'][0]['first_nightly']=value
        with pytest.raises(ValueError):domain.validate(invalid)


def saved_trip(tmp_path, document):
    """Put a trip in the database the way an earlier install would have, before Hub ever loads."""
    with sqlite3.connect(tmp_path/'trip.db') as conn:
        conn.execute('CREATE TABLE trip (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL)')
        conn.execute('CREATE TABLE trip_versions (revision INTEGER PRIMARY KEY, saved_at TEXT NOT NULL, document TEXT NOT NULL)')
        conn.execute('INSERT INTO trip VALUES (?, ?, ?)', (document['id'], 4, json.dumps(document)))


def stored(tmp_path):
    with sqlite3.connect(tmp_path/'trip.db') as conn:
        return conn.execute('select id, revision, document from trip').fetchall()


def older_trip():
    """A trip saved by an earlier version: its own ID, older cost field names, no stay names."""
    data = seed.seed_trip()
    data.update(id='alex-and-sam-trip', revision=4)
    del data['first_stay'], data['second_stay']
    for scenario in data['scenarios']:
        scenario['nyc_nightly'] = scenario.pop('first_nightly')
        scenario['nj_nightly'] = scenario.pop('second_nightly')
    data['scenarios'][0]['nyc_nightly'] = 150
    return data


def test_a_saved_trip_is_read_exactly_as_stored_and_never_replaced_by_the_starter_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, 'DATA_DIR', tmp_path)
    saved_trip(tmp_path, older_trip())
    before = stored(tmp_path)
    trip = store.load()
    assert trip == json.loads(before[0][2])  # no renamed or added fields
    assert trip['id'] == 'alex-and-sam-trip' and trip['scenarios'][0]['nyc_nightly'] == 150
    assert stored(tmp_path) == before  # no seed row, nothing rewritten
    assert domain.summarize(trip, 'wed')['costs']['first'] == 600  # older names still count


def test_stay_names_come_from_the_trip_then_private_settings_then_neutral_words(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, 'DATA_DIR', tmp_path)
    saved_trip(tmp_path, older_trip())
    trip = store.load()
    assert store.stay_names(trip) == {'first': 'First stay', 'second': 'Second stay'}
    (tmp_path/'household.json').write_text(json.dumps({'trip': {'first_stay': 'Harbor Town', 'second_stay': 'Hill Farm'}}))
    assert store.stay_names(trip) == {'first': 'Harbor Town', 'second': 'Hill Farm'}
    assert store.stay_names({**trip, 'first_stay': 'Own name'})['first'] == 'Own name'
    assert 'first_stay' not in store.load()


def test_saving_an_older_trip_stores_the_new_field_names(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, 'DATA_DIR', tmp_path)
    saved_trip(tmp_path, older_trip())
    saved = store.save(store.load(), 4)
    assert saved['scenarios'][0]['first_nightly'] == 150 and 'nyc_nightly' not in saved['scenarios'][0]
    assert json.loads(stored(tmp_path)[0][2])['scenarios'][0]['first_nightly'] == 150
