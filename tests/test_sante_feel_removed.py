"""The manual feel is gone (owner, 2026-10-31: « Supprime aussi le ressenti manuel »): no input on Santé, no route,
and an answer stored before changes nothing that is shown — no score, reason, chart, explanation or night tag. The
stored rows themselves stay until the athlete removes them: the export (tests/test_data_export), the consent's
withdrawal and the account's deletion (tests/test_health_consent, tests/test_account_deletion) still hold them."""
from datetime import date, datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.models.user import User
from app.services import race_prep as rp
from app.services import sante
from app.services.health import FEEL
from tests import test_coros
from tests.owner_v4 import D8, seed_owner_v4
from tests.test_sante import _garmin_rows, _main, _runs, _seed_rows, on_owner_day  # noqa: F401 (a fixture)

as_user, no_commit = test_coros.as_user, test_coros.no_commit
D = date(2026, 10, 8)


def _feels(user: User, today: date) -> list[HealthMetric]:
    """Every shape a feel row was written in: the one-tap answers (Bien, Moyen, Cassé, with the older fatigue, pain
    and effort), the v3 morning check-in (« moins bien », « malade », « alcool hier »), one dated tomorrow."""
    rows = []
    for k in range(30):
        answers = {"v": 1, "feel": ("good", "ok", "bad")[k % 3], "pain": "present", "fatigue": "high"}
        rows.append(HealthMetric(user_id=user.id, date=today - timedelta(days=k), metric=FEEL, source="PaceForge",
                                 value=3 if k % 2 else 2, n_samples=1,
                                 details={"daily_checkin": answers, "why": ["sick", "legs"], "alcohol": True}))
    rows.append(HealthMetric(user_id=user.id, date=today + timedelta(days=1), metric=FEEL, source="PaceForge",
                             value=3, n_samples=1, details={"daily_checkin": {"v": 1, "feel": "bad"}}))
    return rows


async def test_sante_has_no_feel_even_with_answers_stored(as_user: AsyncClient, db_session: AsyncSession,
                                                          test_user: User, on_owner_day):  # noqa: F811
    await seed_owner_v4(db_session, test_user)
    db_session.add_all(_feels(test_user, D8))
    await db_session.flush()
    main = _main((await as_user.get("/sante")).text)
    for gone in ('id="ressenti', "pf-feel", "Ressenti du jour", "/sante/ressenti", "/sante/feel", "aria-pressed",
                 "Ressenti", ">Bien<", ">Moyen<", ">Cassé<", "hx-swap-oob"):
        assert gone not in main, gone
    help_page = (await as_user.get("/sante/aide")).text
    assert "Bien, Moyen ou Cassé" not in help_page and "ressenti du jour" not in help_page.lower()


async def test_the_feel_routes_are_gone(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    for path, data in (("/sante/ressenti", {"day": D.isoformat(), "feel": "bad"}),
                       ("/sante/feel", {"feel": "3", "why": ["sick"], "alcohol": "1"})):
        for headers in ({}, {"HX-Request": "true"}):
            r = await as_user.post(path, data=data, headers=headers)
            assert r.status_code in (404, 405), (path, r.status_code)
    assert not await db_session.scalar(select(func.count(HealthMetric.id)).where(HealthMetric.user_id == test_user.id))


async def test_a_stored_feel_changes_no_score_reason_chart_or_tag(db_session: AsyncSession, test_user: User):
    """A full Garmin baseline with a lower VFC (a state, its reasons, the dials, the curves, the explanations), the
    race page's nights: the same with or without 31 stored feels, one dated tomorrow (it never moves the page's day
    either: the clock says the 8th)."""
    await _seed_rows(db_session, test_user, _garmin_rows(D, hrv_last=60.0))
    await _runs(db_session, test_user, D)
    now = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)

    async def shown() -> tuple:
        page = await sante.health_page(db_session, test_user.id, now=now)
        nights = await rp._nights(db_session, test_user.id, D, D - timedelta(days=70), [], [])
        return page, {d: sorted(n.tags) for d, n in nights.items()}

    before, tags = await shown()
    assert before["today"] == D and before["state"]["key"] == "hrv" and before["score"]["value"] == 69
    db_session.add_all(_feels(test_user, D))
    await db_session.flush()
    after, after_tags = await shown()
    assert after == before and after_tags == tags
    assert not any({"alcohol", "ill"} & set(t) for t in after_tags.values())
