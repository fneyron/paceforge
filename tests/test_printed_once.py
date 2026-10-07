"""Each number is printed once across Santé, Activités and the race page: a
figure whose latest value another page already prints opens on a resting
readout (viz.rest). The server prints every default readout, so the HTML is
enough to check it."""
import re
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.user import User
from app.services import sante_training as st
from tests.test_race_prep import _race, _seed

READ = re.compile(r'data-viz-key="([^"]+)".*?<span data-r>(.*?)</span><b data-r>(.*?)</b><span data-r>(.*?)</span>',
                  re.S)


def readouts(html: str) -> dict[str, tuple[str, str, str]]:
    return {k: (a, b, c) for k, a, b, c in READ.findall(html)}


async def test_no_default_readout_repeats_a_number_of_another_page(client: AsyncClient, db_session: AsyncSession,
                                                                  test_user: User, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    today = await st.athlete_today(db_session, test_user.id)
    await _seed(db_session, test_user, today)  # 4 outings a week, a night every night (7h20, FC 45)
    soon = await _race(db_session, test_user, today + timedelta(days=5))
    done = await _race(db_session, test_user, today - timedelta(days=5), name="Trail passé")

    sommeil = readouts((await client.get("/sante?vue=sommeil")).text)
    activites = (await client.get("/activities")).text
    before = readouts((await client.get(f"/simulator/routes/{soon}")).text)
    after = readouts((await client.get(f"/simulator/routes/{done}")).text)

    last_night = sommeil["nuits"][1]
    assert last_night.startswith("Nuit ") and "sur 24" in last_night  # Sommeil prints last night's total…
    assert before["nuits-course"][0] == "J‑14 → J‑1" and "Nuit " not in before["nuits-course"][1]  # …not the race
    assert "FC" in sommeil["coeur"][1]
    assert after["recup"][:2] == ("J+1 → J+14", "VFC et FC de nuit")  # …nor last night's VFC and FC
    # this week's hours: Activités' week heading prints them; neither A1 nor the taper does by default
    week = readouts(activites)["semaines"]
    assert week[0] == "12 semaines" and "en cours" not in week[0]
    taper = before["affutage"]
    assert taper[0] == "S‑6 → S0" and taper[1].startswith("base : ") and "en cours" not in taper[0]
    shown = {v for r in (sommeil, readouts(activites)) for t in r.values() for v in t if re.search(r"\d", v)}
    for r in (before, after):
        assert not shown & {v for t in r.values() for v in t}
