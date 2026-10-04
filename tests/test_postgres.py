"""Tests against real PostgreSQL, including DDL rollback and process concurrency."""
import os
import hashlib
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

import httpx
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError, ResourceClosedError

from backend import db
from backend.app import app
from backend.migrate import config, upgrade


def test_migrations_empty_idempotent_and_transactional(tmp_path):
    with db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM users').fetchone()[0] == 0
        connection.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES ('u','pilot','hash',1,'date')")
    upgrade()
    upgrade()
    with db.connect() as connection:
        assert connection.execute("SELECT username FROM users WHERE id='u'").fetchone()[0] == 'pilot'
        assert connection.execute('SELECT version_num FROM alembic_version').fetchone()[0] == db.HEAD
    with pytest.raises(ResourceClosedError):
        connection.execute('SELECT 1')
    broken = tmp_path / 'migrations'
    shutil.copytree(Path('backend/migrations'), broken)
    (broken / 'versions' / '0002_broken.py').write_text('''from alembic import op
revision = '0002_broken'
down_revision = '0001_postgresql'
branch_labels = depends_on = None
def upgrade():
    op.execute('CREATE TABLE should_rollback (id INTEGER)')
    op.execute('INSERT INTO absent_table VALUES (1)')
''', encoding='utf-8')
    cfg = config()
    cfg.set_main_option('script_location', str(broken))
    with pytest.raises(DBAPIError):
        command.upgrade(cfg, 'head')
    with db.connect() as connection:
        assert connection.execute("SELECT to_regclass('should_rollback')").fetchone()[0] is None
        assert connection.execute('SELECT version_num FROM alembic_version').fetchone()[0] == db.HEAD


def test_parallel_migrations_and_explicit_startup():
    command.downgrade(config(), 'base')
    with pytest.raises(Exception):
        with TestClient(app):
            pass
    def migrate():
        return subprocess.run([sys.executable, '-m', 'backend.migrate'], capture_output=True, timeout=30).returncode
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: migrate(), range(2))) == [0, 0]
    with TestClient(app) as client:
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/auth/setup').json() == {'needed': True}
    with db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM users').fetchone()[0] == 0


@pytest.fixture
def workers(tmp_path, monkeypatch):
    """Two actual Uvicorn child processes use the same isolated PG schema."""
    monkeypatch.setenv('COOKIE_SECURE', 'false')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    log = tmp_path / 'workers.log'
    with log.open('w+', encoding='utf-8') as output:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.app:app',
                                    '--host', '127.0.0.1', '--port', str(port),
                                    '--workers', '2', '--no-access-log',
                                    '--proxy-headers', '--forwarded-allow-ips', '127.0.0.1'],
                                   stdout=output, stderr=subprocess.STDOUT, env=os.environ.copy())
        base = f'http://127.0.0.1:{port}'
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                output.flush()
                if log.read_text().count('Application startup complete') == 2:
                    break
                if process.poll() is not None:
                    pytest.fail('Le serveur à deux workers ne démarre pas')
                time.sleep(.1)
            else:
                pytest.fail('Deux workers non prêts après 30 secondes')
            assert log.read_text().count('Started server process') == 2
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def post(base, path, payload, cookies=None, headers=None):
    with httpx.Client(base_url=base, cookies=cookies, timeout=30) as client:
        token = client.get('/api/auth/csrf').json()['token']
        return client.post(path, json=payload, headers={'X-CSRF-Token': token, **(headers or {})})


def independent_while_locked(sql, values, first, second):
    """Require B to finish while A is demonstrably waiting on a real PG lock."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        with db.connect() as holder:
            holder.execute(sql, values).fetchone()
            pid = holder.execute('SELECT pg_backend_pid()').fetchone()[0]
            waiting = pool.submit(first)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                holder.execute('SELECT pg_stat_clear_snapshot()').fetchone()
                blocked = holder.execute('SELECT count(*) FROM pg_stat_activity WHERE ?=ANY(pg_blocking_pids(pid))', (pid,)).fetchone()[0]
                if blocked:
                    break
                time.sleep(.05)
            else:
                raise AssertionError('A ne rencontre pas le verrou PostgreSQL attendu')
            other = pool.submit(second)
            try:
                result = other.result(timeout=3)
            except FutureTimeout:
                raise AssertionError('B est bloquée par une opération indépendante sur A') from None
            assert not waiting.done(), 'A doit attendre la libération de son verrou'
        return waiting.result(timeout=10), result


def test_login_limit_shared_between_workers(monkeypatch, request):
    monkeypatch.setenv('LOGIN_MAX_ATTEMPTS', '5')
    with db.connect() as connection:
        for value in ('user:missing', 'address:127.0.0.1'):
            key = hashlib.sha256(value.encode()).hexdigest()
            connection.execute('INSERT INTO login_attempts VALUES (?,999,1,1)', (key,))
    base = request.getfixturevalue('workers')
    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(lambda _: post(base, '/api/auth/login',
                            {'username': 'missing', 'password': 'wrong'}).status_code, range(12)))
    assert statuses.count(401) == 5
    assert statuses.count(429) == 7
    with db.connect() as connection:
        rows = connection.execute('SELECT failed_count, locked_until FROM login_attempts').fetchall()
        assert len(rows) == 2
        assert all(row['failed_count'] == 5 and row['locked_until'] > time.time() for row in rows)


def test_login_independent_buckets_two_workers(workers):
    key = hashlib.sha256(b'user:missing_a').hexdigest()
    with db.connect() as connection:
        connection.execute('INSERT INTO login_attempts VALUES (?,0,1,0)', (key,))
    def attempt(name, address):
        return post(workers, '/api/auth/login', {'username': name, 'password': 'wrong'},
                    headers={'X-Forwarded-For': address}).status_code
    assert independent_while_locked('SELECT bucket_key FROM login_attempts WHERE bucket_key=? FOR UPDATE', (key,),
                lambda: attempt('missing_a', '192.0.2.10'), lambda: attempt('missing_b', '192.0.2.11')) == (401, 401)
    with db.connect() as connection:
        rows = connection.execute('SELECT failed_count FROM login_attempts').fetchall()
        assert len(rows) == 4 and all(row[0] == 1 for row in rows)


def test_pool_configuration_and_schema_health(monkeypatch):
    monkeypatch.setenv('DB_POOL_SIZE', '2')
    monkeypatch.setenv('DB_MAX_OVERFLOW', '1')
    monkeypatch.setenv('DB_POOL_TIMEOUT', '7')
    monkeypatch.setenv('DB_POOL_RECYCLE', '60')
    db.dispose()
    pool = db.engine().pool
    with db.engine().connect() as connection:
        assert connection.get_isolation_level() == 'READ COMMITTED'
    assert pool.size() == 2 and pool._max_overflow == 1 and pool.timeout() == 7
    assert pool._recycle == 60 and pool._pre_ping
    with TestClient(app) as client:
        with db.connect() as connection:
            connection.execute("UPDATE alembic_version SET version_num='unknown'")
        assert client.get('/api/health').status_code == 503


def test_case_insensitive_login_uniqueness_and_search():
    with TestClient(app) as client:
        password = 'fictional_case_password_123'
        assert client.post('/api/auth/setup', json={'username': 'Pilot', 'password': password}).status_code == 201
        assert client.post('/api/users', json={'username': 'pilot', 'password': password}).status_code == 409
        assert client.post('/api/auth/login', json={'username': 'PILOT', 'password': password}).status_code == 200
        assert client.post('/api/batteries', json={'number': 'Pack001', 'chemistry': 'LiPo', 'cells': 3, 'nominal_mah': 1300}).status_code == 201
        assert len(client.get('/api/batteries?q=pack').json()) == 1
        assert client.post('/api/lots', json={'name': 'Purchase Test'}).status_code == 201
        assert len(client.get('/api/lots?q=purchase').json()) == 1
        assert client.post('/api/chargers', json={'name': 'Device Test'}).status_code == 201
        assert len(client.get('/api/chargers?q=device').json()) == 1
