"""How well does the previous edition predict this one's splits?

For a race run on the same course two years in a row, each runner of the later
edition is predicted from the earlier edition's finishers of the same level:
their checkpoint shares (time at the checkpoint / finish time, stops removed
where timed), kernel-weighted by finish time, read at his own finish time.
Checkpoints are matched by name. Scored like calibrate.score_race.
"""
from __future__ import annotations

import math
import statistics
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import calibrate as cal  # noqa: E402


def shares(race):
    out = []
    for r in race["runners"]:
        mv = cal.moving(r)
        if not mv[-1]:
            continue
        out.append((mv[-1] / 3600, {c["name"]: (a / mv[-1] if a is not None else None) for c, a in zip(race["cps"], mv)}))
    return out


def predict_share(prev, name, T, bw=2.5):
    num = den = 0.0
    for t, sh in prev:
        v = sh.get(name)
        if v is None:
            continue
        w = math.exp(-0.5 * ((t - T) / bw) ** 2)
        num += w * v
        den += w
    return num / den if den > 1e-3 else None


def same_points(prev_race, race, km_tol=0.6, rel_tol=0.06, gain_tol=60):
    """Checkpoint names whose preceding section (length and climb) is unchanged
    between editions, on a finish within 3 % — a moved start shifts every km
    but keeps the sections; a name kept on a moved checkpoint must not pass."""
    km = lambda c: c.get("km_official", c["km"])  # noqa: E731 — same ruler both years
    if abs(km(race["cps"][-1]) - km(prev_race["cps"][-1])) > 0.03 * km(race["cps"][-1]):
        return set()
    before = {c["name"]: i for i, c in enumerate(prev_race["cps"])}
    ok = set()
    for i, c in enumerate(race["cps"][1:], start=1):
        j = before.get(c["name"])
        if not j:
            continue
        a, b = race["cps"][i - 1], prev_race["cps"][j - 1]
        if a["name"] != b["name"]:
            continue
        d_now, d_prev = km(c) - km(a), km(prev_race["cps"][j]) - km(b)
        if abs(d_now - d_prev) > max(km_tol, rel_tol * d_prev):
            continue
        g_now = (c.get("dplus") or 0) - (a.get("dplus") or 0)
        g_prev = (prev_race["cps"][j].get("dplus") or 0) - (b.get("dplus") or 0)
        if abs(g_now - g_prev) > max(gain_tol, 0.1 * g_prev):
            continue
        ok.add(c["name"])
    return ok


def score(prev_race, race, groups=cal.GROUPS):
    prev = shares(prev_race)
    names = same_points(prev_race, race)
    res = {}
    for lo, hi in groups:
        gaps = []
        n = 0
        for r in race["runners"]:
            mv = cal.moving(r)
            if not mv[-1] or not (lo * 3600 <= mv[-1] < hi * 3600):
                continue
            n += 1
            T = mv[-1] / 3600
            for c, a in list(zip(race["cps"], mv))[1:-1]:
                if a is None or c["name"] not in names:
                    continue
                s = predict_share(prev, c["name"], T)
                if s is not None:
                    gaps.append(abs(s * mv[-1] - a) / 60)
        if n >= 3 and gaps:
            res[f"{lo}-{hi}h"] = (n, statistics.mean(gaps))
    return res
