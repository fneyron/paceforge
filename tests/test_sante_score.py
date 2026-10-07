"""« Forme du jour » (app/services/sante_score.py, SCORE_SPEC.md): every
component rule, the weights renormalised, the caps, the placement inside the
action's band for each tone, the owner's 7 Oct (the exact score from the
formula), a rich wearer, an empty user, the provisional normals' labels, and
the illness alert that still needs 14 nights."""
import json
import math
import re
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.user import User
from app.services import nights as nt
from app.services import sante
from app.services import sante_score as sc
from app.services import sante_sleep as sl
from app.services.sante_training import Session
from tests import test_coros
from tests.test_coros import _link
from tests.test_nights import D, night_rows
from tests.test_sante import _add, _seed_owner

as_user, no_commit = test_coros.as_user, test_coros.no_commit


def _session(day, minutes, dplus=0, sid=7, sport="Run", hr=125):
    return Session(id=sid, start=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=7),
                   day=day, sport=sport, minutes=minutes, dplus=dplus, km=minutes / 6, speed=2.8, hr=hr,
                   hr_peak=160, suffer=None, workout_type=0, temp=None, elapsed=minutes, offset=0)


def _runs(n=8, today=D, start=2):
    """`n` easy runs of 50 min every 4 days, the latest `start` days ago: ≥ 6 sessions in 42 days."""
    return [_session(today - timedelta(days=start + 4 * i), 50, sid=100 + i) for i in range(n)]


def _feel(value=2, why=(), alcohol=False, answered=True):
    return {"value": value, "why": list(why), "alcohol": alcohol, "answered": answered}


def _nights(rows, today=D):
    return nt.build_nights(rows, today)


def _view(nights, feel=None, sessions=(), today=D, has_watch=True, **kw):
    feel = feel or {}
    nt.tag_nights(nights, sessions, kw.get("races", []), feel)
    nt.tag_alerts(nights, today, kw.get("races", []))
    return sante._today_view(nights, feel, list(sessions), 190, None, None, kw.get("races", []), today, has_watch)


def _rich_rows(hrv_last=None, hr_last=None, days=60):
    """A Garmin athlete every night: HRV ≈ 70 ms (ln spread ≈ 0.11), nightly HR 44–46, 7h20 asleep."""
    return night_rows(range(0, days), hr=lambda k: hr_last if hr_last is not None and k < 7 else 44.0 + k % 3,
                      hrv=lambda k: hrv_last if hrv_last is not None and k < 7 else 70 * math.exp(0.08 * (k % 5 - 2)))


# ── component rules ─────────────────────────────────────────────────────────

def test_hrv_component_is_full_in_or_above_the_band_and_zero_at_minus_2_5_sd():
    b = {"center": 60.0, "sd": 0.1}
    z = lambda z: 60 * math.exp(z * 0.1)  # noqa: E731
    assert sc.hrv_sub(z(-0.5), b) == pytest.approx(100)
    assert sc.hrv_sub(z(0), b) == 100 and sc.hrv_sub(z(+3), b) == 100  # above the band: never praised, never more
    assert sc.hrv_sub(z(-1.5), b) == pytest.approx(50)
    assert sc.hrv_sub(z(-2.5), b) == pytest.approx(0) and sc.hrv_sub(z(-4), b) == 0


def test_nightly_hr_component_is_full_to_plus_2_and_zero_at_plus_10():
    b = {"center": 45.0}
    assert sc.hr_sub(40, b) == 100 and sc.hr_sub(47, b) == 100
    assert sc.hr_sub(51, b) == pytest.approx(50) and sc.hr_sub(55, b) == 0 and sc.hr_sub(60, b) == 0


def test_sleep_component_over_24_hours_and_the_7_day_debt():
    assert sc.sleep_sub(480) == 100 and sc.sleep_sub(420) == 100
    assert sc.sleep_sub(390) == pytest.approx(80) and sc.sleep_sub(360) == pytest.approx(60)
    assert sc.sleep_sub(300) == pytest.approx(30) and sc.sleep_sub(240) == 0 and sc.sleep_sub(150) == 0
    # a 7-day mean 90 min under the usual: 40, even after a long night; above the usual: nothing off
    assert sc.sleep_sub(490, mean7=380, usual=470) == pytest.approx(40)
    assert sc.sleep_sub(490, mean7=440, usual=470) == pytest.approx(80)
    assert sc.sleep_sub(390, mean7=500, usual=470) == pytest.approx(80)
    assert sc.sleep_sub(490, mean7=200, usual=470) == 0


def test_load_component_after_a_big_outing():
    assert sc.load_sub(None) == 100
    assert sc.load_sub(_session(D, 185)) == 50 and sc.load_sub(_session(D, 100, dplus=1600)) == 50
    assert sc.load_sub(_session(D, 360)) == 30 and sc.load_sub(_session(D, 420, dplus=3000)) == 30


def test_feel_component():
    assert [sc.feel_sub(_feel(v)) for v in (1, 2, 3)] == [100, 85, 50]
    assert sc.feel_sub(_feel(3, ["legs", "fatigue"])) == 30 and sc.feel_sub(_feel(3, ["stress"])) == 40
    assert sc.feel_sub(_feel(3, ["sick", "legs"])) == 10  # malade → 10
    assert sc.feel_sub(_feel(2, alcohol=True)) == 70  # « alcool hier » −15 (Pietilä 2018)
    assert sc.feel_sub(_feel(3, ["sick"], alcohol=True)) == 0  # never under 0
    assert sc.feel_sub(_feel(1, ["legs"])) == 100  # the reasons go with « moins bien » only


def test_weights_are_renormalised_over_the_components_present():
    nights = _nights(_rich_rows())
    a = _view(nights, {D: _feel(1)}, _runs())
    rows = {r["key"]: r for r in a["score"]["rows"]}
    assert [r["key"] for r in a["score"]["rows"]] == ["hrv", "hr", "sleep", "load", "feel"]
    assert [rows[k]["share"] for k in ("hrv", "hr", "sleep", "load", "feel")] == [30, 25, 20, 15, 10]
    # no watch tonight, no check-in: Charge alone is one signal (no score); with the check-in, 15/25 and 10/25
    parts = [{"key": "sleep", "weight": 20 / 45}, {"key": "load", "weight": 15 / 45}, {"key": "feel", "weight": 10 / 45}]
    assert sc._shares(parts) == [45, 33, 22]  # 44,4 · 33,3 · 22,2: the largest remainder takes the missing point
    day = sante._decide_day(_nights({}), {D: _feel(2)}, _runs(), None, [], [], [], D, True)
    s = sc.score_of(day, {})
    assert [p["key"] for p in s["parts"]] == ["load", "feel"]
    assert [p["weight"] for p in s["parts"]] == pytest.approx([0.6, 0.4])
    assert s["raw"] == pytest.approx(0.6 * 100 + 0.4 * 85)


# ── caps and placement ──────────────────────────────────────────────────────

def _day(tone="ok", **ctx):
    base = {"feel": None, "reprise": None, "post": None, "race_week": False, "hrv": None, "hr": None, "short": None}
    return {"ctx": {**base, **ctx}, "verdict": {"tone": tone}}


def test_each_cap_comes_from_a_ladder_rule():
    assert sc.caps(_day()) == []
    assert sc.caps(_day(reprise={"since": D})) == [(60, "reprise")]
    assert sc.caps(_day(post={"days": 2, "race": True})) == [(40, "après la course")]
    assert sc.caps(_day(post={"days": 3, "race": True})) == [(40, "après la course")]
    assert sc.caps(_day(post={"days": 5, "race": False})) == [(65, "après ta grosse sortie")]
    assert sc.caps(_day(hrv="below", hr="above")) == [(60, "VFC sous ta normale")]
    assert sc.caps(_day(hrv="below", feel=_feel(3))) == [(60, "VFC sous ta normale")]
    assert sc.caps(_day(hrv="below")) == []  # a low HRV alone moves nothing (R7 needs HR up or « moins bien »)
    assert sc.caps(_day(hrv="below", hr="above", race_week=True)) == []  # never in race week
    assert sc.caps(_day(short={"tst24": 330})) == [(65, "nuit courte")]


@pytest.mark.parametrize("tone,raw,score", [
    ("ok", 0, 70), ("ok", 50, 85), ("ok", 100, 100), ("ok", 7, 72),
    ("easy", 0, 40), ("easy", 50, 55), ("easy", 65, 59), ("easy", 100, 69),
    ("rest", 0, 0), ("rest", 50, 20), ("rest", 100, 39),
])
def test_the_score_sits_inside_the_band_of_the_action(tone, raw, score):
    assert sc.place(tone, raw) == score
    lo, hi = sc.BANDS[tone]
    assert lo <= score <= hi


def test_the_word_and_colour_follow_the_tone_and_unknown_has_no_score():
    assert (sc.WORDS, sc.TONE_CLS) == ({"ok": "bon", "easy": "moyen", "rest": "bas"},
                                       {"ok": "ok", "easy": "warn", "rest": "danger"})
    assert sc.score_of({"verdict": {"tone": "unknown"}}, {}) == {"value": None, "why": "unknown", "tone": "unknown"}
    v = sc.view({"value": None, "why": "unknown"}, [], has_watch=False, has_sessions=True)
    assert v == {"value": None, "line": "Pas encore de score : connecte ta montre ou Strava."}


# ── the owner's 7 Oct ───────────────────────────────────────────────────────

async def _owner_strava(db: AsyncSession, user: User) -> None:
    """The owner's Strava around the trip: 8 runs in France in September, then
    the Transjeju itself (02/10 21:00 in Korea, 16h53, marked as a race)."""
    for i, day in enumerate([date(2026, 9, 6) + timedelta(days=3 * k) for k in range(8)]):
        db.add(Activity(user_id=user.id, strava_activity_id=8100 + i, sport_type="TrailRun", name=f"Footing {i}",
                        start_date=datetime(day.year, day.month, day.day, 6, tzinfo=timezone.utc), distance=9000,
                        moving_time=3300, elapsed_time=3400, total_elevation_gain=150, average_heartrate=128,
                        raw_data={"utc_offset": 7200}))
    db.add(Activity(user_id=user.id, strava_activity_id=8200, sport_type="TrailRun", name="Transjeju 100M",
                    start_date=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), distance=162000,
                    moving_time=16 * 3600 + 53 * 60, elapsed_time=16 * 3600 + 53 * 60, total_elevation_gain=6100,
                    average_heartrate=132, raw_data={"utc_offset": 32400, "workout_type": 1}))
    await db.flush()


async def test_owner_7_october_exact_score(db_session: AsyncSession, test_user: User):
    """J+5 after a ≥ 10 h race: « Footing facile seulement » (easy, 40–69).
    Every night sits in J-7 → J+7: no band, no VFC nor FC component. Sommeil
    8h10 over 24 h → 100, Charge (no outing in 48 h) → 100: raw 100, capped at
    65 after the race: 40 + 29 × 65 / 100 = 58,85 → 59."""
    await _seed_owner(db_session, test_user)
    await _owner_strava(db_session, test_user)
    a = (await sante.health_page(db_session, test_user.id, today=D))["auj"]
    v, s = a["verdict"], a["score"]
    assert (v["tone"], v["headline"]) == ("easy", "Footing facile seulement")
    assert s["value"] == 59 == math.floor(40 + 29 * 65 / 100 + 0.5)
    assert (s["word"], s["cls"]) == ("moyen", "warn")
    assert [(r["key"], r["sub"], r["share"]) for r in s["rows"]] == [("sleep", 100, 57), ("load", 100, 43)]
    assert s["base"] == "basé sur 2 signaux sur 5" and s["chips"] == ["Sommeil", "Charge"]
    assert s["cap_line"] == "Plafonné : après la course."
    assert s["absent_line"] == "pas mesuré : VFC et FC de nuit (pas assez de nuits) · Ressenti (pas encore répondu)"
    assert s["aria"].startswith("Forme du jour 59 sur 100, moyen : basé sur 2 signaux sur 5 (sommeil, charge).")
    # the 14-day line: the days with a score only (no night on race day or the 3 after: one signal, no score)
    data = s["spark"]["data"]
    assert "2026-10-02" not in data and "2026-10-04" not in data and '"2026-10-07"' in data
    assert s["spark"]["read"] == ["14 derniers jours", "touche un jour", ""]  # today's score is the gauge's


async def test_owner_answers_the_check_in_and_the_cap_still_holds(db_session: AsyncSession, test_user: User):
    await _seed_owner(db_session, test_user)
    await _owner_strava(db_session, test_user)
    for feel, value, tone in (({"why": [], "alcohol": False}, 2, "easy"), ({"why": ["legs"]}, 3, "easy")):
        _add(db_session, test_user, "feel", D, value, feel, "PaceForge")
        await db_session.flush()
        s = (await sante.health_page(db_session, test_user.id, today=D))["auj"]["score"]
        assert s["value"] == 59 and s["chips"] == ["Sommeil", "Charge", "Ressenti"]  # raw ≥ 65: the cap decides
        from sqlalchemy import delete

        from app.models.health import HealthMetric
        await db_session.execute(delete(HealthMetric).where(HealthMetric.metric == "feel"))
    # « malade »: the ladder's R2 (rest, 0–39): raw (20·100 + 15·100 + 10·10) / 45 = 80, capped by « Reprise »
    # (opened today, 60) under the race's 65: 39 × 0,6 = 23,4 → 23
    _add(db_session, test_user, "feel", D, 3, {"why": ["sick"]}, "PaceForge")
    await db_session.flush()
    a = (await sante.health_page(db_session, test_user.id, today=D))["auj"]
    s = a["score"]
    assert a["verdict"]["tone"] == "rest" and s["value"] == 23 and s["word"] == "bas"
    assert {r["key"]: r["sub"] for r in s["rows"]}["feel"] == 10
    assert s["cap_line"] == "Plafonné : reprise, après la course."


async def test_owner_page_draws_the_gauge_and_prints_the_score_once(as_user: AsyncClient, db_session: AsyncSession,
                                                                    test_user: User, monkeypatch):
    async def today(*a, **k):
        return D
    monkeypatch.setattr(sante, "athlete_today", today)
    await _link(db_session, test_user)
    await _seed_owner(db_session, test_user)
    await _owner_strava(db_session, test_user)
    page = (await as_user.get("/sante")).text
    assert 'aria-label="Forme du jour 59 sur 100, moyen : basé sur 2 signaux sur 5' in page
    assert 'class="pf-gauge-arc is-warn"' in page and '<span class="pf-score-word pf-word-warn">moyen</span>' in page
    assert '<span class="pf-gauge-num" aria-hidden="true">59</span>' in page
    visible = re.sub(r"<script.*?</script>|<[^>]+>", " ", page, flags=re.S)
    assert len(re.findall(r"(?<![\d,])59(?![\d,])", visible)) == 1  # the gauge only
    # the breakdown draws bars and weights: no tile's value, no Sommeil readout's
    detail = page.split('class="pf-score-detail"')[1].split("</details>")[0]
    assert "8h10" not in detail and "7h35" not in detail and "bpm" not in detail and " ms" not in detail
    assert 'aria-label="sommeil sur 24 heures : 100 sur 100, poids 57 %"' in detail
    assert "<summary>Comment je calcule ta forme du jour</summary>" in page
    assert "Une différence de quelques points ne veut rien dire" in page and "doi.org/10.1515/teb-2025-0001" in page
    # the check-in swaps the score with the decision (out of band, into the live region)
    r = await as_user.post("/sante/feel", data={"feel": "3", "toggle": "sick"}, headers={"HX-Request": "true"})
    assert 'id="sante-decision" hx-swap-oob="innerHTML"' in r.text and "Forme du jour 23 sur 100, bas" in r.text


# ── a rich wearer ───────────────────────────────────────────────────────────

def test_rich_wearer_a_low_hrv_alone_is_a_green_day_low_in_its_band():
    """A full band (≥ 14 nights), every signal in, the check-in « mieux », but
    the 7-night HRV under the band: the action does not move (a single signal,
    H), the score stays green and drops inside 70–100; the tile says it."""
    calm = _view(_nights(_rich_rows()), {D: _feel(1)}, _runs())
    low = _view(_nights(_rich_rows(hrv_last=58.0)), {D: _feel(1)}, _runs())
    assert calm["verdict"]["rule"] == low["verdict"]["rule"] == "plan" and low["verdict"]["tone"] == "ok"
    tile = next(t for t in low["tiles"] if t["key"] == "hrv")
    assert (tile["label"], tile["glyph"], tile["word"]) == ("VFC · 7 nuits", "▼", "en dessous")
    assert all(t["path"] for t in low["tiles"])  # the tiles keep their 14-day sparklines
    assert calm["score"]["value"] == 100 and calm["score"]["word"] == "bon"
    # the exact value from the formula: VFC = 100 × (z + 2,5) / 2 on the band of the 60 days before the window
    nights = _nights(_rich_rows(hrv_last=58.0))
    b = nt.band(nights, "hrv", D - timedelta(days=6))
    z = (math.log(58.0) - math.log(b["center"])) / b["sd"]
    sub = 100 * (z + 2.5) / 2
    raw = (30 * sub + 25 * 100 + 20 * 100 + 15 * 100 + 10 * 100) / 100
    assert not b["provisional"] and -2.5 < z < -0.5
    assert low["score"]["value"] == math.floor(70 + 30 * raw / 100 + 0.5)
    assert 70 <= low["score"]["value"] < calm["score"]["value"]
    assert {r["key"]: r["sub"] for r in low["score"]["rows"]}["hrv"] == round(sub)


def test_rich_wearer_hrv_down_and_hr_up_is_r7_capped_at_60():
    a = _view(_nights(_rich_rows(hrv_last=58.0, hr_last=49.0)), {}, _runs())  # +4 bpm: above, under the alert
    assert a["verdict"]["rule"] == "hrv" and a["verdict"]["tone"] == "easy"
    s = a["score"]
    assert s["cap_line"] == "Plafonné : VFC sous ta normale." and 40 <= s["value"] <= sc.place("easy", 60)
    assert s["chips"] == ["VFC", "FC de nuit", "Sommeil", "Charge"]  # 4 of 5: no check-in
    assert s["absent_line"] == "pas mesuré : Ressenti (pas encore répondu)"


def test_a_short_night_caps_a_green_day_at_65():
    rows = _rich_rows()
    rows["sleep"].update(night_rows([0], asleep=320, start=(23, 50), end=(6, 40))["sleep"])  # 5h20, usual wake
    a = _view(_nights(rows), {}, _runs())
    assert a["verdict"]["tone"] == "ok" and a["verdict"]["text"].startswith("Nuit courte")
    s = a["score"]
    assert s["cap_line"] == "Plafonné : nuit courte." and s["value"] == sc.place("ok", 65) == 90


def test_reprise_caps_at_60_and_a_big_outing_halves_the_load():
    rows = _rich_rows()
    sick = {D - timedelta(days=1): _feel(3, ["sick"])}  # « malade » yesterday: « Reprise » today
    a = _view(_nights(rows), sick, _runs())
    assert a["verdict"]["rule"] == "reprise" and "Plafonné : reprise" in (a["score"]["cap_line"] or "")
    big = _view(_nights(_rich_rows()), {}, _runs() + [_session(D - timedelta(days=1), 200, sid=99)])
    assert big["verdict"]["rule"] == "legs" and {r["key"]: r["sub"] for r in big["score"]["rows"]}["load"] == 50


def test_the_history_is_the_score_each_day_had():
    """Recomputed per day from what is stored (no table): yesterday's point is
    the score the page gives when « today » is yesterday."""
    rows = _rich_rows(hrv_last=58.0)
    a = _view(_nights(rows), {}, _runs())
    yday = _view(_nights(rows, D - timedelta(days=1)), {}, [s for s in _runs() if s.day < D],
                 today=D - timedelta(days=1))
    data = json.loads(a["score"]["spark"]["data"])
    i = data["d"].index((D - timedelta(days=1)).isoformat())
    assert data["r"][i][1].startswith(f"{yday['score']['value']} · ")
    assert data["r"][i][2] == yday["verdict"]["headline"]
    assert len(data["d"]) == 14 and data["rest"] == ["14 derniers jours", "touche un jour", ""]


# ── an empty user ───────────────────────────────────────────────────────────

async def test_empty_user_has_no_score(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    page = (await as_user.get("/sante")).text  # nothing linked, nothing synced
    assert "pf-gauge" not in page and "Forme du jour" not in page


def test_strava_only_one_signal_is_no_score_until_the_check_in():
    a = _view(_nights({}), {}, _runs(), has_watch=False)
    assert a["verdict"]["tone"] == "ok" and a["score"]["value"] is None
    assert a["score"]["line"] == "Pas encore de score : un seul signal (charge). Dis-moi comment tu te sens ce matin."
    b = _view(_nights({}), {D: _feel(2)}, _runs(), has_watch=False)
    assert b["score"]["value"] == sc.place("ok", 0.6 * 100 + 0.4 * 85) == 98  # 70 + 30 × 0,94
    none = _view(_nights({}), {}, [], has_watch=False)
    assert none["verdict"]["tone"] == "unknown"
    assert none["score"] == {"value": None, "line": "Pas encore de score : connecte ta montre ou Strava."}


# ── provisional normals ─────────────────────────────────────────────────────

def test_provisional_normals_from_7_nights_say_so():
    """9 untagged nights before the 7-night window: a « provisoire » normal (H):
    the tiles' words, the score's rows, Sommeil's line and its readout say it."""
    rows = night_rows(range(0, 16), hr=lambda k: 44.0 + k % 3, hrv=lambda k: 60.0 + k % 4)
    nights = _nights(rows)
    b = nt.band(nights, "hr", D - timedelta(days=6))
    assert b["n"] == 9 and b["provisional"] and nt.band(nights, "hr", D - timedelta(days=6), full=True) is None
    a = _view(nights, {}, _runs())
    words = {t["key"]: t["word"] for t in a["tiles"]}
    assert words["hr"] == "dans ta normale (provisoire)" and words["hrv"] == "dans ta normale (provisoire)"
    rows_ = {r["key"]: r for r in a["score"]["rows"]}
    assert rows_["hrv"]["prov"] and rows_["hr"]["prov"] and "normale provisoire" in rows_["hrv"]["aria"]
    assert sl.sleep_view(nights, D, "14")["building"] is None  # 16 nights up to today: full, nothing to say
    few = _nights(night_rows(range(0, 10)))  # 10 nights: 9 before today, a provisional band for the readout
    s = sl.sleep_view(few, D, "14")
    assert s["building"] == "Ta normale est provisoire : 10 nuits sur 14."
    assert s["heart"]["read"][2].startswith("normale provisoire VFC")
    under = _nights(night_rows(range(0, 6)))
    assert sl.sleep_view(under, D, "14")["building"] == "Ta normale se construit : 6 nuits sur 7."


def test_the_illness_alert_still_needs_a_full_band():
    """Two nights at +11 bpm: on a provisional band (9 nights) no alert, no
    « Reprise », the score is not sent to rest; on a full band (20 nights) R2."""
    def rows(n_before):
        r = night_rows(range(2, 2 + n_before), hr=lambda k: 44.0 + k % 3)
        hot = night_rows([0, 1], hr=56.0)
        return {m: {**r[m], **hot[m]} for m in r}
    prov = _nights(rows(9))
    assert nt.band(prov, "hr", D - timedelta(days=1))["provisional"]
    assert nt.illness_alert(prov, D) is None
    a = _view(prov, {}, _runs())
    assert a["verdict"]["rule"] != "ill" and nt.reprise(prov, {}, D) is None
    full = _nights(rows(20))
    assert nt.illness_alert(full, D) is not None
    b = _view(full, {}, _runs())
    assert b["verdict"]["rule"] == "ill" and b["score"]["word"] == "bas" and b["score"]["value"] <= 39


def test_the_race_page_says_a_provisional_normal():
    """10 nights before J-14 (night bars) and before J-7 (recovery): the usual and the band are « provisoire »."""
    from app.services import race_prep as rp

    rd = D - timedelta(days=3)
    nights = _nights(night_rows(range(21, 31)))  # D-30 → D-21: before J-14 and J-7 of a race on D-3
    bars = rp.night_bars(nights, rd + timedelta(days=20), D)  # a race 17 days ahead: its J-14 is D+3
    assert bars["read"][2] == "normale provisoire" and "(provisoire)" in json.dumps(json.loads(bars["data"])["r"],
                                                                                    ensure_ascii=False)
    rec = rp.recovery(nights, rd, D)
    assert rec["read"][2] == "bande : ta normale avant la course (provisoire)"
    full = _nights(night_rows(range(21, 45)))
    assert rp.recovery(full, rd, D)["read"][2] == "bande : ta normale avant la course"
