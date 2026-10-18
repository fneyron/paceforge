"""Adding the personal maximum must leave existing athletes unconfigured."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_personal_maximum_migration_preserves_users_and_has_no_invented_default():
    path = Path(__file__).parents[1] / 'alembic/versions/i9f0a1b2c3d4_personal_max_hr.py'
    spec = importlib.util.spec_from_file_location('personal_hr_max_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY, firstname VARCHAR(100))'))
        conn.execute(text("INSERT INTO users VALUES (1, 'Camille')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.execute(text('SELECT max_heart_rate FROM users')).scalar() is None
        conn.execute(text('UPDATE users SET max_heart_rate = 180 WHERE id = 1'))
        assert conn.execute(text('SELECT max_heart_rate FROM users')).scalar() == 180
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert 'max_heart_rate' not in {c['name'] for c in inspect(conn).get_columns('users')}
        assert conn.execute(text('SELECT firstname FROM users WHERE id = 1')).scalar() == 'Camille'
    engine.dispose()
