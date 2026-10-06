"""The Santé page in four tabs, each answering one question:
- Aujourd'hui « comment je m'entraîne aujourd'hui ? »: one decision read from
  the nights (HRV and resting HR against the athlete's normal), the training
  load, the legs and the heart rate at easy pace (sante_today), the rows it
  rests on, the watch's own score as a second opinion, the check-in and the
  last 14 nights;
- Entraînement « comment se passent mes semaines ? » (sante_week);
- Course « suis-je prêt pour la prochaine ? » (sante_course);
- Tendances « est-ce que je progresse sur des mois ? » (sante_trends).
The training side comes from the sessions alone (sante_training), so the page
is useful to an athlete who never wears the watch at night. Everything here
reads; nothing calls the watches. A night without the watch is never guessed:
it is a gap, never counted in an average. Each number is printed once.
"""
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.models.route import Route
from app.services.health import (
    SHORT_SLEEP_MIN,
    _today,
    compute_form,
    fmt_minutes,
    hrv_same_scale,
)

# ── words ───────────────────────────────────────────────────────────────────

# the watch's training-load comment → trend key
LOAD_KEYS = {"excessive": "over", "optimized": "good", "maintaining": "keep", "performance": "taper", "low": "low"}
# descriptive, never evaluative: the decision says what to do
LOAD_WORDS = {"over": "forte hausse", "good": "en hausse", "keep": "stable", "taper": "en baisse",
              "low": "en forte baisse"}


def load_key(comment: str | None, ratio: float | None) -> str | None:
    """The load trend from the watch's comment, else from the ratio with
    COROS's bands (< 0.8, 0.8–1, 1–1.5, > 1.5)."""
    c = (comment or "").strip().lower()
    for word, key in LOAD_KEYS.items():
        if c.startswith(word[:5]):
            return key
    if c.startswith(("detrain", "decreas")):
        return "low"
    if ratio is None:
        return None
    if ratio > 1.5:
        return "over"
    if ratio >= 1.0:
        return "good"
    if ratio >= 0.8:
        return "keep"
    return "taper"


def fmt_clock(s: float | None, pace: bool = False) -> str:
    """950 → '15:50', 4254 → '1:10:54'; pace: 200 → '3:20 /km'."""
    if s is None:
        return "—"
    s = int(round(s))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    out = f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
    return out + " /km" if pace else out


def _num(v: float, digits: int = 0) -> str:
    return f"{v:.{digits}f}".replace(".", ",")


WEEKDAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")


def _dayname(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day}"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


# ── sleep: minutes after 18:00 of the evening before ────────────────────────

SLEEP_NEED_MIN = 480
RACE_NEED_MIN = 540  # the week before a race: bank sleep
FLOOR_MIN = 420  # < 7 h: short
NIGHTS = 14
MIN_AVG_NIGHTS = 3  # of 7, for the averages and the debt
MIN_REG_NIGHTS = 5  # of 14, for regularity
MIN_INJURY_NIGHTS = 7  # of 14, for the 2-week average
FALL_ASLEEP_MIN = 15
RACE_WAKE_BEFORE_START = 120
EARLY_START_MIN = 90
EARLY_WAKE_MIN = 60  # a wake-up this much earlier than usual cuts the late REM


def clock_m(hhmm: str | None) -> int | None:
    """'23:34' → 334, '05:31' → 691: minutes after 18:00 of the evening
    before, so midnight never breaks a median or a SD."""
    try:
        h, m = (int(x) for x in str(hhmm).split(":")[:2])
    except (TypeError, ValueError):
        return None
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return (h * 60 + m - 1080) % 1440


def m_clock(m: float) -> str:
    """334 → '23:34' (wraps)."""
    t = (int(round(m)) + 1080) % 1440
    return f"{t // 60:02d}:{t % 60:02d}"


def _round_down5(m: float) -> int:
    return int(m // 5 * 5)


def _hm(minutes: float) -> str:
    """105 → '1h45', 30 → '30 min'."""
    minutes = int(round(minutes))
    return f"{minutes // 60}h{minutes % 60:02d}" if minutes >= 60 else f"{minutes} min"


# ── page ────────────────────────────────────────────────────────────────────

PAGE_METRICS = ("hrv", "rhr", "sleep", "load", "recovery", "stress", "hr_day", "steps", "body_battery", "vo2max",
                "fitness", "sleep_score", "feel")
HISTORY_DAYS = 400
SIGNAL_DAYS = 28


async def health_page(db: AsyncSession, user_id: int, today: date | None = None,
                      now: datetime | None = None, weight_kg: float | None = None) -> dict:
    """Everything /sante draws, tab by tab. `has_data` is False when neither
    a watch nor a session ever arrived."""
    from app.services import sante_today as td
    from app.services import sante_training as st
    from app.services import sante_week as wk

    now = now or datetime.now(timezone.utc)
    latest = (await db.execute(
        select(func.max(HealthMetric.date)).where(HealthMetric.user_id == user_id))).scalar()
    today = today or await athlete_today(db, user_id, now, latest)
    rows = (await db.execute(
        select(HealthMetric.metric, HealthMetric.date, HealthMetric.value, HealthMetric.source, HealthMetric.details)
        .where(HealthMetric.user_id == user_id, HealthMetric.date >= today - timedelta(days=HISTORY_DAYS),
               HealthMetric.date <= today, HealthMetric.metric.in_(PAGE_METRICS)))).all()
    series: dict[str, dict[date, float]] = defaultdict(dict)
    details: dict[str, dict[date, dict]] = defaultdict(dict)
    sources: dict[str, dict[date, str | None]] = defaultdict(dict)
    for metric, d, v, source, det in rows:
        series[metric][d] = v
        details[metric][d] = det or {}
        sources[metric][d] = source
    if series.get("hrv"):
        series["hrv"] = hrv_same_scale(series["hrv"], sources["hrv"])
    last_sleep = next((r for r in (await db.execute(
        select(HealthMetric.date, HealthMetric.value, HealthMetric.details).where(
            HealthMetric.user_id == user_id, HealthMetric.metric == "sleep")
        .order_by(HealthMetric.date.desc()).limit(60))).all() if _measured(r.value, r.details)), None)

    sessions = await st.load_sessions(db, user_id, today)
    peak = st.hr_max(sessions, today)
    rest = st.hr_rest(series.get("rhr", {}),
                      {d: det["min"] for d, det in details.get("hr_day", {}).items() if det.get("min")}, today)
    st.set_loads(sessions, rest, peak)
    tr = st.form(sessions, today)
    legs = st.legs(sessions, today)
    runs = st.easy_runs(sessions, peak)
    easy = st.easy_hr(runs, today)
    if easy and easy["n"] < 2:
        easy = None
    next_race, last_race, past_races = await _races(db, user_id, today)

    form = compute_form({m: series.get(m, {}) for m in ("hrv", "rhr", "sleep")}, today)
    nights = _nights(series.get("sleep", {}), details.get("sleep", {}), today,
                     next_race if next_race and (date.fromisoformat(next_race.race_date) - today).days <= 7 else None,
                     last_sleep, series.get("sleep_score", {}))
    weeks = st.weeks(sessions, now)
    watch_load = _load(series, details, today)
    watch_src = next((sources["load"][d] for d in sorted(series.get("load", {}), reverse=True)), None)

    # what the decision reads
    nr = None
    if next_race:
        nr = {"days": (date.fromisoformat(next_race.race_date) - today).days, "name": next_race.name}
    post = _post_race(last_race, sessions, today)
    hard48 = _hard48(sessions, today)
    jump = any((j := wk._jump(weeks, i)) and j >= wk.JUMP for i in (len(weeks) - 2, len(weeks) - 1))
    feel = None
    if today in series.get("feel", {}):
        feel = {"value": series["feel"][today], "legs_heavy": bool(details["feel"][today].get("legs_heavy"))}
    ctx = {"form": form, "tr": tr, "legs": legs, "easy": easy, "feel": feel, "next_race": nr, "post_race": post,
           "today": today, "ill": _ill(form, series.get("rhr", {}), today), "hard48": hard48,
           "short_night": nights.get("short_night"), "jump": jump, "watch_load": watch_load}
    verdict = td.decide(ctx)

    # the rows, from the same objects
    signals = []
    if tr:
        signals.append(td.fatigue_row(tr))
    elif watch_load and watch_load["ratio"] is not None:
        signals.append(td.watch_load_row(watch_load, watch_src or "montre"))
    if legs:
        signals.append(td.legs_row(legs, today))
    if easy:
        signals.append(td.easy_hr_row(easy))
    if form.get("hrv_z") is not None:
        dip = (f"Basse après ta sortie {td.of_day(hard48.day, today)} : normal, ça revient en 24–48 h."
               if hard48 and form["hrv_z"] < -0.5 else None)
        lo, hi = form["hrv_band"]
        signals.append(td.hrv_row(form, signal_chart(series.get("hrv", {}), today, (lo, hi), form["hrv_baseline"],
                                                     min_span=0.3 * form["hrv_baseline"]), dip))
    if form.get("rhr_baseline") is not None and form.get("rhr_7d") is not None:
        base = form["rhr_baseline"]
        signals.append(td.rhr_row(form, signal_chart(series.get("rhr", {}), today, (base - 3, base + 3), base,
                                                     min_span=12)))
    for r in (td.sleep_row(nights, series.get("sleep_score", {}), today), td.stress_row(series.get("stress", {}), today)):
        if r:
            signals.append(r)
    rec_day = max((d for d in series.get("recovery", {}) if d >= today - timedelta(days=1)), default=None)
    watch = td.watch_row({"value": series["recovery"][rec_day], **details["recovery"][rec_day]} if rec_day else None,
                         sources["recovery"].get(rec_day) if rec_day else None,
                         next((details["body_battery"][d].get("at_wake") or series["body_battery"][d]
                               for d in (today,) if d in series.get("body_battery", {})), None))
    if watch:
        signals.append(watch)
        verdict["second"] = td.disagreement(watch, verdict["tone"], legs["big"] if legs else None, today)
    visible, more = td.order_rows(signals, verdict["drivers"])

    nights_line = None
    if nights["n14"] == 0 and latest is not None:
        since = f" (dernière le {last_sleep.date.strftime('%d/%m')})" if last_sleep else ""
        nights_line = (f"VFC, FC au repos, sommeil : 0 nuit mesurée sur les 7 dernières{since}. Porte ta montre "
                       "3 nuits sur 7 pour les voir ici.")
    elif nights["n7"] >= MIN_AVG_NIGHTS and (form.get("nights_base") or 0) < 14:
        nights_line = (f"VFC et FC au repos : il faut 14 nuits sur 2 mois pour connaître ta normale "
                       f"(tu en as {form.get('nights_base') or 0}).")

    # training tab
    first_monday = weeks[0]["monday"]
    hrv_band = form.get("hrv_band")
    race_days = [date.fromisoformat(r.race_date) for r in past_races]
    weeks26 = st.weeks(sessions, now, 26)
    resid = st.residuals(runs, today)
    course = _course(next_race, last_race, sessions, tr, today, weight_kg,
                     _usual(series.get("sleep", {}), details.get("sleep", {}), today))
    training = {
        "header": wk.header(weeks, sessions, tr, today, (course or {}).get("taper_line")),
        "bars": wk.bars(weeks, sessions, series.get("sleep", {}), series.get("hrv", {}), hrv_band)
        if any(w["count"] for w in weeks) else None,
        "fatigue": wk.fatigue_chart(tr, first_monday, today, race_days) if tr else None,
        "fatigue_line": wk.fatigue_line(tr, today) if tr else None,
        "insights": wk.insights(sessions, runs, resid, weeks26, series.get("sleep", {}), today),
    }
    return {
        "has_data": latest is not None or bool(sessions),
        "has_watch_data": latest is not None,
        "today": today,
        "verdict": verdict, "rows": visible, "more": more, "nights_line": nights_line, "feel": feel,
        "nights": nights if nights["n14"] else None,
        "night_state": _night_state(series, sources, today),
        "training": training,
        "course": course,
        "trends": _trends(sessions, series, details, past_races, today, tr["series"] if tr else {}),
    }


async def athlete_today(db: AsyncSession, user_id: int, now: datetime | None = None,
                        latest: date | None = None) -> date:
    """The athlete's date: the server's clock moved by their UTC offset (from
    their latest Strava session), else the watch's latest day (_today)."""
    from app.services.sante_training import utc_offset

    now = now or datetime.now(timezone.utc)
    if latest is None:
        latest = (await db.execute(
            select(func.max(HealthMetric.date)).where(HealthMetric.user_id == user_id))).scalar()
    offset = await utc_offset(db, user_id)
    today = (now + timedelta(seconds=offset)).date() if offset is not None else _today(latest)
    if latest and today < latest <= today + timedelta(days=1):  # the watch is already on tomorrow
        today = latest
    return today


def _course(*args):
    try:
        from app.services.sante_course import course_tab
    except ImportError:  # wired when the Course tab lands
        return None
    return course_tab(*args)


def _trends(*args):
    try:
        from app.services.sante_trends import trends_tab
    except ImportError:
        return None
    return trends_tab(*args)


def _night_state(series, sources, today: date) -> dict[str, dict]:
    """Per watch: last night received (with its length), not yet, or not worn
    (daytime data for today already came but no night)."""
    out = {}
    for src in ("COROS", "Garmin"):
        night = sources.get("sleep", {}).get(today) == src
        if night and series["sleep"].get(today):
            out[src] = {"state": "received", "txt": fmt_minutes(series["sleep"][today])}
        elif any(sources.get(m, {}).get(today) == src for m in ("steps", "stress", "hr_day", "body_battery")):
            out[src] = {"state": "none"}
        else:
            out[src] = {"state": "pending"}
    return out


async def _races(db: AsyncSession, user_id: int, today: date):
    """(the next race, the last one run in the past 14 days, those of the past
    12 months) — sports hidden right now left out."""
    from app.features import hidden_sports

    q = select(Route).where(Route.user_id == user_id, Route.race_date.is_not(None),
                            Route.race_date >= (today - timedelta(days=365)).isoformat())
    if hidden_sports():
        q = q.where(Route.sport_type.notin_(hidden_sports()))
    routes = (await db.execute(q.order_by(Route.race_date))).scalars().all()
    nxt = next((r for r in routes if r.race_date >= today.isoformat()), None)
    past = [r for r in routes if r.race_date < today.isoformat()]
    last = next((r for r in reversed(past) if r.race_date >= (today - timedelta(days=14)).isoformat()), None)
    return nxt, last, past


def _post_race(last_race, sessions, today: date) -> dict | None:
    """The days after a race of 3 h or more (a Route raced, or a session marked
    as a race), or after an exceptional outing (6 h and 1.5 × the longest of
    the 60 days before): J+1..J+7, J+10 after 10 h."""
    cands = []
    if last_race:
        res = last_race.result_json or {}
        secs = res.get("total_actual_s") or last_race.target_time_s
        if secs and secs >= 3 * 3600:
            cands.append((date.fromisoformat(last_race.race_date), secs / 60, last_race.name))
    for s in sessions:
        if not 1 <= (today - s.day).days <= 10:
            continue
        before = max((x.minutes for x in sessions if s.day - timedelta(days=60) <= x.day < s.day), default=0)
        if (s.workout_type == 1 and s.minutes >= 180) or (s.minutes >= 360 and s.minutes >= 1.5 * before):
            from app.services.sante_today import of_day
            cands.append((s.day, s.minutes, f"ta sortie {of_day(s.day, today)}"))
    for day, minutes, name in sorted(cands, reverse=True):
        days = (today - day).days
        limit = 10 if minutes >= 600 else 7
        if 1 <= days <= limit:
            return {"day": day, "days": days, "minutes": minutes, "name": name, "limit": limit}
    return None


def _hard48(sessions, today: date):
    """A long or hard session yesterday or the day before (its night HRV dip is expected)."""
    loads = [s.load for s in sessions if s.day >= today - timedelta(days=90)]
    med = statistics.median(loads) if loads else 0
    cands = [s for s in sessions if 1 <= (today - s.day).days <= 2
             and (s.minutes >= 180 or s.dplus >= 1000 or (med and s.load >= 2 * med))]
    return max(cands, key=lambda s: s.load, default=None)


def _load(series, details, today) -> dict | None:
    """The watch's own load ratio (shown only without enough sessions)."""
    s = series.get("load", {})
    d = max(s, default=None)
    if d is None or (today - d).days > 7:
        return None
    det = details["load"][d]
    ratio = det.get("ratio")
    if ratio is None and det.get("long"):
        ratio = round(s[d] / det["long"], 2)
    key = load_key(det.get("comment"), ratio)
    if key is None:
        return None
    return {"ratio": ratio, "key": key, "word": LOAD_WORDS[key], "stale": d < today - timedelta(days=1),
            "when": d.strftime("%d/%m")}


def _ill(form: dict, rhr: dict[date, float], today: date) -> bool:
    """Resting HR well above the athlete's usual two measured nights running:
    often the start of an illness (strict '>', missing nights never count)."""
    base, sd = form.get("rhr_baseline"), form.get("rhr_sd")
    if base is None or sd is None:
        return False
    limit = base + max(2 * sd, 5)
    return all(rhr.get(d) is not None and rhr[d] > limit for d in (today, today - timedelta(days=1)))


# ── the 28-night chart behind VFC and FC au repos ──────────────────────────


SIG_W, SIG_H, SIG_PAD = 300, 96, 8


def signal_chart(values: dict[date, float], today: date, band: tuple[float, float], base: float,
                 min_span: float) -> dict | None:
    """28 nights: faint dots per night, the 7-day mean (broken below 3 nights
    in the trailing 7), the athlete's normal band with its edges labelled.
    The y range is never narrower than `min_span` (1 bpm must not look like a
    cliff)."""
    lo_day = today - timedelta(days=SIGNAL_DAYS - 1)
    pts = {d: v for d, v in values.items() if lo_day <= d <= today}
    if not pts:
        return None
    rolling = {}
    for i in range(SIGNAL_DAYS):
        d = lo_day + timedelta(days=i)
        window = [values[x] for x in (d - timedelta(days=k) for k in range(7)) if x in values]
        if len(window) >= 3:
            rolling[d] = statistics.fmean(window)
    vals = [*pts.values(), *rolling.values(), *band]
    lo, hi = min(vals), max(vals)
    if hi - lo < min_span:
        mid = (hi + lo) / 2
        lo, hi = mid - min_span / 2, mid + min_span / 2
    step = SIG_W / SIGNAL_DAYS

    def x(d: date) -> float:
        return round(((d - lo_day).days + 0.5) * step, 1)

    def y(v: float) -> float:
        return round(SIG_PAD + (1 - (v - lo) / (hi - lo)) * (SIG_H - 2 * SIG_PAD), 1)

    path, pen = [], False
    for i in range(SIGNAL_DAYS):
        d = lo_day + timedelta(days=i)
        if d in rolling:
            path.append(f"{'L' if pen else 'M'}{x(d)} {y(rolling[d])}")
            pen = True
        else:
            pen = False
    top, bottom = y(band[1]), y(band[0])
    return {"w": SIG_W, "h": SIG_H, "dots": [(x(d), y(v)) for d, v in sorted(pts.items())],
            "line": " ".join(path), "band": (top, round(bottom - top, 1)),
            "band_lo": _num(band[0]), "band_hi": _num(band[1]),
            "lo_pct": round(bottom / SIG_H * 100, 1), "hi_pct": round(top / SIG_H * 100, 1),
            "start": lo_day.strftime("%d/%m")}


# ── the nights ──────────────────────────────────────────────────────────────

def _measured(v, det) -> tuple[int, int] | None:
    """(bed_m, wake_m) of a night with a real window (not a phone's in-bed time)."""
    if v is None or not det or det.get("from_in_bed"):
        return None
    b, w = clock_m(det.get("bedtime")), clock_m(det.get("wake"))
    if b is None or w is None or w <= b:
        return None
    return b, w


def _usual(sleep, det, today) -> dict | None:
    """Median bedtime and wake (and the awake minutes inside the window) over
    the measured nights of the last 28 days, else the last 60 (3 at least)."""
    for days in (28, 60):
        nights = []
        for d, v in sleep.items():
            if today - timedelta(days=days - 1) <= d <= today:
                win = _measured(v, det.get(d))
                if win:
                    nights.append((win[0], win[1], v))
        if len(nights) >= 3:
            return {"bed": statistics.median(b for b, _, _ in nights),
                    "wake": statistics.median(w for _, w, _ in nights),
                    "awake": max(0.0, statistics.median(w - b - v for b, w, v in nights))}
    return None


def _nights(sleep: dict[date, float], det: dict[date, dict], today: date, race, last_sleep,
            scores: dict[date, float] | None = None) -> dict:
    """The 14 nights (named by their wake-up day, last night first), the
    7-night average and debt, regularity, the takeaway sentence, the strip."""
    race_day = date.fromisoformat(race.race_date) if race else None
    need = RACE_NEED_MIN if race_day else SLEEP_NEED_MIN
    usual = _usual(sleep, det, today)

    rows = []
    for i in range(NIGHTS):
        d = today - timedelta(days=i)
        v = sleep.get(d)
        win = _measured(v, det.get(d))
        rows.append({"day": d, "label": "cette nuit" if i == 0 else _dayname(d),
                     "value": v if win else None, "bed": win[0] if win else None, "wake": win[1] if win else None,
                     "pending": i == 0 and not win,
                     "score": round((scores or {})[d]) if win and d in (scores or {}) else None})
    measured = [r for r in rows if r["value"] is not None]
    last7 = [r for r in rows[:7] if r["value"] is not None]
    n7, n14 = len(last7), len(measured)

    target_bed = bed_by = None
    if usual:
        target_bed = usual["wake"] - need - usual["awake"]  # sleep onset that gives `need` before the usual wake
        bed_by = _round_down5(target_bed - FALL_ASLEEP_MIN)
    bed_txt = f"au lit vers {m_clock(bed_by)}" if bed_by is not None else f"vise {need // 60} h par nuit"

    avg7 = statistics.fmean(r["value"] for r in last7) if n7 >= MIN_AVG_NIGHTS else None
    avg14 = statistics.fmean(r["value"] for r in measured) if n14 >= MIN_INJURY_NIGHTS else None
    debt = max(0.0, statistics.fmean(need - r["value"] for r in last7)) if avg7 is not None else None

    regularity = None
    if n14 >= MIN_REG_NIGHTS:
        sd_bed = statistics.stdev(r["bed"] for r in measured)
        sd_wake = statistics.stdev(r["wake"] for r in measured)
        which = "réveil" if sd_wake >= sd_bed else "coucher"
        times = [r["wake"] if which == "réveil" else r["bed"] for r in measured]
        sd = max(sd_bed, sd_wake)
        word, tone = (("régulier", "muted") if sd <= 30 else ("assez régulier", "muted") if sd <= 60
                      else ("irrégulier", "warn") if sd <= 90 else ("très irrégulier", "danger"))
        lo_t, hi_t = m_clock(min(times)), m_clock(max(times))
        regularity = {"sd": round(sd), "word": word, "tone": tone,
                      "range": f"{which} à {lo_t}" if lo_t == hi_t else f"{which} entre {lo_t} et {hi_t}"}

    # last night: short, or cut short by an early wake-up (the late-REM loss)
    short_night = None
    last = rows[0]
    if last["value"] is not None:
        early = usual is not None and last["wake"] <= usual["wake"] - EARLY_WAKE_MIN
        if last["value"] < SHORT_SLEEP_MIN or (last["value"] < need - 60 and early):
            short_night = ("Nuit courte" + (" et réveil tôt" if early else "")
                           + " : place ta séance de qualité ce matin plutôt que ce soir, ou passe-la en facile.")
            if last["value"] <= need - 60:
                short_night += " Une sieste de 20 à 45 min avant 16 h aide."

    takeaway = _takeaway(race, race_day, today, need, usual, bed_txt, avg7, avg14, debt, regularity)

    # coverage: the one place the page asks for the watch at night
    if n14 == 0:
        since = (f"Aucune nuit avec ta montre depuis le {last_sleep.date.strftime('%d/%m')}"
                 f" ({fmt_minutes(last_sleep.value)})." if last_sleep
                 else "Aucune nuit avec ta montre pour l'instant.")
        coverage = (since + " Porte-la la nuit : dès 3 nuits sur 7 tu vois ta dette, dès 5 sur 14 la régularité "
                    "de tes horaires.")
    elif n7 < MIN_AVG_NIGHTS:
        s = "s" if n7 > 1 else ""
        coverage = (f"{n7} nuit{s} mesurée{s} sur les 7 dernières : il en faut 3 pour ta moyenne. "
                    "Porte ta montre la nuit.")
    else:
        coverage = None

    return {
        "rows": rows, "n7": n7, "n14": n14, "need_h": need // 60, "race": bool(race_day),
        "avg7_txt": fmt_minutes(avg7) if avg7 is not None else None,
        "regularity": regularity, "takeaway": takeaway, "coverage": coverage,
        # few nights: the watch may come off on the bad ones
        "selective": n7 >= MIN_AVG_NIGHTS and n14 < MIN_INJURY_NIGHTS,
        "short_night": short_night, "strip": _strip(rows, usual, target_bed, need) if n14 else None,
    }


def _takeaway(race, race_day, today, need, usual, bed_txt, avg7, avg14, debt, regularity) -> str | None:
    """One sentence, first match wins (race week, < 6 h, < 7 h over 2 weeks,
    debt, irregular hours, all good). Only once 3 of the last 7 nights are
    measured, except in race week."""
    if race_day:  # the race-week plan (wake shift, the last night) lives in Course
        return f"Semaine de course : vise {need // 60} h par nuit" + (f", {bed_txt}." if usual else ".")
    if avg7 is None:
        return None
    if avg7 < SHORT_SLEEP_MIN:
        return ("Moins de 6 h par nuit cette semaine : tu attrapes plus facilement un rhume et tes capacités baissent "
                f"sans que tu le sentes. {_cap(bed_txt)} les prochains soirs.")
    if avg14 is not None and avg14 < FLOOR_MIN:
        out = "Moins de 7 h par nuit sur 2 semaines : chez les coureurs d'endurance, c'est lié à plus de blessures."
        if debt is not None and debt >= 30 and usual:
            out += f" Il te manque environ {_hm(debt)} par nuit : {bed_txt}."
        return out
    if debt is not None and debt >= 60:
        return (f"Il te manque environ {_hm(debt)} par nuit sur tes {need // 60} h : ça se rattrape en plusieurs "
                f"jours, pas en une grasse matinée. {_cap(bed_txt)}.")
    if debt is not None and debt >= 30:
        return "Un peu court cette semaine : te coucher 30 min plus tôt suffit."
    if regularity and regularity["sd"] > 60:
        return (f"Tes horaires varient beaucoup ({regularity['range']}) : des horaires réguliers t'aident à mieux "
                "dormir.")
    return "Assez de sommeil, horaires réguliers : rien à changer."


STRIP_ROW = 20


def _strip(rows: list[dict], usual: dict | None, target_bed: float | None, need: int) -> dict:
    """The 14 sleep windows on one time axis (percent x, pixel y): measured
    nights as bars coloured by duration, missing nights as a faint dotted
    hairline, the band = `need` before the usual wake, hour ticks and the band
    edges on the axis."""
    usual_wake = usual["wake"] if usual else None
    beds = [r["bed"] for r in rows if r["bed"] is not None] + ([target_bed] if target_bed is not None else [])
    wakes = [r["wake"] for r in rows if r["wake"] is not None] + ([usual_wake] if usual_wake is not None else [])
    t0 = max(0, min(240, (int(min(beds)) - 30) // 60 * 60)) if beds else 240
    t1 = min(1200, max(900, -(-(int(max(wakes)) + 30) // 60) * 60)) if wakes else 900

    def x(m: float) -> float:
        return round((m - t0) / (t1 - t0) * 100, 2)

    bars = []
    for i, r in enumerate(rows):
        night = f"Nuit du {_dayname(r['day'] - timedelta(days=1))} au {_dayname(r['day'])}"
        if r["value"] is not None:
            tone = "ok" if r["value"] >= FLOOR_MIN else "short" if r["value"] >= SHORT_SLEEP_MIN else "vshort"
            bars.append({"y": i * STRIP_ROW, "x": x(r["bed"]), "w": max(round(x(r["wake"]) - x(r["bed"]), 2), 0.5),
                         "tone": tone, "label": r["label"], "dur": fmt_minutes(r["value"]), "score": r.get("score"),
                         "title": (f"{night} : coucher {m_clock(r['bed'])}, lever {m_clock(r['wake'])}, "
                                   f"{fmt_minutes(r['value'])} de sommeil"),
                         "sr": f"{r['label']} : {fmt_minutes(r['value'])}, de {m_clock(r['bed'])} à {m_clock(r['wake'])}"})
        else:
            what = "pas encore reçue" if r["pending"] else "pas de montre"
            bars.append({"y": i * STRIP_ROW, "missing": True, "tone": "miss", "label": r["label"], "dur": "—",
                         "title": f"{night} : {what}", "sr": f"{r['label']} : {what}"})
    band = None
    if usual_wake is not None and target_bed is not None:
        band = {"x": x(target_bed), "w": round(x(usual_wake) - x(target_bed), 2)}
    edges = [target_bed, usual_wake] if band else []
    hours = range(-(-t0 // 120) * 120, t1 + 1, 120)  # even hours (18:00 is m = 0)
    ticks = [{"x": x(m), "label": f"{(m + 1080) // 60 % 24}h", "edge": False}
             for m in hours if all(abs(m - e) > 45 for e in edges)]
    ticks += [{"x": x(e), "label": m_clock(e), "edge": True} for e in edges]
    for t in ticks:
        t["align"] = "start" if t["x"] < 6 else "end" if t["x"] > 94 else "mid"
    measured = [r["value"] for r in rows if r["value"] is not None]
    s = "s" if len(measured) > 1 else ""
    aria = (f"Tes 14 dernières nuits : {len(measured)} mesurée{s}"
            + (f", sommeil de {fmt_minutes(min(measured))} à {fmt_minutes(max(measured))}" if measured else ""))
    return {"bars": bars, "band": band, "ticks": sorted(ticks, key=lambda t: t["x"]),
            "scored": any(r.get("score") for r in rows),
            "grid": [x(m) for m in hours], "height": len(rows) * STRIP_ROW, "half": 7 * STRIP_ROW,
            "aria": aria}
