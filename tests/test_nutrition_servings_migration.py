"""The servings migration: a column with 1 by default, PF 90 rows set to 3 once, a clean downgrade."""
import importlib.util
import pathlib

import sqlalchemy as sa

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "y9b0c1d2e3f4_nutrition_servings.py"


def _load():
    spec = importlib.util.spec_from_file_location("mig_servings", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_servings_migration_is_the_head_and_round_trips():
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    mig = _load()
    assert mig.down_revision == "x8a9b0c1d2e3"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    assert ScriptDirectory.from_config(cfg).get_heads() == [mig.revision]

    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE nutrition_products (id INTEGER PRIMARY KEY, user_id INTEGER, name VARCHAR(100), kind VARCHAR(20), "
                          "carbs_g FLOAT, sodium_mg FLOAT, kcal FLOAT, caffeine_mg FLOAT, volume_ml FLOAT, created_at TIMESTAMP)"))
        for i, (name, g) in enumerate((("Precision Fuel PF 90 Gel", 90), ("precision fuel pf 90 gel (orange)", 90), ("Maurten Gel 160", 40),
                                       ("Baouw Gel", 30), ("Precision Fuel PF 90", 30)), start=1):
            c.execute(sa.text("INSERT INTO nutrition_products (id, user_id, name, kind, carbs_g, sodium_mg) VALUES (:i, 1, :n, 'gel', :g, 0)"), {"i": i, "n": name, "g": g})
        with Operations.context(MigrationContext.configure(c)):
            mig.upgrade()
        rows = dict(c.execute(sa.text("SELECT name, servings FROM nutrition_products")).all())
        # a PF 90 typed per prise (30 g) is not cut in three: it stays at 1
        assert rows == {"Precision Fuel PF 90 Gel": 3, "precision fuel pf 90 gel (orange)": 3, "Maurten Gel 160": 1, "Baouw Gel": 1,
                        "Precision Fuel PF 90": 1}
        # a new row gets 1 without saying it; running the data step again changes nothing
        c.execute(sa.text("INSERT INTO nutrition_products (id, user_id, name, kind, carbs_g, sodium_mg) VALUES (9, 1, 'Mon gel', 'gel', 25, 0)"))
        assert c.execute(sa.text("SELECT servings FROM nutrition_products WHERE id = 9")).scalar() == 1
        assert mig.apply(c) == 0
        with Operations.context(MigrationContext.configure(c)):
            mig.downgrade()
        assert "servings" not in {col["name"] for col in sa.inspect(c).get_columns("nutrition_products")}
        assert c.execute(sa.text("SELECT count(*) FROM nutrition_products")).scalar() == 6
