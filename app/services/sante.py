"""Santé v4: one page (owner, 2026-10-08: « mets tout dans un seul », « plus
de graphiques »), like WHOOP (2026-10-08, the approved mockup, owner: « Fais
comme WHOOP, ça doit rester simple »). Top to bottom:
- three equal dials in a row, in WHOOP's order, each a percentage over its
  name and a plain word (never colour alone) and a link to its card below:
  Sommeil (the last 24 h, naps in, nights.day_tst24, as a percentage of an
  8-h need, at most 100 % (H); « suffisant » from 7 h (Watson 2015a's
  habitual amount read on one day, H), « un peu court » from 6 h (H),
  « court » under 6 h (Craven 2022), its arc then in the warning colour),
  Récupération (the 0–100 score as a percentage in its state's colour,
  « bonne », « en cours », « faible »; empty, « pas de score », when no night
  was measured, like WHOOP: sante_score.dial), Entraînement (the last 7 days' activity time
  against the usual week, as a percentage; « comme d'habitude » within ± 20 %,
  H); the illness alert's one sentence under them (sante_today; v4.3, owner:
  « Ne mentionne pas les sorties dans la partie Santé, ça complexifie »: no
  activity named). No sub-score anywhere, no « Détail du score »;
- three cards right under the dials, in their order (the approved mockup):
  Récupération (rows: VFC and FC de nuit over 7 nights — the 7-night mean as
  a signed percentage of the usual value and its word, « comme d'habitude » at
  0 %, « en construction » and when the usual values will be ready — and
  « Effort récent », the days left in a recovery window whenever one is
  open), Sommeil (sante_sleep: last night, its stages), Entraînement (the
  last 7 days and the usual week, a link to Activités, where the weeks are);
  each title a link to its details further down: « Récupération · 14 jours »,
  « VFC · 30 nuits » and « FC de nuit · 30 nuits », each night card opening
  on its status line in words, the last 7 nights against the usual values the
  score reads (v4.3, owner: « est-ce que c'est bien ou pas bien ? »), then
  the 24-h sleep chart and the habits; every measured night counts, last night included
  (owner, 2026-10-08: « Tous les relevés VFC doivent compter en fait, pareil
  pour la FC »), and a night or a day without a measure draws nothing (« s'il
  n'y a pas de mesure tu ne mets rien, pas de point »);
- the closed folds (how the recovery is computed, how the nights are read,
  each ending on a link to /sante/sources; the nights' table).
From past activities and the nights only: no planned race, no check-in, no
training prescription, no sync status (Réglages': the page syncs on its own
when it opens and reloads quietly when something new arrived). Only
PaceForge's own nightly values (nights.py), read against the athlete's own
band; no brand value is read. A night without the watch is a gap, never a
zero. Each number is printed once on the page: a dial's percentage on the
dial, the hours and the values on the cards.
"""
import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from functools import partial

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.services import nights as nt
from app.services import sante_score as sc
from app.services import sante_sleep as sl
from app.services import sante_today as td
from app.services import sante_training as st
from app.services import viz
from app.services.health import WATCH_SOURCES, fmt_minutes

logger = logging.getLogger(__name__)

HISTORY_DAYS = 400
CARD_NIGHTS = 30  # the VFC and FC de nuit cards
SPARSE_NIGHTS, SPARSE_DAYS = 10, 14  # fewer measured nights in the 30: the axis spans them (14 days at least)


async def health_page(db: AsyncSession, user_id: int, today: date | None = None, now: datetime | None = None,
                      r: str | None = None) -> dict:
    """Everything /sante draws. `has_data` is False when neither a watch nor an
    activity ever arrived. `r`: the sleep bars' range (14 or 90; else the
    first holding a night)."""
    now = now or datetime.now(timezone.utc)
    latest = (await db.execute(select(func.max(HealthMetric.date)).where(HealthMetric.user_id == user_id))).scalar()
    clock = None  # the athlete's wall clock, only for the day the page finds itself (a given day stays as given)
    if today is None:
        today = await athlete_today(db, user_id, now, latest)
        clock = await local_clock(db, user_id, now)
    sessions = await st.load_sessions(db, user_id, today)
    peak = st.hr_max(sessions, today)
    # Garmin's day altitudes: where the athlete slept, first (R2)
    day_alt = await nt.day_altitudes(db, user_id, today - timedelta(days=HISTORY_DAYS), today)
    # the nights' tags read the efforts' classes only
    nights = await nt.load_nights(db, user_id, today, days=HISTORY_DAYS, sessions=sessions,
                                  efforts=st.efforts(sessions), peak=peak, day_alt=day_alt)
    # a Longue run like a race (R3): its HR against the athlete's bounds as of its day (the nights' resting HR)
    rest_of = partial(nt.rest_hr, nights)
    # a dawn finish: D on the day before its first morning
    efforts = nt.anchor_efforts(nights, st.efforts(sessions, rest_of))
    sources = set((await db.execute(select(HealthMetric.source).distinct().where(
        HealthMetric.user_id == user_id, HealthMetric.metric != "feel"))).scalars().all())
    # the night awaited is the calendar day's (how soon the page syncs again); the page reads the cycle's day
    night_state = await _night_state(db, user_id, today, sources & set(WATCH_SOURCES))
    until, today = today, cycle_day(nights, today, clock)
    out = {
        "has_data": bool(sources) or bool(sessions),  # older rows may come from another source (Apple Health)
        "has_watch_data": bool(sources),
        "today": today,
        "night_state": night_state,
    }
    if not out["has_data"]:
        out["day_label"] = viz.d_short(today)
        return out
    with nt.memo():  # the nights no longer change: their bands and alerts are computed once
        nt.freeze(nights)
        day = _assess(nights, sessions, efforts, today)
        history = (_history(nights, sessions, efforts, today, rest_of, day_alt)
                   + [(today, day["state"], day["score"])])
        out["recup"] = sc.history_card(history, today)
        hero = sl.hero(nights, today)
        samples = await _timeline(db, user_id, nights, hero["day"]) if hero else {}
        out["sleep"] = sl.sleep_section(nights, today, r, samples)
        out["vfc"] = _night_card(nights, "hrv", today, day)
        out["fc"] = _night_card(nights, "hr", today, day)
        out.update(_top(day, efforts, sessions, bool(sources), out, until))
    out["day_label"] = viz.d_short(today)
    out["method"] = sc.typo(sc.METHOD)
    return out


# ── the page's day: a cycle, like WHOOP's (from one sleep to the next), never midnight ─────────────────────

CYCLE_NOON = 12  # (H) from noon, a day whose night never came is that day anyway (the watch was not worn)


async def local_clock(db: AsyncSession, user_id: int, now: datetime) -> datetime | None:
    """The athlete's wall-clock time (naive) from their UTC offset (their latest sessions, as athlete_today); None
    without one (a seam the tests move)."""
    offset = await st.utc_offset(db, user_id)
    return None if offset is None else (now + timedelta(seconds=offset)).replace(tzinfo=None)


def _slept(nights, d: date) -> bool:
    """The night that ended on the morning of `d` reached the page (its sleep or its heart values)."""
    n = nights.get(d)
    return bool(n and (n.asleep is not None or n.hr is not None or n.hrv is not None))


def cycle_day(nights, today: date, clock: datetime | None) -> date:
    """The day Santé reads, like WHOOP's cycle, « from sleep onset to sleep onset » rather than midnight to
    midnight (WHOOP's engineering blog; owner, 2026-10-09, awake at 1 a.m. on a plane: « J'ai sommeil vide »):
    before noon (H), while this morning's night has not reached the page and last night's has, it is still
    yesterday — its night, its score, its 7 days (and the activities since midnight); from noon, or as soon as the
    night is in, today (a night without the watch then reads « pas enregistré »). `clock` None (no offset known,
    or a day given): `today`."""
    if clock is None or clock.date() != today or clock.hour >= CYCLE_NOON:
        return today
    yesterday = today - timedelta(days=1)
    return yesterday if not _slept(nights, today) and _slept(nights, yesterday) else today


# ── one day: its state and its score ────────────────────────────────────────

def _night_stat(nights, metric: str, d: date) -> dict:
    """The 7-night mean of every measured night (3 at least, Plews 2014; Lau
    2022) and the usual values it is read against (nights.normal: every
    measured night of the 60 days up to and including last night, on the
    watch that mean reads: one band per watch, Dial 2025; « provisoire » from
    7 to 13 nights, H); the band even without a mean (« en construction » is
    said only when there is none). The status is judged on the unrounded
    mean. `seen`: the metric measured in those 60 days."""
    m = nt.mean7(nights, metric, d)
    src = nt.mean_source(nights, metric, d)
    b = nt.normal(nights, metric, d)
    v = m["value"] if m else None
    lo = d - timedelta(days=nt.BAND_DAYS - 1)
    seen = src is not None and any(lo <= x <= d and n.value(metric) is not None for x, n in nights.items())
    return {"value": v, "normal": b, "status": nt.status(v, b) if b and v is not None else None, "seen": seen}


def _assess(nights, sessions, efforts, d: date) -> dict:
    """What day `d` reads — today, or a past day of the 14-day card (_history:
    the rows and activities known that day) — and its score and state (the
    band of the score). Every measured night counts in the 7-night means (an
    alert episode's own nights too: the score shows the episode, never « dans
    tes valeurs habituelles » the morning the alert stops)."""
    alert = nt.illness_alert(nights, d)
    stats = {k: _night_stat(nights, k, d) for k in ("hr", "hrv")}
    tst = nt.day_tst24(nights, d)  # the 24 h before this morning's wake: the Sommeil row's number and the score's
    m7 = nt.mean7(nights, "tst24", d)
    usual = nt.band(nights, "tst24", d - timedelta(days=6), source=nt.mean_source(nights, "tst24", d)) if m7 else None
    day = {"day": d, "stats": stats, "tst24": tst,
           "sleep": {"mean7": m7["value"] if m7 else None, "usual": usual["center"] if usual else None,
                     "prov": bool(usual and usual["provisional"])},
           "window": st.effort_window(efforts, d), "alert": alert}
    score = sc.score_of(day)
    day.update(score=score, state=td.state(score, day))
    return day


def _history(nights, sessions, efforts, today: date, rest_of=None, day_alt=None) -> list:
    """[(day, state, score)] of the 13 days before today, each as the page
    computed it on that day: from the nights up to that day (the bands, the
    means, the alert and the 24 h read nothing dated after it) and the
    activities known then only — an activity is known once it ended (it is
    uploaded then), so one still running at midnight belongs to the next day
    — and the efforts as that day saw them (st.efforts on those activities: a
    chain across midnight joins a later day, so a past day may have seen a
    smaller effort). The nights' tags only keep a night from firing the
    illness alert (every measured night counts in the bands and the means):
    as of that day they are today's (an effort tags the nights after it, a
    session the night after it: both known by then) but for « sortie intense
    le soir » (it reads that day's HR bounds). The nights are tagged once, as
    of today; a day whose « late » tags and efforts are the same, with no
    activity still running at its midnight, reads them and shares their bands
    (nights.memo); any other day is tagged anew from what it knew. `rest_of`:
    what the efforts were computed with (st.efforts): a Longue's heart-rate
    test reads the bounds as of its own day, the same whichever later day
    reads it; `day_alt`: Garmin's day altitudes the nights were tagged with (a
    night reads the day before)."""
    recent = {x: n for x, n in nights.items() if x > today - timedelta(days=sc.HISTORY_NIGHTS)}
    base = {x: nt.retagged(n) for x, n in recent.items()}
    nt.tag_activities(base, sessions, efforts, nt.rest_hr(base, today), st.hr_max(sessions, today), day_alt)
    nt.freeze(base)
    by_day = defaultdict(list)
    for s in sessions:
        by_day[s.day].append(s)
    ends = {s.id: st.local_end(s) for s in sessions}
    late = {x: c for x, n in base.items()
            if (c := nt.late_candidates(n, by_day.get(x - timedelta(days=1), []) + by_day.get(x, [])))}
    out = []
    for k in range(sc.HISTORY_DAYS - 1, 0, -1):
        d = today - timedelta(days=k)
        midnight = datetime.combine(d + timedelta(days=1), time(0))
        ss = [s for s in sessions if ends[s.id] <= midnight]  # finished by the end of `d`
        known = {s.id for s in ss}
        # the efforts as that day saw them: today's that it knew whole; computed anew when one was only partly
        # known (a chain across midnight: that day saw a smaller effort)
        same = not any(e.ids & known and not e.ids <= known for e in efforts)
        efs = [e for e in efforts if e.ids <= known] if same else nt.anchor_efforts(base, st.efforts(ss, rest_of))
        running = any(s.day <= d and s.id not in known for s in sessions)
        peak, rest = st.hr_max(ss, d), nt.rest_hr(base, d)
        if not running and same and all(any(nt.vigorous(s, rest, peak) for s in c) == ("late" in base[x].tags)
                                         for x, c in late.items() if x <= d):
            nd = base
        else:
            nd = {x: nt.retagged(n) for x, n in recent.items() if x <= d}
            nt.tag_activities(nd, ss, efs, rest, peak, day_alt)
            nt.freeze(nd)
        past = _assess(nd, ss, efs, d)
        out.append((d, past["state"], past["score"]))
    return out


# ── the top: three dials, like WHOOP's (2026-10-08, owner: « Fais comme WHOOP, ça doit rester simple ») ─────

# the Sommeil dial's word from this morning's 24 h (H): 7 h and more (Watson 2015a), 6 to 7 h, under 6 h (Craven 2022)
SLEEP_WORDS = ((sl.REF_MIN, "suffisant"), (nt.SHORT_DAY_MIN, "un peu court"), (0, "court"))
SLEEP_NEED = sl.SLEEP_NEED
NO_NIGHT_WORD = "pas enregistré"
USUAL_WEEKS, USUAL_MIN_WEEKS = 11, 4  # (H) the usual week: the mean of the last 11 complete weeks (Activités'
# « par semaine en moyenne », the same weeks and the same moving time), 4 weeks with an activity at least
TRAIN_TURN = 2  # the Entraînement dial's full turn: twice the usual week
USUAL_SPREAD = 0.2  # (H) within ± 20 % of the usual week: « comme d'habitude »
NO_HABIT = "pas encore d'habitude"  # the Entraînement dial without a usual week
NO_USUAL = "pas encore de semaine habituelle"  # its card's row then
EFFORT_ORANGE = 65  # a recovery window's cap from which « Effort récent » wears orange (its 65 days); red under it
NIGHT_ROWS = {"hrv": ("vfc", "VFC"), "hr": ("fc", "FC de nuit")}
ALERT_WORD = "nettement plus haute depuis 2 nuits"  # the FC de nuit row under the illness alert
SAME = "comme d'habitude"  # a 7-night mean at its usual value, to the percent: « 0 % » said in words


def sleep_dial(tst24: int | None, href: str | None) -> dict:
    """« Sommeil »: the 24 h before this morning's wake, naps in (nights.day_tst24: the score's figure), as a
    percentage of an 8-h need, at most 100 % (H; the hours are the Sommeil card's, printed once), its arc in the
    sleep colour (one stable hue), in the warning colour under 6 h; its word from the hours: 7 h or more
    « suffisant » (AASM/SRS: Watson 2015a), 6 to 7 h « un peu court » (H), under 6 h « court » (Craven 2022); « — »
    and « pas enregistré » without a night this morning. `href`: the Sommeil card, None without one (no night
    ever: a plain dial)."""
    if tst24 is None:
        return viz.ring("sommeil", None, "—", "Sommeil", NO_NIGHT_WORD, tone="none", href=href,
                        aria="Sommeil : pas de nuit enregistrée ce matin.")
    word = next(w for lo, w in SLEEP_WORDS if tst24 >= lo)
    p = min(100, sc.rounded(100 * tst24 / SLEEP_NEED))
    return viz.ring("sommeil", tst24 / SLEEP_NEED, str(p), "Sommeil", word, unit="%", href=href,
                    tone="warn" if tst24 < nt.SHORT_DAY_MIN else "sleep",
                    aria=f"Sommeil {sc.pct(p)} de tes 8 heures de besoin, {word}.")


def usual_week(sessions, today: date) -> float | None:
    """The usual week (H), the figure Activités › « Semaines » prints as « par semaine en moyenne »: the mean of
    the activities' moving minutes over the last 11 complete weeks (Monday → Sunday, local days) since the first
    activity's week; None with fewer than 4 of those weeks holding an activity."""
    if not sessions:
        return None
    monday = today - timedelta(days=today.weekday())
    first = min(s.day for s in sessions)
    per = defaultdict(float)
    for s in sessions:
        per[s.day - timedelta(days=s.day.weekday())] += s.minutes
    weeks = [m for m in (monday - timedelta(days=7 * k) for k in range(1, USUAL_WEEKS + 1))
             if m + timedelta(days=6) >= first]
    if sum(1 for m in weeks if per.get(m, 0.0) > 0) < USUAL_MIN_WEEKS:
        return None
    return statistics.fmean(per.get(m, 0.0) for m in weeks) or None


def training(sessions, today: date, until: date | None = None) -> dict:
    """« Entraînement », the third dial and its card: the activities' time of the last 7 days (stops included)
    against the usual week (usual_week), as a percentage, 100 % as usual, the arc full at twice it, in the accent
    colour (one stable hue); within ± 20 % « comme d'habitude » (H), else « plus que d'habitude » or « moins que
    d'habitude »; without a usual week, the 7 days' time itself and « pas encore d'habitude ». The card prints the
    7 days' time and the usual week's (to 5 min: a typical value), each once: without a usual week the dial
    prints the time, the card only its words. `until`: the calendar day when the page still reads yesterday's
    cycle (cycle_day): the activities since midnight count in it. {dial, week, usual, word}."""
    until = until or today
    week = sum(s.minutes for s in sessions if today - timedelta(days=6) <= s.day <= until)  # moving time, as Activités
    usual = usual_week(sessions, today)
    href = "#entrainement"
    if usual is None:
        return {"dial": viz.ring("entrainement", None, viz.hm(week), "Entraînement", NO_HABIT, tone="accent",
                                 href=href, aria=f"Entraînement : {viz.hm_long(week)} d'activité ces 7 derniers "
                                                 f"jours, {NO_HABIT}."),
                "week": None, "usual": None, "word": NO_USUAL}
    ratio = week / usual
    word = ("plus que d'habitude" if ratio > 1 + USUAL_SPREAD else
            "moins que d'habitude" if ratio < 1 - USUAL_SPREAD else "comme d'habitude")
    p = sc.rounded(100 * ratio)
    return {"dial": viz.ring("entrainement", week / (TRAIN_TURN * usual), str(p), "Entraînement", word,
                             tone="accent", unit="%", href=href,
                             aria=f"Entraînement {sc.pct(p)} de ta semaine habituelle, {word}."),
            "week": viz.hm(week), "usual": f"ta semaine habituelle{viz.NBSP}: {viz.hm(round(usual / 5) * 5)}",
            "word": None}


def _row(key: str, name: str, value: str | None, word: str, tone: str | None, qual: str | None = None,
         detail: str | None = None) -> dict:
    """One row of a card under the dials: a dot in `tone` (ok, accent: neutral, warn, danger, none: grey) and the
    name on the left (`qual` under it, muted: « 7 nuits »), the value over its word on the right (`detail`, a
    second line: when the usual values will be ready); never colour alone."""
    return {"key": key, "name": name, "qual": qual, "value": value, "word": word, "detail": detail, "tone": tone}


def night_row(card: dict | None, metric: str) -> dict | None:
    """« VFC » / « FC de nuit » (7 nuits) in the Récupération card: its chart card's status (card_status: one
    source of truth), its percentage and its word, in its colour: « −8 % plus basse que d'habitude », « +2 % dans
    tes valeurs habituelles », « comme d'habitude » at 0 %; grey without a comparison, « en construction » over
    when its usual values will be ready (« prête dans 2 nuits »), or « trop peu de nuits pour comparer »; under the
    illness alert the FC de nuit row says « nettement plus haute depuis 2 nuits » (red). None without its card
    (the watch never measured it in 30 days)."""
    if card is None:
        return None
    key, name = NIGHT_ROWS[metric]
    s = card["status"]
    return _row(key, name, s["value"], s["word"], s["tone"] or "none", qual="7 nuits", detail=s.get("detail"))


def effort_row(efforts, day: dict) -> dict | None:
    """« Effort récent », whenever a recovery window is open (owner, 2026-10-08: « Un effort récent, il faut le
    prendre en compte et afficher la fatigue quand même »), whether its cap binds the score or not: « 8 jours »
    over « avant d'être récupéré », the days from today to the last day of the windows open (« dernier jour » on
    it); never the activity, its date or its hours (v4.3: no activity named on Santé), never a percentage (the
    window is a step, not a curve). A red dot while the window caps the score at 35 or 45 (its first days),
    orange at 65."""
    w = day["window"]
    if not w:
        return None
    d = day["day"]
    last = max(x["until"] for e in efforts if (x := st.effort_window([e], d)))
    n = (last - d).days
    value = "dernier jour" if n <= 0 else f"{n}{viz.NBSP}jour{'s' if n > 1 else ''}"
    return _row("effort", "Effort récent", value, "avant d'être récupéré",
                "warn" if w["cap"] >= EFFORT_ORANGE else "danger")


def _top(day: dict, efforts, sessions, has_watch: bool, page: dict, until: date | None = None) -> dict:
    """The three dials, each a link to its card right under the dials, else to its details further down (a plain
    dial when the page has neither): Sommeil, Récupération, Entraînement (WHOOP's order); the illness alert's
    sentence under them (sante_today), or the line that says why there is no score; the Récupération card's rows
    (VFC, FC de nuit, Effort récent) and the Entraînement card."""
    state, score = day["state"], day["score"]
    sleep = page.get("sleep") or {}
    sleep_href = ("#sommeil" if sleep.get("state") == "old" or sleep.get("hero") else
                  "#sommeil-detail" if sleep.get("state") == "ok" else None)
    rows = [r for r in (night_row(page.get("vfc"), "hrv"), night_row(page.get("fc"), "hr"),
                        effort_row(efforts, day)) if r]
    recup_href = "#recuperation" if rows else "#recuperation-detail" if page.get("recup") else None
    train = training(sessions, day["day"], until)
    return {"dials": [sleep_dial(day["tst24"], sleep_href), sc.dial(score, state, recup_href), train["dial"]],
            "state": state, "line": None if state else td.no_state_line(has_watch),
            "connect": not has_watch,  # no watch: how to add the nights, under the line
            "rows": rows, "training": train, "score": score}


# ── the cards ───────────────────────────────────────────────────────────────

CARDS = {"hrv": ("vfc", "VFC", "ms", "millisecondes"), "hr": ("fc", "FC de nuit", "bpm", "battements par minute")}


def _span(metric: str, values: list, bands: list) -> float:
    """The y axis's least span: VFC ≥ 20 ms and ≥ 30 % of its median (± 15 %),
    FC de nuit ≥ 14 bpm, and the normal never more than half the height (a
    few ms or bpm must not look like a cliff)."""
    widest = max((b[1] - b[0] for b in bands if b), default=0)
    if metric == "hrv":
        vals = [v for v in values if v is not None]
        return max(20.0, 0.3 * statistics.median(vals) if vals else 0.0, 2 * widest)
    return max(14.0, 2 * widest)


# each night card's status line (v4.3): its last 7 nights against the usual values the score reads, in the words
# and the colour of its facts row (sante_score.row_tone); what it often means, only out of them on the side that
# matters; without usual values yet, how many nights until they exist (v4.4, owner: « les explications en
# français ne sont pas claires »: « tes valeurs habituelles », « d'habitude », never « ta normale »)
STATUS = {"in": "dans tes valeurs habituelles", "below": "plus basse que d'habitude",
          "above": "plus haute que d'habitude"}
BAD = {"hrv": "below", "hr": "above"}  # VFC under its normal, FC de nuit over it: the side that matters
MEANING = {"hrv": "Ça arrive avec la fatigue, le stress, l'alcool ou un début de maladie.",
           "hr": "Ça arrive avec la fatigue, la chaleur, l'alcool ou un début de maladie."}
NO_MEAN = "Trop peu de nuits mesurées ces 7 derniers jours pour comparer."
FEW = "trop peu de nuits pour comparer"  # its row's word
BUILDING = "en construction"  # a signal without usual values yet: its row's word, its card's first words
# while they build, the chart card says it in words (the row prints when they will be ready: printed once; owner,
# 2026-10-09, « Tu ne mets pas les fourchettes pour VFC et FC repos dans les graphiques ? »)
BUILDS = "Tes valeurs habituelles s'afficheront ici dès 7 nuits mesurées."
# the cards' legend (v4.4, owner: « comment matérialiser que c'est en cours de construction dans le graphique ? »):
# every measured night is a filled dot (each one counts: owner, 2026-10-08); the band solid, or dashed while
# provisional
LEGEND_DOT = ("is-dot", "nuit")
LEGEND_MEAN = ("is-mean", "moyenne sur 7 nuits")
LEGEND_BAND = ("is-band", "tes valeurs habituelles")
LEGEND_PROV = ("is-band-prov", "valeurs habituelles (provisoires)")


def nights_to_normal(nights, metric: str, d: date) -> int:
    """How many nights until the usual values the score reads on `metric` exist, every coming night being
    measured: the first j ≥ 1 for which the band of day d + j (nights.normal: every measured night of the 60 days
    up to and including d + j, on the latest watch: 7 at least, H) holds 7 nights — those already slept and the
    j coming ones."""
    src = nt.mean_source(nights, metric, d)
    known = [x for x, n in nights.items() if x <= d and n.value(metric) is not None and n.source_of(metric) == src]
    for j in range(1, nt.MIN_PROVISIONAL_NIGHTS):
        lo = d + timedelta(days=j - nt.BAND_DAYS + 1)  # the first day of the band of day d + j
        if sum(1 for x in known if x >= lo) + j >= nt.MIN_PROVISIONAL_NIGHTS:
            return j
    return nt.MIN_PROVISIONAL_NIGHTS


def signed_pct(value: float, centre: float) -> int:
    """How far a 7-night mean is from the usual value (the band's centre: the median, the geometric mean for
    VFC), in whole percent (half up)."""
    return sc.rounded(100 * (value - centre) / centre)


def card_status(nights, metric: str, day: dict) -> dict:
    """{key, value, word, detail, text, tone, meaning}, the card's status line and its row in the Récupération card
    (one source of truth):
    the 7-night mean as a signed percentage against the usual value (v4.4, owner: « Mets des pourcentages plutôt
    que des valeurs (comme WHOOP / Oura) »: « −8 % », « +3 % », « comme d'habitude » at 0 %) and its place against
    the usual values, « dans tes valeurs habituelles » / « plus basse que d'habitude » / « plus haute que
    d'habitude » (key: in, below, above) with a dot in its colour (sante_score.row_tone: green in them, orange or
    red out of them on the side that matters, neutral on the other side, never praised), and « Ça arrive avec … »
    only out of them on the side that matters. Under the illness alert the FC de nuit reads the alert's 2 nights,
    as the score does: their percentage, « nettement plus haute depuis 2 nuits ». Without a band (none): « en
    construction » and, on the row (`detail`), how many nights until there is one (nights_to_normal: « prête dans
    2 nuits », « prête après ta prochaine nuit »); a plain line when the week holds under 3 measured nights
    (few). `text`: the chart card's line, in words (the row prints the numbers: each once)."""
    s = day["stats"][metric]
    if not s["normal"]:
        n = nights_to_normal(nights, metric, day["day"])
        when = f"dans {n}{viz.NBSP}nuits" if n > 1 else "après ta prochaine nuit"
        return {"key": "none", "value": None, "word": BUILDING, "detail": f"prête {when}", "tone": None,
                "meaning": None, "text": BUILDS}
    part = next((p for p in day["score"]["parts"] if p["key"] == metric), None)
    if s["value"] is None or part is None:
        return {"key": "few", "value": None, "word": FEW, "text": NO_MEAN, "tone": None, "meaning": None}
    tone = sc.row_tone(part)
    status = BAD[metric] if tone in ("warn", "danger") else s["status"]
    centre = s["normal"]["center"]
    if metric == "hr" and day["alert"]:  # the alert's own 2 nights, as the score reads them
        p, word = signed_pct(statistics.fmean(day["alert"]["values"]), centre), ALERT_WORD
    else:
        p = signed_pct(s["value"], centre)
        word = SAME if p == 0 and status == "in" else STATUS[status]
    value = None if p == 0 else f"{viz.signed(p)}{viz.NBSP}%"
    return {"key": status, "value": value, "word": word, "text": f"{value} {word}" if value else word, "tone": tone,
            "meaning": MEANING[metric] if status == BAD[metric] else None}


def _night_card(nights, metric: str, today: date, day: dict) -> dict | None:
    """« VFC · 30 nuits » / « FC de nuit · 30 nuits » (viz.night_card): None
    without a night measured in the 30 days. With fewer than 10 measured
    nights the axis spans them (from the first, 14 days at least), not 30
    nights with a few dots against the right edge. Each day's band is the
    usual values its 7-night mean is read against (nights.normal: every
    measured night of the 60 days up to and including that night, on the
    watch the mean reads: v4.3, one normal for the card, its status line and
    the score), its status line opens the card (card_status). Every measured
    night is a filled dot (each one counts: owner, 2026-10-08), a night
    without a measure draws nothing; the provisional band is dashed; the
    legend names only what is drawn."""
    days = [today - timedelta(days=CARD_NIGHTS - 1 - i) for i in range(CARD_NIGHTS)]
    values = [nights[d].value(metric) if d in nights else None for d in days]
    seen = [i for i, v in enumerate(values) if v is not None]
    if not seen:
        return None
    if len(seen) < SPARSE_NIGHTS:
        keep = max(SPARSE_DAYS, CARD_NIGHTS - seen[0])
        days, values = days[-keep:], values[-keep:]
    bands, prov = [], []
    for d in days:
        b = nt.normal(nights, metric, d)
        bands.append((b["lo"], b["hi"]) if b else None)
        prov.append(bool(b and b["provisional"]))
    means = [(nt.mean7(nights, metric, d) or {}).get("value") for d in days]
    key, name, unit, unit_long = CARDS[metric]
    c = viz.night_card(key, days, values, band=bands, prov=prov, mean=means, unit=unit, unit_long=unit_long,
                       name=name, min_span=_span(metric, values, bands))
    c["status"] = card_status(nights, metric, day)
    c["legend"] = ([LEGEND_DOT] + ([LEGEND_MEAN] if c["mean"] else []) + ([LEGEND_BAND] if c["band"] else [])
                   + ([LEGEND_PROV] if c["band_prov"] else []))
    return c


# ── reading ─────────────────────────────────────────────────────────────────

async def _timeline(db: AsyncSession, user_id: int, nights, d: date) -> dict:
    """{d: [(stage, start, end)]}: the real stage intervals (Garmin
    sleepLevels) of the night `d` when it has them, cut to its main window."""
    from app.models.health import HealthSample

    n = nights.get(d)
    if not n or not n.timeline or not n.start:
        return {}
    rows = (await db.execute(select(HealthSample.kind, HealthSample.start_at, HealthSample.end_at).where(
        HealthSample.user_id == user_id, HealthSample.metric == "sleep", HealthSample.source == "Garmin",
        HealthSample.start_at >= n.start - timedelta(hours=1), HealthSample.start_at < n.end)
        .order_by(HealthSample.start_at))).all()
    segs = [(k, a, b) for k, a, b in rows if b > n.start and a < n.end]
    return {d: segs} if segs else {}


async def athlete_today(db: AsyncSession, user_id: int, now: datetime | None = None,
                        latest: date | None = None) -> date:
    """sante_training.athlete_today (a seam the tests move to a fixed day)."""
    return await st.athlete_today(db, user_id, now, latest)


async def _night_state(db: AsyncSession, user_id: int, today: date, seen: set[str]) -> dict[str, dict]:
    """Per watch that ever sent something: last night received, not yet, or
    not worn (today's daytime data came but no night). Drives how soon the
    page syncs again."""
    rows = (await db.execute(select(HealthMetric.metric, HealthMetric.source, HealthMetric.value).where(
        HealthMetric.user_id == user_id, HealthMetric.date == today,
        HealthMetric.metric.in_(("sleep", "steps", "hr_day"))))).all()
    out = {}
    for src in ("COROS", "Garmin"):
        if src not in seen:
            continue
        sleep = next((v for m, s, v in rows if m == "sleep" and s == src and v), None)
        if sleep:
            out[src] = {"state": "received", "txt": fmt_minutes(sleep)}
        elif any(s == src and m in ("steps", "hr_day") for m, s, _ in rows):
            out[src] = {"state": "none"}
        else:
            out[src] = {"state": "pending"}
    return out
