"""The Santé page: today's recovery and training load, trends, fitness, the
fitness verdict — from the daily values in HealthMetric (COROS).

Everything here reads; nothing calls COROS. Only what decides training or race
readiness is drawn: load, recovery and the night signals (HRV, resting HR,
sleep). Stress, steps and the daily heart rate are synced but not shown. An
athlete who rarely wears the watch at night is told what the empty charts need.
"""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.services.health import (
    BASELINE_DAYS,
    RECENT_DAYS,
    _today,
    compute_form,
    fmt_minutes,
    hrv_same_scale,
)

PERIODS = (30, 90)

# ── words ───────────────────────────────────────────────────────────────────

# COROS's training-load comment → (key, French label, one-line meaning)
LOAD_STATUS = {
    "excessive": ("over", "Surcharge",
                  "Tes 7 derniers jours pèsent bien plus que d'habitude : risque de blessure, prévois du repos."),
    "optimized": ("good", "Optimal",
                  "Ta charge monte à un bon rythme : c'est comme ça qu'on progresse."),
    "maintaining": ("keep", "Maintien",
                    "Ta charge reste proche de ton habitude : tu entretiens ta forme."),
    "performance": ("taper", "Récupération",
                    "Tu en fais moins que d'habitude : tu récupères et t'affûtes, idéal avant une course."),
    "low": ("low", "Charge basse",
            "Tu en fais nettement moins que d'habitude : ta forme de fond va baisser si ça dure."),
}


def load_status(comment: str | None, ratio: float | None) -> tuple[str, str, str]:
    """(key, label, meaning) from COROS's comment, else from the ratio with
    COROS's own bands (< 0.8, 0.8–1, 1–1.5, > 1.5)."""
    c = (comment or "").strip().lower()
    for word, status in LOAD_STATUS.items():
        if c.startswith(word[:5]):
            return status
    if c.startswith(("detrain", "decreas")):
        return LOAD_STATUS["low"]
    if ratio is None:
        return ("unknown", comment or "—", "")
    if ratio > 1.5:
        return LOAD_STATUS["excessive"]
    if ratio >= 1.0:
        return LOAD_STATUS["optimized"]
    if ratio >= 0.8:
        return LOAD_STATUS["maintaining"]
    return LOAD_STATUS["performance"]


def recovery_level(level: str | None) -> str | None:
    """COROS's recovery advice in French (unknown wordings shown as they come)."""
    if not level:
        return None
    t = level.lower()
    for words, fr in ((("rest",), "Repos conseillé"),
                      (("light", "easy", "low"), "Séance légère conseillée"),
                      (("moderate",), "Séance modérée possible"),
                      (("high", "intens", "hard", "ready"), "Prêt pour une séance intense"),
                      (("full", "complete"), "Complètement récupéré")):
        if any(w in t for w in words):
            return fr
    return level


def fmt_clock(s: float | None, pace: bool = False) -> str:
    """950 → '15:50', 4254 → '1:10:54'; pace: 200 → '3:20 /km'."""
    if s is None:
        return "—"
    s = int(round(s))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    out = f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
    return out + " /km" if pace else out


def fmt_int(v: float | None) -> str:
    """18055 → '18 055' (narrow no-break space, French style)."""
    return "—" if v is None else f"{int(round(v)):,}".replace(",", " ")


def _day(d: date, today: date) -> str:
    if d == today:
        return "aujourd'hui"
    if d == today - timedelta(days=1):
        return "hier"
    return "le " + d.strftime("%d/%m")


# ── charts ──────────────────────────────────────────────────────────────────

CHART_W, CHART_H, PAD = 300, 64, 5


def chart(lines: list[dict[date, float]], today: date, days: int, fmt=None,
          bars: bool = False, band: tuple[float, float] | None = None) -> dict | None:
    """Plain-SVG geometry for one or more daily series over the last `days`
    days: lines broken where a day is missing (isolated days drawn as dots),
    or bars; an optional horizontal band (a normal range); one hover target
    per day (`fmt`: value → text). None when there is nothing in the window."""
    fmt = fmt or "{:.0f}".format
    lo_day = today - timedelta(days=days - 1)
    window = [{d: v for d, v in s.items() if lo_day <= d <= today} for s in lines]
    vals = [v for s in window for v in s.values()]
    if not vals:
        return None
    lo, hi = (0.0 if bars else min(vals)), max(vals)
    if band:
        lo, hi = min(lo, band[0]), max(hi, band[1])
    if hi - lo < 1e-9:
        lo, hi = (lo - 1, hi + 1) if not bars else (0.0, hi or 1.0)
    if not bars:  # a little air above and below the line
        span = hi - lo
        lo, hi = lo - span * 0.08, hi + span * 0.08
    step = CHART_W / days

    def x(i: int) -> float:
        return round((i + 0.5) * step, 1)

    def y(v: float) -> float:
        return round(PAD + (1 - (v - lo) / (hi - lo)) * (CHART_H - 2 * PAD), 1)

    out: dict = {"w": CHART_W, "h": CHART_H, "series": [], "bars": [], "hits": []}
    if bars:
        base = y(lo)
        for d, v in window[0].items():
            top = y(v)
            out["bars"].append((round((d - lo_day).days * step + step * 0.15, 1), top,
                                round(step * 0.7, 1), round(max(base - top, 0.8), 1)))
    else:
        for s in window:
            path, dots, pen = [], [], False
            for i in range(days):
                d = lo_day + timedelta(days=i)
                v = s.get(d)
                if v is None:
                    pen = False
                    continue
                path.append(f"{'L' if pen else 'M'}{x(i)} {y(v)}")
                if not pen and (d + timedelta(days=1)) not in s:
                    dots.append((x(i), y(v)))  # alone: a line of one point draws nothing
                pen = True
            last = max(s) if s else None
            out["series"].append({"d": " ".join(path), "dots": dots,
                                  "last": (x((last - lo_day).days), y(s[last])) if last else None})
    if band:
        top, bottom = y(band[1]), y(band[0])
        out["band"] = (top, round(bottom - top, 1))
    for i in range(days):
        d = lo_day + timedelta(days=i)
        vs = [s.get(d) for s in window]
        label = d.strftime("%d/%m") + " · " + " / ".join(fmt(v) if v is not None else "—" for v in vs)
        out["hits"].append((round(i * step, 1), round(step, 1), label))
    return out


# ── page ────────────────────────────────────────────────────────────────────

_NIGHT_HINT = "Porte ta montre la nuit pour avoir ta VFC, ta FC au repos et ton sommeil."
_LOAD_HINT = "La charge arrive avec tes séances enregistrées sur ta montre COROS."


async def health_page(db: AsyncSession, user_id: int, days: int = 30) -> dict:
    """Everything /sante draws. `has_data` is False when nothing ever arrived."""
    days = days if days in PERIODS else PERIODS[0]
    latest = (await db.execute(
        select(func.max(HealthMetric.date)).where(HealthMetric.user_id == user_id))).scalar()
    today = _today(latest)
    lo = today - timedelta(days=max(days, RECENT_DAYS + BASELINE_DAYS))
    rows = (await db.execute(
        select(HealthMetric.metric, HealthMetric.date, HealthMetric.value, HealthMetric.source,
               HealthMetric.details)
        .where(HealthMetric.user_id == user_id, HealthMetric.date >= lo, HealthMetric.date <= today)
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
    # the fitness assessment is written once per sync: the last one, even if older
    last_fit = (await db.execute(
        select(HealthMetric.date, HealthMetric.details).where(
            HealthMetric.user_id == user_id, HealthMetric.metric == "fitness")
        .order_by(HealthMetric.date.desc()).limit(1))).first()
    last_vo2 = (await db.execute(
        select(HealthMetric.date, HealthMetric.value).where(
            HealthMetric.user_id == user_id, HealthMetric.metric == "vo2max")
        .order_by(HealthMetric.date.desc()).limit(1))).first()

    return {
        "has_data": latest is not None,
        "today": today,
        "days": days,
        "periods": PERIODS,
        "recovery": _recovery(series, details, today),
        "load": _load(series, details, today),
        "form": _form(series, today),
        "trends": _trends(series, details, today, days),
        "fitness": _fitness(last_fit, last_vo2, today),
    }


def _latest(series: dict[date, float], today: date, max_age: int) -> date | None:
    d = max(series, default=None)
    return d if d is not None and (today - d).days <= max_age else None


def _recovery(series, details, today) -> dict | None:
    d = _latest(series.get("recovery", {}), today, 3)
    if d is None:
        return None
    det = details["recovery"][d]
    return {"pct": round(series["recovery"][d]), "level": recovery_level(det.get("level")),
            "full_h": det.get("full_h"), "when": _day(d, today), "stale": d < today - timedelta(days=1)}


def _load(series, details, today) -> dict | None:
    d = _latest(series.get("load", {}), today, 7)
    if d is None:
        return None
    det = details["load"][d]
    key, label, meaning = load_status(det.get("comment"), det.get("ratio"))
    return {"short": round(series["load"][d]), "long": round(det["long"]) if det.get("long") is not None else None,
            "ratio": det.get("ratio"), "key": key, "label": label, "meaning": meaning,
            "when": _day(d, today), "stale": d < today - timedelta(days=1)}


def _form(series, today) -> dict:
    return compute_form({m: series.get(m, {}) for m in ("hrv", "rhr", "sleep")}, today)


def _trends(series, details, today, days) -> dict:
    lo_day = today - timedelta(days=days - 1)

    def window(metric: str) -> dict[date, float]:
        return {d: v for d, v in series.get(metric, {}).items() if lo_day <= d <= today}

    items, missing = [], []

    def add(key, title, hint, s, value, legend=None, **kw):
        g = chart([s] if not isinstance(s, list) else s, today, days, **kw)
        if g is None:
            missing.append((title, hint))
            return
        last = max(s[0] if isinstance(s, list) else s, default=None)
        items.append({"key": key, "title": title, "value": value, "legend": legend, "when": _day(last, today) if last else None, "chart": g})

    # training load: short vs long term
    short, long_ = window("load"), {d: details["load"][d].get("long") for d in window("load")}
    long_ = {d: v for d, v in long_.items() if v is not None}
    last = max(short, default=None)
    add("load", "Charge d'entraînement", _LOAD_HINT, [short, long_],
        f"{short[last]:.0f} / " + (f"{long_[last]:.0f}" if last in long_ else "—") if last else "—", legend=[("Court terme", "dark"), ("Long terme", "light")])

    rhr = window("rhr")
    add("rhr", "FC au repos", _NIGHT_HINT, rhr, f"{rhr[max(rhr)]:.0f} bpm" if rhr else "—")

    hrv = window("hrv")
    norm_day = max(series.get("hrv_norm", {}), default=None)
    band = None
    if norm_day is not None:
        nd = details["hrv_norm"][norm_day]
        if nd.get("lo") is not None and nd.get("hi") is not None:
            band = (nd["lo"], nd["hi"])
    add("hrv", "VFC (variabilité cardiaque)", _NIGHT_HINT, hrv, f"{hrv[max(hrv)]:.0f} ms" if hrv else "—", band=band)

    sleep = {d: v / 60 for d, v in window("sleep").items()}
    add("sleep", "Sommeil", _NIGHT_HINT, sleep, fmt_minutes(sleep[max(sleep)] * 60) if sleep else "—",
        bars=True, fmt=lambda v: fmt_minutes(v * 60))

    # one line per distinct advice, naming the charts it would fill
    hints: dict[str, list[str]] = {}
    for title, hint in missing:
        hints.setdefault(hint, []).append(title.split(" (")[0])
    # (titles, advice, is the night advice: the verdict may already say it)
    return {"charts": items, "missing": [(", ".join(t), h, h == _NIGHT_HINT) for h, t in hints.items()],
            "start": lo_day.strftime("%d/%m")}


def _fitness(last_fit, last_vo2, today) -> dict | None:
    det = dict(last_fit.details or {}) if last_fit else {}
    vo2 = det.get("vo2max")
    if vo2 is None and last_vo2:
        vo2 = last_vo2.value
    if not det and vo2 is None:
        return None
    when = max([d for d in (last_fit.date if last_fit else None, last_vo2.date if last_vo2 else None) if d])
    return {
        "vo2max": f"{vo2:.0f}" if vo2 is not None else None,
        "threshold": fmt_clock(det["threshold_s"], pace=True) if det.get("threshold_s") else None,
        "when": _day(when, today),
    }

