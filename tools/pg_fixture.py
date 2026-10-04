"""Explicit fake fixture for local PostgreSQL lifecycle validation ONLY.

Not copied into the production image. Requires a dedicated *_test database.
"""
import hashlib
import json
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from aiosmtpd.controller import Controller
from backend import db
from backend.app import app
from backend.migrate import upgrade

value = os.environ.get('TEST_DATABASE_URL')
if not value or db.database_url(value).database != 'lipowatcher_test' or db.database_url(value).host != 'postgres':
    raise SystemExit('Recette locale uniquement : postgres / lipowatcher_test requis')
if sys.argv[1] in {'check-restored', 'api-restored'}:
    value = db.database_url(value).set(database='lipowatcher_restore_test').render_as_string(hide_password=False)
os.environ['DATABASE_URL'] = value
os.environ['COOKIE_SECURE'] = 'false'
db.dispose()
PASSWORD = 'fictional_lifecycle_password_123'


def snapshot():
    tables = ('users', 'login_sessions', 'batteries', 'entries', 'guide_steps',
              'login_attempts', 'battery_models', 'model_revisions', 'purchase_lots',
              'chargers', 'auth_tokens', 'request_limits', 'entry_annotations',
              'transfers', 'alembic_version')
    with db.connect() as connection:
        content = {table: sorted([dict(row) for row in connection.execute('SELECT * FROM '+table)],
                                 key=lambda row: json.dumps(row, sort_keys=True)) for table in tables}
    encoded = json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
    return {'sha256': hashlib.sha256(encoded).hexdigest(),
            'counts': {table: len(rows) for table, rows in content.items()}}


def post(client, path, data):
    csrf = client.get('/api/auth/csrf').json()['token']
    result = client.post(path, json=data, headers={'X-CSRF-Token': csrf})
    result.raise_for_status()
    return result.json()


def main():
    mode = sys.argv[1].removesuffix('-restored')
    expected = Path('/validation/pg-expected.json')
    if mode == 'seed':
        upgrade()
        with TestClient(app) as client:
            assert client.get('/api/auth/setup').json()['needed']
            post(client, '/api/auth/setup', {'username': 'lifecycle', 'password': PASSWORD})
            model = post(client, '/api/models', {'brand': 'Fake brand', 'model': 'Pack test',
                         'chemistry': 'LiPo', 'cells': 3, 'nominal_mah': 1300})
            post(client, '/api/lots/from-model', {'model_id': model['id'], 'numbers': ['001'],
                       'condition': 'new', 'lot': {'name': 'Fake purchase', 'total_price': 10}})
            battery = client.get('/api/batteries').json()[0]
            charger = post(client, '/api/chargers', {'name': 'Fake device', 'channels': 2})
            post(client, f'/api/batteries/{battery["id"]}/entries',
                 {'kind': 'charge', 'added_mah': 780, 'charger_id': charger['id'],
                  'charger_channel': 1, 'before_v': [3.7, None, 3.72]})
            token = client.get('/api/auth/csrf').json()['token']
            result = client.put(f'/api/batteries/{battery["id"]}/guide/inspection',
                                json={'done': True}, headers={'X-CSRF-Token': token})
            result.raise_for_status()
            # Synthetic verified test accounts; full verification is exercised
            # through actual SMTP in test_accounts_transfers.py.
            post(client, '/api/users', {'username': 'lifecycle_receiver', 'password': PASSWORD})
            with db.connect() as connection:
                connection.execute("UPDATE users SET email='sender@example.com',email_verified_at='2026-10-05' WHERE username='lifecycle'")
                connection.execute("UPDATE users SET email='receiver@example.com',email_verified_at='2026-10-05' WHERE username='lifecycle_receiver'")
            class Inbox:
                async def handle_DATA(self, server, session, envelope):
                    return '250 OK'
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            smtp = Controller(Inbox(), hostname='127.0.0.1', port=port)
            smtp.start()
            try:
                for key, value in {'APP_ENV': 'test', 'PUBLIC_URL': 'http://testserver',
                                   'SMTP_HOST': '127.0.0.1', 'SMTP_PORT': str(port),
                                   'SMTP_TLS': 'none', 'SMTP_FROM': 'noreply@example.com'}.items():
                    os.environ[key] = value
                preview = post(client, f'/api/batteries/{battery["id"]}/transfer-preview',
                               {'recipient_email': 'receiver@example.com'})
                transfer = post(client, f'/api/batteries/{battery["id"]}/transfers',
                                {'recipient_email': 'receiver@example.com', 'snapshot_hash': preview['snapshot_hash']})
                with TestClient(app) as receiver:
                    post(receiver, '/api/auth/login', {'username': 'lifecycle_receiver', 'password': PASSWORD})
                    post(receiver, f'/api/transfers/{transfer["id"]}/accept', {'number': '001', 'confirmed': True})
                    original = receiver.get('/api/batteries/'+battery['id']).json()['entries'][0]
                    post(receiver, f'/api/batteries/{battery["id"]}/entries/{original["id"]}/annotations',
                         {'text': 'Fake reference annotation'})
                assert client.get('/api/batteries/'+battery['id']).status_code == 404
            finally:
                smtp.stop()
        expected.write_text(json.dumps(snapshot()), encoding='utf-8')
        print('Fixture fictive créée exclusivement dans la base de recette')
    elif mode == 'check':
        assert snapshot() == json.loads(expected.read_text(encoding='utf-8'))
        print('Empreinte de toutes les tables et nombres de lignes identiques')
    elif mode == 'api':
        with TestClient(app) as client:
            post(client, '/api/auth/login', {'username': 'lifecycle_receiver', 'password': PASSWORD})
            battery = client.get('/api/batteries').json()[0]
            detail = client.get('/api/batteries/'+battery['id']).json()
            assert detail['number'] == '001' and detail['total_added_mah'] == 780
            assert detail['entries'][0]['before_v'] == [3.7, None, 3.72]
            assert detail['guide_steps']['inspection']
            assert detail['entries'][0]['locked'] == 1
            assert detail['entries'][0]['annotations'][0]['text'] == 'Fake reference annotation'
            assert len(detail['transfer_chain']) == 1
            assert len(client.get('/api/transfers').json()) == 1
            client.cookies.clear()
            post(client, '/api/auth/login', {'username': 'lifecycle', 'password': PASSWORD})
            assert client.get('/api/batteries/'+battery['id']).status_code == 404
            assert len(client.get('/api/models').json()) == 1
            assert len(client.get('/api/lots').json()) == 1
            assert len(client.get('/api/chargers').json()) == 1
        print('Connexion, modèle, lot, chargeur, guide et relevés après restauration : OK')
    else:
        raise SystemExit('seed | check | api')


if __name__ == '__main__':
    main()
