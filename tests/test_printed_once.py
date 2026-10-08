"""Each number is printed once across Santé, Activités and the race page: a
figure whose latest value another block already prints opens on a resting
readout (viz.rest): on the one-page Santé (v4), the rings print today's
score and last night's 24 h, so « Récupération · 14 jours » and « Sommeil sur
24 h » rest on a hint and on the mean. The server prints every default
readout, so the HTML is enough to check it."""
import html
import re
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.user import User
from app.services import sante_training as st
from tests.test_race_prep import _race, _seed

FIG = re.compile(r'data-viz-key="([^"]+)"(.*?)</figure>', re.S)
SLOT = re.compile(r'<(span|b)\b[^>]*?\sdata-r(?:\s[^>]*)?>(.*?)</\1>', re.S)


def readouts(page: str) -> dict[str, tuple[str, ...]]:
    """Each figure's default readout as the server printed it: [date, value, context] on the race page and
    Activités, [value, word, the day · context] on Santé's cards (two compact rows)."""
    out = {}
    for key, body in FIG.findall(page):
        out[key] = tuple(html.unescape(re.sub(r"<[^>]+>", "", v)) for _, v in SLOT.findall(body))[:3]
    return out


async def test_no_default_readout_repeats_a_number_of_another_page(client: AsyncClient, db_session: AsyncSession,
                                                                  test_user: User, monkeypatch, same_today):
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    today = await st.athlete_today(db_session, test_user.id)
    await _seed(db_session, test_user, today)  # 4 outings a week, a night every night (7h20, FC 45)
    soon = await _race(db_session, test_user, today + timedelta(days=5))
    done = await _race(db_session, test_user, today - timedelta(days=5), name="Trail passé")

    page = (await client.get("/sante")).text
    sante = readouts(page)
    activites = (await client.get("/activities")).text
    before = readouts((await client.get(f"/simulator/routes/{soon}")).text)
    after = readouts((await client.get(f"/simulator/routes/{done}")).text)

    # no VFC in the seed: no card; no Charge card (v4.1: the activities are Activités')
    assert set(sante) == {"recuperation", "sommeil-14", "sommeil-90", "fc"}
    # the rings print today's score and last night's total: their cards rest on their means, one line each…
    assert sante["recuperation"][1:] == ("en moyenne", "") and sante["recuperation"][0].isdigit()
    assert sante["sommeil-14"] == ("7h20", "en moyenne", "") and sante["sommeil-90"] == ("7h20", "en moyenne", "")
    # …the nightly HR card prints last night's value (no ring, no other block does)…
    assert sante["fc"][0] == "45\u00a0bpm" and " · normale " in sante["fc"][2]
    assert before["nuits-course"][0] == "J‑14 → J‑1" and "Nuit " not in before["nuits-course"][1]  # not the race…
    assert after["recup"][:2] == ("J+1 → J+14", "VFC et FC de nuit")  # …nor last night's VFC and FC
    # this week's hours: Activités' week heading prints them; neither A1 nor the taper does by default
    week = readouts(activites)["semaines"]
    assert week[0] == "12 semaines" and "en cours" not in week[0]
    taper = before["affutage"]
    assert taper[0] == "S‑6 → S0" and taper[1].startswith("base : ") and "en cours" not in taper[0]
    shown = {v for r in (sante, readouts(activites)) for t in r.values() for v in t if re.search(r"\d", v)}
    for r in (before, after):
        assert not shown & {v for t in r.values() for v in t}
