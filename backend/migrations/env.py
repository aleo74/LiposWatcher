from alembic import context
from sqlalchemy import text
from backend.db import new_engine

# Transaction covers DDL and revision. Competing jobs wait then reread revision.
migration_engine = new_engine()
try:
    with migration_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("SELECT pg_advisory_xact_lock(73492000)"))
            context.configure(connection=connection, target_metadata=None,
                              transactional_ddl=True)
            with context.begin_transaction():
                context.run_migrations()
finally:
    migration_engine.dispose()
