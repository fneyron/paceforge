"""Each number is printed once across Santé, Activités and the race page: a
figure whose latest value another block already prints opens on a resting
readout (viz.rest): on the one-page Santé (v4.4), the ring prints today's
score and the Sommeil row this morning's 24 h, so « Récupération · 14 jours »
and « Sommeil sur 24 h » rest on their means. The server prints every default
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
    test_user.max_heart_rate = 180  # confirmed reference keeps the easy-run card available for this layout test
    await _seed(db_session, test_user, today)  # 4 outings a week, a night every night (7h20, FC 45)
    soon = await _race(db_session, test_user, today + timedelta(days=5))
    done = await _race(db_session, test_user, today - timedelta(days=5), name="Trail passé")

    page = (await client.get("/sante")).text
    sante = readouts(page)
    activites = (await client.get("/activities")).text
    before = readouts((await client.get(f"/simulator/routes/{soon}")).text)
    after = readouts((await client.get(f"/simulator/routes/{done}")).text)

    # No VFC in the seed. New accumulated load and fitness stay distinct from recovery.
    # the seed's nights are all within the 30 of the short range: no « 3 mois » (2026-10-17)
    assert set(sante) == {"recuperation", "sommeil-14", "fc", "charge-accumulee", "forme-footing"}
    assert "trend-hr" not in sante  # the duplicate FC de nuit trend left the page
    assert "entrainement" not in sante
    assert sante["charge-accumulee"][1:] == ("en moyenne", "")
    assert sante["forme-footing"] == ("", "Tes sorties comparables", "")
    # the ring prints today's score, the Sommeil row last night's total: the Récupération card rests on its title, no
    # number (the audit, 2026-10-09: its mean decided nothing and looked like the dial's), the sleep's on its means…
    assert sante["recuperation"] == ("100/100", "en moyenne", "")
    assert sante["sommeil-14"] == ("7h20", "en moyenne", "")
    # …the nightly HR card prints last night's value (its row a word, no other block prints it)…
    assert sante["fc"][0] == "45\u00a0bpm" and "Garmin" not in sante["fc"][2]
    assert sante["fc"][1] == "en moyenne"  # real dated measurement, not the rolling average
    assert " · d'habitude " not in sante["fc"][2]  # source and reference share the central explanation
    context = re.search(r'data-viz-detail-for="fc">(.*?)</p>', page, re.S).group(1)
    assert "Garmin" in context and "moyenne 7 jours" in context
    fc = dict(FIG.findall(page))["fc"]
    assert "Moyenne" in re.search(r'aria-valuetext="([^"]*)"', fc).group(1)
    assert before["nuits-course"][0] == "J‑14 → J‑1" and "Nuit " not in before["nuits-course"][1]  # not the race
    assert "recup" not in after  # v4.3: no recovery part on the race page after the race
    # this week's hours: Activités' week heading prints them; neither A1 (it rests on the average of the complete
    # weeks, v4.3) nor the taper does by default
    week = readouts(activites)["semaines-duree"]
    assert week[0].startswith("sur ") and week[2] == "par semaine en moyenne" and "en cours" not in " ".join(week)
    taper = before["affutage"]
    assert taper[0] == "S‑6 → S0" and taper[1].startswith("base : ") and "en cours" not in taper[0]
    shown = {v for r in (sante, readouts(activites)) for t in r.values() for v in t if re.search(r"\d", v)}
    for r in (before, after):
        assert not shown & {v for t in r.values() for v in t}
