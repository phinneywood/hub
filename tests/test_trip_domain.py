from copy import deepcopy
import json
import sqlite3
import pytest
from hub.tools.trip import domain, seed, store


def test_scenarios_and_unknowns():
    data = seed.seed_trip()
    a, b = domain.summarize(data, 'wed'), domain.summarize(data, 'thu')
    assert (a['nyc_nights'], a['nj_nights'], a['full_nyc_days']) == (4,3,3)
    assert (b['nyc_nights'], b['nj_nights'], b['full_nyc_days']) == (5,2,4)
    assert a['costs'] == {'nyc':None,'nj':None,'car':None}
    assert not a['complete'] and a['missing_costs'] == ['nyc','nj','car']
    assert data['records']['itinerary'][1]['time'] is None
    assert data['selected_scenario']=='wed' and data['selected_flight'] is None
    assert all(f['status']=='candidate' for f in data['flights'])
    data['scenarios'][0]['nyc_nightly'] = 100
    assert domain.summarize(data,'wed')['costs']['nyc'] == 400
    assert domain.summarize(data,'thu')['costs']['nyc'] is None


def test_flight_dates_duration_and_fares():
    data = seed.seed_trip()
    overnight=next(f for f in data['flights'] if f['id']=='as314')
    assert overnight['outbound']['arrival'].startswith('2027-05-23')
    assert domain.flight_duration(overnight['outbound']) == 324
    assert domain.flight_duration(data['flights'][0]['outbound']) == 321
    data['selected_flight']='as477'
    totals=domain.summarize(data,'wed')
    assert totals['regular_fares_total']==1836
    assert totals['companion_before_tax']==1323
    assert data['flights'][0]['status']=='candidate'


def test_validation_duplicate_negative_and_unsafe_links():
    data = seed.seed_trip()
    assert domain.validate(data) == data
    for mutate in [
        lambda d:d['records']['preparation'].append(deepcopy(d['records']['preparation'][0])),
        lambda d:d['scenarios'][0].update(nyc_nightly=-1),
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
    assert data['flights'][0]['outbound']['departure'].endswith('-07:00')
    assert data['flights'][0]['outbound']['arrival'].endswith('-04:00')
    nonexistent=deepcopy(data)
    nonexistent['flights'][0]['outbound']['departure']='2027-03-14T02:30:00'
    with pytest.raises(ValueError,match='does not exist'):
        domain.validate(nonexistent)
    for value in (float('nan'),float('inf'),-float('inf')):
        invalid=deepcopy(data);invalid['scenarios'][0]['nyc_nightly']=value
        with pytest.raises(ValueError):domain.validate(invalid)
