"""Santé › Tendances: « est-ce que je progresse sur des mois ? »

Twelve monthly points per curve, the current month last. A point is the
median of at least 4 values of that month (science §5: monthly medians, raw
values faint underneath, the line broken where a month is short); a curve
needs 3 points, else it goes to « Pas encore de courbe » with one line saying
what is missing.
- Ton fond: CTL (sante_training.fitness) on each month's last day — CTL is
  already a 6-week average, so the month's last day is its state — as % of its
  12-month peak. The first 6 weeks of history are left out (the average is
  still filling up).
- FC à allure facile: sante_training.easy_hr_months on the flat easy runs;
  HRmax is computed here with sante_training.hr_max(sessions, today).
- VO2 max (montre): the watch's estimate (vo2max rows, else fitness details),
  threshold pace as a sub-line; under 4 months of history only today's value.
- Nights: FC au repos, VFC (medians on ln RMSSD, back-transformed), Sommeil
  (monthly mean, the watch's score as a second label). « FC la plus basse de
  la journée » (hr_day min) stands in only when the nights give no resting-HR
  curve, and is only compared with itself.
A change is called only above the noise of its signal, held over the months
the science asks (fond ≥ 10 % in 3 months; easy HR ≥ 3 bpm and resting HR
≥ 2.5 bpm, two months running; VO2 ≥ 2; VFC > 0.5 SD of ln; sleep ≥ 20 min),
else « ≈ stable ». The change is read against up to 6 months back, so a slow
drift counts as well as a quick one.
« Ce qui va ensemble »: fond against easy-pace HR (else the watch's VO2 max)
on the same two months — L1 « tes heures paient » (the only causal wording,
hedged), L2 « ton cœur ne suit pas », L3 a fond that erodes (not after a race
nor in a taper). Never more than 2, each with its months and « lien observé,
pas forcément cause ».
Everything reads; nothing is written.
"""
import math
import statistics
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta

from app.services import sante_training as st
from app.services.sante_today import hm, num, signed

MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre")
MONTHS_SHORT = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
INITIALS = "JFMAMJJASOND"

N_MONTHS = 12
MIN_PER_MONTH = 4  # values behind a monthly point
MIN_MONTHS = 3  # monthly points behind a curve
MIN_FOND = 10.0  # CTL peak below this: too little training for a curve
VO2_HISTORY_MONTHS = 4
BACK = (6, 5, 4, 3, 2, 7, 8, 9, 10, 11)  # months back to read a change against, in order of preference

# noise thresholds (science §5, §8)
FOND_THR = 0.10  # relative, over 3 months
EASY_THR, RHR_THR, DAYMIN_THR = 3.0, 2.5, 3.0  # bpm, held 2 months
VO2_THR = 2.0  # a monthly median already holds 4 weeks
SLEEP_THR = 20.0  # minutes, held 2 months
HRV_SD_K, HRV_SD_FLOOR = 0.5, 0.05  # × SD of ln RMSSD, held 2 months

CAUTION = "lien observé, pas forcément cause"

# chart geometry (fixed viewBox, never stretched)
W, H = 340, 110
X0 = 40  # left gutter for the gridline values
MW = (W - X0) / N_MONTHS
Y_TOP, Y_BOT, Y_MONTHS = 30, 86, 105  # labels sit above their dot, flags above them
CHAR_W = 8.0  # ≈ width of one character at 13–14 px, for keeping labels inside
SW, SXA, SXB = 320, 122, 236  # slope chart: width, left and right columns


# ── months ──────────────────────────────────────────────────────────────────

def months_axis(today: date) -> list[tuple[int, int]]:
    """The 12 (year, month) ending with the current month, oldest first."""
    k = today.year * 12 + today.month - 1
    return [(i // 12, i % 12 + 1) for i in range(k - N_MONTHS + 1, k + 1)]


def _start(months: list[tuple[int, int]]) -> date:
    return date(months[0][0], months[0][1], 1)


def _month_end(m: tuple[int, int], today: date) -> date:
    return min(date(m[0], m[1], monthrange(*m)[1]), today)


def _de(month: str) -> str:
    return ("d'" if month[0] in "aeiouéèh" else "de ") + month


def _in(values: dict[date, float], months, today: date, lo: float = -math.inf, hi: float = math.inf) -> dict:
    start = _start(months)
    return {d: v for d, v in (values or {}).items() if v is not None and start <= d <= today and lo <= v <= hi}


def monthly(values: dict[date, float], months: list[tuple[int, int]], agg=statistics.median,
            min_n: int = MIN_PER_MONTH, today: date | None = None) -> list[float | None]:
    """One point per month of the axis: `agg` of the month's values, None
    below `min_n` values — and the month in progress only from its 14th day
    (a few nights would read as a month)."""
    idx = {m: i for i, m in enumerate(months)}
    groups: list[list[float]] = [[] for _ in months]
    for d, v in values.items():
        i = idx.get((d.year, d.month))
        if i is not None and v is not None:
            groups[i].append(v)
    if today is not None and today.day < 14 and months and months[-1] == (today.year, today.month):
        groups[-1] = []
    return [agg(g) if len(g) >= min_n else None for g in groups]


def _count(points) -> int:
    return sum(1 for v in points if v is not None)


# ── change: only above the noise ────────────────────────────────────────────

def change(points: list[float | None], thr: float, hold: int = 1, back=BACK, ratio: bool = False) -> dict | None:
    """The last point against an earlier one (`back` months before, in order of
    preference). Real when |change| ≥ `thr` and the last `hold` points after the
    reference all moved that much the same way. None without a reference."""
    present = [i for i, v in enumerate(points) if v is not None]
    if not present:
        return None
    last = present[-1]
    ref = next((last - k for k in back if last - k >= 0 and points[last - k] is not None), None)
    if ref is None:
        return None
    base = points[ref]

    def d(v: float) -> float:
        return v / base - 1 if ratio else v - base

    delta = d(points[last])
    after = [d(points[i]) for i in range(ref + 1, last + 1) if points[i] is not None]
    real = (abs(delta) >= thr and len(after) >= hold
            and all(abs(x) >= thr and (x > 0) == (delta > 0) for x in after[-hold:]))
    # above the noise but not held yet: neither a change nor « stable »
    return {"delta": delta, "months": last - ref, "ref": ref, "last": last, "real": real,
            "pending": abs(delta) >= thr and not real}


def chip(ch: dict | None, fmt, better_down: bool = False) -> dict | None:
    """« ↗ +6 % en 3 mois », « ↘ −4 bpm en 6 mois · en mieux » or « ≈ stable »."""
    if ch is None:
        return None
    if ch.get("pending"):
        return {"text": "à confirmer", "tone": "muted"}
    if not ch["real"]:
        return {"text": "≈ stable", "tone": "muted"}
    up = ch["delta"] > 0
    text = f"{'↗' if up else '↘'} {fmt(ch['delta'])} en {ch['months']} mois"
    if better_down and not up:
        return {"text": text + " · en mieux", "tone": "ok"}
    return {"text": text, "tone": "muted"}


def _line(ch: dict | None, months, up: str, down: str, stable: str, none: str | None = None) -> str | None:
    if ch is None:
        return none
    m = MONTHS[months[ch["ref"]][1] - 1]
    if ch.get("pending"):
        return f"{'Plus haut' if ch['delta'] > 0 else 'Plus bas'} qu'en {m} : à confirmer le mois prochain."
    tpl = (up if ch["delta"] > 0 else down) if ch["real"] else stable
    return tpl.format(m=m)


# ── charts ──────────────────────────────────────────────────────────────────

def _xm(i: int) -> float:
    return round(X0 + (i + 0.5) * MW, 1)


def _xd(d: date, months) -> float | None:
    try:
        i = months.index((d.year, d.month))
    except ValueError:
        return None
    return round(X0 + (i + (d.day - 0.5) / monthrange(d.year, d.month)[1]) * MW, 1)


def _grid(lo: float, hi: float, steps) -> list[float]:
    """Two round values inside [lo, hi]: the coarsest step giving at least two,
    the pair nearest the middle."""
    for step in sorted(steps, reverse=True):
        ticks = [k * step for k in range(math.ceil(lo / step), math.floor(hi / step) + 1)]
        if len(ticks) >= 2:
            i = min(range(len(ticks) - 1), key=lambda j: abs(ticks[j] + ticks[j + 1] - lo - hi))
            return ticks[i:i + 2]
    return []


def _race_ticks(races, months, today: date) -> list[dict]:
    out = []
    for d, name in _races(races):
        if d <= today and (x := _xd(d, months)) is not None:
            out.append({"x": x, "name": f"{name} · {d.day} {MONTHS[d.month - 1]}"})
    return out


def curve(points: list[float | None], raw: list[tuple[date, float]], months, races, today: date, fmt,
          title: str, steps=(1, 2, 5, 10, 20, 50, 100), min_span: float = 6) -> dict:
    """The card's SVG (viewBox 0 0 340 110): two gridlines, faint raw values,
    the monthly line broken across short months, first and last labelled,
    month initials, races as flags on the top edge."""
    present = [(i, v) for i, v in enumerate(points) if v is not None]
    vals = [v for _, v in present]
    lo, hi = min(vals), max(vals)
    rv = sorted(v for _, v in raw)
    if len(rv) >= 10:  # the bulk of the raw values, not their outliers
        lo, hi = min(lo, rv[len(rv) // 10]), max(hi, rv[-1 - len(rv) // 10])
    span = max(hi - lo, min_span)
    mid = (lo + hi) / 2
    lo, hi = mid - span * 0.6, mid + span * 0.6

    def y(v: float) -> float:
        return round(Y_BOT - (v - lo) / (hi - lo) * (Y_BOT - Y_TOP), 1)

    paths, seg = [], []
    for i, v in enumerate(points):
        if v is None:
            if len(seg) > 1:
                paths.append("M" + " L".join(f"{x} {yy}" for x, yy in seg))
            seg = []
        else:
            seg.append((_xm(i), y(v)))
    if len(seg) > 1:
        paths.append("M" + " L".join(f"{x} {yy}" for x, yy in seg))

    def label(i: int, v: float, last: bool) -> dict:
        text, x, yy = fmt(v), _xm(i), y(v)
        half = len(text) * CHAR_W / 2
        return {"x": round(min(max(x, X0 + half), W - half), 1), "y": round(yy - 8, 1), "text": text, "last": last}

    (i0, v0), (i1, v1) = present[0], present[-1]
    pts = []
    for d, v in raw:
        x = _xd(d, months)
        if x is not None and lo <= v <= hi:
            pts.append((x, y(v)))
    return {
        "w": W, "h": H,
        "aria": f"{title}, médiane par mois : {fmt(v0)} en {MONTHS[months[i0][1] - 1]}, "
                f"{fmt(v1)} en {MONTHS[months[i1][1] - 1]}",
        "grid": [{"y": y(g), "ly": round(y(g) + 4.5, 1), "label": fmt(g)} for g in _grid(lo, hi, steps)],
        "x0": X0, "lx": X0 - 4,
        "points": pts,
        "paths": paths,
        "dots": [(_xm(i), y(v)) for i, v in present],
        "labels": [label(i0, v0, False), label(i1, v1, True)],
        "months": [{"x": _xm(i), "y": Y_MONTHS, "text": INITIALS[m - 1]} for i, (_, m) in enumerate(months)],
        "races": _race_ticks(races, months, today),
    }


def slope(cols: tuple[str, str], rows: list[tuple[str, float, float, str, str, str]]) -> dict:
    """Two columns (start and end month), one line per metric in its own
    band, real values printed at both ends (viewBox 0 0 320 96 for two)."""
    out = []
    for k, (name, v0, v1, t0, t1, cls) in enumerate(rows):
        c = 40 + 36 * k
        dy = 0 if v1 == v0 else (9 if v1 > v0 else -9)
        out.append({"name": name, "ny": c + 4.5, "y0": c + dy, "y1": c - dy, "t0": t0, "t1": t1,
                    "ty0": c + dy + 4.5, "ty1": c - dy + 4.5, "cls": cls})
    return {"w": SW, "h": 24 + 36 * len(rows), "xa": SXA, "xb": SXB, "ta": SXA - 8, "tb": SXB + 8,
            "cols": [{"x": SXA, "text": cols[0]}, {"x": SXB, "text": cols[1]}], "rows": out,
            "aria": " ; ".join(f"{r[0]} : {r[3]} en {cols[0]}, {r[4]} en {cols[1]}" for r in rows)}


# ── cards ───────────────────────────────────────────────────────────────────

def _card(key, title, window, value, unit=None, sub=None, ch=None, line=None, note=None, caption=None, chart=None):
    return {"key": key, "title": title, "window": window, "value": value, "unit": unit, "sub": sub, "chip": ch,
            "line": line, "note": note, "caption": caption, "chart": chart}


def _stale(points, months) -> str:
    """« en juin » when the last point is older than last month."""
    last = max(i for i, v in enumerate(points) if v is not None)
    return f"en {MONTHS[months[last][1] - 1]}" if last < len(months) - 2 else ""


def _unit(*parts: str) -> str | None:
    out = " ".join(p for p in parts if p)
    return out or None


def _plural(n: int, one: str, many: str) -> str:
    return one if n <= 1 else many


def locked_line(label: str, n: int, one: str, many: str, ok_months: int) -> str:
    """« VFC : 3 nuits en 12 mois — il en faut 4 par mois »."""
    if n == 0:
        return f"{label} : aucune {one} en 12 mois"
    if ok_months == 0 or n < MIN_PER_MONTH * MIN_MONTHS:
        return (f"{label} : {n} {_plural(n, one, many)} en 12 mois — il en faut {MIN_PER_MONTH} par mois "
                f"pendant {MIN_MONTHS} mois")
    return f"{label} : {ok_months} mois avec {MIN_PER_MONTH} {many} ou plus — il en faut {MIN_MONTHS}"


def pace_txt(s: float) -> str:
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}/km"


def _races(races) -> list[tuple[date, str]]:
    out = []
    for r in races or []:
        rd, name = (r.get("race_date"), r.get("name")) if isinstance(r, dict) else (
            getattr(r, "race_date", None), getattr(r, "name", ""))
        if isinstance(rd, str):
            try:
                rd = date.fromisoformat(rd[:10])
            except ValueError:
                continue
        if isinstance(rd, date):
            out.append((rd, (name or "ta course").strip()))
    return sorted(out)


def _fond(series: dict, months, today: date, races) -> tuple[dict | None, str | None, dict | None]:
    """(card, locked line, {raw, pct}) for « Ton fond »."""
    if not series:
        return None, "Ton fond : aucune activité en 12 mois", None
    warm = min(series) + timedelta(days=st.MIN_HISTORY_DAYS)
    start = _start(months)
    days = {d: v[0] for d, v in series.items() if d >= warm and start <= d <= today}
    if not days:
        return None, "Ton fond : il faut 6 semaines d'activités", None
    peak_day = max(days, key=lambda d: (days[d], d))
    peak = days[peak_day]
    if peak < MIN_FOND:
        return None, "Ton fond : trop peu d'activités en 12 mois pour une courbe", None
    groups: dict[tuple[int, int], list[tuple[date, float]]] = defaultdict(list)
    for d, v in days.items():
        groups[(d.year, d.month)].append((d, v))
    raw = [max(groups[m])[1] if len(groups.get(m, ())) >= MIN_PER_MONTH else None for m in months]
    n = _count(raw)
    if n < MIN_MONTHS:
        return None, f"Ton fond : {n} mois d'activités — il en faut {MIN_MONTHS}", None
    pct = [100 * v / peak if v is not None else None for v in raw]
    now = 100 * days[max(days)] / peak
    since = MONTHS[min(days).month - 1] if min(days) > start + timedelta(days=31) else None
    ch = change(pct, FOND_THR, back=(3,), ratio=True)

    peak_m = MONTHS[peak_day.month - 1]
    near = next((f"avant {nm}" for d, nm in _races(races) if 0 <= (d - peak_day).days <= 28), None) or next(
        (f"avec {nm}" for d, nm in _races(races) if 1 <= (peak_day - d).days <= 7), None)
    if now >= 98:
        line = "Ton fond n'a jamais été aussi haut " + (f"depuis {since}." if since else "cette année.")
    else:
        head = f"Ton pic date {_de(peak_m)}" + (f" ({near})" if near else "")
        last_pts = [v for v in pct if v is not None]
        rising = len(last_pts) >= 2 and last_pts[-1] > last_pts[-2]
        tail = (" ; tu es juste sous ton pic" if peak_day > today - timedelta(days=45)
                else " ; tu remontes" if ch and ch["real"] and ch["delta"] > 0 and rising
                else " ; tu t'entraînes moins ces derniers mois" if ch and ch["real"] and ch["delta"] < 0 else "")
        line = head + tail + "."
    card = _card("fond", "Ton fond", "12 mois", f"{num(now)} %",
                 f"de ton meilleur niveau depuis {since}" if since else "de ton meilleur niveau de l'année",
                 ch=chip(ch, lambda r: f"{signed(r * 100)} %"), line=line,
                 chart=curve(pct, [], months, races, today, lambda v: f"{num(v)} %", "Ton fond",
                             steps=(5, 10, 20, 25, 50), min_span=20))
    return card, None, {"raw": raw, "pct": pct}


def _easy(sessions, months, today: date, races) -> tuple[dict | None, str | None, dict | None]:
    """FC à allure facile: HR at the reference pace, month by month."""
    runs = st.easy_runs(sessions, st.hr_max(sessions, today))
    start = _start(months)
    n = sum(1 for s in runs if start <= s.day <= today)
    em = st.easy_hr_months(runs, today)
    points = [em["months"].get(m) for m in months] if em else [None] * N_MONTHS
    ok = _count(points)
    label = "FC à allure facile"
    if ok < MIN_MONTHS:
        return None, locked_line(label, n, "sortie facile et plate", "sorties faciles et plates", ok), None
    p = pace_txt(em["pace_s"])
    year = [s for s in runs if s.day > today - timedelta(days=365)]
    _, b = st.theil_sen([s.speed for s in year], [s.hr for s in year])  # the slope easy_hr_months used
    ref = 1000 / em["pace_s"]
    raw = [(s.day, s.hr - b * (s.speed - ref)) for s in year if s.day >= start]
    ch = change(points, EASY_THR, hold=2)
    line = _line(ch, months,
                 up=f"Depuis {{m}}, ton cœur monte plus haut à {p} : fatigue, chaleur ou fond en baisse vont souvent avec.",
                 down=f"Depuis {{m}}, ton cœur travaille moins pour courir à {p} : signe de forme.",
                 stable=f"Même effort cardiaque à {p} qu'en {{m}}.")
    last = [v for v in points if v is not None][-1]
    card = _card("easy_hr", label, f"{n} sorties", num(last), _unit(f"bpm à {p}", _stale(points, months)),
                 ch=chip(ch, lambda d: f"{signed(d)} bpm", better_down=True), line=line,
                 caption="plus bas = plus en forme",
                 chart=curve(points, raw, months, races, today, lambda v: num(v), label))
    return card, None, {"points": points, "pace": p}


def _vo2(metrics, details, months, today: date, races) -> tuple[dict | None, str | None, list | None]:
    """VO2 max (montre) and the threshold pace."""
    vals = {d: v for d, v in (metrics.get("vo2max") or {}).items() if v is not None and 10 <= v <= 95 and d <= today}
    fit = details.get("fitness") or {}
    for d, det in fit.items():
        v = (det or {}).get("vo2max")
        if d not in vals and v is not None and 10 <= v <= 95 and d <= today:
            vals[d] = v
    label = "VO2 max (montre)"
    caption = "estimation de la montre, à ±10 près : regarde la direction"
    win = _in(vals, months, today)
    if not win:
        return None, f"{label} : aucune mesure en 12 mois", None
    seuil = [(d, det["threshold_s"]) for d, det in fit.items()
             if det and 120 <= (det.get("threshold_s") or 0) <= 900 and today - timedelta(days=365) <= d <= today]
    sub = f"Allure seuil {pace_txt(max(seuil)[1])}" if seuil else None
    first = min(vals)
    span = (today.year - first.year) * 12 + today.month - first.month - (today.day < first.day)
    points = monthly(win, months, today=today)
    latest = vals[max(vals)]
    if span < VO2_HISTORY_MONTHS:
        return _card("vo2", label, None, num(latest), sub=sub, caption=caption,
                     note=f"Historique depuis le {first.day} {MONTHS[first.month - 1]} : "
                          f"la tendance s'affiche à {VO2_HISTORY_MONTHS} mois."), None, None
    if _count(points) < MIN_MONTHS:
        if max(vals) >= today - timedelta(days=60):
            return _card("vo2", label, None, num(latest), sub=sub, caption=caption,
                         note=f"Trop peu de mesures par mois pour une courbe (il en faut {MIN_PER_MONTH})."), None, None
        return None, locked_line(label, len(win), "mesure", "mesures", _count(points)), None
    ch = change(points, VO2_THR)
    line = _line(ch, months, up="Ta montre te voit progresser depuis {m}.",
                 down="Ta montre te voit baisser depuis {m} ; chaleur, dénivelé et fatigue la font aussi baisser.",
                 stable="Ta montre ne voit pas de changement net depuis {m}.")
    last = [v for v in points if v is not None][-1]
    card = _card("vo2", label, "12 mois", num(last), _unit(_stale(points, months)), sub=sub,
                 ch=chip(ch, lambda d: signed(d)), line=line, caption=caption,
                 chart=curve(points, sorted(win.items()), months, races, today, lambda v: num(v), label, min_span=4))
    return card, None, points


def _night_card(key, label, values, months, today, races, *, unit, thr, lines, better_down=False, caption=None,
                one="nuit", many="nuits"):
    """A heart-rate curve from one value a day (FC au repos, FC la plus basse de la journée)."""
    points = monthly(values, months, today=today)
    ok = _count(points)
    if ok < MIN_MONTHS:
        return None, locked_line(label, len(values), one, many, ok)
    ch = change(points, thr, hold=2)
    last = [v for v in points if v is not None][-1]
    card = _card(key, label, f"{len(values)} {many}", num(last), _unit(unit, _stale(points, months)),
                 ch=chip(ch, lambda d: f"{signed(d)} {unit}", better_down=better_down),
                 line=_line(ch, months, *lines), caption=caption,
                 chart=curve(points, sorted(values.items()), months, races, today, lambda v: num(v), label))
    return card, None


HR_LINES = ("Plus haute qu'en {m} : fatigue, stress, chaleur ou virus la font souvent monter.",
            "Plus basse qu'en {m} : souvent un signe de forme.",
            "Même niveau qu'en {m} : rien à signaler.")


def _hrv(values, months, today, races):
    label = "VFC (nuit)"
    win = {d: v for d, v in values.items() if v > 0}
    ln = {d: math.log(v) for d, v in win.items()}
    pl = monthly(ln, months, today=today)
    ok = _count(pl)
    if ok < MIN_MONTHS:
        return None, locked_line("VFC", len(win), "nuit", "nuits", ok)
    sd = max(statistics.stdev(ln.values()) if len(ln) >= 2 else 0.0, HRV_SD_FLOOR)
    ch = change(pl, HRV_SD_K * sd, hold=2)
    points = [math.exp(v) if v is not None else None for v in pl]
    last = [v for v in points if v is not None][-1]
    line = _line(ch, months, "Plus haute qu'en {m} : va souvent avec une meilleure récupération.",
                 "Plus basse qu'en {m} : charge, nuits courtes ou stress la font souvent baisser.",
                 "Même niveau qu'en {m} : rien à signaler.")
    card = _card("hrv", label, f"{len(win)} nuits", num(last), _unit("ms", _stale(points, months)),
                 ch=chip(ch, lambda d: f"{signed((math.exp(d) - 1) * 100)} %"), line=line,
                 chart=curve(points, sorted(win.items()), months, races, today, lambda v: num(v), label, min_span=10))
    return card, None


def _sleep(values, scores, months, today, races):
    label = "Sommeil"
    points = monthly(values, months, agg=statistics.fmean, today=today)
    ok = _count(points)
    if ok < MIN_MONTHS:
        return None, locked_line(label, len(values), "nuit", "nuits", ok)
    sc = monthly(scores, months, agg=statistics.fmean, today=today)
    li = max(i for i, v in enumerate(points) if v is not None)
    ch = change(points, SLEEP_THR, hold=2)
    line = _line(ch, months, "Tu dors plus qu'en {m}.", "Tu dors moins qu'en {m}.",
                 "Tes nuits durent autant qu'en {m}.")
    card = _card("sleep", label, f"{len(values)} nuits", hm(points[li]),
                 _unit(_stale(points, months), f"· score {num(sc[li])}" if sc[li] is not None else ""),
                 ch=chip(ch, lambda d: f"{signed(d)} min"), line=line,
                 chart=curve(points, sorted(values.items()), months, races, today, hm, label,
                             steps=(15, 30, 60, 120), min_span=60))
    return card, None


# ── « Ce qui va ensemble » ──────────────────────────────────────────────────

def _links(fond: dict | None, easy: dict | None, vo2: list | None, months, races, today: date
           ) -> tuple[list[dict], str | None]:
    """L2 then L3 then L1 (never L1 next to L2): fond against easy-pace HR,
    else the watch's VO2 max, on the same two months."""
    if not fond:
        return [], None
    series = []
    if easy:
        series.append(("hr", easy["points"]))
    if vo2:
        series.append(("vo2", vo2))
    raw, pct = fond["raw"], fond["pct"]
    p = easy["pace"] if easy else ""
    racing = _races(races)

    def frame(pts, span):
        common = [i for i in range(N_MONTHS) if raw[i] is not None and pts[i] is not None]
        if len(common) < 4 or common[-1] < N_MONTHS - 2:
            return None
        e = common[-1]
        s = e - span
        if s < 0 or raw[s] is None or pts[s] is None:
            return None
        return s, e

    deltas: dict[tuple[str, int], tuple[int, int, float, float]] = {}
    for key, pts in series:
        for span in (2, 3):
            fr = frame(pts, span)
            if fr:
                s, e = fr
                deltas[(key, span)] = (s, e, raw[e] / raw[s] - 1, pts[e] - pts[s])
    if not deltas:
        return [], None

    def hr_d(span):
        return deltas[("hr", span)][3] if ("hr", span) in deltas else None

    def hit(rule, key, span):
        if (key, span) not in deltas:
            return False
        s, e, g, d = deltas[(key, span)]
        h = hr_d(span)
        if rule == "pay":
            return g >= 0.10 and (d <= -2 if key == "hr" else d >= 2 and not (h is not None and h >= 3))
        if rule == "heart":
            return g >= 0.10 and (d >= 3 if key == "hr" else d <= -2 and not (h is not None and h <= -2))
        end = _month_end(months[e], today)
        quiet = not any(end - timedelta(days=42) < rd <= end or today < rd <= today + timedelta(days=21)
                        for rd, _ in racing)
        return quiet and g <= -0.20 and (d >= 3 if key == "hr" else d <= -2 and not (h is not None and h <= -2))

    out = []
    for rule, span in (("heart", 2), ("detrain", 2), ("pay", 3)):
        if rule == "pay" and any(o["key"] == "heart" for o in out):
            continue
        key = next((k for k, _ in series if hit(rule, k, span)), None)
        if key is None:
            continue
        s, e, g, d = deltas[(key, span)]
        since = MONTHS[months[s][1] - 1]
        if key == "hr":
            what, d_txt, name, t0, t1 = f"ta FC à {p}", f"{signed(d)} bpm", "FC facile", num(
                easy["points"][s]), f"{num(easy['points'][e])} bpm"
        else:
            what, d_txt, name, t0, t1 = "ton VO2 max (montre)", signed(d), "VO2 max", num(vo2[s]), num(vo2[e])
        # the fond's size is printed on the slope chart: the sentence gives the other change
        if rule == "pay":
            title = "Tes heures paient"
            moved = "a baissé de" if key == "hr" else "a gagné"
            text = (f"Depuis {since}, ton fond monte et {what} {moved} {d_txt.lstrip('+−')} : "
                    "sans doute tes heures qui paient.")
        elif rule == "heart":
            title = "Ton cœur ne suit pas"
            text = (f"Ton fond monte depuis {since} mais {what} " + ("aussi" if key == "hr" else "baisse")
                    + f" ({d_txt}) : ton cœur ne suit pas — essaie une semaine plus légère et regarde "
                    + ("si elle redescend." if key == "hr" else "s'il remonte."))
        else:
            title = "Ton fond s'érode"
            text = (f"Ton fond baisse depuis {since} et {what} " + ("remonte" if key == "hr" else "aussi")
                    + f" ({d_txt}) : ta forme s'érode un peu.")
        out.append({"key": rule, "title": title, "text": text,
                    "slope": slope((MONTHS_SHORT[months[s][1] - 1], MONTHS_SHORT[months[e][1] - 1]),
                                   [("Fond", pct[s], pct[e], f"{num(pct[s])} %", f"{num(pct[e])} %", "accent"),
                                    (name, (easy["points"] if key == "hr" else vo2)[s],
                                     (easy["points"] if key == "hr" else vo2)[e], t0, t1, "ink")]),
                    "foot": f"chez toi, {e - s + 1} mois · {CAUTION}"})
        if len(out) == 2:
            break
    order = {"pay": 0, "heart": 1, "detrain": 2}
    out.sort(key=lambda o: order[o["key"]])
    if out:
        return out, None
    other = "ta FC à allure facile" if any(k == "hr" for k, _ in deltas) else "ton VO2 max (montre)"
    return [], f"Pas de lien net ces derniers mois entre ton fond et {other}."


# ── the tab ─────────────────────────────────────────────────────────────────

def trends_tab(sessions: list[st.Session], metrics: dict, details: dict, races, today: date,
               ctl_series: dict | None) -> dict:
    """{"cards", "links", "links_note", "locked", "locked_n"} for the Tendances
    tab. `ctl_series` is sante_training.fitness() ({} → computed here from the
    sessions' loads); HRmax comes from sante_training.hr_max(sessions, today)."""
    metrics, details = metrics or {}, details or {}
    months = months_axis(today)
    series = {d: v for d, v in (ctl_series or {}).items() if d <= today}
    if not series and sessions:
        series = st.fitness(st.daily_loads(sessions), today)
    cards, locked, locked_n = [], [], 0

    def add(card, lock, n=1):
        nonlocal locked_n
        if card:
            cards.append(card)
        elif lock:
            locked.append(lock)
            locked_n += n

    fc, fl, fond = _fond(series, months, today, races)
    add(fc, fl)
    ec, el, easy = _easy(sessions or [], months, today, races)
    add(ec, el)
    vc, vl, vo2 = _vo2(metrics, details, months, today, races)
    add(vc, vl)

    rhr = _in(metrics.get("rhr"), months, today, 25, 100)
    hrv = _in(metrics.get("hrv"), months, today, 5, 300)
    sleep = _in(metrics.get("sleep"), months, today, 120, 960)
    if not (rhr or hrv or sleep):
        add(None, "VFC, FC au repos, sommeil : aucune nuit mesurée en 12 mois", 3)
        rc = None
    else:
        rc, rl = _night_card("rhr", "FC au repos (nuit)", rhr, months, today, races, unit="bpm", thr=RHR_THR,
                             lines=HR_LINES, better_down=True)
        add(rc, rl and rl.replace(" (nuit)", ""))
        add(*_hrv(hrv, months, today, races))
        add(*_sleep(sleep, _in(metrics.get("sleep_score"), months, today, 1, 100), months, today, races))
    if rc is None:
        lows = {d: (det or {}).get("min") for d, det in (details.get("hr_day") or {}).items()}
        low = _in({d: v for d, v in lows.items() if v is not None}, months, today, 25, 120)
        if low:
            card, _ = _night_card("hr_day_min", "FC la plus basse de la journée", low, months, today, races,
                                  unit="bpm", thr=DAYMIN_THR, lines=HR_LINES, better_down=True, one="jour", many="jours",
                                  caption="mesurée le jour, pas la nuit : compare-la seulement à elle-même")
            if card:
                cards.append(card)

    links, note = _links(fond, easy, vo2, months, races, today)
    return {"cards": cards, "links": links, "links_note": note, "locked": locked, "locked_n": locked_n}
