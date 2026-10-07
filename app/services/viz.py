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
- durations: « 5h50 », « 2h20 », « 45 min » (hm); spoken « 5 heures 50 »;
- clock times: « 23:35 », rounded to 5 min for sleep times (approximate);
- numbers: decimal comma, true minus U+2212, narrow no-break space U+202F
  before units: « 58 ms », « −3 bpm »;
- dates: « mar. 6 oct. »; spoken « mardi 6 octobre ».
Layout: viewBox width 320 (the 358 px phone's content width), plot 0–288, the
y ticks right-aligned at 320; one slot per day/week, marks at slot centres so
stacked figures share an x axis.
"""
import json
import math
import statistics
from datetime import date, datetime, time, timedelta

NNBSP = " "
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
    without a main episode « Sieste 1h22 · nuit incomplète ? »."""
    if main is None:
        return f"Sieste {hm(nap)} · nuit incomplète ?" if nap else "—"
    if nap:
        return f"Nuit {hm(main)} · sieste {hm(nap)} · {hm(main + nap)} sur 24{NNBSP}h"
    return f"Nuit {hm(main)} sur 24{NNBSP}h"


def sleep_spoken(main: float | None, nap: float | None) -> str:
    if main is None:
        return f"sieste {hm_long(nap)}, nuit incomplète" if nap else "pas de mesure"
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


def paths(xs, ys) -> str:
    """Polyline broken wherever a value is missing (never drawn across a gap)."""
    out, pen = [], False
    for x, y in zip(xs, ys):
        if y is None:
            pen = False
            continue
        out.append(f"{'L' if pen else 'M'}{x:.1f} {y:.1f}")
        pen = True
    return " ".join(out)


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


def _data(xs, ys, days, r, a, h=None, sel=None, link=None) -> dict:
    """{data: the JSON pf-viz.js reads, read: the default readout (printed by
    the server: the chart is complete without JS), sel, n}."""
    sel = (len(xs) - 1 if sel is None else sel) if xs else None
    out = {"x": xs, "y": ys, "d": [d.isoformat() for d in days], "r": r, "a": a,
           "h": h or [None] * len(xs), "sel": sel}
    if link:
        out["link"] = link
    data = json.dumps(out, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return {"data": data, "read": r[sel] if xs else ["", "—", ""], "aria_now": a[sel] if xs else "", "sel": sel,
            "n": len(xs)}


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


# ── K1 tile ─────────────────────────────────────────────────────────────────

def tile(label: str, value: str | None, unit: str, means: list, band: tuple | None, *, word: str | None = None,
         glyph: str | None = None, tone: str = "muted", href: str = "#", aria: str = "", driver: bool = False,
         gap: str | None = None) -> dict:
    """One signal tile: the label, ONE number (the window value, e.g. a 7-night
    mean, formatted by the caller), the status word with its glyph, and a
    112×40 sparkline of the rolling mean over 14 days whose end dot is that
    same number. Band edges are never printed on a tile. Without a value: « — »
    and `gap` (« 2 nuits sur 7 · il en faut 3 »), no line."""
    t = {"label": label, "value": value, "unit": unit, "word": word, "glyph": glyph, "tone": tone, "href": href,
         "aria": aria, "driver": driver, "gap": gap, "path": None}
    pts = [v for v in means if v is not None]
    if value is None or not pts:
        return t
    Wt, Ht, P = 112, 40, 4
    lo, hi = span_of(pts + list(band or ()), 0, pad=0.15)
    y = scale(lo, hi, P, Ht - P)
    xs = [round(i * (Wt - 6) / max(len(means) - 1, 1), 1) for i in range(len(means))]
    ys = [y(v) for v in means]
    last = max(i for i, v in enumerate(ys) if v is not None)
    t.update(path=paths(xs, ys), end={"x": xs[last], "y": ys[last]},
             band={"y": y(band[1]), "h": round(y(band[0]) - y(band[1]), 1)} if band else None)
    return t


# ── K2 band chart: one to three stacked panels on one x axis ────────────────

PANEL_H, PANEL_GAP, FLAG = 92, 18, 22


def band_chart(key: str, days: list[date], panels: list[dict], *, races=(), longs=(), tags=None, sel=None,
               title: str = "", slot_labels: list[tuple[str, str]] | None = None, unit_word: str = "nuits") -> dict:
    """Stacked panels sharing one x axis, one scrub and one readout (« Cœur la
    nuit »: VFC above, FC de nuit below, respiration as a slim third panel).
    Each panel: {name, unit, unit_long, values: [per day], band: [(lo, hi) |
    None per day], mean: [7-night mean | None per day] (drawn, never printed),
    min_span, digits, up_is: "warn" | "muted" (what « au-dessus » means), h,
    judge: [bool per day] (optional: a night drawn but never judged, e.g. in
    J-7 → J+7 around a race, gets no out-of-band word nor hollow dot)}.
    Readout: [date, « VFC 58 ms · FC 46 bpm », « normale 55–63 · ◇ alcool »];
    an out-of-band night is a hollow dot and the word « au-dessus » /
    « en dessous », never colour alone. A night with a context tag (`tags`)
    is drawn as a small diamond (◇). `slot_labels`: [(readout, spoken)] per
    slot when the slots are not nights (the weekly « 1 an » view)."""
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
        judge = p.get("judge") or [True] * n
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
            judged = (p.get("judge") or [True] * n)[i]
            if judged and b and v > b[1]:
                word = " au-dessus"
            elif judged and b and v < b[0]:
                word = " en dessous"
            vals.append(f"{p['name']} {num(v, dg, p['unit'])}{word}")
            if b:
                normals.append(f"{p['name']} {num(b[0], dg)}–{num(b[1], dg)}" if len(panels) > 1
                               else f"normale {num(b[0], dg)}–{num(b[1], dg)}")
            spoken.append(f"{p['name']} {num(v, dg)} {p['unit_long']}{word}"
                          + (f", ta normale {num(b[0], dg)} à {num(b[1], dg)}" if b else ""))
        ctx = _context(d, races, longs, tags)
        if not vals:
            r.append([label, "—", " · ".join(["pas de montre cette nuit" if not slot_labels else "pas assez de nuits"]
                                             + ctx)])
            a.append(". ".join([f"{said} : pas de mesure"] + ctx))
            continue
        normal = ("normale " + ", ".join(normals)) if len(panels) > 1 and normals else (normals[0] if normals else "")
        r.append([label, " · ".join(vals), " · ".join(([normal] if normal else []) + ctx)])
        a.append(f"{said} : " + ", ".join(spoken) + "".join(f". {c}" for c in ctx))
    ev = events(days, xs, races, longs)
    measured = sum(1 for i in range(n) if any(p["values"][i] is not None for p in panels))
    return {"key": key, "n": n, "W": W, "H": H, "X1": X1, "panels": out_panels, "xt": x_labels(days, xs), **ev,
            "dot_r": 2.5 if n <= 31 else 1.6 if n <= 120 else 1.0, "flag_y": FLAG - 3,
            "summary": f"{title}, {n} {unit_word} : {measured} mesuré{'e' if unit_word == 'nuits' else ''}"
                       f"{'s' if measured > 1 else ''}",
            **_data(xs, y_series, days, r, a, sel=sel)}


# ── K3 nights: 24-h amounts above, bed → wake windows below ─────────────────

AMOUNT = (8, 112)
TIMING = (132, 236)
REF_MIN = 7 * 60  # ≈ 7 h line (Johnston 2020)
AXIS_LO, AXIS_HI = 20 * 60, 36 * 60  # 20:00 → 12:00, minutes after midnight of the evening's day


def _axis_min(t: datetime, day: date) -> float:
    """Minutes after midnight of the evening before the wake day `day`."""
    return (t - datetime.combine(day - timedelta(days=1), time(0))).total_seconds() / 60


SHORT_MAIN_MIN = 180  # (H) a main episode shorter than this may be a night cut in two: « nuit incomplète ? »


def nights_chart(days: list[date], nights: dict, *, usual: dict | None = None, races=(), longs=(),
                 tags: dict | None = None, link: str | None = None, sel=None, mean: list | None = None,
                 band: list | None = None, slot_labels: list[tuple[str, str]] | None = None,
                 unit_word: str = "jours") -> dict:
    """One linked figure over the days: 24-h amounts (night solid, nap lighter
    and hatched, < 6 h drawn as an outlined bar, ≈ 7 h line, 7-night mean
    line) above the bed → wake windows (floating bars on a 20:00 → 12:00 axis
    fitted over the main windows AND the in-axis naps; a nap inside the axis
    is its own segment with a gap, outside it an edge mark; real wake gaps cut
    out; faint median lines, and ±1 SD bands around them once the spread is
    known). `nights`: {day: nights.Night}. `usual`: nights.timing() (bed,
    wake, bed_sd, wake_sd as minutes after 18:00). `mean`: the 7-night mean
    line per slot (default: a rolling mean of the shown totals); `band`: the
    athlete's 24-h normal per slot ((lo, hi) | None); `slot_labels`:
    [(readout, spoken)] per slot when the slots are weeks."""
    n = len(days)
    xs = slot_x(n)
    slot = X1 / max(n, 1)
    bw = round(min(14, slot * 0.62), 1)
    tot = [nights[d].tst24 if d in nights else None for d in days]
    amounts = [v for v in tot if v is not None] + [nights[d].nap_min for d in days if d in nights]
    top_v = max(amounts + [REF_MIN + 60])
    ya = scale(0, top_v, AMOUNT[0], AMOUNT[1])
    # timing axis: fitted over main windows and in-axis naps (± 30 min), inside 20:00 → 12:00
    spans = []
    for d in days:
        nt = nights.get(d)
        if nt and nt.start:
            spans += [_axis_min(nt.start, d), _axis_min(nt.end, d)]
        for a, b, _ in (nt.naps if nt else []):
            if a and nt.in_axis(a, b):
                spans += [_axis_min(a, d), _axis_min(b, d)]
    t0 = max(AXIS_LO, (math.floor((min(spans) - 30) / 60) * 60) if spans else AXIS_LO)
    t1 = min(AXIS_HI, (math.ceil((max(spans) + 30) / 60) * 60) if spans else AXIS_HI)
    yt = scale(t1, t0, TIMING[0], TIMING[1])  # earlier times at the top

    cols, r, a = [], [], []
    mean = mean if mean is not None else [rolling(tot, i) for i in range(n)]
    for i, d in enumerate(days):
        nt = nights.get(d)
        cx = xs[i]
        col = {"i": i, "x": round(cx - bw / 2, 1), "w": bw, "cx": cx}
        ctx = _context(d, races, longs, tags)
        label, said = slot_labels[i] if slot_labels else (night_label(d), night_label(d))
        main, nap = (nt.asleep, nt.nap_min) if nt else (None, None)
        if nt and nt.asleep is not None:
            y_night = ya(nt.asleep)
            col.update(night={"y": y_night, "h": round(AMOUNT[1] - y_night, 1)}, short=nt.tst24 < 6 * 60)
            if nt.nap_min:
                y_top = ya(nt.tst24)
                col["nap"] = {"y": y_top, "h": round(y_night - y_top, 1)}
            w0, w1 = _axis_min(nt.start, d), _axis_min(nt.end, d)
            col["win"] = {"y": yt(max(w0, t0)), "h": round(yt(min(w1, t1)) - yt(max(w0, t0)), 1)}
        elif nt and nt.nap_min:  # a nap-only day: its nap alone, lighter (no 24-h total)
            y_top = ya(nt.nap_min)
            col["nap"] = {"y": y_top, "h": round(AMOUNT[1] - y_top, 1)}
        else:
            col["miss"] = True
        segs, edges = [], []
        for na, nb, _ in (nt.naps if nt else []):
            if na is None:
                continue
            if nt.in_axis(na, nb):
                s0, s1 = _axis_min(na, d), _axis_min(nb, d)
                segs.append({"y": yt(s0), "h": round(yt(s1) - yt(s0), 1)})
            else:
                edges.append({"y": TIMING[1] + 3 if _axis_min(na, d) > t1 else TIMING[0] - 6})
        col.update(nap_segs=segs, nap_edges=edges)
        cols.append(col)
        times = f"{clock(nt.start)} → {clock(nt.end)}" if nt and nt.start else ""
        naps = [f"sieste {clock(na)} → {clock(nb)}" for na, nb, _ in (nt.naps if nt else []) if na is not None]
        partial = bool(nt and nt.asleep is not None and nt.asleep < SHORT_MAIN_MIN)
        extra = ([times] if times else []) + naps + (["nuit incomplète ?"] if partial else []) + ctx
        r.append([label, sleep_readout(main, nap) if nt else "—",
                  " · ".join(extra) or ("pas de montre cette nuit" if not nt and not slot_labels else "")])
        sentence = f"{said} : {sleep_spoken(main, nap) if nt else 'pas de mesure'}"
        if times:
            sentence += f", couché vers {clock(nt.start)}, levé vers {clock(nt.end)}"
        for na, nb, _ in (nt.naps if nt else []):
            if na is not None:
                sentence += f", sieste de {clock(na)} à {clock(nb)}"
        if nt and nt.tst24 is not None and nt.tst24 < 6 * 60:
            sentence += ", moins de 6 heures sur 24 heures"
        a.append(sentence + "".join(f". {c}" for c in ctx))
    hours = [{"y": yt(m), "label": f"{(m // 60) % 24:02d}:00"} for m in range(int(t0 // 60 * 60) + 60, int(t1), 120)]
    med, med_bands = {}, []
    if usual and usual.get("bed") is not None:
        for k in ("bed", "wake"):
            if usual.get(k) is not None:
                m = usual[k] + 18 * 60  # minutes after 18:00 → after midnight of the evening
                if t0 <= m <= t1:
                    med[k] = yt(m)
                    sd = usual.get(f"{k}_sd")
                    if sd:
                        a0, a1 = max(t0, m - sd), min(t1, m + sd)
                        med_bands.append({"y": yt(a0), "h": round(yt(a1) - yt(a0), 1)})
    band_poly, band_lo, band_hi = [], "", ""
    if band and any(band):
        lo = [ya(b[0]) if b else None for b in band]
        hi = [ya(b[1]) if b else None for b in band]
        band_poly, band_lo, band_hi = band_polys(xs, lo, hi), paths(xs, lo), paths(xs, hi)
    ev = events(days, xs, races, longs)
    measured = sum(1 for d in days if d in nights and nights[d].asleep is not None)
    what = "nuit" if unit_word == "jours" else "semaine"
    return {"n": n, "W": W, "H": 262, "X1": X1, "cols": cols, "hours": hours, "med": med, "med_bands": med_bands,
            "ref": {"y": ya(REF_MIN), "label": "7" + NNBSP + "h"}, "mean": paths(xs, [ya(v) for v in mean]),
            "band": band_poly, "band_lo": band_lo, "band_hi": band_hi,
            "amount": AMOUNT, "timing": TIMING, "xt": x_labels(days, xs), **ev,
            "summary": f"Sommeil sur 24 h et horaires, {n} {unit_word} : {measured} {what}{'s' if measured > 1 else ''}"
                       f" mesurée{'s' if measured > 1 else ''}",
            **_data(xs, [], days, r, a, sel=sel, link=link)}


# ── K4 hypnogram: real intervals only ───────────────────────────────────────

LANES = {"awake": 0, "rem": 1, "core": 2, "deep": 3}
LANE_NAMES = (("Éveil", "awake"), ("REM", "rem"), ("Léger", "core"), ("Profond", "deep"))


def hypnogram(segs: list[tuple[str, datetime, datetime]] | None, start: datetime, end: datetime) -> dict | None:
    """The selected night's shape, from real stage intervals (Garmin) only:
    None otherwise (COROS has no timeline). Lanes by position, one neutral hue,
    no minutes, no %, no end labels (the times are in the readout)."""
    if not segs or not start or not end or end <= start:
        return None
    X0, XE, LH, GAP = 52, 316, 12, 6
    span = (end - start).total_seconds()
    x = lambda t: round(X0 + (t - start).total_seconds() / span * (XE - X0), 1)  # noqa: E731
    ly = lambda st: 2 + LANES[st] * (LH + GAP)  # noqa: E731
    segs = sorted((k, max(a, start), min(b, end)) for k, a, b in segs if k in LANES and b > a)
    segs.sort(key=lambda s: s[1])
    rects = [{"x": x(a), "w": max(round(x(b) - x(a), 1), 1.0), "y": ly(k)} for k, a, b in segs]
    steps = []
    for (k0, _, _), (k1, a1, _) in zip(segs, segs[1:]):
        y0, y1 = sorted((ly(k0), ly(k1)))
        steps.append({"x": x(a1), "y0": y0 + LH / 2, "y1": y1 + LH / 2})
    return {"W": W, "H": 4 * (LH + GAP), "X0": X0, "X1": XE, "LH": LH, "segs": rects, "steps": steps,
            "lanes": [(nm, ly(k)) for nm, k in LANE_NAMES],
            "aria": "Forme de la nuit estimée par la montre : une allure, pas une mesure"}


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
