"""Explicit local recipe. Replaces only lipowatcher*_test databases.

Recreates this project's app/postgres containers, retaining the PG volume.
Run from the host after building and migrating the development app.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def compose(*args):
    subprocess.run(['docker', 'compose', *args], cwd=ROOT, check=True)


def sql(command):
    compose('exec', '-T', 'postgres', 'sh', '-c', command)


def fixture(mode):
    compose('run', '--rm', '--no-deps',
            '--volume', f'{ROOT / "tools"}:/app/tools:ro',
            '--volume', f'{ROOT / "data"}:/validation',
            '--volume', f'{ROOT / "requirements-dev.txt"}:/app/requirements-dev.txt:ro',
            'app', 'python', '-c',
            "import subprocess; subprocess.run(['pip','install','--quiet','-r','requirements-dev.txt'],check=True); "
            f"subprocess.run(['python','tools/pg_fixture.py','{mode}'],check=True)")


if __name__ == '__main__':
    # Fixed local database names; do not accept an arbitrary destructive target.
    sql('test "$POSTGRES_DB" != lipowatcher_test && test "$POSTGRES_DB" != lipowatcher_restore_test')
    sql('dropdb -U "$POSTGRES_USER" --if-exists --force lipowatcher_test')
    sql('createdb -U "$POSTGRES_USER" lipowatcher_test')
    fixture('seed')
    sql('pg_dump -U "$POSTGRES_USER" -d lipowatcher_test -Fc -f /tmp/lipowatcher-test.dump')
    compose('cp', 'postgres:/tmp/lipowatcher-test.dump', str(ROOT / 'data' / 'pg-validation.dump'))
    compose('up', '-d', '--force-recreate', 'postgres', 'app')
    fixture('check')
    compose('cp', str(ROOT / 'data' / 'pg-validation.dump'), 'postgres:/tmp/restore-test.dump')
    sql('dropdb -U "$POSTGRES_USER" --if-exists --force lipowatcher_restore_test')
    sql('createdb -U "$POSTGRES_USER" lipowatcher_restore_test')
    sql('pg_restore -U "$POSTGRES_USER" -d lipowatcher_restore_test --no-owner --no-acl --single-transaction --exit-on-error /tmp/restore-test.dump')
    fixture('check-restored')
    fixture('api-restored')
    print('Persistance et restauration PostgreSQL réelles : OK')
