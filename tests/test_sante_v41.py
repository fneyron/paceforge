"""Santé v4.1 (owner, 2026-10-08, and the review of v4): the pages as the owner
and the reviewers see them.

- « Retire les infos de synchro dans la page Santé »: no watch line, no sync
  button on Santé (the silent sync stays); « Synchroniser maintenant » per
  watch in Réglages.
- « WHOOP et Oura … n'affichent pas les phases de sommeil ? »: the main night's
  stages, shown and never judged, a bar and a legend with each phase's minutes.
- « Concernant récupération … ce ne doit pas être redondant »: recovery on
  Santé only (no Charge card here, no recovery line on Activités).
- « Dis-moi c'est quoi la barre ? »: no unlabelled mark (the Charge ring's tick
  is a word now; the Contributeurs' bars and every card's marks are named).
- The review's findings at page level: OWN-1 (a window without a night),
  DAWN-FINISH, REG-3 (the toggle without JS), REG-4 (the race page tags as
  Santé), the a11y fixes (UX2, UX5–UX11)."""
import html as H
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.user import User
from app.services import nights as nt
from app.services import race_prep as rp
from app.services import sante
from app.services import sante_sleep as sl
from app.services import sante_today as td
from app.services import sante_training as st
from tests import test_sante
from tests.owner_v4 import D8, seed_owner_v4
from tests.test_coros import _link
from tests.test_nights import night_rows
from tests.test_sante import _garmin_rows, _main, _runs, _seed_rows

# the shared fixtures (a linked athlete, commits turned into flushes)
as_user, no_commit = test_sante.as_user, test_sante.no_commit
ROOT = Path(__file__).resolve().parent.parent
D5 = date(2026, 10, 5)


async def _page(as_user: AsyncClient, monkeypatch, day: date) -> str:
    async def today(*a, **k):
        return day
    monkeypatch.setattr(sante, "athlete_today", today)
    return (await as_user.get("/sante")).text


# ── A1: no sync information on Santé ────────────────────────────────────────

async def test_sante_shows_no_sync_information(as_user: AsyncClient, db_session: AsyncSession, test_user: User,
                                               monkeypatch):
    """No « COROS · synchro il y a 25 min », no « cette nuit pas encore reçue », no sync button: Réglages show each
    watch's last sync. The page still syncs a stale watch on its own and reloads quietly (an empty poller)."""
    from app.services import coros as coros_service

    conn = await _link(db_session, test_user)
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(minutes=25)
    await seed_owner_v4(db_session, test_user)
    await db_session.flush()
    started = []
    monkeypatch.setattr(coros_service, "schedule_sync", lambda uid: started.append(uid) or True)
    html = await _page(as_user, monkeypatch, D8)
    head = html.split('class="pf-sante-head">')[1].split("</div>", 1)[0]
    assert "COROS" not in head and "synchro" not in head.lower() and "pf-sync" not in html
    assert re.sub(r"<[^>]+>", " ", head).split() == ["Santé", "·", "Aujourd'hui", "jeu.", "8", "oct."]
    assert "Synchroniser" not in html and "il y a 25 min" not in html and "pas encore reçue" not in html
    assert started == []  # synced 25 min ago: no background sync, no poller
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await db_session.flush()
    html = await _page(as_user, monkeypatch, D8)
    assert started == [test_user.id]
    poller = re.search(r'<div id="sante-sync" hx-get="/sante/sync-status[^"]*" hx-trigger="load delay:3s" '
                       r'hx-swap="outerHTML"></div>', html)
    assert poller and "Mise à jour" not in html  # silent
    settings = (await as_user.get("/settings")).text
    assert "Dernière synchro" in settings and "Synchroniser maintenant" in settings


# ── A2: the sleep stages ────────────────────────────────────────────────────

async def test_owner_7_october_stages_from_the_daily_summary(db_session: AsyncSession, test_user: User):
    """His 06 → 07 night: Profond 49 min · Léger 3h25 · Paradoxal 1h36 · Éveil 13 min, from COROS's « Sleep
    Summary » of the main sleep (6h03 = its period), never the « daily » ratios (they hold the 2h20 nap)."""
    await seed_owner_v4(db_session, test_user)
    page = await sante.health_page(db_session, test_user.id, today=date(2026, 10, 7))
    h = page["sleep"]["hero"]
    assert (h["label"], h["times"], h["nap"], h["total"]) == ("Cette nuit", "23:35 → 05:40", "+ sieste 2h20", "8h10")
    # printed to 10 min (H, v4.2): 13, 205, 49 and 96 min
    assert [(p["name"], p["hm"]) for p in h["phases"]["parts"]] == [
        ("Éveil", "10 min"), ("Léger", "3h30"), ("Profond", "50 min"), ("Paradoxal", "1h40")]
    # no timeline drawn (no intervals): the nap's times are said in words
    assert h["timeline"] is None and h["out_naps"] == ["sieste 06:40 → 09:05"]
    nights = await nt.load_nights(db_session, test_user.id, D8)
    assert nights[date(2026, 10, 7)].stages == {"awake": 13, "light": 205, "deep": 49, "rem": 96}
    table = {r["iso"]: r["phases"] for r in sl.rows(nights, D8)}
    assert table["2026-10-07"] == ["10 min", "3h30", "50 min", "1h40"]


async def test_a_night_without_stages_keeps_its_plain_bar_and_no_legend(as_user: AsyncClient,
                                                                       db_session: AsyncSession, test_user: User,
                                                                       monkeypatch):
    rows = night_rows([0, 1, 2], today=D8, source="COROS", hr_method="coros_sleep_summary")
    for d in rows["sleep"]:
        rows["sleep"][d][1]["timeline"] = False
    await _seed_rows(db_session, test_user, rows)
    h = (await sante.health_page(db_session, test_user.id, today=D8))["sleep"]["hero"]
    assert h["phases"] is None and h["timeline"]["main"] and not h["stages"]
    main = _main(await _page(as_user, monkeypatch, D8))
    assert 'class="pf-tl-night"' in main and "pf-phases" not in main and "Estimées par la montre" not in main


async def test_the_stages_bar_and_its_legend_on_the_owners_page(as_user: AsyncClient, db_session: AsyncSession,
                                                                test_user: User, monkeypatch):
    await seed_owner_v4(db_session, test_user)
    main = _main(await _page(as_user, monkeypatch, D8))
    hero = main.split('<section id="sommeil"')[1].split("</section>")[0]  # the Sommeil card under the dials
    bar = re.search(r'<div class="pf-phases-bar" aria-hidden="true">(.*?)</div>', hero).group(1)
    assert re.findall(r'class="pf-ph is-(\w+)" style="flex-grow: (\d+)"', bar) == [
        ("awake", "12"), ("light", "326"), ("deep", "71"), ("rem", "119")]
    legend = re.search(r'<ul class="pf-phases-legend" aria-label="Phases de la nuit">(.*?)</ul>', hero).group(1)
    assert [re.sub(r"<[^>]+>", "", li) for li in re.findall(r"<li>(.*?)</li>", legend)] == [
        "Éveil 10 min", "Léger 5h30", "Profond 1h10", "Paradoxal 2h00"]
    # that they are the watch's estimate, the shape of the night and not its quality: « Comment je lis tes nuits »
    # says it (the card shows the bar and its legend, like the mockup the owner approved, 2026-10-08)
    fold = main.split("<summary>Comment je lis tes nuits</summary>")[1].split("</details>")[0]
    assert ("Ta montre estime les phases : elles montrent la forme de ta nuit, pas sa qualité."
            in H.unescape(fold).replace("\u00a0", " "))
    for word in ("bon", "mauvais", "objectif", "insuffisant", "%"):  # shown, never judged
        assert word not in re.sub(r"<[^>]+>", " ", hero), word
    css = (ROOT / "app/static/css/interface.css").read_text()
    for stage in ("awake", "light", "deep", "rem"):  # calm colours, never a status colour
        rule = re.search(rf"\.pf-tl-seg\.is-{stage}, \.pf-ph\.is-{stage} \{{[^}}]*\}}", css).group(0)
        assert f"--pf-st-{stage}" in rule and not any(t in rule for t in ("--pf-ok", "--pf-warn", "--pf-danger"))


def test_the_stage_colours_reach_3_to_1_on_the_cards():
    from tests.test_viz import _tokens, contrast

    theme = (ROOT / "app/static/css/theme.css").read_text()
    css = (ROOT / "app/static/css/interface.css").read_text()
    light, dark = _tokens(theme, ":root"), {**_tokens(theme, ":root"), **_tokens(theme, ".dark")}
    def stages(block):
        body = re.search(re.escape(block) + r" \{ (--pf-st-awake[^}]*)\}", css).group(1)
        return {k: tuple(int(x) for x in v.split()) for k, v in re.findall(r"--(pf-st-\w+): (\d+ \d+ \d+)", body)}
    st_light, st_dark = stages(":root"), stages(".dark")
    assert set(st_light) == set(st_dark) == {"pf-st-awake", "pf-st-light", "pf-st-deep", "pf-st-rem"}
    for t, stages in ((light, st_light), (dark, st_dark)):
        for name, rgb in stages.items():
            for ground in ("pf-soft", "pf-surface", "pf-bg"):
                assert contrast(rgb, t[ground]) >= 3, (name, ground)


# ── A3: recovery on Santé only ──────────────────────────────────────────────

async def test_recovery_lives_on_sante_only(as_user: AsyncClient, client: AsyncClient, db_session: AsyncSession,
                                            test_user: User, monkeypatch):
    """The owner's Transjeju: Santé reads it (its window caps the score; « Effort récent » counts its days down)
    without naming it (v4.3); no Charge ring (v4.4: the week's volume is Activités'); Activités shows it as an
    outing of its week, with no recovery line, no taper line, no fatigue model."""
    await seed_owner_v4(db_session, test_user)
    html = await _page(as_user, monkeypatch, D8)
    assert "Transjeju" not in _main(html) and "/activity/" not in _main(html)
    assert "pf-ring-charge" not in html and "Effort récent" in html
    assert "Charge · 14 jours" not in html and 'data-viz-key="charge"' not in html

    async def today(*a, **k):
        return D8
    monkeypatch.setattr(st, "athlete_today", today)
    activites = (await as_user.get("/activities")).text
    for gone in ("Récupération", "volume bas", "Affûtage", "Fond et fatigue", 'id="fatigue"', "fatigue"):
        assert gone not in activites, gone
    assert "plus longue que d'habitude" not in activites  # the ultra is not a « spike »


# ── A4: no unlabelled mark ──────────────────────────────────────────────────

async def test_every_mark_on_sante_has_words(as_user: AsyncClient, db_session: AsyncSession, test_user: User,
                                             monkeypatch):
    """The dials: no tick, no line, each with its name and its word; the Récupération card's rows: each dot with
    its name and its words (v4.4); the VFC and FC cards: a status line in words, a legend for the dot, the 7-night
    line and the normal; a day without data: no mark at all (owner, 2026-10-08: « s'il n'y a pas de mesure tu ne
    mets rien, pas de point »), so nothing in the legend, its readout says it."""
    today = date(2026, 10, 8)
    rows = _garmin_rows(today)
    for d in (today - timedelta(days=4), today - timedelta(days=9)):  # two nights missing
        for metric in rows:
            rows[metric].pop(d, None)
    await _seed_rows(db_session, test_user, rows)
    await _runs(db_session, test_user, today)
    main = _main(await _page(as_user, monkeypatch, today))
    top = main.split('<div class="pf-dials">')[1].split('<section id="recuperation"')[0]
    assert "pf-ring-tick" not in main and "<line" not in top

    def words(body):
        return H.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip().replace("\u00a0", " "))

    dials = [words(b) for b in re.findall(r'<a class="pf-ring pf-ring-\w+ is-\w+"[^>]*>(.*?)</a>', top, re.S)]
    assert dials[0] == "92 % Sommeil suffisant" and re.fullmatch(r"\d+ % Récupération bonne", dials[1]), dials
    # a run every 3 days: 2 or 3 runs a week, the 7 days within 20 % of the weeks' mean (89 %)
    assert dials[2] == "89 % Entraînement comme d'habitude"
    card = main.split('<section id="recuperation"')[1].split("</ul>")[0]
    rows = [words(body) for body in re.findall(r'<li class="pf-row is-\w+">(.*?)</li>', card)]
    assert len(rows) == 2  # each dot with its name and its words; no recovery window open: no Effort récent
    assert re.fullmatch(r"VFC 7 nuits \u2212\d+ % plus basse que d'habitude", rows[0]), rows[0]
    assert re.fullmatch(r"FC de nuit 7 nuits (comme d'habitude|[+\u2212]\d+ % dans tes valeurs habituelles)",
                        rows[1]), rows[1]
    assert card.count('<i class="pf-dot" aria-hidden="true"></i>') == 2
    for key in ("vfc", "fc"):
        card = re.split(r'<div id="fc"|</section>', main.split(f'<div id="{key}" class="pf-card">')[1])[0]
        # its status line: a dot and its words, no percentage (the row prints it: once; the 2 missing nights leave
        # the VFC week a little under its normal)
        assert re.search(r'<p class="pf-card-status is-(ok|warn)"><i class="pf-dot" aria-hidden="true"></i>'
                         r'(dans tes valeurs habituelles|plus basse que d&#39;habitude|comme d&#39;habitude)</p>',
                         card), key
        legend = card.split('<p class="pf-viz-legend" aria-hidden="true">')[1].split("</p>")[0]
        assert ('<i class="pf-lg is-dot"></i>nuit' in legend and "moyenne sur 7 nuits" in legend
                and '<i class="pf-lg is-band"></i>tes valeurs habituelles' in legend), key
        assert "is-out" not in legend and "is-band-prov" not in legend  # every night counts, a full band
    sleep = main.split('data-viz-key="sommeil-14"')[1].split("</div>\n        </div>")[0]
    legend = sleep.split('<p class="pf-viz-legend" aria-hidden="true">')[1].split("</p>")[0]
    svg = sleep.split('<svg class="pf-viz-svg"')[1].split("</svg>")[0]
    assert "pas de mesure" not in legend and "is-gap" not in sleep and "pf-viz-gap" not in svg  # 2 nights missing
    data = json.loads(re.search(r'class="pf-viz-data">(.*?)</script>', sleep, re.S).group(1))
    gone = [r for r in data["r"] if r[2].endswith(" · pas de mesure")]
    assert len(gone) == 2 and all(r[:2] == ["—", ""] for r in gone)  # a tap on one still says it


# ── B: the review's findings at page level ──────────────────────────────────

async def test_owner_5_october_names_the_transjeju_without_a_night(as_user: AsyncClient, db_session: AsyncSession,
                                                                   test_user: User, monkeypatch):
    """OWN-1: D+2 after the Transjeju (ended 03/10 13:53), no night measured since (his real COROS log): no score,
    an empty dial (owner, 2026-10-09, like WHOOP: « Mets un cadran vide, oui ») and the line that says why; the
    page names no activity (v4.3, owner: « mets juste les scores »). The Sommeil dial: no night this morning; the
    Récupération card's rows: no usual values yet for VFC nor FC de nuit (when they will be ready), the effort's
    window open to 16/10 at 35 (red): the fatigue is still said, on its row."""
    await _link(db_session, test_user)
    await seed_owner_v4(db_session, test_user)
    page = await sante.health_page(db_session, test_user.id, today=D5)
    st_, score = page["state"], page["score"]
    assert st_ is None and (score["value"], score["measured"]) == (None, False) and page["line"] == td.NO_NIGHT
    sleep, recup = page["dials"][:2]
    assert (sleep["value"], sleep["sub"]) == ("—", "pas enregistré") and "ring" not in page
    assert (recup["value"], recup["unit"], recup["sub"], recup["tone"]) == ("—", None, "pas de score", "none")
    assert recup["aria"] == "Récupération : pas de score ce matin."
    assert [(f["name"], f["value"], f["word"], f["detail"], f["tone"]) for f in page["rows"]] == [
        ("VFC", None, "en construction", "prête dans 4\u00a0nuits", "none"),
        ("FC de nuit", None, "en construction", "prête dans 4\u00a0nuits", "none"),
        ("Effort récent", "11\u00a0jours", "avant d'être récupéré", None, "danger")]
    html = await _page(as_user, monkeypatch, D5)
    assert '<span class="pf-ring-sub" aria-hidden="true">pas de score</span>' in html and ">estimée<" not in html
    assert "Transjeju" not in _main(html) and "Pas de nuit mesurée ce matin." not in html


async def test_a_strava_only_athlete_after_an_ultra_gets_a_state(as_user: AsyncClient, db_session: AsyncSession,
                                                                 test_user: User, monkeypatch):
    """OWN-A: no watch, an ultra 2 days ago: no score from the activity alone any more (owner, 2026-10-09, like
    WHOOP: « Mets un cadran vide, oui »), the line says why and the connect links show how to add the nights; the
    « Effort récent » row still says the fatigue."""
    today = date(2026, 10, 8)
    await _runs(db_session, test_user, today, n=8)
    d = today - timedelta(days=2)
    db_session.add(Activity(user_id=test_user.id, strava_activity_id=8811, sport_type="TrailRun", name="Grand Raid",
                            start_date=datetime(d.year, d.month, d.day, 3, tzinfo=timezone.utc), distance=110_000,
                            moving_time=13 * 3600, elapsed_time=14 * 3600, total_elevation_gain=5000,
                            raw_data={"utc_offset": 7200}))
    await db_session.flush()
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["state"] is None and page["score"]["value"] is None and page["connect"]
    assert page["line"] == td.NO_WATCH and "effort" in {r["key"] for r in page["rows"]}
    html = await _page(as_user, monkeypatch, today)
    assert "Effort récent" in html and 'href="/settings#coros"' in html and "Grand Raid" not in _main(html)
    assert "Connecte ta montre pour ta récupération." not in html


async def test_the_morning_after_a_dawn_finish_on_the_page(db_session: AsyncSession, test_user: User):
    """DAWN-FINISH: a 22-h 100-miler finishing at 03:00, asleep 04:00 → 11:00: that morning reads it (D+1,
    capped at 35), « Effort récent » red (no Charge récente in the score any more: like WHOOP, the effort weighs
    through its window's cap, 2026-10-08); that sleep is « après ultra »."""
    today = date(2026, 10, 8)
    rows = _garmin_rows(today)
    rows["sleep"][today] = (400, {"main_start": f"{today}T04:00", "main_end": f"{today}T11:00", "timeline": True},
                            "Garmin")
    await _seed_rows(db_session, test_user, rows)
    await _runs(db_session, test_user, today)
    y = today - timedelta(days=1)
    db_session.add(Activity(user_id=test_user.id, strava_activity_id=8812, sport_type="TrailRun", name="100 miles",
                            start_date=datetime(y.year, y.month, y.day, 3, tzinfo=timezone.utc),  # 05:00 local
                            distance=160_000, moving_time=20 * 3600, elapsed_time=22 * 3600,
                            total_elevation_gain=9000, raw_data={"utc_offset": 7200}))
    await db_session.flush()
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["state"]["key"] == "effort" and page["state"]["text"] is None and page["score"]["value"] == 35
    assert [p["key"] for p in page["score"]["parts"]] == ["hrv", "hr", "sleep"]
    assert {f["key"]: f["tone"] for f in page["rows"]}["effort"] == "danger"
    marks = {r["iso"]: r["marks"] for r in page["sleep"]["rows"]}
    assert "◇ après un gros effort" in marks[today.isoformat()]  # 22 h: an ultra (v4.2), no outing named (v4.3)


async def test_the_range_toggle_works_without_js(as_user: AsyncClient, db_session: AsyncSession, test_user: User,
                                                 monkeypatch):
    """REG-3: « 14 nuits / 3 mois » is a GET form (?r=90, back to the chart: #sommeil-detail): with scripts off or
    pf-viz.js not loaded, it still switches; pf-viz.js switches in place."""
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, _garmin_rows(today, days=60))
    html = _main(await _page(as_user, monkeypatch, today))
    form = re.search(r'<form class="pf-seg pf-seg-sm pf-viz-ranges" data-viz-ranges role="group" '
                     r'aria-label="Période" method="get" action="/sante#sommeil-detail">(.*?)</form>', html,
                     re.S).group(1)
    assert 'type="submit" name="r" value="90" data-range="90" aria-pressed="false">3 mois' in form
    r = await as_user.get("/sante?r=90")
    assert '<div data-range-panel="90">' in r.text and '<div data-range-panel="14" hidden>' in r.text


async def test_the_race_page_tags_the_nights_as_sante(db_session: AsyncSession, test_user: User):
    """REG-4: « sortie intense le soir » from the laps too (load_segments), and the 3 nights after an effort of 6 h
    or more, on the race page as on Santé: the same nights never fire the alert."""
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, night_rows(range(0, 30), today=today))
    d = today - timedelta(days=6)  # an interval session ending 21:00 local, 2 h before sleep (23:00)
    db_session.add(Activity(user_id=test_user.id, strava_activity_id=8813, sport_type="Run", name="Fractionné",
                            start_date=datetime(d.year, d.month, d.day, 18, tzinfo=timezone.utc), distance=12_000,
                            moving_time=3600, elapsed_time=3600, average_heartrate=142, max_heartrate=180,
                            raw_data={"utc_offset": 7200},
                            laps=[{"average_heartrate": 172, "moving_time": 360}] * 4
                            + [{"average_heartrate": 120, "moving_time": 180}] * 12))
    big = today - timedelta(days=12)
    db_session.add(Activity(user_id=test_user.id, strava_activity_id=8814, sport_type="Hike", name="Rando",
                            start_date=datetime(big.year, big.month, big.day, 4, tzinfo=timezone.utc),
                            distance=30_000, moving_time=7 * 3600, elapsed_time=7 * 3600, raw_data={"utc_offset": 7200}))
    await db_session.flush()
    sessions = await st.load_sessions(db_session, test_user.id, today)
    race_nights = await rp._nights(db_session, test_user.id, today, today - timedelta(days=60), sessions, [])
    sante_nights = await nt.load_nights(db_session, test_user.id, today, sessions=sessions,
                                        efforts=st.efforts(sessions), peak=st.hr_max(sessions, today))
    late = today - timedelta(days=5)
    assert "late" in race_nights[late].tags and "late" in sante_nights[late].tags
    after = [big + timedelta(days=k) for k in (1, 2, 3)]
    assert all("big" in race_nights[x].tags and "big" in sante_nights[x].tags for x in after)
    # the same nights never fire the alert there (their only effect: every measured night counts, 2026-10-08)
    assert ({x for x, n in race_nights.items() if not n.tags.isdisjoint(nt.CONTEXT)}
            == {x for x, n in sante_nights.items() if not n.tags.isdisjoint(nt.CONTEXT)} != set())


def test_the_readouts_fit_one_line_at_358_px():
    """UX2: a past day's readout in compact words (v4.3: the date, the score, the state; « pas de mesure ce
    jour-là » the longest); a provisional normal says « (provisoire) » after its numbers."""
    from app.services import sante_score as sc

    assert len("mer. 30 sept. · " + sc.NO_MEASURE) <= 44  # ≈ 290 px at 13 px
    assert max(len(f"{td.GLYPHS[t]} {w}") for t, w in td.WORDS.items()) <= 24  # the word line, 15 px


def test_the_large_readouts_keep_their_unit_apart():
    """UX11: a plain no-break space between a large value and its unit (the display font has no narrow one)."""
    nights = nt.build_nights(night_rows(range(0, 28), today=D8), D8)  # every night at 23:00
    h = sl.habits(nights, D8)
    assert h["regular"] == "28 nuits sur 28 à moins d'1\u00a0h de ton coucher habituel"


def test_the_a11y_fixes_in_css_and_js():
    """UX5: a mouse leaving a figure puts it back to rest (its link still reachable: the figure, not the plot); a
    second tap goes back to rest. UX7: pinch-zoom on the charts. UX6: forced colours for the hours, the lanes, the
    7 h label, the 40/70 lines, the legend, the stages. UX10: the folds' chevrons are decoration."""
    js = (ROOT / "app/static/js/pf-viz.js").read_text()
    assert 'fig.addEventListener("pointerleave", function (e) {' in js
    assert 'if (e.pointerType === "mouse" && !drag) select(dflt, { silent: true });' in js
    assert "select(i === cur && cur !== dflt ? dflt : i);" in js
    css = (ROOT / "app/static/css/interface.css").read_text()
    assert "touch-action: pan-y pinch-zoom;" in css and "touch-action: pan-y;" not in css
    fc = css[css.index("/* ── Santé v4"):css.index("/* ── Réglages")]
    fc = fc[fc.index("@media (forced-colors: active)"):]
    for rule in (".pf-tl text, .pf-tl .pf-tl-lane, .pf-viz-svg text.pf-viz-strong { fill: CanvasText; }",
                 ".pf-tl-step { stroke: CanvasText; opacity: 1; }", ".pf-viz-hair { stroke: CanvasText; }",
                 ".pf-viz-legend .pf-lg:is(.is-night, .is-day, .is-dot) { background: CanvasText; "
                 "box-shadow: none; }",
                 ".pf-viz-legend .pf-lg:is(.is-ref, .is-mean) { background: none; border-top-color: CanvasText; }",
                 ".pf-ph.is-deep { background: CanvasText; }"):
        assert rule in fc, rule
    assert 'content: " ›" / "";' in css and 'content: " ⌄" / "";' in css and 'content: "›" / "";' in css
    for path in ("app/services/sante.py", "app/services/sante_score.py", "app/services/sante_sleep.py",
                 "app/services/training_view.py", "app/services/race_prep.py"):
        assert "Touche " not in (ROOT / path).read_text(), path  # the sliders' text: « Choisis … »


def test_the_sommeil_hero_is_not_the_race_pages_grid():
    """UX3: the Sommeil card stays a block at 1 024 px and more (.pf-hero is the race page's grid)."""
    page = (ROOT / "app/templates/partials/sante_page.html").read_text()
    assert 'class="pf-card pf-sum"' in page and "pf-hero" not in page
    css = (ROOT / "app/static/css/interface.css").read_text()
    rules = re.findall(r"\.pf-sum[^{]*\{[^}]*\}", css)
    assert rules and not any("grid" in r for r in rules)  # a plain block


# ── the VFC and FC de nuit cards: « la barre à côté des points » ─────────────

async def test_the_night_cards_draw_no_mark_without_a_night(as_user: AsyncClient, db_session: AsyncSession,
                                                            test_user: User, monkeypatch):
    """The owner's nights: 29/09 → 01/10, none 02/10 → 05/10 (the Transjeju and the days after), 06/10 → 08/10.
    The 7-night mean used to run flat over 01/10 → 05/10 with no dot under it, and the selection was a grey
    full-height column: both read as « la barre à côté des points ». Now the mean is drawn over the measured nights
    only (broken on any night without one), the legend names it only when a segment of it is drawn, and the
    selection is a ring around the night's dot."""
    await _link(db_session, test_user)
    await seed_owner_v4(db_session, test_user)
    main = _main(await _page(as_user, monkeypatch, D8))
    drawn = {}
    for key in ("vfc", "fc"):
        card = re.split(r'<div id="fc"|</section>', main.split(f'<div id="{key}" class="pf-card">')[1])[0]
        svg = card.split('<svg class="pf-viz-svg"')[1].split("</svg>")[0]
        data = json.loads(re.search(r'class="pf-viz-data">(.*?)</script>', card, re.S).group(1))
        days, xs = [date.fromisoformat(d) for d in data["d"]], data["x"]
        dots = {int(i) for i in re.findall(r'class="pf-viz-dot[^"]*" data-i="(\d+)"', svg)}
        seen = {days[i] for i in dots}
        assert {date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 7), D8} <= seen, key
        assert not any(date(2026, 10, 2) <= d <= D5 for d in seen), key
        legend = card.split('<p class="pf-viz-legend" aria-hidden="true">')[1].split("</p>")[0]
        line = re.search(r'<path d="([^"]*)" class="pf-viz-line"', svg)
        drawn[key] = []
        if line:
            for seg in re.split(r" (?=M)", line.group(1)):
                pts = [float(p[1:].split()[0]) for p in re.findall(r"[ML][\d.]+ [\d.]+", seg)]
                idx = [min(range(len(xs)), key=lambda i, px=px: abs(xs[i] - px)) for px in pts]
                assert len(idx) > 1 and idx == list(range(idx[0], idx[-1] + 1)), key  # adjacent nights only
                assert set(idx) <= dots, key  # a dot under every point of it
                drawn[key].append([days[i] for i in idx])
            assert "moyenne sur 7 nuits" in legend, key
        else:
            assert "moyenne sur 7 nuits" not in legend, key
        assert not any(date(2026, 10, 2) <= d <= D5 for seg in drawn[key] for d in seg), key
        assert "<rect" not in svg, key  # no grey column behind the selected night
        assert 'class="pf-viz-ring"/>' in svg and 'class="pf-viz-at"/>' in svg, key
        assert f'class="pf-viz-dot is-sel" data-i="{days.index(D8)}"' in svg, key  # the latest night, in ink
    # every measured night counts (owner, 2026-10-08): FC de nuit's 7-night windows of 06/10 → 08/10 hold 3 nights
    # each (01/10 or 30/09 with 06/10, 07/10, 08/10): one segment over their 3 dots; VFC has no 06/10, so no window
    # holds 3 nights next to another such night (01/10 alone: no segment, no legend item). It used to run flat from
    # 01/10 to 05/10, the window still finding 29/09 → 01/10
    assert drawn == {"vfc": [], "fc": [[date(2026, 10, 6), date(2026, 10, 7), D8]]}, drawn
