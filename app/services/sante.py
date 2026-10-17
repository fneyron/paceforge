"""Santé: three visual rings, with explanations available on demand.

Sommeil compares the measured 24 h (naps included) to an 8-h reference, not a personal physiological need.
The estimated seven-night gap is shown separately (sante_sleep.deficit). Signaux nocturnes combines the usable
VFC and night-HR trends with the existing acute-alert, breathing and short-sleep rules (sante_score.score_of).
It is a descriptive /100 index, never a percentage of recovery or a training clearance. Recorded race/ultra
context is dated separately, without score caps or a physiological recovery deadline. Before noon a pending
night may retain yesterday's explicitly dated cycle.
Entraînement compares the last seven days' load to the usual week: 100 % is a reference, not a target, and the
full arc is 200 %; the same load (sante_load, one pipeline) draws the Charge accumulée and Activités' 7 / 28 days.

The rings open native disclosures with sleep details, nightly signals and history, or training context. The
nightly VFC/FC charts remain visible: rolling trends, measured points and provisional references are labelled
separately. Extra daytime measurements stay visible with recent median references. Measurement dates and
successful syncs are distinct in the source details.

No check-in, planned race or brand recovery score enters the calculation. Each source is preserved separately;
nights.read_rows applies the user's watch preference and baselines remain specific to a series (source and method).
Missing measurements remain gaps, never zeroes. Heuristic choices are marked (H); these readings are not
validated clinical measures; the dated decisions behind the rules are in docs/decisions.md. The page's reads come
first; its pure computation runs in a worker thread.
"""
import logging
import math
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from functools import partial
from typing import Any

import anyio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.services import nights as nt
from app.services import sante_load
from app.services import sante_explain
from app.services import sante_score as sc
from app.services import sante_sleep as sl
from app.services import sante_today as td
from app.services import sante_training as st
from app.services import sante_trends
from app.services import viz
from app.services.health import HRV_METHOD, WATCH_SOURCES, fmt_minutes
from app.services.sante_daily import daily_and_trends

logger = logging.getLogger(__name__)

HISTORY_DAYS = 400
CARD_NIGHTS = 30  # the VFC and FC de nuit cards
SPARSE_NIGHTS, SPARSE_DAYS = 10, 14  # fewer measured nights in the 30: the axis spans them (14 days at least)


async def health_page(db: AsyncSession, user_id: int, today: date | None = None, now: datetime | None = None,
                      r: str | None = None) -> dict:
    """Everything /sante draws. `has_data` is False when neither a watch nor an
    activity ever arrived. `r`: the sleep bars' range (14 or 90; else the
    first holding a night). Every read first, then the pure computation in a worker thread (it took 1.5–2.8 s of
    CPU on the event loop)."""
    from app.models.user import User

    now = now or datetime.now(timezone.utc)
    latest = (await db.execute(select(func.max(HealthMetric.date)).where(HealthMetric.user_id == user_id))).scalar()
    user = await db.get(User, user_id)  # read once, held for the page (every later read of it is this one)
    zone = await st.athlete_clock(db, user_id, user)
    clock = None  # the athlete's wall clock, only for the day the page finds itself (a given day stays as given)
    if today is None:
        today = await athlete_today(db, user_id, now, latest, zone=zone)
        clock = await local_clock(db, user_id, now, zone=zone)
    sex = getattr(user, "sex", None)  # no such field yet: Banister's men's constants (sante_training.LOAD_WOMEN)
    sessions = await st.load_sessions(db, user_id, today, zone=zone)
    peak = st.hr_max(sessions, today)
    # the laps (else km splits) of the sessions with HR: their loads and their steady splits (Forme)
    await st.load_hr_segments(db, user_id, sessions, today - timedelta(days=st.RATE_DAYS))
    # Garmin's day altitudes: where the athlete slept, first (R2)
    day_alt = await nt.day_altitudes(db, user_id, today - timedelta(days=HISTORY_DAYS), today)
    # the nights' tags read the efforts' classes only
    nights = await nt.load_nights(db, user_id, today, days=HISTORY_DAYS, sessions=sessions,
                                  efforts=st.efforts(sessions), peak=peak, day_alt=day_alt)
    sources = set((await db.execute(select(HealthMetric.source).distinct().where(
        HealthMetric.user_id == user_id, HealthMetric.metric != "feel"))).scalars().all())
    # the night awaited is the calendar day's (how soon the page syncs again); the page reads the cycle's day
    night_state = await _night_state(db, user_id, today, sources & set(WATCH_SOURCES))
    until, today = today, cycle_day(nights, today, clock)
    daily, trends = await daily_and_trends(db, user_id, until, now=now, zone=zone)
    out: dict[str, Any] = {
        "has_data": bool(sources) or bool(sessions) or bool(daily),
        "has_watch_data": bool(sources),
        "today": today,
        "calendar_day": until,
        "cycle_pending": today != until,
        "night_state": night_state,
        "day_label": viz.d_short(today),
    }
    if not out["has_data"]:
        return out
    hero_day = sl.hero_day(nights, today)
    samples = await _timeline(db, user_id, nights, hero_day) if hero_day else {}
    out.update(await anyio.to_thread.run_sync(partial(
        _compute, nights, sessions, today, until, r, samples, bool(sources), peak, day_alt, sex)))
    # The same real nightly respiratory card lives with Recovery; a phone's resp_day aggregate is never promoted to
    # a night. The other daily cards (FC sur 24 h, stress, respiration sur 24 h) left the page (owner, 2026-10-16:
    # « les tendances sur poids fc pas »): Tendances, 90 days of the weight, the body fat, the night HR, the steps.
    # The one-tap feel's dots, under the stacked curves (owner, 2026-10-17), on the days it is asked (the calendar's)
    from app.services import sante_checkin
    feel_days = [until - timedelta(days=CARD_NIGHTS - 1 - i) for i in range(CARD_NIGHTS)]
    feels = await sante_checkin.feels(db, user_id, feel_days[0], until)
    out["feel_track"] = viz.feel_track("ressenti-30", feel_days, feels) if feels else None
    out["respiration"] = next((c for c in daily if c["key"] == "resp_night"), None)
    marks = out.pop("race_marks", [])
    for c in (out.get("vfc"), out.get("fc"), (out["respiration"] or {}).get("chart"), out["feel_track"],
              *((out.get("sleep") or {}).get("bars") or {}).values()):
        viz.flag_days(c, marks)
    out["trends"] = [t for t in (trends.get("weight"), trends.get("body_fat"), out.pop("hr_trend", None),
                                 trends.get("steps")) if t]
    out["explanations"] = sante_explain.build(out)
    return out


def _compute(nights, sessions, today: date, until: date, r, samples, has_watch: bool, peak: float, day_alt,
             sex=None) -> dict:
    """The page's pure computation (no database): the day's state, its 14 days, the cards, the dials, the load."""
    from app.services import training_view
    from app.services import sante_context

    # a Longue run like a race (R3): its HR against the athlete's bounds as of its day (the nights' resting HR)
    rest_of = partial(nt.rest_hr, nights)
    # a dawn finish: D on the day before its first morning
    efforts = nt.anchor_efforts(nights, st.efforts(sessions, rest_of))
    raced = st.raced_nights(efforts)
    out: dict[str, Any] = {}
    with nt.memo():  # the nights no longer change: their baselines, trends and alerts are computed once
        nt.freeze(nights)
        day = _assess(nights, sessions, efforts, today)
        history = (_history(nights, sessions, efforts, today, rest_of, day_alt)
                   + [(today, day["state"], day["score"])])
        out["recup"] = sc.history_card(history, today)
        out["sleep"] = sl.sleep_section(nights, today, r, samples, raced)
        if out["sleep"].get("hero") and today != until:
            out["sleep"]["hero"]["label"] = viz.night_label(out["sleep"]["hero"]["day"]).capitalize()
        out["vfc"] = _night_card(nights, "hrv", today, day)
        out["fc"] = _night_card(nights, "hr", today, day)
        out["hr_trend"] = sante_trends.night_trend(nights, today)
        # one load for every view (sante_load): today's bounds, the nights' resting HR and the sessions' peak
        rest = nt.rest_hr(nights, until)
        load = st.loads(sessions, until, rest, peak, sex)
        out.update(_top(day, sessions, has_watch, out, until, load))
        out["synthesis"] = sante_context.synthesis(day, nights, until, efforts, sessions,
                                                   sl.deficit(nights, today, raced))
        # the race on the curves (owner, 2026-10-17: the card of signals gone): a dashed line on its days over 3
        # months, its name once over them
        context = out["synthesis"]["context"]
        out["race_marks"] = sante_context.race_marks(efforts, sessions, until - timedelta(days=89), until)
        out["race_note"] = context[0]["label"] if context else None
    done = [s for s in sessions if s.end_day <= until]
    out["load"] = sante_load.card(sessions, until, load)
    out["fitness"] = training_view.footing(done, until, peak)
    out["fitness_progress"] = st.fitness_progress(done, until, peak)
    if out["fitness"]:
        import json
        c = out["fitness"]
        data = json.loads(c["data"])
        data["r"] = [[r[1], "FC ajustée", r[0] + (" · " + r[2] if r[2] else "")] for r in data["r"]]
        c["data"] = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
        # The measured values are available on touch, without repeating Activités' latest readout.
        viz.rest(c, ["", "Tes sorties comparables", ""], c["summary"])
    out["explanations"] = sante_explain.build(out)
    return out


# ── the page's day: a cycle, like WHOOP's (from one sleep to the next), never midnight ─────────────────────

CYCLE_NOON = 12  # (H) from noon, a day whose night never came is that day anyway (the watch was not worn)


async def local_clock(db: AsyncSession, user_id: int, now: datetime, zone=None) -> datetime | None:
    """The athlete's wall-clock time (naive) on sante_training.athlete_clock; a seam the tests move (None: no
    clock, a day as the calendar's). `zone`: athlete_clock's, when the caller read it."""
    if zone is None:
        zone = await st.athlete_clock(db, user_id)
    moment = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone).replace(tzinfo=None)


def _slept(nights, d: date) -> bool:
    """The night that ended on the morning of `d` reached the page (its sleep or its heart values)."""
    n = nights.get(d)
    return bool(n and (n.asleep is not None or n.hr is not None or n.hrv is not None))


def cycle_day(nights, today: date, clock: datetime | None) -> date:
    """The day Santé reads, like WHOOP's cycle, « from sleep onset to sleep onset » rather than midnight to
    midnight (WHOOP's engineering blog): before noon (H), while this morning's night has not reached the page
    and last night's has, it is still
    yesterday — its night, its state, its 7 days (and the activities since midnight); from noon, or as soon as the
    night is in, today (a night not received then reads « pas de données »). `clock` None (a day given):
    `today`."""
    if clock is None or clock.date() != today or clock.hour >= CYCLE_NOON:
        return today
    yesterday = today - timedelta(days=1)
    return yesterday if not _slept(nights, today) and _slept(nights, yesterday) else today


# ── one day: its state ──────────────────────────────────────────────────────

def _night_stat(nights, metric: str, d: date, trend: dict | None) -> dict:
    """The 7-night mean of every measured night (3 at least, Plews 2014; Lau 2022) and the usual values it is read
    against (nights.normal: the nights from d − 67 to d − 8 on its series, one baseline per series: Dial 2025;
    « provisoire » from 7 to 13 nights, H, read and said so); the status from its trend (nights.trend: the
    band widened to 1.28 standard errors of a 7-night mean, nights.threshold). `seen`: the metric measured in
    those 60 days."""
    t = trend or {}
    b = t.get("baseline")
    lo = d - timedelta(days=nt.BAND_DAYS - 1)
    src = t.get("source")
    seen = src is not None and any(n.value(metric) is not None for x, n in nights.items() if lo <= x <= d)
    return {"value": t.get("value"), "normal": b, "status": t.get("status") if b else None, "seen": seen,
            "score_eligible": not (metric == "hrv" and sc.is_sdnn(trend))}


def _resp_ref(nights, d: date) -> dict | None:
    """The breathing rate's place in the day's state (sante_score.quality): None when the watch never measured it
    in the 60 days; else {last (measured last night), ready (a full baseline, the flag readable), n}."""
    lo = d - timedelta(days=nt.BAND_DAYS - 1)
    if not any(n.resp is not None for x, n in nights.items() if lo <= x <= d):
        return None
    n = nights.get(d)
    last = bool(n and n.resp is not None)
    source = n.resp_source if last else None
    b = nt.band(nights, "resp", nt.baseline_until(d), source=source, full=True) if last else None
    return {"last": last, "ready": bool(b), "n": nt.baseline_count(nights, "resp", d, source) if last else 0}


def _assess(nights, sessions, efforts, d: date) -> dict:
    """What day `d` reads — today, or a past day of the 14-day card (_history: the rows and activities known that
    day) — and its state (sante_score.score_of). Every measured night counts in the trends (an alert episode's own
    nights too: never « dans tes valeurs habituelles » the morning the alert stops); `resp`: the breathing flag
    (nights.resp_up_nights); `window`: the effort window (sante_training.effort_window)."""
    trends = {k: nt.trend(nights, k, d) for k in ("hrv", "hr")}
    # the 24 h before this morning's wake (the Sommeil dial's figure) on the nights known that day only: a nap
    # after the wake moves to the next morning once that morning exists (a past day of the 14-day card must not
    # read it from the later nights)
    tst = nt.day_tst24({x: n for x, n in nights.items() if x <= d}, d)
    night = nights.get(d)
    fresh = [k for k in ("hr", "hrv") if night and night.value(k) is not None]
    if tst is not None:
        fresh.append("sleep")
    # a heart value of last night on another series than the one its trend reads (another watch, another
    # calculation): no state from it, and the line says so rather than « il manque des données de nuit »
    elsewhere = [k for k in ("hrv", "hr") if night and night.value(k) is not None and trends[k]
                 and night.source_of(k) != trends[k]["source"] and not sc.is_sdnn({"source": night.source_of(k)})]
    day = {"day": d, "night_measured": _slept(nights, d), "trends": trends,
           "stats": {k: _night_stat(nights, k, d, trends[k]) for k in ("hr", "hrv")}, "tst24": tst,
           "measured_components": fresh, "window": st.effort_window(efforts, d),
           "alert": nt.illness_alert(nights, d), "resp": nt.resp_up_nights(nights, d),
           "resp_ref": _resp_ref(nights, d),
           "elsewhere": {k: ("device" if nt.device(night.source_of(k)) != nt.device(trends[k]["source"])
                             else "method") for k in elsewhere}}
    score = sc.score_of(day)
    day.update(score=score, state=td.state(score, day))
    return day


def _history(nights, sessions, efforts, today: date, rest_of=None, day_alt=None) -> list:
    """[(day, state, score)] of the 13 days before today, each as the page
    computed it on that day: from the nights up to that day (the baselines, the
    trends, the alert and the 24 h read nothing dated after it) and the
    activities known then only — an activity is known once it ended (it is
    uploaded then), so one still running at midnight belongs to the next day
    — and the efforts as that day saw them (st.efforts on those activities: a
    chain across midnight joins a later day, so a past day may have seen a
    smaller effort). The nights' tags only keep a night from firing the
    illness alert (every measured night counts in the baselines and the trends):
    as of that day they are today's (an effort tags the nights after it, a
    session the night after it: both known by then) but for « sortie intense
    le soir » (it reads that day's HR bounds). The nights are tagged once, as
    of today; a day whose « late » tags and efforts are the same, with no
    activity still running at its midnight, reads them and shares their baselines
    (nights.memo); any other day is tagged anew from what it knew. `rest_of`:
    what the efforts were computed with (st.efforts): a Longue's heart-rate
    test reads the bounds as of its own day, the same whichever later day
    reads it; `day_alt`: Garmin's day altitudes the nights were tagged with (a
    night reads the day before)."""
    recent = {x: n for x, n in nights.items() if x > today - timedelta(days=sc.HISTORY_NIGHTS)}
    base = {x: nt.retagged(n) for x, n in recent.items()}
    nt.tag_activities(base, sessions, efforts, nt.rest_hr(nights, today), st.hr_max(sessions, today), day_alt)
    nt.freeze(base)
    # the series each day read, decided as far back as the live page's (nights: the full nights, its SERIES_LOOKBACK)
    nt.share_series(base, nights, today, sc.HISTORY_NIGHTS)
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
            nt.share_series(nd, nights, d, sc.HISTORY_NIGHTS)
        past = _assess(nd, ss, efs, d)
        out.append((d, past["state"], past["score"]))
    return out


# ── the top: three dials, like WHOOP's ──────────────────────────────────────────────────────────────────────

# the Sommeil dial's word from this morning's 24 h against the 8-h need: « suffisant » from 7 h (Watson 2015a;
# Hirshkowitz 2015), « court » under 6 h (Craven 2022), « un peu court » between
NO_NIGHT_WORD = "pas de données"
TRAIN_TURN = 2  # the Entraînement dial's full turn: twice the usual week
NO_HABIT = "pas encore d'habitude"  # the Entraînement dial without a usual week
NO_USUAL = "pas encore de semaine habituelle"  # its card's row then
# its card's « Intensité »: the minutes' weight against the usual week's
INTENSITY_SPREAD = 0.1  # (H) within ± 10 %: « comme d'habitude »
INTENSITY_READ = 0.5  # (H) half of the minutes read from their HR, the 7 days' and the usual weeks', or no row
INTENSITY_WORDS = ("plus élevée que d'habitude", "comme d'habitude", "plus basse que d'habitude")
TRAIN_WORDS = {"more": "plus que d'habitude", "same": "comme d'habitude", "less": "moins que d'habitude"}
ALERT_WORD = "nettement plus haute depuis 2 nuits"  # the FC de nuit card under the illness alert
SAME = "comme d'habitude"  # a 7-night mean at its usual value, to the percent: « 0 % » said in words


def sleep_word(tst24: int, need: int = sl.NEED_DEFAULT) -> str:
    """The Sommeil dial's word: « suffisant » from 7 h (420 min, 7/8 of the 8-h need), « court » under 6 h, « un peu
    court » between."""
    if tst24 >= sl.REF_MIN:
        return "suffisant"
    return "court" if tst24 < nt.SHORT_DAY_MIN else "un peu court"


def sleep_dial(tst24: int | None, need: int, href: str | None) -> dict:
    """« Sommeil »: the 24 h before this morning's wake, naps in (nights.day_tst24), as a percentage of the 8-h need
    (sante_sleep.NEED_DEFAULT), at most 100 % (the hours are the Sommeil card's, printed once), its arc in the
    sleep colour (one stable hue), in the warning colour under 6 h; its word (sleep_word); « — » and « pas de
    données » without a night this morning. `href`: the Sommeil card, None without one (no night ever: a plain
    dial)."""
    if tst24 is None:
        return viz.ring("sommeil", None, "—", "Sommeil", NO_NIGHT_WORD, tone="none", href=href,
                        aria="Sommeil : aucune donnée de nuit reçue pour ce matin.")
    word = sleep_word(tst24, need)
    p = min(100, sc.rounded(100 * tst24 / need))
    return viz.ring("sommeil", tst24 / need, str(p), "Sommeil", word, unit="%", href=href,
                    tone="warn" if tst24 < nt.SHORT_DAY_MIN else "sleep",
                    aria=f"Sommeil {sc.pct(p)} du repère de {viz.hm_long(need)}, {word}.")


def usual_week(sessions, since: date, value=None) -> float | None:
    """The usual week (H): the mean of the activities' moving minutes (or of `value(s)`: their load) over the 11
    weeks just before the 7 days (sante_load.usual_weeks: 4 of them holding an activity at least); None without
    one, or one of nothing."""
    weeks = sante_load.usual_weeks(sessions, since, value)
    return (statistics.fmean(weeks) or None) if weeks is not None else None


def training(sessions, today: date, until: date | None = None, rest: float = 50.0, peak: float = 190.0,
             load: dict | None = None, sex: str | None = None) -> dict:
    """« Entraînement », the third dial and its card (research_ind_train.md): the
    heart-rate load of the last 7 days (sante_load: one pipeline, `load` the day's sante_training.loads, else
    computed with `rest` and `peak`, today's bounds for every week, H) against the usual week's (the mean of the
    11 weeks before the 7 days), as a percentage, 100 % as usual, the arc full at twice it, in the accent colour;
    « comme d'habitude » while the 7 days' load lies between the lightest and the heaviest of those 11 weeks that
    hold an activity, else « plus que d'habitude » or
    « moins que d'habitude »; without a usual week (4 active weeks), the measured duration and « 7 derniers jours ».
    The card prints the 7 days' moving time and the usual week's (to 5 min: a typical value; Activités'
    hours), each once, and « Intensité »: the minutes' weight against the usual week's (the load's ratio over the
    hours', so the dial is the hours' ratio times it: no number contradicts another), « plus élevée que
    d'habitude » / « comme d'habitude » / « plus basse que d'habitude » within ± 10 % (H), a word, never a number;
    left out when under half of the minutes, of the 7 days or of the usual weeks, were read from their HR (H): the
    dial is then nearly the hours' ratio. `until`: the calendar day when the page still reads yesterday's cycle
    (cycle_day): the window is always the seven calendar days ending at until. Each activity counts on the day it
    ended (Session.end_day). `since`: the 7 days' first day, the card's link to them in Activités (filtered on
    those days, not on the week). {dial, week, usual, word, intensity, since, ratio}."""
    until = until or today
    since = until - timedelta(days=6)
    sessions = sante_load.valid(sessions)
    days7 = [s for s in sessions if since <= s.end_day <= until]
    week = sum(s.minutes for s in days7)  # moving time, as Activités
    usual = usual_week(sessions, since)
    href = "#entrainement"
    load = load if load is not None else st.loads(sessions, until, rest, peak, sex)
    ratio = sante_load.week_ratio(sessions, load, until) if usual is not None else None
    if ratio is None:
        return {"dial": viz.ring("entrainement", None, viz.hm(week), "Entraînement", "7 derniers jours", tone="accent",
                                 href=href, aria=f"Entraînement : {viz.hm_long(week)} d'activité ces 7 derniers "
                                                 f"jours, {NO_HABIT}."),
                "week": viz.hm(week), "usual": None, "word": NO_USUAL, "intensity": None, "since": since,
                "ratio": None}
    usual_load = ratio["usual"]
    # snapped to 1e-9 (as sante_score.rounded): minutes that all weigh the same (no HR at all) give exactly the
    # hours' ratio; a usual week that weighs nothing reads its hours (week_ratio's `by`)
    intensity = round(ratio["load7"] / week / (usual_load / usual), 9) if week and ratio["by"] == "load" else 1.0
    word = TRAIN_WORDS[ratio["side"]]
    p = sc.rounded(100 * ratio["ratio"])
    read7 = sum(s.minutes for s in days7 if load[s.id][1])
    read_usual = usual_week(sessions, since, lambda s: s.minutes if load[s.id][1] else 0.0) or 0.0
    pace = None
    if week and read7 >= INTENSITY_READ * week and read_usual >= INTENSITY_READ * usual:
        pace = (INTENSITY_WORDS[0] if intensity > 1 + INTENSITY_SPREAD else
                INTENSITY_WORDS[2] if intensity < 1 - INTENSITY_SPREAD else INTENSITY_WORDS[1])
    return {"dial": viz.ring("entrainement", ratio["ratio"] / TRAIN_TURN, str(p), "Entraînement", word,
                             tone="accent", unit="%", href=href,
                             aria=f"Entraînement {sc.pct(p)} de ta semaine habituelle, {word}."),
            "week": viz.hm(week), "usual": f"ta semaine habituelle{viz.NBSP}: {viz.hm(round(usual / 5) * 5)}",
            "word": None, "intensity": pace, "since": since, "ratio": p}


def training_context(train: dict) -> dict:
    """The recovery card's context line (the load never caps the state: the HRV already reads what it does to the
    night): « Charge 7 j : x % de l'habitude », the Entraînement dial's
    number; the 7 days' time without a usual week. {recorded (sessions in the 7 days), word}."""
    count = train["chart"]["count"] if train.get("chart") else 0
    if train["ratio"] is None:
        return {"recorded": count, "word": f"{train['week']} sur 7 jours", "ratio": None}
    return {"recorded": count, "word": f"{train['ratio']}{viz.NBSP}% de ta semaine habituelle",
            "ratio": train["ratio"]}


def _top(day: dict, sessions, has_watch: bool, page: dict, until: date, load: dict) -> dict:
    """The three dials, each a link to its card right under the dials, else to its details further down (a plain
    dial when the page has neither): Sommeil, Récupération, Entraînement (WHOOP's order); the illness alert's
    sentence under them (sante_today), or the line that says why there is no state; the Entraînement card (`load`:
    the day's sante_training.loads) and its number as the recovery card's context."""
    state, score = day["state"], day["score"]
    sleep = page.get("sleep") or {}
    sleep_href = ("#sommeil" if sleep.get("state") == "old" or sleep.get("hero") else
                  "#sommeil-detail" if sleep.get("state") == "ok" else None)
    # the Récupération card holds the index's days; without them, the stacked VFC and FC de nuit curves
    recup_href = ("#recuperation" if page.get("recup") or score["value"] is not None else
                  "#vfc" if page.get("vfc") else "#fc" if page.get("fc") else None)
    train = training(sessions, day["day"], until, load=load)
    train["chart"] = st.training_days(sessions, day["day"], until)
    train["dial"]["note"] = "7 jours" if train["usual"] is not None else None
    train["dial"]["usual_marker"] = train["usual"] is not None
    if train["usual"] is not None:
        train["dial"]["aria"] += " 100 % représente ta semaine habituelle ; un tour complet représente 200 %."
    recup = sc.dial(score, state, recup_href)
    if state and state["tone"] != "ok" and state["reasons"]:  # the card of signals' reason, under its ring
        recup.update(note=state["reasons"][0]["label"], reason=True)
        recup["aria"] += f" {recup['note']}."
    return {"dials": [sleep_dial(day["tst24"], sl.NEED_DEFAULT, sleep_href), recup, train["dial"]],
            "state": state, "line": None if state else td.no_state_line(has_watch, score["missing"]),
            "connect": not has_watch,  # no watch: how to add the nights, under the line
            "training": train, "score": score, "training_effect": training_context(train),
            "quality": sc.quality(day)}


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


# each night card's status line: its last 7 nights against the usual values the state reads, in words and its
# colour (sante_score.row_tone); what it often means, only out of them on the side that matters; without usual
# values yet, how many nights until they exist (plain words: « tes valeurs habituelles », « d'habitude », never
# « ta normale »)
STATUS = {"in": "dans tes valeurs habituelles", "below": "plus basse que d'habitude",
          "above": "plus haute que d'habitude"}
BAD = sc.BAD  # VFC under its normal, FC de nuit over it: the side that matters
MEANING = {"hrv": sc.TEXTS["hrv"], "hr": sc.TEXTS["hr"]}
NO_MEAN = "Trop peu de nuits mesurées ces 7 derniers jours pour comparer."
FEW = "trop peu de nuits pour comparer"
BUILDING = "en construction"  # a signal without usual values yet: its card's first words
# while they build, the chart card says it in words (the reference row says when they will be ready: printed once):
# the nights still needed for the lagged reference (nights.BASELINE_LAG) or its provisional fallback (nights_to_normal)
BUILDS = "Une référence apparaît dès {n} nuits mesurées avec la même source et le même calcul."
# the cards' legend: every measured night is a filled dot (each one counts); the band solid, or dashed while
# provisional
LEGEND_DOT = ("is-dot", "nuit")
LEGEND_MEAN = ("is-mean", "moyenne 7 j")
LEGEND_BAND = ("is-band", "repère habituel")
LEGEND_PROV = ("is-band-prov", "repère provisoire")


def nights_to_normal(nights, metric: str, d: date) -> int:
    """Nights needed for either the lagged reference or its recent provisional fallback, on this series.
    Assumes each coming night is measured; expired observations never count toward that future window."""
    src = nt.mean_source(nights, metric, d)
    required = nt.reference_min_nights(metric, src)
    known = [x for x, n in nights.items() if x <= d and n.value(metric) is not None and n.source_of(metric) == src]
    lag = nt.BASELINE_LAG + 1
    for j in range(1, required + lag + 1):
        lo, hi = d + timedelta(days=j - nt.BAND_DAYS - nt.BASELINE_LAG), d + timedelta(days=j - lag)
        lagged = sum(1 for x in known if lo <= x <= hi) + max(0, j - lag)
        recent_lo = d + timedelta(days=j - nt.BAND_DAYS + 1)
        recent = sum(1 for x in known if x >= recent_lo) + j
        if max(lagged, recent) >= required:
            return j
    return required + lag


def signed_pct(value: float, centre: float) -> int:
    """How far a 7-night mean is from the usual value (the baseline's centre: the median, the geometric mean for
    VFC), in whole percent (half up)."""
    return sc.rounded(100 * (value - centre) / centre)


def card_status(nights, metric: str, day: dict) -> dict:
    """{key, value, word, detail, text, tone, meaning}, the card's status line:
    the 7-night mean as a signed percentage against the usual value (as WHOOP and Oura print it: « −8 % », « +3 % »,
    « comme d'habitude » at 0 %) and its place against
    the usual values (nights.trend: outside from T, nights.threshold), « dans tes valeurs
    habituelles » / « plus basse que d'habitude » / « plus haute que d'habitude » (key: in, below, above) with a dot
    in its colour (sante_score.row_tone: green in them, orange out of them on the side that matters, neutral on the
    other side, never praised; none for a descriptive SDNN or a series not measured last night), and « Ça arrive
    avec … » only out of them on the side that matters. Under the illness alert the FC de nuit reads the alert's 2
    nights: their percentage, « nettement plus haute depuis 2 nuits », red. Without a baseline (none): « en
    construction » and (`detail`) how many nights until there is one (nights_to_normal: « prête dans 2 nuits »,
    « prête après ta prochaine nuit »); a plain line when the week holds under 3 measured nights (few). `text`: the
    chart card's line, in words."""
    s, t = day["stats"][metric], day["trends"].get(metric)
    if not s["normal"]:
        n = nights_to_normal(nights, metric, day["day"])
        when = f"dans {n}{viz.NBSP}nuits" if n > 1 else "après ta prochaine nuit"
        return {"key": "none", "value": None, "word": BUILDING, "detail": f"prête {when}", "tone": None,
                "meaning": None, "text": BUILDS.format(n=nt.reference_min_nights(
                    metric, nt.mean_source(nights, metric, day["day"])))}
    alert = metric == "hr" and bool(day["alert"])
    # the illness alert reads its own 2 nights: shown even when the week is too thin for a trend (never hidden
    # behind « trop peu de nuits pour comparer »)
    if not alert and (s["value"] is None or s["status"] is None):
        return {"key": "few", "value": None, "word": FEW, "text": NO_MEAN, "tone": None, "meaning": None}
    # A trend (or SDNN) remains readable even when it cannot enter today's state; its colour then stays neutral.
    colour = alert or (t and t["last"] and s["score_eligible"])
    tone = sc.row_tone(metric, t, alert) if colour else None
    status = BAD[metric] if alert else s["status"]
    centre = s["normal"]["center"]
    if alert:  # the alert's own 2 nights
        p, word = signed_pct(statistics.fmean(day["alert"]["values"]), centre), ALERT_WORD
    else:
        p = signed_pct(s["value"], centre)
        word = SAME if p == 0 and status == "in" else STATUS[status]
    value = None if p == 0 else f"{viz.signed(p)}{viz.NBSP}%"
    return {"key": status, "value": value, "word": word, "text": f"{value} {word}" if value else word, "tone": tone,
            "meaning": MEANING[metric] if status == BAD[metric] else None}


def _drawn(b: dict | None, metric: str, t: float | None) -> tuple | None:
    """A day's usual values as drawn: its baseline's centre ± T·σ̂, T the threshold its 7-night mean is read with
    (nights.trend's; 0.5 without one), so a mean drawn inside reads « dans tes valeurs habituelles »."""
    if not b:
        return None
    t = t or nt.SWC
    if metric == "hrv":
        return (b["center"] * math.exp(-t * b["sd"]), b["center"] * math.exp(t * b["sd"]))
    return (b["center"] - t * b["sd"], b["center"] + t * b["sd"])


def _night_card(nights, metric: str, today: date, day: dict) -> dict | None:
    """« VFC · 30 nuits » / « FC de nuit · 30 nuits » (viz.night_card): None
    without a night measured in the 30 days. With fewer than 10 measured
    nights the axis spans them (from the first, 14 days at least), not 30
    nights with a few dots against the right edge. Each day's band is the
    usual values its 7-night mean is read against (nights.normal: the nights
    from that day − 67 to − 8, on the series the mean reads: one baseline for
    the card, its status line and the state, drawn ± T·σ̂: _drawn; a series
    younger than that window draws its provisional band from the 60 nights
    before the day, dashed, flagged provisoire: nights.drawn_band), its status
    line opens the card (card_status). Every measured
    night is a filled dot (each one counts), a night
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
    averages = [nt.mean7(nights, metric, d) or {} for d in days]
    bands, prov = [], []
    for d in days:
        b = nt.drawn_band(nights, metric, d)  # the lagged baseline, else a young series' provisional one
        bands.append(_drawn(b, metric, (nt.trend(nights, metric, d) or {}).get("T")))
        prov.append(bool(b and b["provisional"]))
    means = [m.get("value") for m in averages]
    sources = [nights[d].source_of(metric) if d in nights else None for d in days]
    # The method remains visible when the brand is hidden: RMSSD and SDNN are
    # incompatible measurements. Only label the known import pipelines.
    methods = [
        "SDNN" if src and "(SDNN sommeil)" in src else
        "RMSSD" if src in ("COROS", "Garmin") or (src and src.startswith("Garmin (")) else None
        for src in sources
    ] if metric == "hrv" else None
    notes = [nights[d].measurement_notes.get(metric) if d in nights else None for d in days]
    key, name, unit, unit_long = CARDS[metric]
    c = viz.night_card(key, days, values, band=bands, prov=prov, mean=means, unit=unit, unit_long=unit_long,
                       name=name, min_span=_span(metric, values, bands), sources=sources,
                       mean_sources=[nt.mean_source(nights, metric, d) for d in days],
                       mean_counts=[m.get("n", 0) for m in averages], notes=notes, label_nights=True, compact=True,
                       show_source=False, method_labels=methods)
    c["status"] = card_status(nights, metric, day)
    current = nights.get(today)
    value = current.value(metric) if current else None
    # Keep today's value distinct from the most recent observation. The chart
    # already selects that real dated point and leaves today's missing slot empty.
    c["latest"] = viz.num(value, unit=unit) if value is not None else None
    last = max(i for i, v in enumerate(values) if v is not None)
    c["measurement"] = {"value": viz.num(values[last], unit=unit), "date": days[last].isoformat(),
                        "date_label": viz.d_short(days[last]), "source": viz.source_label(sources[last]),
                        "is_current": days[last] == today,
                        "missing_label": None if days[last] == today else "Pas de mesure cette nuit"}
    alert = day["alert"] if metric == "hr" else None
    c["period"] = "2 nuits" if alert else "7 derniers jours"
    average = {"value": statistics.fmean(alert["values"]), "n": 2} if alert else averages[-1]
    reference = day["stats"][metric]["normal"]
    provisional = bool(reference and reference["provisional"])
    source = nt.mean_source(nights, metric, today)
    required = nt.reference_min_nights(metric, source)
    # the nights the reference holds (a young series' provisional one: the nights up to last night), else those
    # of the lagged window so far
    count = reference["n"] if reference else nt.reference_count(nights, metric, today, source)
    label = f"Référence · {count} nuits" if reference else f"Référence · {count}/{required} nuits"
    reference_detail = f"Référence provisoire : {count} nuits mesurées." if provisional else None
    c["reference"] = {"n": count, "required": required, "provisional": provisional, "ready": bool(reference),
                      "label": label, "detail": reference_detail, "source": viz.source_label(source),
                      "remaining": 0 if reference else nights_to_normal(nights, metric, today)}
    c["trend"] = {"label": "Tendance sur 2 nuits" if alert else "Tendance sur 7 jours",
                  "short_label": "Moy. 2 nuits" if alert else "Moy. 7 j",
                  "value": viz.num(average["value"], unit=unit) if average.get("value") is not None else None,
                  "n": average.get("n", 0), "provisional": provisional,
                  "reference": reference_detail}
    c["status_short"] = ("Élevée sur 2 nuits" if alert else
                         {"in": "Habituelle", "above": "Au-dessus", "below": "En dessous",
                          "none": "Repères en cours", "few": "Peu de données"}[c["status"]["key"]])
    c["legend"] = ([LEGEND_DOT] + ([LEGEND_MEAN] if c["mean"] else []) + ([LEGEND_BAND] if c["band"] else [])
                   + ([LEGEND_PROV] if c["band_prov"] else []))
    c["interpretation"] = sante_explain.nightly(
        metric, c, day["trends"].get(metric), same_series=sources[last] == source,
        latest_below=bool(bands[-1] and value is not None and value < bands[-1][0]))
    return c


# ── reading ─────────────────────────────────────────────────────────────────

async def _timeline(db: AsyncSession, user_id: int, nights, d: date) -> dict:
    """{d: [(stage, start, end)]}: the real stage intervals (Garmin
    sleepLevels) of the night `d` when it has them, cut to its main window."""
    from app.models.health import HealthSample

    n = nights.get(d)
    if not n or not n.timeline or not n.start:
        return {}
    if n.source and n.source.startswith("Apple Santé:"):
        row = await db.scalar(select(HealthMetric.details["segments"]).where(HealthMetric.user_id == user_id,
                              HealthMetric.date == d, HealthMetric.metric == "sleep", HealthMetric.source == n.source))
        segs = [(k, datetime.fromisoformat(a), datetime.fromisoformat(b)) for k, a, b in row or []]
        return {d: segs} if segs else {}
    rows = (await db.execute(select(HealthSample.kind, HealthSample.start_at, HealthSample.end_at).where(
        HealthSample.user_id == user_id, HealthSample.metric == "sleep", HealthSample.source == "Garmin",
        HealthSample.start_at >= n.start - timedelta(hours=1), HealthSample.start_at < n.end)
        .order_by(HealthSample.start_at))).all()
    segs = [(k, a, b) for k, a, b in rows if b > n.start and a < n.end]
    return {d: segs} if segs else {}


async def athlete_today(db: AsyncSession, user_id: int, now: datetime | None = None,
                        latest: date | None = None, zone=None) -> date:
    """sante_training.athlete_today (a seam the tests move to a fixed day)."""
    return await st.athlete_today(db, user_id, now, latest, zone=zone)


async def _night_state(db: AsyncSession, user_id: int, today: date, seen: set[str]) -> dict[str, dict]:
    """Per watch that ever sent something: last night received, not yet, or
    not worn (today's daytime data came but no night). Drives how soon the
    page syncs again. Reads the HRV row's method only (a Garmin day row carries its raw readings)."""
    rows = (await db.execute(select(HealthMetric.metric, HealthMetric.source, HealthMetric.value,
                                    HealthMetric.details["method"].as_string()).where(
        HealthMetric.user_id == user_id, HealthMetric.date == today,
        HealthMetric.metric.in_(("sleep", "steps", "hr_day", "hrv"))))).all()
    out = {}
    for src in ("COROS", "Garmin"):
        if src not in seen:
            continue
        sleep = next((v for m, s, v, _ in rows if m == "sleep" and s == src and v), None)
        if sleep:
            out[src] = {"state": "received", "txt": fmt_minutes(sleep)}
            if src == "COROS":
                out[src]["hrv_pending"] = not any(m == "hrv" and s == src and v is not None and method == HRV_METHOD
                                                  for m, s, v, method in rows)
        elif any(s == src and m in ("steps", "hr_day") for m, s, _, _ in rows):
            out[src] = {"state": "none"}
        else:
            out[src] = {"state": "pending"}
    return out
