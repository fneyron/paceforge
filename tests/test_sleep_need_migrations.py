"""The sleep need's migration: users.sleep_need_min added (z0c1d2e3f4a5, the answer to a question asked for an hour);
the code no longer reads it since 2026-10-09 (the need is computed, never asked), the next migration drops it once no
running code maps it. It round trips."""
import importlib.util
import pathlib

import sqlalchemy as sa

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "alembic" / "versions"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"mig_{name}", VERSIONS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(c, fn):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with Operations.context(MigrationContext.configure(c)):
        fn()


def test_the_add_is_the_head_and_round_trips():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    add = _load("z0c1d2e3f4a5_sleep_need")
    assert add.down_revision == "y9b0c1d2e3f4"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    assert ScriptDirectory.from_config(cfg).get_heads() == [add.revision]
    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255), weight_kg FLOAT)"))
        c.execute(sa.text("INSERT INTO users (id, email) VALUES (1, 'a@b.c')"))
        _run(c, add.upgrade)
        assert c.execute(sa.text("SELECT sleep_need_min FROM users WHERE id = 1")).scalar() is None
        _run(c, add.downgrade)
        assert "sleep_need_min" not in {col["name"] for col in sa.inspect(c).get_columns("users")}
        assert c.execute(sa.text("SELECT count(*) FROM users")).scalar() == 1


def test_the_code_no_longer_maps_the_column():
    from app.models.user import User

    assert "sleep_need_min" not in User.__table__.columns
