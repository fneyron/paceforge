"""The Santé page: one decision for today, the last 14 nights, fitness — from
the daily values in HealthMetric (COROS, Garmin).

Everything here reads; nothing calls the watches. The page answers « what do
I do today, and is my sleep holding up? » with the fewest elements:
- the decision: night signals (compute_form), training load and last night
  reconciled into one action headline, backed by at most three numbers
  (VFC, FC au repos, charge), each next to the athlete's own usual value;
- the nights: 14 sleep windows (bedtime → wake) against a band of the sleep
  needed before the usual wake time, with one takeaway sentence from the
  sleep research (duration, debt, regularity, race week);
- fitness: VO2max and threshold pace.
The watch's own recovery score is not shown: its formula is undisclosed and
it contradicted the page. Sleep stages are not shown either (wrist stages
agree poorly with polysomnography). A night without the watch is never
guessed: it is drawn as a gap and never counted in an average.
"""
import statistics
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.models.route import Route
from app.services.health import (
    BASELINE_DAYS,
    RECENT_DAYS,
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

async def health_page(db: AsyncSession, user_id: int, today: date | None = None) -> dict:
    """Everything /sante draws. `has_data` is False when nothing ever arrived."""
    latest = (await db.execute(
        select(func.max(HealthMetric.date)).where(HealthMetric.user_id == user_id))).scalar()
    today = today or _today(latest)
    lo = today - timedelta(days=RECENT_DAYS + BASELINE_DAYS)
    rows = (await db.execute(
        select(HealthMetric.metric, HealthMetric.date, HealthMetric.value, HealthMetric.source,
               HealthMetric.details)
        .where(HealthMetric.user_id == user_id, HealthMetric.date >= lo, HealthMetric.date <= today,
               HealthMetric.metric.in_(("hrv", "rhr", "sleep", "load")))
    )).all()
    series: dict[str, dict[date, float]] = defaultdict(dict)
    details: dict[str, dict[date, dict]] = defaultdict(dict)
    hrv_sources: dict[date, str | None] = {}
    for metric, d, v, source, det in rows:
        series[metric][d] = v
        details[metric][d] = det or {}
        if metric == "hrv":
            hrv_sources[d] = source
    if series.get("hrv"):
        series["hrv"] = hrv_same_scale(series["hrv"], hrv_sources)
    # the last night ever worn, however old (for « depuis le 27/08 »)
    last_sleep = next((r for r in (await db.execute(
        select(HealthMetric.date, HealthMetric.value, HealthMetric.details).where(
            HealthMetric.user_id == user_id, HealthMetric.metric == "sleep")
        .order_by(HealthMetric.date.desc()).limit(60))).all() if _measured(r.value, r.details)), None)
    # the fitness assessment is written once per sync: the last one, even if older
    last_fit = (await db.execute(
        select(HealthMetric.date, HealthMetric.details).where(
            HealthMetric.user_id == user_id, HealthMetric.metric == "fitness")
        .order_by(HealthMetric.date.desc()).limit(1))).first()
    last_vo2 = (await db.execute(
        select(HealthMetric.date, HealthMetric.value).where(
            HealthMetric.user_id == user_id, HealthMetric.metric == "vo2max")
        .order_by(HealthMetric.date.desc()).limit(1))).first()
    race = await _next_race(db, user_id, today)

    form = compute_form({m: series.get(m, {}) for m in ("hrv", "rhr", "sleep")}, today)
    load = _load(series, details, today)
    nights = _nights(series.get("sleep", {}), details.get("sleep", {}), today, race, last_sleep)
    return {
        "has_data": latest is not None,
        "today": today,
        "verdict": _verdict(form, load, nights, series.get("rhr", {}), today),
        "signals": _signals(form, load, series, today),
        "nights": nights,
        "fitness": _fitness(last_fit, last_vo2),
    }


async def _next_race(db: AsyncSession, user_id: int, today: date):
    """The next race in the coming 7 days (sports hidden right now left out)."""
    from app.features import hidden_sports

    q = select(Route).where(
        Route.user_id == user_id, Route.race_date.is_not(None),
        Route.race_date >= (today + timedelta(days=1)).isoformat(),
        Route.race_date <= (today + timedelta(days=7)).isoformat())
    if hidden_sports():
        q = q.where(Route.sport_type.notin_(hidden_sports()))
    return (await db.execute(q.order_by(Route.race_date).limit(1))).scalars().first()


def _load(series, details, today) -> dict | None:
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


# ── the decision ────────────────────────────────────────────────────────────

def _ill(form: dict, rhr: dict[date, float], today: date) -> bool:
    """Resting HR well above the athlete's usual two measured nights running:
    often the start of an illness (strict '>', missing nights never count)."""
    base, sd = form.get("rhr_baseline"), form.get("rhr_sd")
    if base is None or sd is None:
        return False
    limit = base + max(2 * sd, 5)
    return all(rhr.get(d) is not None and rhr[d] > limit for d in (today, today - timedelta(days=1)))


def _verdict(form: dict, load: dict | None, nights: dict, rhr: dict[date, float], today: date) -> dict:
    """{tone: ok|easy|rest|unknown, headline, text, note}: one decision, first match wins."""
    status, reasons = form["status"], form.get("reasons") or []
    key = load["key"] if load else None
    if _ill(form, rhr, today):
        return {"tone": "rest", "headline": "Reste tranquille aujourd'hui",
                "text": "Ta FC au repos est nettement au-dessus de ton habitude deux nuits de suite : c'est souvent "
                        "un début de maladie (ou l'alcool, la chaleur, l'altitude). Repos ou footing très facile "
                        "jusqu'à ce qu'elle redescende.", "note": None}
    if status == "fatigue":
        hrv_reason = any("VFC" in r for r in reasons)
        text = ("Tu récupères moins bien que d'habitude : footing facile ou repos. Reprends l'intensité quand "
                + ("ta VFC revient à son niveau habituel." if hrv_reason
                   else "ta FC au repos redescend à son niveau habituel."))
        if key in ("good", "keep", "taper"):
            text += " Ta charge n'est pas en cause : pense sommeil, stress ou début de maladie."
        return {"tone": "rest", "headline": "Pas d'intensité aujourd'hui", "text": text, "note": None}

    short = nights.get("short_night")
    triggers = [t for t, on in (("watch", status == "watch"), ("over", key == "over"), ("short", bool(short))) if on]
    if triggers:
        clauses = []
        if status == "watch":
            if reasons == ["nuits courtes"]:
                clauses.append("Tes nuits sont plus courtes que d'habitude : n'enchaîne pas deux séances dures.")
            else:
                clauses.append("Un signal sort de ta normale : seul, ce n'est pas grave, mais n'enchaîne pas deux "
                               "séances dures.")
        if key == "over":
            clauses.append("Ta VFC est haute en pleine charge : ça peut être de la surcharge, pas un feu vert."
                           if status == "fresh" else
                           "Tes 7 derniers jours pèsent bien plus que d'habitude : n'ajoute ni volume ni intensité.")
        if short:
            clauses.append(short)
        headline = ("Séance dure le matin seulement" if triggers == ["short"]
                    else "Séance prévue, sans en rajouter" if triggers == ["over"]
                    else "Garde ta séance facile")
        return {"tone": "easy", "headline": headline, "text": " ".join(clauses[:2]),
                "note": _coverage(form, load) if status == "unknown" else None}
    if status in ("ok", "fresh"):
        text = ("Ta VFC est au-dessus de ta normale : tu récupères bien." if status == "fresh"
                else "Ta VFC et ta FC au repos sont dans ta normale.")
        if key == "taper":
            text += " Charge en baisse : tu t'affûtes."
        elif key == "low":
            text += " Ta charge baisse nettement : ta forme de fond baissera si ça dure."
        return {"tone": "ok", "headline": "Séance dure possible", "text": text, "note": None}
    note = _coverage(form, load)
    if key in ("good", "keep"):
        return {"tone": "ok", "headline": "Entraînement prévu OK",
                "text": "Ta charge monte à un bon rythme." if key == "good"
                else "Ta charge reste proche de ton habitude.", "note": note}
    if key == "taper":
        return {"tone": "ok", "headline": "Semaine légère",
                "text": "Tu en fais moins que d'habitude : tu récupères, idéal avant une course.", "note": note}
    if key == "low":
        return {"tone": "unknown", "headline": "Charge basse",
                "text": "Tu en fais nettement moins que d'habitude : ta forme de fond baissera si ça dure.",
                "note": note}
    return {"tone": "unknown", "headline": "Pas encore d'avis", "text": None, "note": note}


def _coverage(form: dict, load: dict | None) -> str:
    """What the decision rests on while the nights can't say yet (asking for
    the watch at night is left to « Tes nuits », said once)."""
    have = form.get("nights_base") or 0
    need = (f"il faut 14 nuits sur 2 mois pour connaître ta normale (tu en as {have})" if have < 14
            else f"il faut 3 nuits mesurées sur les 7 dernières (tu en as {form.get('nights_recent') or 0})")
    return f"Avis basé sur ta charge seulement : {need}." if load else _cap(need) + "."


# ── the signals ─────────────────────────────────────────────────────────────

HRV_BAND_SD = 0.5
RHR_BAND_BPM = 3
SIGNAL_DAYS = 28


def _signals(form: dict, load: dict | None, series, today: date) -> list[dict]:
    """Up to three rows: value over 7 days, the athlete's usual, a status word
    (tone ok/warn/danger/muted). VFC and FC au repos open on a 28-day chart."""
    out = []
    if form.get("hrv_baseline") is not None and form.get("hrv_7d") is not None:
        base, sd, v = form["hrv_baseline"], form["hrv_sd"], form["hrv_7d"]
        z = (v - base) / sd if sd else 0
        heavy = bool(load) and load["key"] == "over"
        word, tone = (("basse", "danger") if z < -1 else ("un peu basse", "warn") if z < -0.5
                      else (("haute en charge", "warn") if heavy else ("haute", "ok")) if z > 0.5
                      else ("normale", "muted"))
        band = (base - HRV_BAND_SD * sd, base + HRV_BAND_SD * sd)
        out.append({"key": "hrv", "label": "VFC · 7 j", "value": f"{_num(v)} ms", "ref": f"d'habitude {_num(base)}",
                    "word": word, "tone": tone,
                    "chart": signal_chart(series.get("hrv", {}), today, band, base, min_span=0.3 * base),
                    "resume": (f"Reprends l'intensité quand la ligne revient vers {_num(base)} ms, ta moyenne."
                               if z < -0.5 else None)})
    if form.get("rhr_baseline") is not None and form.get("rhr_7d") is not None:
        base, v, delta = form["rhr_baseline"], form["rhr_7d"], form["rhr_delta_bpm"]
        word, tone = (("haute", "danger") if delta >= 5 else ("un peu haute", "warn") if delta >= 3
                      else ("normale", "muted"))
        out.append({"key": "rhr", "label": "FC au repos · 7 j", "value": f"{_num(v)} bpm",
                    "ref": f"d'habitude {_num(base)}", "word": word, "tone": tone,
                    "chart": signal_chart(series.get("rhr", {}), today, (base - RHR_BAND_BPM, base + RHR_BAND_BPM),
                                          base, min_span=12),
                    "resume": (f"Reprends l'intensité quand la ligne revient vers {_num(base)} bpm, ta moyenne."
                               if delta >= 3 else None)})
    if load and load["ratio"] is not None:
        out.append({"key": "load", "label": "Charge · 7 j", "value": _num(load["ratio"], 2),
                    "ref": "× ton habitude" + (f" · au {load['when']}" if load["stale"] else ""),
                    "word": load["word"], "tone": "danger" if load["key"] == "over" else "muted",
                    "chart": None, "resume": None})
    return out


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


def _nights(sleep: dict[date, float], det: dict[date, dict], today: date, race, last_sleep) -> dict:
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
                     "pending": i == 0 and not win})
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
    if race_day:
        days = (race_day - today).days
        wake_clause = ""
        if race.start_hour is not None:
            start = race.start_hour * 60 + (race.start_minute or 0)
            race_wake = (start - RACE_WAKE_BEFORE_START - 1080) % 1440
            if usual and usual["wake"] - race_wake > EARLY_START_MIN:
                gap = usual["wake"] - race_wake
                shift = min(60, -(-gap // max(days, 1) // 5) * 5)
                wake_clause = (f" Départ à {m_clock((start - 1080) % 1440)} : si tu te lèves 2 h avant, réveil vers "
                               f"{m_clock(race_wake)}, {_hm(gap)} plus tôt que d'habitude. Avance ton coucher et ton "
                               f"lever d'environ {shift} min par jour.")
        if days == 1:
            return (f"{race.name} demain : mal dormir la veille d'une course est courant et pèse peu après une bonne "
                    "semaine. Au lit à ton heure." + wake_clause)
        return (f"{race.name} dans {days} jours : vise 9 h par nuit d'ici là, {bed_txt}. C'est cette semaine de "
                "sommeil qui compte, plus que la dernière nuit." + wake_clause)
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
                         "tone": tone, "label": r["label"], "dur": fmt_minutes(r["value"]),
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
            "grid": [x(m) for m in hours], "height": len(rows) * STRIP_ROW, "half": 7 * STRIP_ROW,
            "aria": aria}


# ── fitness ─────────────────────────────────────────────────────────────────

def _fitness(last_fit, last_vo2) -> dict | None:
    det = dict(last_fit.details or {}) if last_fit else {}
    vo2 = det.get("vo2max")
    if vo2 is None and last_vo2:
        vo2 = last_vo2.value
    if not det and vo2 is None:
        return None
    return {
        "vo2max": f"{vo2:.0f}" if vo2 is not None else None,
        "threshold": fmt_clock(det["threshold_s"], pace=True) if det.get("threshold_s") else None,
    }
