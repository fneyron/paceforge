"""« Intervalles » on an activity's page: its laps drawn as the session it was (owner, 25 nov. 2026: « Met le graph
avec les intervalles de séances »), above the table that lists them, shown exactly when that table is.

One bar per lap, in order: its width its time, its height how fast it was run — a speed read as a pace on foot,
the power on a bike when the laps carry it, else the speed (the table's own distinction). The same lap fields as
the table, whatever the source (Strava's laps, and COROS's and the FIT files' laps put in Strava's shape):
moving_time (s), distance (m), average_watts, average_heartrate. A lap without a time takes no room; a lap with a
time but nothing to measure its pace keeps its room, empty: the session's time stays honest and no bar is
invented. The geometry and every string are computed here; partials/_viz.html (viz_laps) draws it and
app/static/js/pf-viz.js moves the selection, like every other chart (viz.py's module docstring).
"""
import json
import math

from app.services.viz import NBSP, NNBSP, W, X1, hm, hm_long

H, TOP, BASE = 132, 20, 128  # viewBox height; the tallest bar's top (the unit's label above it); the bars' foot
GAP = 2.0  # between two bars, narrower when the laps are many (never more than 30 % of the plot in gaps)
MIN_W, MIN_H = 0.6, 1.5  # a bar is never drawn thinner or lower than this: a short lap still shows
# Choix pratique: the height starts at a floor taken from the session itself, not at zero speed (a 3:20/km repeat
# and a 5:30/km warm-up would stand at 96 % and 58 %: the session flat). The floor is half the fastest lap, or
# 80 % of the slowest when that is lower: walked recoveries keep a visible bar, sprinted repetitions reach the top,
# and a steady run (every lap within a few %) stands level near the top instead of magnifying its noise.
FLOOR_FAST, FLOOR_SLOW = 0.5, 0.8
# Choix pratique: an effort is a lap run at least 10 % faster (or stronger, in watts) than the session's own mean
# (its distance over its time; on a bike its time-weighted power). Repetitions stand 20 % and more above it,
# a tempo block about 10 %; a steady run with auto-laps stays within a few % and shows no effort at all.
EFFORT = 1.10
REF_GAP = 26  # the reference lines: three at most, this far apart (viewBox units)


def _n(v) -> float:
    """A lap's number as stored, or 0 (missing, null, not a number, negative, infinite)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        return 0.0
    return float(v)


def _time(t: float) -> str:
    """The table's lap time: « 1'02" », « 65'00" »."""
    return f"{int(t // 60)}'{int(t % 60):02d}\""


def _pace(speed: float) -> str:
    """The table's pace: « 3:27/km » (seconds truncated, as the table prints them)."""
    p = 1000 / speed
    return f"{int(p // 60)}:{int(p % 60):02d}/km"


def _pace_said(speed: float) -> str:
    p = 1000 / speed
    m, s = int(p // 60), int(p % 60)
    return f"{m} minute{'s' if m > 1 else ''} {s:02d} au kilomètre"


def _kmh(speed: float) -> str:
    return f"{speed * 3.6:.1f}".replace(".", ",") + f"{NNBSP}km/h"


def _dist(d: float) -> str:
    return f"{round(d)}{NNBSP}m" if d < 1000 else f"{d / 1000:.2f}".replace(".", ",") + f"{NNBSP}km"


def _dist_said(d: float) -> str:
    return f"{round(d)} mètres" if d < 1000 else f"{d / 1000:.2f}".replace(".", ",") + " kilomètres"


def _time_said(t: float) -> str:
    m, s = int(t // 60), int(t % 60)
    if not m:
        return f"{s} seconde{'s' if s > 1 else ''}"
    return f"{m} minute{'s' if m > 1 else ''}" + (f" {s:02d}" if s else "")


def _refs(lo: float, hi: float, y, kind: str) -> list[dict]:
    """Two or three quiet reference lines at round values inside the plot, fastest first, REF_GAP apart: whole
    minutes of pace (3, 4, 5, 6, 8, 10, 12, 15 min: the slow ones spaced out), else half minutes, else quarter
    minutes (foot); tens of km/h or 100 W, then finer (bike)."""
    if kind == "pace":
        passes = [[120, 180, 240, 300, 360, 480, 600, 720, 900, 1200],
                  [*range(120, 361, 30), 420, 480, 540, 600], [*range(120, 601, 15)]]
        cand = [[(1000 / s, f"{s // 60}:{s % 60:02d}") for s in p] for p in passes]
    elif kind == "speed":
        cand = [[(k / 3.6, str(k)) for k in range(step, 101, step)] for step in (10, 5, 2, 1)]
    else:
        cand = [[(float(k), str(k)) for k in range(step, 2001, step)] for step in (100, 50, 25, 10)]
    out: list[dict] = []
    for values in cand:
        out = []
        for v, label in sorted(values, reverse=True):
            if not lo < v < hi:
                continue
            yv = y(v)
            if yv < TOP + 8 or yv > BASE - 8 or (out and yv - out[-1]["y"] < REF_GAP):
                continue
            out.append({"y": yv, "label": label})
            if len(out) == 3:
                break
        if len(out) >= 2:
            break
    return out


def lap_chart(laps, sport_group: str | None) -> dict | None:
    """The figure for viz_laps (see the module docstring), or None when no lap has a bar to draw. Readout per lap:
    [« Intervalle 12 · 1'02" · 300 m », « 3:27/km », « 172 bpm »] (only the parts it has: on a bike the power
    first, then the speed); resting readout: the count, the total time and the fastest lap. data.e holds each lap's
    left edge: pf-viz.js finds the bar under the finger by its edges (the bars' widths differ), data.rows the table
    whose rows (data-i) follow the selection."""
    rows = []
    for lap in laps or []:
        lap = lap if isinstance(lap, dict) else {}
        rows.append({"t": _n(lap.get("moving_time")), "d": _n(lap.get("distance")),
                     "w": _n(lap.get("average_watts")), "hr": _n(lap.get("average_heartrate"))})
    bike = sport_group == "bike"
    power = bike and any(r["w"] and r["t"] for r in rows)
    kind = "power" if power else "speed" if bike else "pace"
    for r in rows:
        r["v"] = (r["w"] if power else r["d"] / r["t"] if r["d"] else 0.0) if r["t"] else 0.0
    drawn = [r for r in rows if r["v"]]
    if not drawn:
        return None
    vmax, vmin = max(r["v"] for r in drawn), min(r["v"] for r in drawn)
    floor = min(vmin * FLOOR_SLOW, vmax * FLOOR_FAST)
    span = vmax - floor

    def y(v: float) -> float:
        return round(BASE - (v - floor) / span * (BASE - TOP), 1)

    seconds = sum(r["t"] for r in drawn)
    mean = (sum(r["w"] * r["t"] for r in drawn) if power else sum(r["d"] for r in drawn)) / seconds

    timed = [r for r in rows if r["t"]]
    total = sum(r["t"] for r in timed)
    gap = min(GAP, X1 * 0.3 / len(timed)) if len(timed) > 1 else 0.0
    room = X1 - gap * (len(timed) - 1)
    x, first = 0.0, True
    bars, xs, edges, reads, said = [], [], [], [], []
    for i, r in enumerate(rows):
        w = room * r["t"] / total if r["t"] else 0.0
        edges.append(0.0 if first else round(x - gap / 2, 2))
        xs.append(round(x + w / 2, 1) if r["t"] else round(max(0.0, x - gap / 2), 1))
        if r["v"]:
            bw = max(w, MIN_W)
            top = min(y(r["v"]), BASE - MIN_H)
            bars.append({"i": i, "x": round(min(max(0.0, x + w / 2 - bw / 2), X1 - bw), 2), "w": round(bw, 2), "y": top,
                         "h": round(BASE - top, 1), "effort": r["v"] >= mean * EFFORT})
        if r["t"]:
            x += w + gap
            first = False
        # the readout: « Intervalle 12 · 1'02" · 300 m » over « 3:27/km » and « 172 bpm », only what the lap has
        head, spoken = [f"Intervalle{NBSP}{i + 1}"], []
        if r["t"]:
            head.append(_time(r["t"]))
            spoken.append(_time_said(r["t"]))
        if r["d"]:
            head.append(_dist(r["d"]))
            spoken.append(_dist_said(r["d"]))
        speed = r["d"] / r["t"] if r["d"] and r["t"] else 0.0
        if power:
            main = f"{int(r['w'])}{NNBSP}W" if r["w"] else ""
            more = [_kmh(speed)] if speed else []
            if r["w"]:
                spoken.append(f"{int(r['w'])} watts")
            if speed:
                spoken.append(_kmh(speed).replace(f"{NNBSP}km/h", " kilomètres-heure"))
        elif bike:
            main, more = (_kmh(speed) if speed else ""), []
            if speed:
                spoken.append(_kmh(speed).replace(f"{NNBSP}km/h", " kilomètres-heure"))
        else:
            main, more = (_pace(speed) if speed else ""), []
            if speed:
                spoken.append(_pace_said(speed))
        if r["hr"]:
            more.append(f"{int(r['hr'])}{NNBSP}bpm")
            spoken.append(f"{int(r['hr'])} battements par minute")
        # the large value with a plain no-break space (the display font has no U+202F: viz.py's formats)
        reads.append([f"{NBSP}· ".join(head), (main or "—").replace(NNBSP, NBSP), f"{NBSP}· ".join(more)])
        said.append(f"Intervalle {i + 1} : " + (", ".join(spoken) if spoken else "pas de mesure"))

    for i in range(len(rows) - 1, -1, -1):  # laps without a time after the last bar: never under the finger
        if rows[i]["t"]:
            break
        edges[i] = W * 10
    best = max(range(len(rows)), key=lambda k: rows[k]["v"])
    fast = rows[best]["v"]
    best_txt = (f"{int(fast)}{NNBSP}W" if power else _kmh(fast) if bike else _pace(fast))
    best_said = (f"{int(fast)} watts" if power else _kmh(fast).replace(f"{NNBSP}km/h", " kilomètres-heure") if bike
                 else _pace_said(fast))
    n = len(rows)
    word = "le plus fort" if power else "le plus rapide"
    rest = [f"{n} intervalles{NBSP}· {hm(total / 60)}", best_txt.replace(NNBSP, NBSP), f"{word}{NBSP}: n°{NBSP}{best + 1}"]
    efforts = sum(1 for b in bars if b["effort"])
    summary = (f"Intervalles : {n} tours en {hm_long(total / 60)}. {word.capitalize()} : le numéro {best + 1}, "
               f"{best_said}." + (f" {efforts} tour{'s' if efforts > 1 else ''} nettement plus "
                                  f"{'fort' if power else 'rapide'}{'s' if efforts > 1 else ''} que la moyenne "
                                  "de la séance, en couleur." if efforts else "")
               + " Le tableau qui suit donne chaque tour.")
    rest_said = (f"{n} intervalles en {hm_long(total / 60)}. {word.capitalize()} : le numéro {best + 1}, {best_said}. "
                 "Choisis un intervalle pour le lire.")
    data = {"x": xs, "e": edges, "y": [], "r": reads, "a": said, "sel": n, "rest": rest, "restA": rest_said,
            "rows": "#laps-rows"}
    unit = {"pace": "/km", "speed": "km/h", "power": "W"}[kind]
    return {"key": "intervalles", "n": n, "W": W, "H": H, "X1": X1, "top": TOP, "base": BASE, "bars": bars,
            "refs": _refs(floor, vmax, y, kind), "unit": unit, "kind": kind, "floor": floor, "mean": mean,
            "summary": summary, "rest": True, "read": rest, "aria_now": rest_said, "sel": n, "links": False,
            "link_now": None, "tone_now": "",
            "data": json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")}
