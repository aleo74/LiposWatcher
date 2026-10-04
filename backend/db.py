"""SQLAlchemy Core transactions and a per-process psycopg connection pool.

Positional service parameters become named SQLAlchemy bindings, never SQL text.
"""
import os
import threading
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from alembic.script import ScriptDirectory
from .migrate import config

HEAD = ScriptDirectory.from_config(config()).get_current_head()
_engine = None
_engine_lock = threading.Lock()


def database_url(value=None):
    value = value or os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL requis (PostgreSQL)")
    url = make_url(value)
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise RuntimeError("DATABASE_URL doit utiliser PostgreSQL avec psycopg")
    return url.set(drivername="postgresql+psycopg")


def new_engine(value=None, **kwargs):
    return create_engine(
        database_url(value), pool_size=int(os.environ.get("DB_POOL_SIZE", "5")),
        max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "5")),
        pool_timeout=float(os.environ.get("DB_POOL_TIMEOUT", "30")),
        pool_recycle=int(os.environ.get("DB_POOL_RECYCLE", "1800")),
        pool_pre_ping=True, hide_parameters=True, isolation_level="READ COMMITTED", **kwargs,
    )


def engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = new_engine()
        return _engine


def dispose():
    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None


class Record(dict):
    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class Result:
    def __init__(self, result):
        self.result = result

    def fetchone(self):
        row = self.result.mappings().fetchone()
        return Record(row) if row is not None else None

    def fetchall(self):
        return [Record(row) for row in self.result.mappings().fetchall()]

    def __iter__(self):
        return (Record(row) for row in self.result.mappings())

    @property
    def rowcount(self):
        return self.result.rowcount


def bind(sql, values):
    parts = sql.split("?")
    if len(parts) - 1 != len(values):
        raise ValueError("Nombre de paramètres SQL incorrect")
    statement = parts[0] + "".join(f":p{i}" + part for i, part in enumerate(parts[1:]))
    return text(statement), {f"p{i}": value for i, value in enumerate(values)}


class Transaction:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, values=()):
        statement, params = bind(sql, values)
        return Result(self.connection.execute(statement, params))

    def executemany(self, sql, rows):
        rows = list(rows)
        if rows:
            statement, _ = bind(sql, rows[0])
            return Result(self.connection.execute(statement, [bind(sql, row)[1] for row in rows]))

    def commit(self):
        self.connection.commit()


@contextmanager
def connect():
    with engine().connect() as connection:
        try:
            yield Transaction(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def check_schema():
    with connect() as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        if not row or row[0] != HEAD:
            raise RuntimeError("Migrations requises : python -m backend.migrate")
