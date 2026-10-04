import json
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from email import message_from_bytes
from email.policy import default

import pytest
from aiosmtpd.controller import Controller
from fastapi.testclient import TestClient

from backend import db, mail
from backend.app import app
from test_postgres import workers, post, independent_while_locked

PASSWORD = 'fictional_test_password_123'


class Inbox:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(message_from_bytes(envelope.content, policy=default))
        return '250 OK'

    def token(self, email, purpose):
        messages = [m for m in self.messages if m['To'] == email and f'/{purpose}#token=' in m.get_content()]
        assert messages, 'Expected a message delivered through the actual SMTP server'
        return re.search(r'#token=([A-Za-z0-9_-]+)', messages[-1].get_content()).group(1)


@pytest.fixture
def world(tmp_path, monkeypatch):
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    inbox = Inbox()
    smtp = Controller(inbox, hostname='127.0.0.1', port=port)
    smtp.start()
    for key, value in {'APP_ENV': 'test', 'PUBLIC_URL': 'http://testserver', 'SMTP_HOST': '127.0.0.1',
                       'SMTP_PORT': str(port), 'SMTP_TLS': 'none', 'SMTP_FROM': 'noreply@example.com',
                       'AUTH_REQUEST_MAX': '100', 'PUBLIC_REGISTRATION': 'true'}.items():
        monkeypatch.setenv(key, value)
    clients = []
    with TestClient(app) as admin:
        assert admin.post('/api/auth/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 201
        for name in ('alice', 'bob', 'eve'):
            client = TestClient(app)
            clients.append(client)
            email = name+'@example.com'
            assert client.post('/api/auth/register', json={'email': email, 'password': PASSWORD}).status_code == 202
            assert client.post('/api/auth/login', json={'username': email, 'password': PASSWORD}).status_code == 200
            assert client.post('/api/auth/verify-email', json={'token': inbox.token(email, 'verify')}).status_code == 200
            assert client.post('/api/auth/login', json={'username': email, 'password': PASSWORD}).status_code == 200
        yield admin, *clients, inbox
    for client in clients:
        client.close()
    smtp.stop()


def pack(client, number='001', **extras):
    r = client.post('/api/batteries', json={'number': number, 'cells': 3, 'nominal_mah': 1300, **extras})
    assert r.status_code == 201, r.text
    return r.json()['id']


def entry(client, battery, **extras):
    r = client.post(f'/api/batteries/{battery}/entries', json={'kind': 'charge', 'added_mah': 780,
                 'before_v': [3.7, None, 3.72], 'resistance_mohm': [7, None, 8], **extras})
    assert r.status_code == 201, r.text
    return r.json()


def proposal(client, battery, recipient='bob@example.com', notes=None):
    body = {'recipient_email': recipient, 'share_notes': notes or []}
    preview = client.post(f'/api/batteries/{battery}/transfer-preview', json=body)
    assert preview.status_code == 200, preview.text
    r = client.post(f'/api/batteries/{battery}/transfers', json=body|{'snapshot_hash': preview.json()['snapshot_hash']})
    assert r.status_code == 201, r.text
    return r.json()


def accept(client, transfer, number='002'):
    return client.post('/api/transfers/'+transfer['id']+'/accept', json={'number': number, 'confirmed': True})


def test_registration_legacy_email_and_privileges(world):
    admin, alice, bob, eve, inbox = world
    me = alice.get('/api/auth/me').json()
    assert me['email_verified_at'] and me['is_admin'] == 0
    assert alice.post('/api/users', json={'username': 'intruder', 'password': PASSWORD}).status_code == 403
    assert alice.post('/api/auth/register', json={'email': 'alice@example.com', 'password': PASSWORD}).json() == alice.post('/api/auth/register', json={'email': 'new@example.com', 'password': PASSWORD, 'is_admin': 1}).json()
    with db.connect() as connection:
        assert connection.execute("SELECT is_admin FROM users WHERE email='new@example.com'").fetchone()[0] == 0
    b = pack(admin)
    assert admin.post('/api/auth/email', json={'email': 'legacy@example.com', 'password': PASSWORD}).status_code == 202
    assert admin.get('/api/auth/me').json()['email'] is None
    token = inbox.token('legacy@example.com', 'verify')
    assert admin.post('/api/auth/verify-email', json={'token': token}).status_code == 200
    assert admin.post('/api/auth/verify-email', json={'token': token}).status_code == 400
    assert admin.get('/api/batteries/'+b).status_code == 200
    assert admin.get('/api/auth/me').json()['username'] == 'admin'
    with db.connect() as connection:
        row = connection.execute('SELECT token_hash FROM auth_tokens WHERE email=?', ('legacy@example.com',)).fetchone()
        assert token != row['token_hash'] and len(row['token_hash']) == 64


def test_verification_reset_expiry_replay_sessions(world):
    admin, alice, bob, eve, inbox = world
    assert alice.post('/api/auth/forgot-password', json={'email': 'missing@example.com'}).json() == alice.post('/api/auth/forgot-password', json={'email': 'alice@example.com'}).json()
    token = inbox.token('alice@example.com', 'reset')
    with db.connect() as connection:
        connection.execute("UPDATE auth_tokens SET expires_at='2000-01-01' WHERE purpose='reset'")
    assert alice.post('/api/auth/reset-password', json={'token': token, 'password': PASSWORD+'new'}).status_code == 400
    alice.post('/api/auth/forgot-password', json={'email': 'alice@example.com'})
    token = inbox.token('alice@example.com', 'reset')
    assert alice.post('/api/auth/reset-password', json={'token': token, 'password': PASSWORD+'new'}).status_code == 200
    assert alice.get('/api/auth/me').status_code == 401
    assert alice.post('/api/auth/reset-password', json={'token': token, 'password': PASSWORD}).status_code == 400
    assert alice.post('/api/auth/login', json={'username': 'alice@example.com', 'password': PASSWORD}).status_code == 401
    assert alice.post('/api/auth/login', json={'username': 'alice@example.com', 'password': PASSWORD+'new'}).status_code == 200
    bob.post('/api/auth/email', json={'email': 'next@example.com', 'password': PASSWORD})
    token = inbox.token('next@example.com', 'verify')
    with db.connect() as connection:
        connection.execute("UPDATE auth_tokens SET expires_at='2000-01-01' WHERE email='next@example.com'")
    assert bob.post('/api/auth/verify-email', json={'token': token}).status_code == 400
    assert bob.get('/api/auth/me').json()['email'] == 'bob@example.com'


def test_verification_bound_to_account_and_invitation_email_change(world):
    admin, alice, bob, eve, inbox = world
    bob.post('/api/auth/email', json={'email': 'replacement@example.com', 'password': PASSWORD})
    token = inbox.token('replacement@example.com', 'verify')
    assert eve.post('/api/auth/verify-email', json={'token': token}).status_code == 400
    b = pack(alice)
    t = proposal(alice, b)
    assert bob.post('/api/auth/verify-email', json={'token': token}).status_code == 200
    assert accept(bob, t).status_code == 404
    assert bob.get('/api/transfers/'+t['id']).status_code == 404
    assert bob.get('/api/transfers').json() == []


def test_recovery_before_verification_does_not_bypass_verification(world):
    admin, alice, bob, eve, inbox = world
    email = 'unverified-recovery@example.com'
    with TestClient(app) as client:
        assert client.post('/api/auth/register', json={'email': email, 'password': PASSWORD}).status_code == 202
        assert client.post('/api/auth/forgot-password', json={'email': email}).status_code == 202
        token = inbox.token(email, 'reset')
        assert client.post('/api/auth/reset-password', json={'token': token, 'password': PASSWORD+'new'}).status_code == 200
        assert client.post('/api/auth/login', json={'username': email, 'password': PASSWORD+'new'}).status_code == 200
        assert client.get('/api/auth/me').json()['email_verified_at'] is None
        b = pack(client)
        assert client.post(f'/api/batteries/{b}/transfer-preview', json={'recipient_email': 'bob@example.com'}).status_code == 403
        assert client.post('/api/auth/resend-verification').status_code == 202
        assert client.post('/api/auth/verify-email', json={'token': inbox.token(email, 'verify')}).status_code == 200


def test_tokens_not_reflected_and_link_replay(world):
    admin, alice, bob, eve, inbox = world
    r = alice.post('/api/auth/register', json={'email': 'bad', 'password': 'SECRET'})
    assert r.status_code == 422 and 'SECRET' not in r.text
    b = pack(alice)
    t = proposal(alice, b)
    token = inbox.token('bob@example.com', 'transfer')
    assert accept(bob, t).status_code == 200
    assert bob.post('/api/transfers/resolve', json={'token': token}).status_code == 410


def test_csrf_limits_mail_configuration(world, monkeypatch, caplog):
    admin, alice, bob, eve, inbox = world
    assert alice.request('POST', '/api/auth/logout', csrf=False).status_code == 403
    assert alice.post('/api/auth/logout', headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    assert alice.post('/api/auth/logout', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert alice.post('/api/auth/logout', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    with db.connect() as connection:
        connection.execute('DELETE FROM request_limits')
    monkeypatch.setenv('AUTH_REQUEST_MAX', '2')
    for _ in range(2):
        assert eve.post('/api/auth/forgot-password', json={'email': 'missing@example.com'}).status_code == 202
    assert eve.post('/api/auth/forgot-password', json={'email': 'missing@example.com'}).status_code == 429
    for _ in range(2):
        assert eve.post('/api/auth/register', json={'email': 'signup@example.com', 'password': PASSWORD}).status_code == 202
    assert eve.post('/api/auth/register', json={'email': 'signup@example.com', 'password': PASSWORD}).status_code == 429
    monkeypatch.setenv('APP_ENV', 'production')
    assert not mail.available()
    monkeypatch.setenv('AUTH_REQUEST_MAX', '100')
    r = alice.post('/api/auth/register', json={'email': 'another@example.com', 'password': PASSWORD})
    assert r.status_code == 503 and '#token' not in r.text
    mail.deliver('test@example.com', 'verify', 'temporary_test_token')
    assert 'temporary_test_token' not in caplog.text and 'test@example.com' not in caplog.text


def test_unverified_account_cannot_transfer(world):
    admin, alice, bob, eve, inbox = world
    b = pack(admin)
    assert admin.post(f'/api/batteries/{b}/transfer-preview', json={'recipient_email': 'bob@example.com'}).status_code == 403
    alice.post('/api/auth/register', json={'email': 'unverified@example.com', 'password': PASSWORD})
    with TestClient(app) as unverified:
        unverified.post('/api/auth/login', json={'username': 'unverified@example.com', 'password': PASSWORD})
        b = pack(alice)
        t = proposal(alice, b, 'unverified@example.com')
        assert unverified.get('/api/transfers').json() == []
        assert accept(unverified, t).status_code == 403


def test_transfer_privacy_isolation_stable_id_and_lost_access(world):
    admin, alice, bob, eve, inbox = world
    b = pack(alice, notes='private battery', acquired_on='2026-01-01')
    lot = alice.post('/api/lots', json={'name': 'private lot', 'seller': 'private seller', 'total_price': 99}).json()
    assert alice.put('/api/lots/'+lot['id']+'/members', json={'battery_ids': [b]}).status_code == 200
    ch = alice.post('/api/chargers', json={'name': 'personal device', 'brand': 'Technical brand', 'model': 'Technical model', 'channels': 2}).json()
    e = entry(alice, b, charger_id=ch['id'], charger_channel=2, aircraft='private aircraft', notes='private notes', heat='private incident')
    qr = alice.get(f'/api/batteries/{b}/qr.png').content
    t = proposal(alice, b, notes=[f'entry:{e["id"]}:heat'])
    token = inbox.token('bob@example.com', 'transfer')
    for stranger in (eve, admin):
        assert stranger.get('/api/transfers/'+t['id']).status_code == 404
        assert stranger.post('/api/transfers/resolve', json={'token': token}).status_code in (403, 404)
        assert accept(stranger, t).status_code in (403, 404)
    assert alice.post(f'/api/batteries/{b}/transfer-preview', json={'recipient_email': 'alice@example.com'}).status_code == 422
    view = bob.post('/api/transfers/resolve', json={'token': token}).json()
    assert view['status'] == 'pending' and alice.get('/api/batteries/'+b).status_code == 200
    encoded = json.dumps(view)
    for private in ('private battery', 'private notes', 'private aircraft', 'private seller', 'personal device', lot['id'], ch['id'], 'alice@example.com'):
        assert private not in encoded
    assert 'private incident' in encoded and 'Technical brand' in encoded
    assert accept(bob, t).status_code == 200
    assert accept(bob, t).status_code == 409
    assert bob.get(f'/api/batteries/{b}/qr.png').content == qr
    for url in (f'/api/batteries/{b}', f'/api/batteries/{b}/qr.png'):
        assert alice.get(url).status_code == 404
    assert alice.put(f'/api/batteries/{b}/entries/{e["id"]}', json={'kind': 'charge', 'added_mah': 1}).status_code == 404
    assert alice.delete(f'/api/batteries/{b}/entries/{e["id"]}').status_code == 404
    assert b not in alice.get('/api/export/batteries.csv').text
    assert e['id'] not in alice.get('/api/export/entries.csv').text
    receipt = alice.get('/api/transfers/'+t['id']).json()
    assert 'snapshot' not in receipt and 'recipient_email' not in receipt
    current = bob.get('/api/batteries/'+b).json()
    assert current['id'] == b and current['number'] == '002' and current['lot_id'] is None and current['acquired_on'] is None
    assert current['entries'][0]['before_v'] == [3.7, None, 3.72]
    assert 'author_id' not in current['entries'][0]
    assert bob.put(f'/api/batteries/{b}/entries/{e["id"]}', json={'kind': 'charge', 'added_mah': 1}).status_code == 409
    assert bob.delete(f'/api/batteries/{b}/entries/{e["id"]}').status_code == 409
    assert bob.delete('/api/batteries/'+b).status_code == 409


@pytest.mark.parametrize('action,status', [('cancel', 'canceled'), ('refuse', 'refused'), ('expire', 'expired')])
def test_terminal_states(world, action, status):
    admin, alice, bob, eve, inbox = world
    b = pack(alice)
    t = proposal(alice, b)
    if action == 'expire':
        with db.connect() as connection:
            connection.execute("UPDATE transfers SET expires_at='2000-01-01' WHERE id=?", (t['id'],))
    else:
        c = alice if action == 'cancel' else bob
        assert c.post('/api/transfers/'+t['id']+'/'+action).status_code == 200
    assert bob.get('/api/transfers/'+t['id']).json()['status'] == status
    assert accept(bob, t).status_code == 409
    assert alice.get('/api/batteries/'+b).status_code == 200


def test_preview_changes_and_one_pending(world):
    admin, alice, bob, eve, inbox = world
    b = pack(alice)
    e = entry(alice, b)
    preview = alice.post(f'/api/batteries/{b}/transfer-preview', json={'recipient_email': 'bob@example.com'}).json()
    assert alice.post(f'/api/batteries/{b}/transfers', json={'recipient_email': 'eve@example.com', 'snapshot_hash': preview['snapshot_hash']}).status_code == 409
    t = proposal(alice, b)
    p = alice.post(f'/api/batteries/{b}/transfer-preview', json={'recipient_email': 'bob@example.com'}).json()
    assert alice.post(f'/api/batteries/{b}/transfers', json={'recipient_email': 'bob@example.com', 'snapshot_hash': p['snapshot_hash']}).status_code == 409
    assert alice.put(f'/api/batteries/{b}/entries/{e["id"]}', json={'kind': 'charge', 'added_mah': 781}).status_code == 200
    # Editing and then reverting must not reactivate an old invitation.
    assert alice.put(f'/api/batteries/{b}/entries/{e["id"]}', json={'kind': 'charge', 'added_mah': 780, 'before_v': [3.7,None,3.72], 'resistance_mohm': [7,None,8]}).status_code == 200
    assert accept(bob, t).status_code == 409
    assert alice.get('/api/transfers/'+t['id']).json()['status'] == 'invalidated'
    body = {'recipient_email': 'bob@example.com'}
    p = alice.post(f'/api/batteries/{b}/transfer-preview', json=body).json()
    entry(alice, b)
    assert alice.post(f'/api/batteries/{b}/transfers', json=body|{'snapshot_hash': p['snapshot_hash']}).status_code == 409


def test_number_collision_and_concurrent_acceptance(world):
    admin, alice, bob, eve, inbox = world
    pack(bob, '001')
    b = pack(alice)
    t = proposal(alice, b)
    assert accept(bob, t, '001').status_code == 409
    assert alice.get('/api/batteries/'+b).status_code == 200
    def concurrent():
        with TestClient(app) as c:
            c.cookies.update(bob.cookies)
            return accept(c, t, '003').status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _: concurrent(), range(2)))
    assert outcomes == [200, 409]
    assert len(bob.get('/api/batteries').json()) == 2


def test_successive_transfers_provenance_and_annotations(world):
    admin, alice, bob, eve, inbox = world
    b = pack(alice)
    old = entry(alice, b)
    t = proposal(alice, b)
    assert accept(bob, t).status_code == 200
    annotation = bob.post(f'/api/batteries/{b}/entries/{old["id"]}/annotations', json={'text': 'Correction technique déclarée'}).json()
    new = entry(bob, b, added_mah=500)
    assert old['origin_ref'] != new['origin_ref']
    t2 = proposal(bob, b, 'eve@example.com', ['annotation:'+annotation['id']])
    assert accept(eve, t2).status_code == 200
    received = eve.get('/api/batteries/'+b).json()
    assert len(received['transfer_chain']) == 2
    assert set(e['origin_ref'] for e in received['entries']) == {old['origin_ref'], new['origin_ref']}
    record = next(e for e in received['entries'] if e['id'] == old['id'])
    assert record['annotations'][0]['text'] == 'Correction technique déclarée'
    assert record['annotations'][0]['origin_ref'] == new['origin_ref']
    exported = eve.get('/api/export/entries.csv').text
    assert 'Correction technique déclarée' in exported and 'author_id' not in exported
    assert 'bob@example.com' not in json.dumps(received)
    assert bob.get('/api/batteries/'+b).status_code == 404
    assert eve.delete(f'/api/batteries/{b}/entries/{new["id"]}').status_code == 409
    assert eve.post(f'/api/batteries/{b}/entries/{new["id"]}/annotations', json={'text': 'Observation supplémentaire'}).status_code == 201


def test_legacy_device_identification_frozen_before_consent(world):
    admin, alice, bob, eve, inbox = world
    b = pack(alice)
    ch = alice.post('/api/chargers', json={'name': 'Private name', 'brand': 'Recorded brand', 'model': 'Model A', 'channels': 2}).json()
    e = entry(alice, b, charger_id=ch['id'], charger_channel=1)
    with db.connect() as connection:
        # Simulate a pre-migration reading, which has no original device snapshot.
        connection.execute('UPDATE entries SET charger_snapshot=NULL WHERE id=?', (e['id'],))
    body = {'recipient_email': 'bob@example.com'}
    preview = alice.post(f'/api/batteries/{b}/transfer-preview', json=body).json()
    device = preview['snapshot']['entries'][0]['charger']
    assert device['brand'] == 'Recorded brand' and 'non vérifiée' in device['identification_basis']
    assert alice.put('/api/chargers/'+ch['id'], json={'name': 'Another private name', 'brand': 'Changed private device brand', 'channels': 2}).status_code == 200
    t = alice.post(f'/api/batteries/{b}/transfers', json=body|{'snapshot_hash': preview['snapshot_hash']}).json()
    assert accept(bob, t).status_code == 200
    received = bob.get('/api/batteries/'+b).json()['entries'][0]
    assert json.loads(received['charger_snapshot'])['brand'] == 'Recorded brand'
    assert 'private' not in received['charger'].lower()


def test_actual_two_workers_transfer_once(world, workers):
    admin, alice, bob, eve, inbox = world
    battery_id = pack(alice)
    entry(alice, battery_id)
    transfer = proposal(alice, battery_id)
    def take(_):
        return post(workers, '/api/transfers/'+transfer['id']+'/accept',
                    {'number': '001', 'confirmed': True}, dict(bob.cookies)).status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(take, range(8)))
    assert statuses.count(200) == 1 and statuses.count(409) == 7
    assert alice.get('/api/batteries/'+battery_id).status_code == 404
    assert alice.get('/api/batteries/'+battery_id+'/qr.png').status_code == 404
    assert battery_id not in alice.get('/api/export/batteries.csv').text
    assert eve.get('/api/batteries/'+battery_id).status_code == 404
    assert bob.get('/api/batteries/'+battery_id).status_code == 200
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM transfers WHERE status='accepted'").fetchone()[0] == 1
        assert connection.execute('SELECT user_id FROM batteries WHERE id=?', (battery_id,)).fetchone()[0] == bob.get('/api/auth/me').json()['id']


def test_actual_two_workers_email_limit(world, monkeypatch, request):
    admin, alice, bob, eve, inbox = world
    with db.connect() as connection:
        connection.execute('DELETE FROM request_limits')
    from backend.accounts import digest
    with db.connect() as connection:
        for value in ('email:ip:127.0.0.1', 'email:email:bob@example.com'):
            connection.execute('INSERT INTO request_limits VALUES (?,999,1)', (digest(value),))
    monkeypatch.setenv('AUTH_REQUEST_MAX', '3')
    base = request.getfixturevalue('workers')
    before = len(inbox.messages)
    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(lambda _: post(base, '/api/auth/forgot-password',
                                    {'email': 'bob@example.com'}).status_code, range(12)))
    assert statuses.count(202) == 3 and statuses.count(429) == 9
    assert len(inbox.messages) == before + 3  # Actual SMTP deliveries, not a mock.
    with db.connect() as connection:
        rows = connection.execute('SELECT count FROM request_limits').fetchall()
        assert len(rows) == 2 and all(row[0] == 12 for row in rows)


def independent_while_battery_locked(battery_id, first, second):
    return independent_while_locked('SELECT id FROM batteries WHERE id=? FOR UPDATE', (battery_id,), first, second)


def test_two_workers_independent_transfers(world, workers):
    admin, alice, bob, eve, inbox = world
    a, b = pack(alice, '001'), pack(alice, '002')
    ta, tb = proposal(alice, a), proposal(alice, b)
    def take(transfer, number):
        return post(workers, '/api/transfers/'+transfer['id']+'/accept',
                    {'number': number, 'confirmed': True}, dict(bob.cookies)).status_code
    assert independent_while_battery_locked(a, lambda: take(ta, '101'), lambda: take(tb, '102')) == (200, 200)


def test_two_workers_independent_entries(world, workers):
    admin, alice, bob, eve, inbox = world
    a, b = pack(alice, '001'), pack(alice, '002')
    def charge(battery):
        return post(workers, f'/api/batteries/{battery}/entries',
                    {'kind': 'charge', 'added_mah': 780}, dict(alice.cookies)).status_code
    assert independent_while_battery_locked(a, lambda: charge(a), lambda: charge(b)) == (201, 201)


def test_two_workers_independent_email_buckets(world, workers):
    from backend.accounts import digest
    admin, alice, bob, eve, inbox = world
    key = digest('email:ip:192.0.2.10')
    with db.connect() as connection:
        connection.execute('INSERT INTO request_limits VALUES (?,0,1)', (key,))
    before = len(inbox.messages)
    def recover(email, address):
        return post(workers, '/api/auth/forgot-password', {'email': email},
                    headers={'X-Forwarded-For': address}).status_code
    assert independent_while_locked('SELECT bucket FROM request_limits WHERE bucket=? FOR UPDATE', (key,),
                lambda: recover('bob@example.com', '192.0.2.10'),
                lambda: recover('alice@example.com', '192.0.2.11')) == (202, 202)
    assert len(inbox.messages) == before + 2


def test_two_workers_reset_token_once(world, workers):
    admin, alice, bob, eve, inbox = world
    assert bob.post('/api/auth/forgot-password', json={'email': 'bob@example.com'}).status_code == 202
    token = inbox.token('bob@example.com', 'reset')
    def reset(_):
        return post(workers, '/api/auth/reset-password', {'token': token, 'password': PASSWORD+'new'}).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(reset, range(4)))
    assert statuses.count(200) == 1 and statuses.count(400) == 3
    assert bob.get('/api/auth/me').status_code == 401


def test_two_workers_accept_or_cancel_once(world, workers):
    admin, alice, bob, eve, inbox = world
    battery = pack(alice)
    transfer = proposal(alice, battery)
    with ThreadPoolExecutor(max_workers=2) as pool:
        take = pool.submit(lambda: post(workers, '/api/transfers/'+transfer['id']+'/accept',
                         {'number': '001', 'confirmed': True}, dict(bob.cookies)).status_code)
        cancel = pool.submit(lambda: post(workers, '/api/transfers/'+transfer['id']+'/cancel', {}, dict(alice.cookies)).status_code)
        assert (take.result(), cancel.result()) in {(200, 409), (409, 200)}
    result = alice.get('/api/transfers/'+transfer['id']).json()['status']
    assert result in {'accepted', 'canceled'}
    assert alice.get('/api/batteries/'+battery).status_code == (404 if result == 'accepted' else 200)


def test_two_workers_edit_or_accept_preserves_consent(world, workers):
    admin, alice, bob, eve, inbox = world
    battery = pack(alice)
    original = entry(alice, battery)
    transfer = proposal(alice, battery)
    def edit():
        import httpx
        with httpx.Client(base_url=workers, cookies=dict(alice.cookies), timeout=15) as client:
            csrf = client.get('/api/auth/csrf').json()['token']
            return client.put(f'/api/batteries/{battery}/entries/{original["id"]}',
                      json={'kind': 'charge', 'added_mah': 781}, headers={'X-CSRF-Token': csrf}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        take = pool.submit(lambda: post(workers, '/api/transfers/'+transfer['id']+'/accept',
                         {'number': '001', 'confirmed': True}, dict(bob.cookies)).status_code)
        change = pool.submit(edit)
        outcome = take.result(), change.result()
    assert outcome in {(200, 404), (409, 200)}
    if outcome[0] == 200:
        assert bob.get('/api/batteries/'+battery).json()['entries'][0]['added_mah'] == 780
    else:
        assert alice.get('/api/transfers/'+transfer['id']).json()['status'] == 'invalidated'
        assert alice.get('/api/batteries/'+battery).json()['entries'][0]['added_mah'] == 781
