"""The sleep need migration: users.sleep_need_min, NULL by default (8 h counted), a clean downgrade."""
import importlib.util
import pathlib

import sqlalchemy as sa

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "z0c1d2e3f4a5_sleep_need.py"


def _load():
    spec = importlib.util.spec_from_file_location("mig_sleep_need", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sleep_need_migration_is_the_head_and_round_trips():
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    mig = _load()
    assert mig.down_revision == "y9b0c1d2e3f4"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    assert ScriptDirectory.from_config(cfg).get_heads() == [mig.revision]
    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255), weight_kg FLOAT)"))
        c.execute(sa.text("INSERT INTO users (id, email) VALUES (1, 'a@b.c')"))
        with Operations.context(MigrationContext.configure(c)):
            mig.upgrade()
        assert c.execute(sa.text("SELECT sleep_need_min FROM users WHERE id = 1")).scalar() is None
        c.execute(sa.text("UPDATE users SET sleep_need_min = 510 WHERE id = 1"))
        with Operations.context(MigrationContext.configure(c)):
            mig.downgrade()
        assert "sleep_need_min" not in {col["name"] for col in sa.inspect(c).get_columns("users")}
        assert c.execute(sa.text("SELECT count(*) FROM users")).scalar() == 1
