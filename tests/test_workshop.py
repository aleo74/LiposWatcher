from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend import db
from backend.accounts import digest
from test_postgres import workers, post

PASSWORD = 'fictional_workshop_password_123'
NEW_PASSWORD = 'fictional_replacement_password_456'


@pytest.fixture
def client():
    with TestClient(app) as c:
        assert c.post('/api/auth/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 201
        yield c


def battery(client, number):
    r = client.post('/api/batteries', json={'number': number, 'brand': 'Flywoo', 'model': '750', 'cells': 2, 'nominal_mah': 750})
    assert r.status_code == 201, r.text
    return r.json()['id']


def entry(client, bid, **data):
    r = client.post(f'/api/batteries/{bid}/entries', json=data)
    assert r.status_code == 201, r.text
    return r.json()['id']


def test_fleet_dates_measurement_absence_and_comparison_isolation(client):
    a, b = battery(client, 'F750-001'), battery(client, 'F750-002')
    entry(client, a, kind='flight', occurred_at='2026-10-10T08:00:00Z')
    entry(client, a, kind='flight', occurred_at='2026-10-10T12:00:00+05:00')
    entry(client, a, kind='baseline', occurred_at='2026-10-11T08:00:00Z', after_v=[3.8, None], resistance_mohm=[5, None], ambient_c=20)
    entry(client, a, kind='baseline', occurred_at='2026-10-12T08:00:00Z', before_v=[None, None], after_v=[None, None], resistance_mohm=[None, None])
    entry(client, a, kind='charge', occurred_at='2026-10-13T08:00:00Z', added_mah=500)
    test_id = entry(client, a, kind='test', occurred_at='2026-10-09T08:00:00Z', discharged_mah=600, cutoff_v=6.5, current_a=.5, ambient_c=22)
    fleet = {r['id']: r for r in client.get('/api/batteries').json()}
    assert fleet[a]['last_used_at'].startswith('2026-10-10T08:00:00')
    assert fleet[a]['last_measured_at'].startswith('2026-10-11T08:00:00')
    assert fleet[a]['last_activity_at'].startswith('2026-10-13T08:00:00')
    assert fleet[a]['charge_count'] == 1
    assert fleet[b]['last_measured_at'] is None and fleet[b]['last_used_at'] is None
    r = client.post('/api/batteries/compare', json={'battery_ids': [a, b]})
    assert r.status_code == 200, r.text
    compared = {r['id']: r for r in r.json()['batteries']}
    test = next(e for e in compared[a]['entries'] if e['id'] == test_id)
    assert test['metrics']['measured_ratio'] == 80
    assert test['ambient_c'] == 22 and test['current_a'] == .5 and test['cutoff_v'] == 6.5
    assert any(e['resistance_mohm'] == [5, None] for e in compared[a]['entries'])
    assert compared[b]['entries'] == []
    assert client.post('/api/batteries/compare', json={'battery_ids': [a, a]}).status_code == 422
    assert client.post('/api/batteries/compare', json={'battery_ids': [a] * 21}).status_code == 422
    assert client.request('POST', '/api/batteries/compare', json={'battery_ids': [a, b]}, csrf=False).status_code == 403
    client.post('/api/users', json={'username': 'bob', 'password': PASSWORD})
    client.post('/api/auth/logout')
    client.post('/api/auth/login', json={'username': 'bob', 'password': PASSWORD})
    own = battery(client, 'F750-001')
    assert client.post('/api/batteries/compare', json={'battery_ids': [own, a]}).status_code == 404
    assert [r['id'] for r in client.get('/api/batteries').json()] == [own]
    client.post('/api/auth/logout')
    assert client.post('/api/batteries/compare', json={'battery_ids': [a, b]}).status_code == 401


def test_change_password_without_email_rotates_session_and_invalidates_old_links(client):
    with TestClient(app) as other_session:
        other_session.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD})
        user_id = client.get('/api/auth/me').json()['id']
        token = 'fictional_reset_token_1234567890'
        with db.connect() as c:
            c.execute('INSERT INTO auth_tokens VALUES (?,?,?,?,?,NULL)', (digest(token), user_id, 'reset', 'fake@example.com', (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()))
        assert client.post('/api/auth/change-password', json={'current_password': 'incorrect', 'password': NEW_PASSWORD}).status_code == 403
        invalid = client.post('/api/auth/change-password', json={'current_password': PASSWORD, 'password': 'short'})
        assert invalid.status_code == 422 and PASSWORD not in invalid.text
        assert client.request('POST', '/api/auth/change-password', json={'current_password': PASSWORD, 'password': NEW_PASSWORD}, csrf=False).status_code == 403
        r = client.post('/api/auth/change-password', json={'current_password': PASSWORD, 'password': NEW_PASSWORD})
        assert r.status_code == 200, r.text
        assert 'lipowatcher_session=' in r.headers['set-cookie']
        assert client.get('/api/auth/me').json()['is_admin'] == 1
        assert other_session.get('/api/auth/me').status_code == 401
        with db.connect() as c:
            assert c.execute('SELECT used_at FROM auth_tokens WHERE token_hash=?', (digest(token),)).fetchone()[0]
        assert other_session.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD}).status_code == 401
        assert other_session.post('/api/auth/login', json={'username': 'admin', 'password': NEW_PASSWORD}).status_code == 200


def test_password_change_limit_is_shared_between_two_workers(client, workers):
    base = workers
    login = post(base, '/api/auth/login', {'username': 'admin', 'password': PASSWORD})
    def attempt():
        return post(base, '/api/auth/change-password', {'current_password': 'wrong', 'password': NEW_PASSWORD}, cookies=login.cookies).status_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: attempt(), range(6)))
    assert results.count(403) == 5 and results.count(429) == 1


def test_concurrent_password_changes_accept_current_password_only_once(client, workers):
    base = workers
    login = post(base, '/api/auth/login', {'username': 'admin', 'password': PASSWORD})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda i: post(base, '/api/auth/change-password', {'current_password': PASSWORD, 'password': NEW_PASSWORD + str(i)}, cookies=login.cookies).status_code, range(2)))
    assert results.count(200) == 1
    assert all(code in (200, 401, 403) for code in results)
