"""Trip HTTP contract tests on a minimal app; no Hub startup workers or real data."""
from copy import deepcopy
import json
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest
from hub import config
from hub.auth import require_owner
from hub.main import same_origin_only, no_store
from hub.tools.trip import pages, seed, domain


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {'owner@example.invalid'})
    app=FastAPI(dependencies=[Depends(require_owner)])
    app.middleware('http')(same_origin_only)
    app.middleware('http')(no_store)
    app.include_router(pages.router,prefix='/trip')
    return TestClient(app)


def post(client,doc,revision=1):
    return client.post('/trip/save',json={'trip':doc,'expected_revision':revision},headers={'Origin':'http://testserver'})


def test_initial_load_unknowns_export_and_save_conflict(client):
    response=client.get('/trip/data');assert response.status_code==200
    doc=response.json()
    assert response.headers['cache-control']=='no-store'
    assert response.headers['content-security-policy']=="default-src 'self'; frame-ancestors 'none'"
    assert doc['scenarios'][0]['first_nightly'] is None
    assert doc['records']['itinerary'][1]['time'] is None
    assert client.get('/trip/export').json()==doc
    doc['notes']='Synthetic note'
    assert post(client,doc).status_code==200
    assert post(client,doc).status_code==409
    assert client.get('/trip/data').json()['notes']=='Synthetic note'
    assert client.get('/trip/data').json()['revision']==2


def test_auth_and_same_origin_fail_closed(client):
    assert client.get('/trip/data',headers={'tailscale-user-login':'other@example.invalid'}).status_code==403
    assert client.get('/trip/data',headers={'tailscale-user-login':'owner@example.invalid'}).status_code==200
    doc=seed.seed_trip()
    for headers in ({},{'Origin':'https://evil.invalid'},{'Sec-Fetch-Site':'cross-site'}):
        assert client.post('/trip/save',json={'trip':doc,'expected_revision':1},headers=headers).status_code==403
    assert client.get('/trip/data').json()['revision']==1


@pytest.mark.parametrize('mutate',[
    lambda d:d.update(title=[]),
    lambda d:d.update(start='2031-10-04',end='9999-12-31'),
    lambda d:d.update(records=[]),
    lambda d:d['records']['documents'][0].update(url='javascript:alert(1)'),
    lambda d:d['records']['documents'][0].update(url='https://user:secret@example.invalid/'),
    lambda d:d['flights'][0].update(source='data:text/html,bad'),
    lambda d:d['flights'][0].update(outbound=[]),
    lambda d:d['flights'][0]['outbound'].update(origin_timezone='missing/zone'),
    lambda d:d['flights'][0]['outbound'].update(departure='2031-10-04T09:05:00+00:00'),
    lambda d:d['flights'][0].update(party_total=-3),
    lambda d:d['scenarios'][0].update(first_nightly='100'),
    lambda d:d['scenarios'][0].update(first_nightly=True),
    lambda d:d['scenarios'][0].update(second_nightly=1e100),
    lambda d:d.update(first_stay=''),
    lambda d:d.update(timezone='Not/AZone'),
    lambda d:d.update(id='another-trip'),
    lambda d:d['records']['preparation'][0].update(done='false'),
    lambda d:d['records']['itinerary'][0].update(date='20311004'),
    lambda d:d.update(selected_flight=['f1']),
])
def test_malformed_import_rejected_without_changes(client,mutate):
    doc=client.get('/trip/data').json();mutate(doc)
    response=post(client,doc)
    assert response.status_code==400,response.text
    assert client.get('/trip/data').json()['revision']==1


def test_import_does_not_forge_history_or_duplicate_records(client):
    doc=client.get('/trip/data').json();doc['history']=[{'revision':999,'notes':'forged'}];doc['revision']=99
    saved=post(client,doc).json()
    assert saved['revision']==2 and len(saved['history'])==1
    assert saved['history'][0]['revision']==1
    again=post(client,saved,2).json()
    assert len(again['flights'])==len(doc['flights'])
    assert len(again['records']['documents'])==len(doc['records']['documents'])


def test_legacy_local_timestamp_normalized_and_new_party_quote(client):
    doc=client.get('/trip/data').json()
    doc['flights'][0]['outbound']['departure']='2031-10-04T09:05'
    saved=post(client,doc).json()
    assert saved['flights'][0]['outbound']['departure']=='2031-10-04T09:05:00-06:00'
    latest=next(f for f in saved['flights'] if f['id']=='f2-party')
    assert latest['party_total']==1170.43 and latest['round_trip_per_person'] is None
    assert latest['return']['arrival']=='2031-10-11T16:38:00-06:00'


def test_page_heading_and_dates_come_from_the_saved_trip(client):
    page=client.get('/trip').text
    assert 'Port Ellery &amp; Larch Valley' in page and 'OCT 4–11, 2031' in page
    assert pages.dates('2031-10-28','2031-11-03')=='OCT 28 – NOV 3, 2031'
    assert pages.dates('2031-12-30','2032-01-02')=='DEC 30, 2031 – JAN 2, 2032'


def test_data_and_export_return_an_older_saved_trip_exactly_as_stored(client, tmp_path):
    """A release that changes no saved data must not change what /trip/data returns
    (the deployer compares it before and after switching versions)."""
    import sqlite3
    older = seed.seed_trip()
    older.update(id='alex-and-sam-trip', revision=3)
    del older['first_stay'], older['second_stay']
    for scenario in older['scenarios']:
        scenario['nyc_nightly'] = scenario.pop('first_nightly')
        scenario['nj_nightly'] = scenario.pop('second_nightly')
    with sqlite3.connect(tmp_path/'trip.db') as conn:
        conn.execute('CREATE TABLE trip (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL)')
        conn.execute('CREATE TABLE trip_versions (revision INTEGER PRIMARY KEY, saved_at TEXT NOT NULL, document TEXT NOT NULL)')
        conn.execute('INSERT INTO trip VALUES (?, ?, ?)', (older['id'], 3, json.dumps(older)))
    assert client.get('/trip/data').json() == older
    assert client.get('/trip/export').json() == older
    page = client.get('/trip').text
    assert '"first": "First stay"' in page and 'nyc_nightly' not in page
