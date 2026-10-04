import pytest
import os
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import text
from backend import db
from backend.migrate import upgrade


@pytest.fixture(autouse=True)
def isolated_postgres(monkeypatch):
    """Dedicated *_test database, fresh schema per test; never touch dev data."""
    value = os.environ.get('TEST_DATABASE_URL')
    if not value:
        pytest.fail('TEST_DATABASE_URL requis : utiliser une base PostgreSQL dédiée *_test')
    url = db.database_url(value)
    dev = os.environ.get('DATABASE_URL')
    if not url.database.endswith('_test') or (dev and url.database == db.database_url(dev).database):
        pytest.fail('La base de test doit finir par _test et être distincte de DATABASE_URL')
    schema = 'test_' + uuid.uuid4().hex
    admin = db.new_engine(value)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    monkeypatch.setenv('DATABASE_URL', url.update_query_dict({'options': f'-csearch_path={schema}'}).render_as_string(hide_password=False))
    db.dispose()
    try:
        upgrade()
        yield
    finally:
        db.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()


@pytest.fixture(autouse=True)
def csrf_client(monkeypatch):
    """Exercise the real CSRF exchange, as the frontend does; never bypass it."""
    original = TestClient.request

    def request(self, method, url, *args, **kwargs):
        protect = kwargs.pop('csrf', True)
        if method.upper() in {'POST', 'PUT', 'PATCH', 'DELETE'} and protect:
            token = original(self, 'GET', '/api/auth/csrf').json()['token']
            kwargs['headers'] = {'X-CSRF-Token': token, **(kwargs.get('headers') or {})}
        return original(self, method, url, *args, **kwargs)

    monkeypatch.setattr(TestClient, 'request', request)
