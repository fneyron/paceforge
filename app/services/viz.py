"""Server-side geometry and French formatting for every interactive chart.

The server computes all geometry and every string, once; the Jinja macros in
partials/_viz.html draw the SVG; app/static/js/pf-viz.js only moves the
selection (no colour, no number formatting, no chart library in JS).

Each builder returns a plain dict with the geometry the macro needs and a
`data` JSON string for pf-viz.js:
    {x: [viewBox x per point], y: [[viewBox y | null per point] per series],
     d: [ISO date per point], r: [[date, value, context] readout strings],
     a: [full-sentence aria strings], h: [link | null], sel: default index,
     link: optional selector of a panel holding [data-night] children}
`d` is what links figures of one group (data-viz-group): selecting a day in
one selects the same date in the others, silently.

Formats (the house formats, one per kind, used everywhere):
- durations: « 5h50 », « 2h20 », « 45 min » (hm, rounded to the minute); spoken « 5 heures 50 »;
- climbs: « +2 401 m » (dplus);
- clock times: « 23:35 », rounded to 5 min for sleep times (approximate);
- numbers: decimal comma, true minus U+2212, narrow no-break space U+202F
  before units: « 58 ms », « −3 bpm »; a plain no-break space U+00A0 in the
  large readouts (a card's value, the habits, a ring): the display fonts have
  no U+202F, so it would glue the unit to the number (« 53ms »);
- dates: « mar. 6 oct. »; spoken « mardi 6 octobre ».
Layout: viewBox width 320 (the 358 px phone's content width), plot 0–288, the
y ticks right-aligned at 320; one slot per day/week, marks at slot centres so
stacked figures share an x axis.

Santé v4 (one page) adds: `ring` (the Récupération ring at the top),
`day_bars` (a card's bars, one per day: Récupération, Sommeil),
`night_card` (a nightly signal's dots, 7-night line and normal: VFC, FC de
nuit) and `timeline` (last night on a clock axis, or its hypnogram). The race
page and Activités keep `band_chart`, `bars`, `lines` and `dots`. No mark
without a label or a legend: the ring says its value and a word names it,
the cards' bars and lines have a legend or labelled lines.
"""
import json
import math
import statistics
from datetime import date, datetime, time, timedelta

NNBSP = " "
NBSP = " "
MINUS = "−"
JOURS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
JOURS_L = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
MOIS_L = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre")
W, X1 = 320, 288
GLYPH = {"race": "⚑", "long": "◆", "tag": "◇", "up": "▲", "down": "▼", "in": "●"}


# ── formats ─────────────────────────────────────────────────────────────────

def num(v: float, digits: int = 0, unit: str = "") -> str:
    """58.4 → « 58 », (1.25, 1) → « 1,3 », (−3, unit bpm) → « −3 bpm »."""
    s = f"{v:.{digits}f}".replace(".", ",").replace("-", MINUS)
    if s in (f"{MINUS}0", f"{MINUS}0,0"):
        s = s[1:]
    return f"{s}{NNBSP}{unit}" if unit else s


def signed(v: float, digits: int = 0, unit: str = "") -> str:
    """« +3 bpm », « −3 bpm », « 0 bpm »."""
    out = num(v, digits, unit)
    return ("+" + out) if round(v, digits) > 0 else out


def hm(minutes: float) -> str:
    """The house duration: 350 → « 5h50 », 45 → « 45 min »."""
    m = int(round(minutes))
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m} min"


def dplus(metres: float) -> str:
    """A week's or a run's climb: « +2 401 m » (rounded; the list's week headings and A1 print it alike)."""
    return "+" + f"{int(round(metres)):,}".replace(",", NNBSP) + f"{NNBSP}m"


def hm_long(minutes: float) -> str:
    """Spoken: 350 → « 5 heures 50 », 65 → « 1 heure 05 », 45 → « 45 minutes »."""
    m = int(round(minutes))
    if m < 60:
        return f"{m} minute{'s' if m > 1 else ''}"
    h = m // 60
    return f"{h} heure{'s' if h > 1 else ''} {m % 60:02d}"


def round5(t: datetime) -> datetime:
    m = round((t.hour * 60 + t.minute + t.second / 60) / 5) * 5
    return datetime.combine(t.date(), time(0)) + timedelta(minutes=m)


def clock(t: datetime, five: bool = True) -> str:
    """« 23:35 » (to the nearest 5 min by default: the watch's times are approximate)."""
    t = round5(t) if five else t
    return f"{t.hour:02d}:{t.minute:02d}"


def d_short(d: date) -> str:
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def d_long(d: date) -> str:
    return f"{JOURS_L[d.weekday()]} {d.day} {MOIS_L[d.month - 1]}"


def d_day(d: date) -> str:
    """Axis label: « mar. 6 »."""
    return f"{JOURS[d.weekday()]} {d.day}"


def night_label(d: date) -> str:
    """« nuit du lun. 5 au mar. 6 » for the night ending on `d`."""
    return f"nuit du {d_day(d - timedelta(days=1))} au {d_day(d)}"


def sleep_readout(main: float | None, nap: float | None) -> str:
    """« Nuit 5h50 · sieste 2h20 · 8h10 sur 24 h »; without a nap « Nuit 7h20 sur 24 h »;
    without a main episode « Sieste 1h22 · pas de nuit mesurée »."""
    if main is None:
        return f"Sieste {hm(nap)} · pas de nuit mesurée" if nap else "—"
    if nap:
        return f"Nuit {hm(main)} · sieste {hm(nap)} · {hm(main + nap)} sur 24{NNBSP}h"
    return f"Nuit {hm(main)} sur 24{NNBSP}h"


def sleep_spoken(main: float | None, nap: float | None) -> str:
    if main is None:
        return f"sieste {hm_long(nap)}, pas de nuit mesurée" if nap else "pas de mesure"
    if nap:
        return f"nuit {hm_long(main)}, sieste {hm_long(nap)}, {hm_long(main + nap)} sur 24 heures"
    return f"{hm_long(main)} de sommeil sur 24 heures"


# ── helpers ─────────────────────────────────────────────────────────────────

def slot_x(n: int) -> list[float]:
    """The centre of each of n slots across the plot."""
    slot = X1 / max(n, 1)
    return [round(slot * (i + 0.5), 1) for i in range(n)]


def nice_ticks(lo: float, hi: float, n: int = 3) -> list[float]:
    """2–4 round values inside [lo, hi]."""
    span = hi - lo
    step = next((s for s in (1, 2, 5, 10, 20, 25, 30, 50, 60, 100, 120, 200, 500) if span / s <= n + 1), 1000)
    a = math.ceil(lo / step) * step
    return [a + i * step for i in range(int((hi - a) / step) + 1) if a + i * step <= hi]


def span_of(values, min_span: float, pad: float = 0.08) -> tuple[float, float]:
    """[lo, hi] covering the values, never narrower than `min_span` (noise must
    not look like a trend: HRV ≥ 20 ms, nightly HR ≥ 8 bpm, easy-pace HR ≥ 10)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return 0.0, float(min_span)
    lo, hi = min(vals), max(vals)
    if hi - lo < min_span:
        mid = (hi + lo) / 2
        lo, hi = mid - min_span / 2, mid + min_span / 2
    p = (hi - lo) * pad
    return lo - p, hi + p


def scale(lo: float, hi: float, y0: float, y1: float):
    """value → viewBox y (y0 at the top for hi, y1 at the bottom for lo)."""
    return lambda v: None if v is None else round(y1 - (v - lo) / ((hi - lo) or 1) * (y1 - y0), 1)


def rolling(values: list, i: int, k: int = 7, need: int = 3, log: bool = False) -> float | None:
    """Mean of the last k points ending at i (geometric when `log`), None under `need` values."""
    w = [v for v in values[max(0, i - k + 1): i + 1] if v is not None]
    if len(w) < need:
        return None
    return math.exp(statistics.fmean(math.log(v) for v in w)) if log else statistics.fmean(w)


def paths(xs, ys, lone: bool = True) -> str:
    """Polyline broken wherever a value is missing (never drawn across a gap).
    `lone=False` leaves out a point with no neighbour: a move alone draws
    nothing, so the path is empty when no segment is drawn (and its legend
    item can go with it)."""
    runs, run = [], []
    for x, y in zip(xs, ys):
        if y is None:
            if run:
                runs.append(run)
            run = []
            continue
        run.append(f"{'L' if run else 'M'}{x:.1f} {y:.1f}")
    if run:
        runs.append(run)
    return " ".join(" ".join(r) for r in runs if lone or len(r) > 1)


def band_polys(xs, lo, hi) -> list[str]:
    """The band as polygons, one per unbroken run (y already scaled)."""
    polys, run = [], []
    for x, a, b in list(zip(xs, lo, hi)) + [(None, None, None)]:
        if a is None or b is None:
            if len(run) > 1:
                top = " ".join(f"{x:.1f},{b:.1f}" for x, _, b in run)
                bot = " ".join(f"{x:.1f},{a:.1f}" for x, a, _ in reversed(run))
                polys.append(f"{top} {bot}")
            run = []
        else:
            run.append((x, a, b))
    return polys


def _data(xs, ys, days, r, a, h=None, sel=None, link=None, t=None) -> dict:
    """{data: the JSON pf-viz.js reads, read: the default readout (printed by
    the server: the chart is complete without JS), sel, n}. `t`: a tone per
    point (ok, warn, danger or ""), set on the figure as data-tone with the
    selection (the readout's word wears it: CSS, never JS colour)."""
    sel = (len(xs) - 1 if sel is None else sel) if xs else None
    h = h or [None] * len(xs)
    out = {"x": xs, "y": ys, "d": [d.isoformat() for d in days], "r": r, "a": a, "h": h, "sel": sel}
    if link:
        out["link"] = link
    if t:
        out["t"] = t
    data = json.dumps(out, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    # `links`: a slot has a link, so the figure reserves its row; `link_now`: the default slot's (printed by the server)
    return {"data": data, "read": r[sel] if xs else ["", "—", ""], "aria_now": a[sel] if xs else "", "sel": sel,
            "n": len(xs), "links": any(h), "link_now": h[sel] if xs else None,
            "tone_now": (t[sel] if t and xs and sel is not None and sel < len(t) else "") or ""}


def rest(c: dict, read: list[str], aria: str, back: int | None = None) -> dict:
    """Give a built figure a resting readout, shown until a touch (pf-viz.js
    D.rest: one step after the last point; Esc and End come back to it), with
    nothing selected. For a figure whose latest value another page or block
    already prints (Activités' week headings, Santé › Sommeil's last night):
    each number is printed once. `back`: where ‹ (or ←) from the rest lands
    when the last slots are still to come (default: the last point)."""
    data = json.loads(c["data"].replace("<\\/", "</"))
    data["rest"], data["restA"], data["sel"] = read, aria, len(data["x"])
    if back is not None:
        data["back"] = back
    c["data"] = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    c.update(read=read, aria_now=aria, sel=len(data["x"]), rest=True, link_now=None, tone_now="")
    return c


def x_labels(days: list[date], xs: list[float]) -> list[dict]:
    """Mondays as « lun. 5 » up to a month, the 1st of each month as « oct. » beyond; weekly slots:
    the first week of every other month."""
    n = len(days)
    if n > 1 and (days[1] - days[0]).days == 7:  # weekly slots: the first week of every other month
        out, prev = [], None
        for d, x in zip(days, xs):
            if d.month != prev and d.month % 2 == 1:
                out.append({"x": x, "label": MOIS[d.month - 1]})
            prev = d.month
        return out
    if n <= 31:
        return [{"x": x, "label": d_day(d)} for d, x in zip(days, xs) if d.weekday() == 0]
    return [{"x": x, "label": MOIS[d.month - 1]} for d, x in zip(days, xs) if d.day == 1 and (n <= 120 or
                                                                                             d.month % 2 == 1)]


def events(days: list[date], xs: list[float], races=(), longs=()) -> dict:
    """⚑ races (a labelled flag + hairline) and ◆ long outings (under the axis)."""
    idx = {d: i for i, d in enumerate(days)}
    return {"races": [{"x": xs[idx[d]], "name": nm} for d, nm in races if d in idx],
            "longs": [{"x": xs[idx[d]]} for d in longs if d in idx]}


def _context(d: date, races=(), longs=(), tags: dict | None = None) -> list[str]:
    out = [f"{GLYPH['race']} {nm}" for dd, nm in races if dd == d]
    out += [f"{GLYPH['long']} sortie longue" for dd in longs if dd == d]
    out += [f"{GLYPH['tag']} {w}" for w in (tags or {}).get(d, [])]
    return out


# ── K2 band chart: one to three stacked panels on one x axis ────────────────

PANEL_H, PANEL_GAP, FLAG = 92, 18, 22


def band_chart(key: str, days: list[date], panels: list[dict], *, races=(), longs=(), tags=None, sel=None,
               title: str = "", slot_labels: list[tuple[str, str]] | None = None, unit_word: str = "nuits",
               labels: bool = True) -> dict:
    """Stacked panels sharing one x axis, one scrub and one readout (« Cœur la
    nuit »: VFC above, FC de nuit below, respiration as a slim third panel).
    Each panel: {name, unit, unit_long, values: [per day], band: [(lo, hi) |
    None per day], mean: [7-night mean | None per day] (drawn, never printed),
    min_span, digits, up_is: "warn" | "muted" (what « au-dessus » means), h,
    judge: [bool per day] (optional: a night drawn but never judged, e.g. in
    J-7 → J+7 around a race, gets no out-of-band word nor hollow dot), prov:
    [bool per day] (optional: a « provisoire » band, 7 to 13 nights, H: its
    normal and its words say « (provisoire) »)}.
    Readout: [date, « VFC 58 ms · FC 46 bpm », « normale 55–63 · ◇ alcool »];
    an out-of-band night is a hollow dot and the word « au-dessus » /
    « en dessous », never colour alone. A night with a context tag (`tags`)
    is drawn as a small diamond (◇). `slot_labels`: [(readout, spoken)] per
    slot when the slots are not nights (the weekly « 1 an » view).
    `labels=False` draws the band without judging any night, as if every
    panel's `judge` were False (the race window: no flag, evidence rows 24–25)."""
    n = len(days)
    xs = slot_x(n)
    out_panels, y_series = [], []
    top = FLAG
    for p in panels:
        h = p.get("h", PANEL_H)
        lo_b = [b[0] if b else None for b in p["band"]]
        hi_b = [b[1] if b else None for b in p["band"]]
        lo, hi = span_of(p["values"] + lo_b + hi_b + p.get("mean", []), p.get("min_span", 0))
        y = scale(lo, hi, top, top + h)
        yv = [y(v) for v in p["values"]]
        dots = []
        judge = [False] * n if not labels else (p.get("judge") or [True] * n)
        for i, v in enumerate(p["values"]):
            if v is None:
                continue
            b = p["band"][i] if judge[i] else None
            out = "up" if b and v > b[1] else "down" if b and v < b[0] else None
            dots.append({"x": xs[i], "y": yv[i], "out": out, "i": i, "tag": bool((tags or {}).get(days[i]))})
        out_panels.append({
            "name": p["name"], "top": top, "bottom": top + h,
            "band": band_polys(xs, [y(v) for v in lo_b], [y(v) for v in hi_b]),
            "edge_lo": paths(xs, [y(v) for v in lo_b]), "edge_hi": paths(xs, [y(v) for v in hi_b]),
            "mean": paths(xs, [y(v) for v in p.get("mean", [None] * n)]), "dots": dots,
            "ticks": [{"y": y(t), "label": num(t)} for t in nice_ticks(lo, hi, 2)],
            "label_y": top + 11,
        })
        y_series.append(yv)
        top += h + PANEL_GAP
    H = top - PANEL_GAP + 20
    r, a = [], []
    for i, d in enumerate(days):
        vals, normals, spoken = [], [], []
        label, said = slot_labels[i] if slot_labels else (night_label(d), d_long(d))
        for p in panels:
            v, b, dg = p["values"][i], p["band"][i], p.get("digits", 0)
            if v is None:
                continue
            word = ""
            judged = labels and (p.get("judge") or [True] * n)[i]
            prov = bool(b and (p.get("prov") or [False] * n)[i])
            if judged and b and v > b[1]:
                word = " au-dessus" + (" (provisoire)" if prov else "")
            elif judged and b and v < b[0]:
                word = " en dessous" + (" (provisoire)" if prov else "")
            vals.append(f"{p['name']} {num(v, dg, p['unit'])}{word}")
            if b:
                normals.append((f"{p['name']} {num(b[0], dg)}–{num(b[1], dg)}" if len(panels) > 1
                                else f"{num(b[0], dg)}–{num(b[1], dg)}", prov))
            spoken.append(f"{p['name']} {num(v, dg)} {p['unit_long']}{word}"
                          + (f", ta normale{' provisoire' if prov else ''} {num(b[0], dg)} à {num(b[1], dg)}"
                             if b else ""))
        ctx = _context(d, races, longs, tags)
        if not vals:
            r.append([label, "—", " · ".join(["pas de montre cette nuit" if not slot_labels else "pas assez de nuits"]
                                             + ctx)])
            a.append(". ".join([f"{said} : pas de mesure"] + ctx))
            continue
        # « normale 55–63 », « normale provisoire VFC 55–63, FC 44–50 » (7 to 13 nights, H), a mix said per panel
        if not normals:
            normal = ""
        elif all(pv for _, pv in normals) or not any(pv for _, pv in normals):
            normal = ("normale provisoire " if normals[0][1] else "normale ") + ", ".join(t for t, _ in normals)
        else:
            normal = "normale " + ", ".join(t + (" (provisoire)" if pv else "") for t, pv in normals)
        r.append([label, " · ".join(vals), " · ".join(([normal] if normal else []) + ctx)])
        a.append(f"{said} : " + ", ".join(spoken) + "".join(f". {c}" for c in ctx))
    ev = events(days, xs, races, longs)
    measured = sum(1 for i in range(n) if any(p["values"][i] is not None for p in panels))
    return {"key": key, "n": n, "W": W, "H": H, "X1": X1, "panels": out_panels, "xt": x_labels(days, xs), **ev,
            "dot_r": 2.5 if n <= 31 else 1.6 if n <= 120 else 1.0, "flag_y": FLAG - 3,
            "summary": f"{title}, {n} {unit_word} : {measured} mesuré{'e' if unit_word == 'nuits' else ''}"
                       f"{'s' if measured > 1 else ''}",
            **_data(xs, y_series, days, r, a, sel=sel)}


# ── K7 bars: weeks, taper (planned vs done), 24-h nights on the race page ───

BARS = (14, 132)


def bars(days: list[date], values: list, *, readouts: list[list[str]], arias: list[str], links=None,
         secondary: list | None = None, planned: list | None = None, target: tuple | None = None,
         reference: float | None = None, races=(), longs=(), min_top: float = 0, sel=None,
         label_fmt=None) -> dict:
    """Vertical bars, one per slot (a week, a day): `values` (None = empty
    slot), an optional thin second mark (`secondary`, e.g. D+, on its own
    scale under the bars), `planned` bars drawn as outlines behind, a `target`
    band (lo, hi) in the values' unit (the taper's −41–60 %), a `reference`
    line. Links go through the readout (`links`), never inside the SVG."""
    n = len(days)
    xs = slot_x(n)
    slot = X1 / max(n, 1)
    bw = round(min(18, slot * 0.62), 1)
    vals = [v for v in values + (planned or []) if v is not None] + [min_top] + list(target or ())
    if reference is not None:
        vals.append(reference)
    y = scale(0, max(vals) * 1.08 or 1, BARS[0], BARS[1])
    out = []
    for i, v in enumerate(values):
        b = {"i": i, "x": round(xs[i] - bw / 2, 1), "w": bw}
        if v is not None:
            b.update(y=y(v), h=round(BARS[1] - y(v), 1))
        if planned and planned[i] is not None:
            b["plan"] = {"y": y(planned[i]), "h": round(BARS[1] - y(planned[i]), 1)}
        out.append(b)
    sec = None
    if secondary:
        smax = max([v for v in secondary if v is not None] or [1])
        sec = [{"x": round(xs[i] - bw / 2, 1), "w": bw, "h": round(8 * v / smax, 1)} for i, v in enumerate(secondary)
               if v]
    ev = events(days, xs, races, longs)
    labels = [{"x": xs[i], "label": (label_fmt or d_day)(d)} for i, d in enumerate(days) if (n - 1 - i) % 2 == 0]
    return {"n": n, "W": W, "H": 168, "X1": X1, "bars": out, "secondary": sec, "base": BARS[1],
            "target": {"y": y(target[1]), "h": round(y(target[0]) - y(target[1]), 1)} if target else None,
            "reference": y(reference) if reference is not None else None, "xt": labels, **ev,
            **_data(xs, [], days, readouts, arias, h=links, sel=sel)}


# ── K2 variant: two directly labelled lines (fond et fatigue) ───────────────

def lines(days: list[date], series: list[dict], *, races=(), sel=None, title: str = "") -> dict:
    """Two (or more) lines on one scale, each labelled at its right end, no y
    numbers; the readout is the date only (no ratio, no %). Each series:
    {name, values, cls}."""
    n = len(days)
    xs = slot_x(n)
    lo, hi = span_of([v for s in series for v in s["values"]], 1)
    lo = min(lo, 0)
    y = scale(lo, hi, 16, 120)
    out = []
    for s in series:
        ys = [y(v) for v in s["values"]]
        last = max((i for i, v in enumerate(ys) if v is not None), default=None)
        out.append({"name": s["name"], "cls": s.get("cls", ""), "path": paths(xs, ys),
                    "end": {"x": xs[last], "y": ys[last]} if last is not None else None})
    r = [[d_short(d), "", ""] for d in days]
    a = [f"{d_long(d)}" for d in days]
    ev = events(days, xs, races)
    return {"n": n, "W": W, "H": 140, "X1": X1, "series": out, "xt": x_labels(days, xs), **ev,
            "summary": title, **_data(xs, [[y(v) for v in s["values"]] for s in series], days, r, a, sel=sel)}


# ── K8 dots: one per run (FC en footing) ────────────────────────────────────

def dots(days: list[date], points: list[dict], *, band: list | None = None, min_span: float = 10, sel=None,
         unit: str = "bpm", unit_long: str = "battements par minute") -> dict:
    """One dot per run on a day axis: {day, value, hot (hollow, with the word),
    href, label}; `band`: [(lo, hi) | None per day] (rolling median ± 3 bpm, H).
    The readout steps run by run."""
    pts = sorted(points, key=lambda p: p["day"])
    idx = {d: i for i, d in enumerate(days)}
    xs_all = slot_x(len(days))
    lo_b = [b[0] if b else None for b in (band or [None] * len(days))]
    hi_b = [b[1] if b else None for b in (band or [None] * len(days))]
    lo, hi = span_of([p["value"] for p in pts] + lo_b + hi_b, min_span)
    y = scale(lo, hi, 16, 120)
    out, xs, ys, ds, r, a, h = [], [], [], [], [], [], []
    for k, p in enumerate(pts):
        if p["day"] not in idx:
            continue
        x = xs_all[idx[p["day"]]]
        out.append({"i": len(xs), "x": x, "y": y(p["value"]), "hot": bool(p.get("hot"))})
        xs.append(x)
        ys.append(y(p["value"]))
        ds.append(p["day"])
        word = " · journée chaude" if p.get("hot") else ""
        r.append([d_short(p["day"]), num(p["value"], 0, unit), (p.get("label") or "") + word])
        a.append(f"{d_long(p['day'])} : {num(p['value'])} {unit_long}" + (", journée chaude" if p.get("hot") else ""))
        h.append(p.get("href"))
    return {"n": len(xs), "W": W, "H": 140, "X1": X1, "dots": out,
            "band": band_polys(xs_all, [y(v) for v in lo_b], [y(v) for v in hi_b]),
            "ticks": [{"y": y(t), "label": num(t)} for t in nice_ticks(lo, hi, 2)],
            "xt": x_labels(days, xs_all), **_data(xs, [ys], ds, r, a, h=h, sel=sel)}


# ── V1 ring: Santé's Récupération ring (v4.4: the only one) ─────────────────

RING_R, RING_W = 42, 11  # viewBox 100 × 100: the radius and a bold stroke
RING_C = round(2 * math.pi * RING_R, 2)


def ring(key: str, fill: float | None, value: str, label: str | None, sub: str | None = None, *,
         tone: str = "accent", href: str | None = None, aria: str = "", note: str | None = None) -> dict:
    """One ring: the track, an arc up to `fill` (0–1, from the top, clockwise;
    None or 0: the track alone), the value in the middle (formatted by the
    caller, printed once), the label under it (None when a word next to the
    ring names it: Santé's state) and a short word (`sub`), and `note`, a
    word that says what the arc compares (« plus que d'habitude »): no mark
    without words. `tone` (ok, warn, danger, accent) is the CSS's colour; the
    value and the words say it too, never colour alone. `href`: what it sums
    up (None: a plain ring, never a link to nothing)."""
    f = 0.0 if fill is None else max(0.0, min(1.0, fill))
    dash = round(f * RING_C, 2)
    return {"key": key, "value": value, "label": label, "sub": sub, "tone": tone, "href": href, "aria": aria,
            "r": RING_R, "w": RING_W, "c": RING_C, "dash": dash, "note": note}


# ── V2 card bars: one bar per day (Récupération, Sommeil, Charge) ───────────

def day_bars(key: str, days: list[date], values: list, *, readouts: list[list[str]], arias: list[str],
             stack: list | None = None, classes: list | None = None, links: list | None = None,
             y_max: float | None = None, reference: tuple[float, str] | None = None, lines=(),
             clip: float | None = None, tones: list | None = None, trend: list | None = None,
             today: int | None = None, sel=None, H: int = 128, summary: str = "", min_top: float = 0,
             hatched: list | None = None) -> dict:
    """Vertical bars, one per day (Santé's cards): `values` (None: an empty
    slot, a faint dot on the base line), an optional lighter part on top
    (`stack`: the naps over the night), a class per bar (`classes`: the
    state's tone), a fixed top (`y_max`: 100 for a score) or the data's, a
    `reference` line (value, label: « 7 h », Johnston 2020), faint `lines`
    [(value, label)] labelled on the right (the score's 40 and 70: the bands,
    never colour alone), `clip`: the top of the axis when one day dwarfs the
    others (that bar runs to the top with a break mark, its value in the
    readout), `tones` (a tone per day for the readout's word), `trend` (a
    value per day drawn as a line over faint bars: a long range's 7-night
    mean), today's day number on a disc (`today`), `hatched` (a bar drawn
    hatched and outlined in its colour: a score estimated without a night
    measured; the card's legend says so). Under the bars: the day of
    the month for 16 slots or fewer, Mondays or months beyond (x_labels). The
    readouts are the caller's ([value, word, the day · context], Santé's
    compact two lines); links go through the readout."""
    n = len(days)
    xs = slot_x(n)
    slot = X1 / max(n, 1)
    bw = round(max(1.4, min(14.0, slot * 0.64)), 1)
    tops = [(v or 0) + ((stack[i] or 0) if stack else 0) for i, v in enumerate(values)]
    if clip is not None:
        top = clip
    elif y_max is not None:
        top = y_max
    else:
        top = max(tops + [min_top, reference[0] if reference else 0, 1]) * 1.12
    base, y0 = H - 22, 8
    y = scale(0, top, y0, base)
    out = []
    for i, v in enumerate(values):
        b = {"i": i, "x": round(xs[i] - bw / 2, 1), "w": bw, "cx": xs[i], "cls": (classes[i] if classes else "") or "",
             "today": i == today, "est": bool(hatched and hatched[i] and v is not None)}
        nap = stack[i] if stack else None
        if v is None and not nap:
            b["miss"] = True
        elif clip is not None and tops[i] > clip:
            b.update(y=y0, h=round(base - y0, 1), brk=y0 + 9)  # past the axis: to the top, broken
        else:
            yv = y(v or 0)
            b.update(y=yv, h=round(base - yv, 1))
            if nap:
                yt = y((v or 0) + nap)
                b["top"] = {"y": yt, "h": round(yv - yt, 1)}
        out.append(b)
    if n <= 16:
        xt = [{"x": xs[i], "label": str(d.day), "today": i == today} for i, d in enumerate(days)]
    else:
        xt = x_labels(days, xs)
    return {"key": key, "n": n, "W": W, "H": H, "X1": X1, "base": base, "top": y0, "bars": out, "xt": xt,
            "hatched": sorted({b["cls"] for b in out if b["est"]}),
            "slot": round(slot, 2), "ref": {"y": y(reference[0]), "label": reference[1]} if reference else None,
            "lines": [{"y": y(v), "label": lab} for v, lab in lines], "rx": round(min(3.0, bw / 2), 1),
            # over measured days only, broken on any day without one: never a line with no bar under it
            "trend": paths(xs, [y(min(v, top)) if v is not None and values[i] is not None else None
                                for i, v in enumerate(trend)], lone=False) if trend else None,
            "summary": summary, **_data(xs, [], days, readouts, arias, h=links, sel=sel, t=tones)}


def day_ticks(days: list[date], xs: list[float]) -> list[dict]:
    """The day of the month under each slot (16 or fewer), else Mondays or months (x_labels)."""
    if len(days) <= 16:
        return [{"x": x, "label": str(d.day)} for d, x in zip(days, xs, strict=True)]
    return x_labels(days, xs)


# ── V3 night card: one nightly signal, a dot per night, the 7-night line, the normal ──

NOT_COUNTED = "ne compte pas"  # a hollow night's word in its readout: out of the usual values (v4.4)

def _band_runs(band: list, prov: list) -> tuple[list, list]:
    """The band split into its full and its provisional days ([(lo, hi) | None] each), every run of one kind
    carried one day into the next run of the other kind, so the two drawings meet with no gap."""
    full, temp = [None] * len(band), [None] * len(band)
    for i, b in enumerate(band):
        if not b:
            continue
        (temp if prov[i] else full)[i] = b
        if i and band[i - 1] and bool(prov[i - 1]) != bool(prov[i]):
            (temp if prov[i - 1] else full)[i] = b  # the earlier run reaches this day
    return full, temp


def night_card(key: str, days: list[date], values: list, *, band: list, prov: list, mean: list, unit: str,
               unit_long: str, name: str, digits: int = 0, min_span: float = 8, H: int = 132,
               counts: list | None = None) -> dict:
    """One nightly signal over the days (Santé's VFC and FC de nuit cards): a
    dot per measured night, never judged one by one (Buchheit 2014: ≈ 12 %
    night to night) — filled when the night counts toward the athlete's usual
    values, hollow when it does not (`counts`: False for a night out of the
    band, after a big effort, in another time zone, at altitude…: v4.4, owner:
    « comment matérialiser que c'est en cours de construction ? ») — the
    7-night mean as a line (drawn, never printed) over the measured nights
    only, broken on any night without one (never a line with no dot under it;
    "" when no segment is left: no legend item), the athlete's usual values
    as a band (the 60 days before each night, on that night's watch): solid
    edges from 14 nights, dashed edges and a lighter fill while provisional
    (7 to 13 nights, H; `band_prov`, `edge_prov_lo/hi`). Readout, two compact
    lines: [« 100 ms », the word « ne compte pas » for a hollow night, « nuit
    du mer. 7 au jeu. 8 · normale 85–110 »] (« … (provisoire) » from 7 to 13
    nights); the latest measured night is selected: its value is the card's.
    `min_span`: the y axis never narrower (noise must not look like a cliff).
    « (provisoire) » comes after the band's numbers: at 358 px the ellipsis
    only ever cuts it."""
    n = len(days)
    xs = slot_x(n)
    counts = counts or [True] * n
    lo_b = [b[0] if b else None for b in band]
    hi_b = [b[1] if b else None for b in band]
    mean = [m if v is not None else None for v, m in zip(values, mean, strict=True)]
    lo, hi = span_of(values + lo_b + hi_b + mean, min_span)
    top, bottom = 10, H - 22
    y = scale(lo, hi, top, bottom)
    yv = [y(v) for v in values]
    r, a = [], []
    for i, d in enumerate(days):
        v, b = values[i], band[i]
        if v is None:
            r.append(["—", "", f"{night_label(d)} · pas de mesure"])
            a.append(f"{night_label(d)} : pas de mesure")  # the readout's night, as for a measured one
            continue
        line, spoken = night_label(d), f"{night_label(d)} : {name} {num(v, digits)} {unit_long}"
        if not counts[i]:
            spoken += ", une nuit qui ne compte pas"
        if b:
            pv = bool(prov[i])
            line += f" · normale {num(b[0], digits)}–{num(b[1], digits)}{' (provisoire)' if pv else ''}"
            spoken += f", ta normale{' provisoire' if pv else ''} de {num(b[0], digits)} à {num(b[1], digits)}"
        r.append([f"{num(v, digits)}{NBSP}{unit}", "" if counts[i] else NOT_COUNTED, line])
        a.append(spoken)
    last = max((i for i, v in enumerate(values) if v is not None), default=None)
    measured = sum(1 for v in values if v is not None)
    full, temp = _band_runs(band, prov)
    ys = {k: ([y(b[0]) if b else None for b in runs], [y(b[1]) if b else None for b in runs])
          for k, runs in (("full", full), ("prov", temp))}
    return {"key": key, "n": n, "W": W, "H": H, "X1": X1, "top": top, "bottom": bottom,
            "slot": round(X1 / max(n, 1), 2),
            "band": band_polys(xs, *ys["full"]), "band_prov": band_polys(xs, *ys["prov"]),
            "edge_lo": paths(xs, ys["full"][0]), "edge_hi": paths(xs, ys["full"][1]),
            "edge_prov_lo": paths(xs, ys["prov"][0]), "edge_prov_hi": paths(xs, ys["prov"][1]),
            "mean": paths(xs, [y(v) for v in mean], lone=False),
            "dots": [{"i": i, "x": xs[i], "y": yv[i], "out": not counts[i]} for i, v in enumerate(values)
                     if v is not None],
            # the selected night's place, drawn by the server too (no ring in a corner before pf-viz.js moves it)
            "at": {"x": xs[last], "y": yv[last]} if last is not None else None,
            "dot_r": 2.6 if n <= 31 else 1.6,
            "ticks": [{"y": y(t), "label": num(t)} for t in nice_ticks(lo, hi, 2)], "xt": day_ticks(days, xs),
            "summary": f"{name}, {n} nuits : {measured} mesurée{'s' if measured > 1 else ''}",
            "title": f"{name} · {n} nuits", **_data(xs, [yv], days, r, a, sel=last)}


# ── V4 timeline: last night on a clock axis (a bar, or the hypnogram) ───────

TL_X0, TL_X1 = 60, 302  # the lane names on the left; the last hour's label centred inside the 320 width
TL_LANE, TL_GAP, TL_MAIN = 12, 6, 22
# the hypnogram's lanes, top to bottom (Garmin's sleepLevels kinds; « core » is light sleep), named as the legend
STAGE_LANES = (("Éveil", "awake"), ("Paradoxal", "rem"), ("Léger", "core"), ("Profond", "deep"))
STAGE_CLASS = {"awake": "awake", "rem": "rem", "core": "light", "deep": "deep"}  # the stages bar's colours
TL_LO, TL_HI = time(18, 0), time(14, 0)  # the axis never runs past 18:00 the evening before → 14:00


def _hour_floor(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def timeline(start: datetime, end: datetime, *, stages=None, naps=()) -> dict:
    """The night ending on `end` on a clock axis, Oura-like: one bar from
    bedtime to wake, or, for a night with real stage intervals (Garmin
    sleepLevels: [(stage, start, end)]), the hypnogram instead (lanes by
    position, each lane named, in its stage's calm colour, the one of the
    stages bar under it; no %: a shape, not a measure; Schyvens 2025; Lee
    2023). Naps (`naps`: [(start, end)] counted that morning) on
    their own lane when they fit the axis (18:00 the evening before → 14:00);
    `out` lists the others (the caller prints their times). Hours every 2 h
    under it; the times themselves are printed once, by the caller."""
    day = end.date()
    lo = datetime.combine(day - timedelta(days=1), TL_LO)
    hi = datetime.combine(day, TL_HI)
    fit = [(a, b) for a, b in naps if a and b and lo <= a and b <= hi]
    out = [(a, b) for a, b in naps if a and b and (a, b) not in fit]
    t0 = max(lo, _hour_floor(min([start] + [a for a, _ in fit]) - timedelta(minutes=30)))
    t1 = min(hi, _hour_floor(max([end] + [b for _, b in fit]) + timedelta(minutes=89)))
    span = (t1 - t0).total_seconds() or 1

    def x(t):
        return round(TL_X0 + max(0.0, min(1.0, (t - t0).total_seconds() / span)) * (TL_X1 - TL_X0), 1)

    lanes, segs, steps, main = [], [], [], None
    y = 4
    if stages:
        rows = {k: 4 + i * (TL_LANE + TL_GAP) for i, (_, k) in enumerate(STAGE_LANES)}
        lanes = [(nm, rows[k], TL_LANE) for nm, k in STAGE_LANES]
        cut = sorted((k, max(a, start), min(b, end)) for k, a, b in stages if k in rows and b > a and b > start
                     and a < end)
        cut.sort(key=lambda s: s[1])
        segs = [{"x": x(a), "w": max(round(x(b) - x(a), 1), 1.0), "y": rows[k], "k": STAGE_CLASS[k]}
                for k, a, b in cut]
        for (k0, _, _), (k1, a1, _) in zip(cut, cut[1:]):
            y0, y1 = sorted((rows[k0], rows[k1]))
            steps.append({"x": x(a1), "y0": y0 + TL_LANE / 2, "y1": y1 + TL_LANE / 2})
        y = 4 + len(STAGE_LANES) * (TL_LANE + TL_GAP)
    else:
        lanes = [("nuit", y, TL_MAIN)]
        main = {"x": x(start), "w": max(round(x(end) - x(start), 1), 2.0), "y": y, "h": TL_MAIN}
        y += TL_MAIN + TL_GAP
    nap_bars = []
    if fit:
        lanes.append(("sieste", y, TL_LANE))
        nap_bars = [{"x": x(a), "w": max(round(x(b) - x(a), 1), 2.0), "y": y, "h": TL_LANE} for a, b in fit]
        y += TL_LANE + TL_GAP
    ticks = []
    t = t0 + timedelta(hours=(t0.hour % 2))  # even hours
    while t <= t1:
        ticks.append({"x": x(t), "label": f"{t.hour:02d}:00"})
        t += timedelta(hours=2)
    H = y + 14
    return {"W": W, "H": H, "X0": TL_X0, "X1": TL_X1, "lanes": lanes, "main": main, "segs": segs, "steps": steps,
            "naps": nap_bars, "out": out, "ticks": ticks, "base": y - TL_GAP + 2, "stages": bool(stages),
            "lane_h": TL_LANE}
