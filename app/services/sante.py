"""Santé v4: one page, WHOOP/Oura-like (owner, 2026-10-08: « mets tout dans un
seul », « plus de graphiques »). Top to bottom:
- the three rings: Récupération (the 0–100 score in its state's colour,
  sante_score; under it only its label), Sommeil (this morning's 24-h total,
  the main night and the day's naps, full at 8 h (H): green from 7 h,
  Johnston 2020; orange 6–7 h; red under 6 h, Craven 2022), Charge (the
  activities' hours of the last 7 days, stops included; the ring runs from 0
  to twice the usual week, the median of the last 12 complete weeks (H), with
  a tick at 1×, so a double week looks different from a normal one; a
  neutral colour, it describes, never warns). Each ring links to the section
  it sums up, and is a plain ring when the page has none;
- the recovery state (the band of the score) and at most one sentence
  (sante_today);
- the Contributeurs (sante_score.contributors);
- the cards « Récupération · 14 jours », the Sommeil section (sante_sleep),
  « VFC · 30 nuits », « FC de nuit · 30 nuits », « Charge · 14 jours »;
- the closed folds (how the recovery is computed, how the nights are read,
  the nights' table).
From past activities and the nights only: no planned race, no check-in, no
training prescription. Only PaceForge's own nightly values (nights.py), read
against the athlete's own band; no brand value is read. A night without the
watch is a gap, never a zero. Each number is printed once on the page.
"""
import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

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
CHARGE_DAYS = 14
CHARGE_MIN_TOP = 120  # min: the Charge axis never tops under 2 h (a short day must not look long)
CHARGE_CLIP, CHARGE_TALL = 1.6, 180  # a day ≥ 3 h over 1,6 × the next highest runs past the axis, broken
SLEEP_FULL = 8 * 60  # (H) the Sommeil ring is full at 8 h
USUAL_WEEKS = 12  # (H) the Charge ring's tick (1×): the median of the last 12 complete weeks
CHARGE_TURN = 2  # the Charge ring's full turn: twice the usual week


async def health_page(db: AsyncSession, user_id: int, today: date | None = None, now: datetime | None = None,
                      r: str | None = None) -> dict:
    """Everything /sante draws. `has_data` is False when neither a watch nor an
    activity ever arrived. `r`: the sleep bars' range (14 or 90; else the
    first holding a night)."""
    now = now or datetime.now(timezone.utc)
    latest = (await db.execute(select(func.max(HealthMetric.date)).where(HealthMetric.user_id == user_id))).scalar()
    today = today or await athlete_today(db, user_id, now, latest)
    sessions = await st.load_sessions(db, user_id, today)
    peak = st.hr_max(sessions, today)
    efforts = st.efforts(sessions)
    nights = await nt.load_nights(db, user_id, today, days=HISTORY_DAYS, sessions=sessions, efforts=efforts,
                                  peak=peak)
    sources = set((await db.execute(select(HealthMetric.source).distinct().where(
        HealthMetric.user_id == user_id, HealthMetric.metric != "feel"))).scalars().all())
    out = {
        "has_data": bool(sources) or bool(sessions),  # older rows may come from another source (Apple Health)
        "has_watch_data": bool(sources),
        "today": today,
        "night_state": await _night_state(db, user_id, today, sources & set(WATCH_SOURCES)),
    }
    if not out["has_data"]:
        out["day_label"] = viz.d_short(today)
        return out
    with nt.memo():  # the nights no longer change: their bands and alerts are computed once
        nt.freeze(nights)
        day = _assess(nights, sessions, efforts, today)
        history = _history(nights, sessions, efforts, today) + [(today, day["state"], day["score"])]
        out["recup"] = sc.history_card(history, today)
        hero = sl.hero(nights, today)
        samples = await _timeline(db, user_id, nights, hero["day"]) if hero else {}
        out["sleep"] = sl.sleep_section(nights, today, r, samples)
        out["vfc"] = _night_card(nights, "hrv", today)
        out["fc"] = _night_card(nights, "hr", today)
        out["charge"] = _charge_card(sessions, today)
        out.update(_top(day, sessions, today, bool(sources), out))
    out["day_label"] = viz.d_short(today)
    out["method"], out["refs"] = sc.METHOD, sc.REFS
    return out


# ── one day: its state and its score ────────────────────────────────────────

def _night_stat(nights, metric: str, d: date, ignore=()) -> dict:
    """The 7-night mean of the usable nights (3 at least, Plews 2014; Lau 2022)
    and the band it is read against: the 60 days before its window, on the
    watch that mean reads (one band per watch, Dial 2025; « provisoire » from 7
    to 13 nights, H). The status is judged on the unrounded mean. `ignore`:
    tags kept in the mean (an alert episode's own nights, when the alert is
    the state: the score then reads them, never the band)."""
    m = nt.mean7(nights, metric, d, ignore=ignore)
    b = nt.band(nights, metric, d - timedelta(days=6), source=nt.mean_source(nights, metric, d)) if m else None
    return {"value": m["value"] if m else None, "normal": b, "status": nt.status(m["value"], b) if b else None}


def _assess(nights, sessions, efforts, d: date) -> dict:
    """What day `d` reads — today, or a past day of the 14-day card (_history:
    the rows and activities known that day) — and its score and state (the
    band of the score). During an illness alert the nightly components read
    the alert episode's own nights (the band stays the normal before it): the
    score shows the episode."""
    alert = nt.illness_alert(nights, d)
    stats = {k: _night_stat(nights, k, d, ignore=nt.EPISODE if alert else ()) for k in ("hr", "hrv")}
    n = nights.get(d)
    tst = n.tst24 if n else None  # the main night of `d` and the naps of `d`: the ring's and the bar's number
    m7 = nt.mean7(nights, "tst24", d)
    usual = nt.band(nights, "tst24", d - timedelta(days=6), source=nt.mean_source(nights, "tst24", d)) if m7 else None
    day = {"day": d, "stats": stats, "tst24": tst,
           "sleep": {"mean7": m7["value"] if m7 else None, "usual": usual["center"] if usual else None,
                     "prov": bool(usual and usual["provisional"])},
           "window": st.effort_window(efforts, d), "alert": alert,
           "sessions42": sum(1 for s in sessions if d - timedelta(days=42) < s.day <= d)}
    score = sc.score_of(day)
    state = td.state(score, day)
    if state:
        state["short"] = td.short_text(state, day)
    day.update(score=score, state=state)
    return day


def _history(nights, sessions, efforts, today: date) -> list:
    """[(day, state, score)] of the 13 days before today, each as the page
    computed it on that day: from the nights and activities known then only.
    A night's tags as of that day are today's (an effort tags the nights after
    it, a session the night after it: both known by then) but for « sortie
    intense le soir » (it reads that day's HR bounds) and « FC de nuit haute »
    (an alert episode's first night is tagged only from the next morning).
    The nights are tagged once, as of today, without the alert episodes; a day
    whose « late » tags are the same reads them, with that day's own alert
    episodes on top, and shares their bands (nights.memo); any other day is
    tagged anew."""
    recent = {x: n for x, n in nights.items() if x > today - timedelta(days=sc.HISTORY_NIGHTS)}
    base = {x: nt.retagged(n) for x, n in recent.items()}
    nt.tag_activities(base, sessions, efforts, nt.rest_hr(base, today), st.hr_max(sessions, today))
    nt.freeze(base)
    by_day = defaultdict(list)
    for s in sessions:
        by_day[s.day].append(s)
    late = {x: c for x, n in base.items()
            if (c := nt.late_candidates(n, by_day.get(x - timedelta(days=1), []) + by_day.get(x, [])))}
    tagged = {frozenset(): base}
    out = []
    for k in range(sc.HISTORY_DAYS - 1, 0, -1):
        d = today - timedelta(days=k)
        ss = [s for s in sessions if s.day <= d]
        peak, rest = st.hr_max(ss, d), nt.rest_hr(base, d)
        if all(any(nt.vigorous(s, rest, peak) for s in c) == ("late" in base[x].tags) for x, c in late.items()
               if x <= d):
            alert = frozenset(x for x in nt.alert_episodes(base, d) if x in base)
            if alert not in tagged:
                tagged[alert] = nt.freeze({x: nt.retagged(n, n.tags | {"alert"}) if x in alert else n
                                           for x, n in base.items()})
            nd = tagged[alert]
        else:
            nd = {x: nt.retagged(n) for x, n in recent.items() if x <= d}
            nt.tag_activities(nd, ss, efforts, rest, peak)
            nt.tag_alerts(nd, d)
            nt.freeze(nd)
        past = _assess(nd, ss, efforts, d)
        out.append((d, past["state"], past["score"]))
    return out


# ── the top: rings, state, contributors ─────────────────────────────────────

def _usual_week(sessions, today: date) -> float | None:
    """The median of the activities' hours (stops included) of the last 12
    complete weeks (Monday → Sunday, local days) since the first one (H)."""
    if not sessions:
        return None
    monday = today - timedelta(days=today.weekday())
    first = min(s.day for s in sessions)
    per = defaultdict(float)
    for s in sessions:
        per[s.day - timedelta(days=s.day.weekday())] += st.effort_minutes(s)
    weeks = [monday - timedelta(days=7 * k) for k in range(1, USUAL_WEEKS + 1)]
    weeks = [m for m in weeks if m + timedelta(days=6) >= first]
    return statistics.median(per.get(m, 0.0) for m in weeks) if weeks else None


def _top(day: dict, sessions, today: date, has_watch: bool, page: dict) -> dict:
    """The rings, the state line and the Contributeurs. A ring links to the
    section it sums up, never to one the page lacks: Récupération to its 14
    days (else the Contributeurs), Sommeil to its section, Charge to its 14
    days."""
    state, score = day["state"], day["score"]
    href = "#recuperation" if page.get("recup") else "#contributeurs" if score.get("value") is not None else None
    rings = [sc.ring(score, state, href)]
    sleep_href = "#sommeil" if (page.get("sleep") or {}).get("state", "never") != "never" else None
    tst = day["tst24"]
    if tst is None:
        rings.append(viz.ring("sommeil", None, "—", "Sommeil", "24 h" if sleep_href else None, tone="none",
                              href=sleep_href, aria="Sommeil : pas de nuit mesurée ce matin"))
    else:
        tone = sc.sleep_tone(tst)
        word = {"ok": "", "warn": ", moins de 7 heures", "danger": ", moins de 6 heures"}[tone]
        rings.append(viz.ring("sommeil", tst / SLEEP_FULL, viz.hm(tst), "Sommeil", "24 h", tone=tone,
                              href=sleep_href, aria=f"Sommeil : {viz.hm_long(tst)} sur 24 heures, siestes comprises"
                                                    f"{word}." + (" Ouvre la section Sommeil." if sleep_href else "")))
    week = sum(st.effort_minutes(s) for s in sessions if today - timedelta(days=6) <= s.day <= today)
    usual = _usual_week(sessions, today)
    charge_href = "#charge" if page.get("charge") else None
    spoken = f"Charge : {viz.hm_long(week)} d'activité sur 7 jours" if week else "Charge : aucune activité sur 7 jours"
    if usual:
        ratio = week / usual
        spoken += (", plus du double de ta semaine habituelle" if ratio > CHARGE_TURN else
                   ", au-dessus de ta semaine habituelle" if ratio > 1.1 else
                   ", sous ta semaine habituelle" if ratio < 0.9 else ", autour de ta semaine habituelle")
    rings.append(viz.ring("charge", week / (CHARGE_TURN * usual) if usual else None,
                          viz.hm(week) if week else f"0{viz.NNBSP}h", "Charge", "7 jours", tone="accent",
                          href=charge_href, tick=1 / CHARGE_TURN if usual else None,
                          aria=spoken + "." + (" Ouvre la charge sur 14 jours." if charge_href else "")))
    return {"rings": rings, "state": state, "line": None if state else td.no_state_line(has_watch),
            "connect": not has_watch and state is None,
            "contrib": sc.contributors(score, state), "score": score}


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


def _night_card(nights, metric: str, today: date) -> dict | None:
    """« VFC · 30 nuits » / « FC de nuit · 30 nuits » (viz.night_card): None
    without a night measured in the 30 days. With fewer than 10 measured
    nights the axis spans them (from the first, 14 days at least), not 30
    nights with a few dots against the right edge."""
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
        n = nights.get(d)
        b = nt.band(nights, metric, d, source=n.source_of(metric) if n and n.value(metric) is not None else None)
        bands.append((b["lo"], b["hi"]) if b else None)
        prov.append(bool(b and b["provisional"]))
    means = [(nt.mean7(nights, metric, d) or {}).get("value") for d in days]
    key, name, unit, unit_long = CARDS[metric]
    return viz.night_card(key, days, values, band=bands, prov=prov, mean=means, unit=unit, unit_long=unit_long,
                          name=name, min_span=_span(metric, values, bands))


def _charge_card(sessions, today: date) -> dict | None:
    """« Charge · 14 jours »: the activities' hours of each day (stops
    included: the effort's own time), the long efforts simply tall bars (one
    that dwarfs the others runs past the axis, broken: the other days stay
    readable); tap a day → its hours and its activities (the longest one
    linked). It rests on the number of activities, nothing selected (an
    empty day is never selected; the hours are the ring's and the taps'). None
    without an activity in the 14 days."""
    days = [today - timedelta(days=CHARGE_DAYS - 1 - i) for i in range(CHARGE_DAYS)]
    by_day = defaultdict(list)
    for s in sessions:
        if days[0] <= s.day <= today:
            by_day[s.day].append(s)
    if not by_day:
        return None
    values, r, a, h = [], [], [], []
    for d in days:
        ss = sorted(by_day.get(d, []), key=st.effort_minutes, reverse=True)
        total = sum(st.effort_minutes(s) for s in ss)
        values.append(total)
        label = "aujourd'hui" if d == today else viz.d_short(d)
        names = " · ".join(s.name or "activité" for s in ss[:2]) + (" …" if len(ss) > 2 else "")
        r.append([viz.hm(total), "", f"{label} · {names}"] if ss else ["—", "", f"{label} · aucune activité"])
        a.append(f"{viz.d_long(d)} : " + (f"{viz.hm_long(total)} d'activité, {names}" if ss else "aucune activité"))
        h.append({"href": f"/activity/{ss[0].id}", "label": "Voir ›"} if ss else None)
    tall = sorted((v for v in values if v), reverse=True)
    clip = None  # one long effort dwarfing the other days: the axis stops at 1,6 × the next (2 h at least)
    if len(tall) > 1 and tall[0] >= CHARGE_TALL and tall[0] > CHARGE_CLIP * tall[1]:
        clip = max(CHARGE_CLIP * tall[1], CHARGE_MIN_TOP) * 1.12
    n = sum(len(by_day[d]) for d in days if d in by_day)
    c = viz.day_bars("charge", days, values, readouts=r, arias=a, links=h, today=len(days) - 1, clip=clip,
                     min_top=CHARGE_MIN_TOP,
                     summary=f"Charge, {CHARGE_DAYS} jours : {n} activité{'s' if n > 1 else ''}")
    word = f"activité{'s' if n > 1 else ''}"
    return viz.rest(c, [str(n), word, ""], f"Charge sur {CHARGE_DAYS} jours : {n} {word}. Touche un jour pour ses "
                    "activités.", back=len(days) - 1)


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
