"""Fit PaceForge's plan shape (gradient curve, fatigue, night, terrain) on real
race results and validate it on held-out events.

Pipeline (end to end, ~1 h on 4 cores, most of it the model-selection CV):

  .venv/bin/python scripts/race_data/fit_data.py              # once: per-race precompute with the app's code
  .venv/bin/python scripts/race_data/fit_data.py --livetrail  # once: LiveTrail courses (extra validation only)
  .venv/bin/python scripts/race_data/fit_model.py             # CV on TRAIN, fit on TRAIN, score held-out TEST
  .venv/bin/python scripts/race_data/fit_model.py --form hours --lam-c 0 --lam-t 3   # skip the CV

numpy/scipy are not in the app venv; install them aside and point PACEFORGE_PYLIB at
them (default <PACEFORGE_FIT_DIR>/pylib):
  uv pip install --python .venv/bin/python --target <dir> numpy scipy

DATA: UTMB Live running races of 40-180 km WITH a GPS track (LiveTrail terrain
classes); races without a track (profile rebuilt from checkpoint D+) are left
out of the fit, their gradient composition being synthetic. A track labelled
> 80 % road on a trail race (Translantau) is treated as unlabelled (class U).
LiveTrail archives (no track) are scored once at the end as an extra
validation set, never in the selection; night is not scored there (the
archives give no start clock).

WHAT IS SCORED (same metric as calibrate.score_race): for each race and level
group (finish 0-12, 12-18, 18-24, 24-30, 30+ h of MOVING time, stops removed
where departures are timed), the plan is simulated at the group's median finish
(flat pace re-levelled 4x, so fatigue-by-hours and night see the right clock),
then scaled to each runner's own finish; the metric is the mean |predicted -
actual| in minutes at the intermediate checkpoints, averaged over (race, group)
weighted by the group's runner count. The production path reproduces
calibrate.score_race exactly.

FAST PATH: fit_data.py stores per 1 km segment a histogram of the 25 m sample
points over (integer gradient, LiveTrail class), built exactly as the app's
_fine_terrain reads the course (gradient over 100 m, rounded). A candidate
gradient curve x terrain multipliers is one matrix product; the sequential
fatigue/night replay is vectorised over all (race, group) pairs (~25 ms/eval).

MODELS
  legacy       the app before the first fit (calibrate.LEGACY_*): its gradient
               table, progress x distance fatigue, night 1.08 / dusk-dawn 1.03.
  current      the app as it stands (race_simulator: _DEFAULT_GRADIENT_FACTORS on
               _fine_terrain, surface road 1 / track 1.06 / trail 1.12, altitude,
               fatigue by hours FATIGUE_C / Q / FRESH, NIGHT_FACTOR): the
               "before" of a refit.
  fitted forms gradient curve with knots -35 -25 -15 -8 -3 0 3 8 15 25 35 %,
               read at the gradient clamped to -20..30 % as the app reads it
               (uphill monotone; downhill log-factor convex in |g|: gains, then
               costs as the descent steepens), night multiplier, LiveTrail
               multipliers (R = 1; F, T, T1, T2, T3, S, U), altitude as production,
               fatigue f = 1 + c (T/10)^q (D/100)^p (h/T)^r - fresh max(0, 1-h/3)
               (h hours on course, T target finish, D km):
     hours          r = q, p = 0: fatigue by hours on course only
     general        q, r, p free (nests progress-based fatigue as in production)
     general+level  + level term log F(g, T) = log F(g) (T/20)^gamma (gamma_up / gamma_down)
     curcurve       the current curve kept, fatigue / night / terrain fitted
  Penalties: lam_c x curvature of the curve, lam_t x (terrain - production)^2.
  Every fit starts from the current app (its curve projected on the form).
  The form and (lam_c, lam_t) are chosen by 2-fold cross-validation over TRAIN
  events; the test events are only scored once, at the end.

SPLIT: by EVENT (all editions and distances of an event on the same side).
The test events are FIXED (TEST_EVENTS, those of the first fit) so that a refit
is scored on the same events as the model it would replace; events added to
the dataset since then go to train.

ADOPTION: a refit goes into race_simulator.py only if it lowers the held-out
mean |gap| against "current" and the Transjeju guard does not get worse; then
validate_app.py re-scores the app's own path before / after.

Outputs: scripts/race_data/fitted_params.json (the fit the app's constants come
from; pass --params scripts/race_data/refit_params.json for a refit not
adopted, as on 2026-10-04), scripts/race_data/fit_results.txt
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import pickle
import random
import statistics
import sys
import time

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")  # the fit parallelises over processes; BLAS threads would oversubscribe
HERE = pathlib.Path(__file__).resolve().parent
FIT_DIR = os.environ.get("PACEFORGE_FIT_DIR", "/tmp/claude-0/-home-user-paceforge/cb4ce7f8-b738-551f-a7c5-c130c0de27e8/scratchpad/fit")
sys.path.insert(0, os.environ.get("PACEFORGE_PYLIB", FIT_DIR + "/pylib"))
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
from scipy import optimize  # noqa: E402

import app.services.race_simulator as rs  # noqa: E402
import calibrate as cal  # noqa: E402
import dataset  # noqa: E402
import fit_data  # noqa: E402
import prev_edition as pe  # noqa: E402

GROUPS = cal.GROUPS
BANDS = [(40, 60), (60, 100), (100, 140), (140, 181)]
GRID = np.arange(fit_data.G_MIN, fit_data.G_MAX + 1)
NG, NC = len(GRID), len(fit_data.CLASSES)
KNOTS = np.array([-35, -25, -15, -8, -3, 0, 3, 8, 15, 25, 35], dtype=float)
DOWN, UP = KNOTS[KNOTS < 0], KNOTS[KNOTS > 0]
REF_H = np.array([9.0, 15.0, 21.0, 27.0, 34.0])  # levels the curve is evaluated at (interpolated per group)
TERRAIN = ["F", "T", "T1", "T2", "T3", "S", "U"]  # multipliers fitted, R = 1
TER_PROD = [1.06, 1.12, 1.12, 1.12, 1.12, 1.12, 1.12]  # production (surface.py: track +6 %, trail +12 %)
APP_CLAMP = (-20, 30)  # race_simulator._get_factor reads the integer gradient clamped to this range
BOGUS_ROAD_SHARE = 0.8  # a trail race whose track is > 80 % "R" has unusable labels (Translantau): all -> U
SEED = 7
TEST_EVENTS = ["chiangmai", "kullamannen", "puertovallarta", "transjeju", "utmb", "whistler"]
GUARD_MODELS = ("legacy", "current", "curcurve_fitrest", "fit_hours", "fit_general", "fit_general+level")
OWNER_RACE, OWNER_BIB = "utmb/transjeju_2026/100m", "10"
ITER = 4  # flat-pace re-levelling passes (calibrate.score_race uses 3; 4 converges with hours fatigue)

# ---------------------------------------------------------------- parameters


def default_curve(g):
    """The current app curve read like _get_factor (integer, clamped -20..30)."""
    return rs._get_factor(fit_data.CURRENT_PROFILE, max(APP_CLAMP[0], min(APP_CLAMP[1], g)))


def x0_from_production():
    """Parameter vector reproducing the current app: its curve (projected on the convex
    downhill form), production terrain, fatigue by hours and night as in race_simulator."""
    lv = [0.0] + [math.log(default_curve(g)) for g in DOWN[::-1]]  # at |g| = 0, 3, 8, 15, 25, 35
    u = np.concatenate([[0.0], -DOWN[::-1]])
    slopes = np.diff(lv) / np.diff(u)
    down = [slopes[0]] + [math.log(max(b - a, 1e-3)) for a, b in zip(slopes, slopes[1:])]
    up, prev = [], 1.0
    for g in UP:
        v = default_curve(g)
        up.append(math.log(max(v - prev, 1e-3)))
        prev = v
    return np.array(down + up + [0.0, 0.0] + [rs.FATIGUE_C, rs.FATIGUE_Q, rs.FATIGUE_Q, 0.0, rs.FATIGUE_FRESH]
                    + [rs.NIGHT_FACTOR] + list(np.log(TER_PROD)))


NAMES = (["down_slope(0..3)"] + [f"log_slope_incr(|g|={int(-g)})" for g in DOWN[::-1][:-1]] + [f"log_step({int(g)})" for g in UP] +
         ["gamma_up", "gamma_down", "c", "q", "r", "p", "fresh", "night"] + [f"log_m_{t}" for t in TERRAIN])
I_GAM = slice(10, 12)
I_C, I_Q, I_R, I_P, I_FRESH = 12, 13, 14, 15, 16
I_NIGHT = 17
I_TER = slice(18, 25)
LEVEL = [10, 11]
FORMS = {  # which parameters each model form fits (the rest stay at x0)
    "hours": [i for i in range(25) if i not in (10, 11, I_R, I_P)],  # r tied to q, p = 0: fatigue by hours on course
    "general": [i for i in range(25) if i not in (10, 11)],
    "general+level": list(range(25)),
    "curcurve": list(range(12, 25)),  # the current gradient curve kept
}


def knot_values(x):
    """Factor at KNOTS. Downhill: log F piecewise linear and CONVEX in |g| (slope only
    increases: gains then costs as the descent steepens); uphill: monotone increasing."""
    slopes = x[0] + np.concatenate([[0.0], np.cumsum(np.exp(x[1:5]))])
    u = np.concatenate([[0.0], -DOWN[::-1]])
    logf = np.concatenate([[0.0], np.cumsum(slopes * np.diff(u))])
    down = np.exp(logf[1:])[::-1]
    up = 1.0 + np.cumsum(np.exp(x[5:10]))
    return np.concatenate([down, [1.0], up])


def curves(x):
    """(NG x len(REF_H)) factor on the integer gradient grid, one column per level."""
    kv = knot_values(x)
    base = np.log(np.interp(np.clip(GRID, *APP_CLAMP), KNOTS, kv))  # read as the app reads it
    gu, gd = x[I_GAM]
    s_up = (REF_H / 20.0) ** gu
    s_dn = (REF_H / 20.0) ** gd
    scale = np.where(GRID[:, None] > 0, s_up[None, :], s_dn[None, :])
    return np.exp(base[:, None] * scale)


def terrain(x):
    return np.concatenate([[1.0], np.exp(x[I_TER])])  # R, F, T, T1, T2, T3, S, U


def describe(x):
    kv = knot_values(x)
    c = curves(x)
    out = {
        "knots_pct": KNOTS.astype(int).tolist(),
        "curve_at_20h": [round(float(v), 3) for v in kv],
        "gamma_up": round(float(x[10]), 3), "gamma_down": round(float(x[11]), 3),
        "curve_by_level_h": {f"{int(h)}h": {int(g): round(float(c[list(GRID).index(int(g)), i]), 3) for g in KNOTS} for i, h in enumerate(REF_H)},
        "fatigue": {"c": round(float(x[I_C]), 4), "q": round(float(x[I_Q]), 4), "r": round(float(x[I_R]), 4), "p": round(float(x[I_P]), 4),
                    "fresh": round(float(x[I_FRESH]), 4),
                    "formula": "1 + c*(T/10)**q*(D/100)**p*(h/T)**r - fresh*max(0, 1 - h/3); h = moving hours on course, "
                               "T = target finish (moving hours), D = race km; r = q and p = 0 is fatigue by hours only"},
        "night": {"night": round(float(x[I_NIGHT]), 4), "dusk_dawn": round(1 + (float(x[I_NIGHT]) - 1) * 0.375, 4)},
        "terrain": dict(zip(["R"] + TERRAIN, [round(float(v), 4) for v in terrain(x)])),
        # drop-in for race_simulator._DEFAULT_GRADIENT_FACTORS (the app reads integer %, clamped to
        # -20..30, and so did the fit)
        "integer_table_20h": {int(g): round(float(v), 3) for g, v in zip(GRID, c[:, 2]) if APP_CLAMP[0] <= g <= APP_CLAMP[1]},
    }
    return out


# ---------------------------------------------------------------- problem


class Problem:
    """All (race, level group) pairs of a race set, as padded numpy arrays."""

    def __init__(self, races, groups=GROUPS, runner_filter=None, min_members=3):
        self.races = races
        H, alt, bprod, bcur = [], [], [], []
        off = []
        n = 0
        for r in races:
            h = np.asarray(r["hist"], dtype=np.float64)
            tot = h.sum(axis=(0, 1))
            if tot[0] > BOGUS_ROAD_SHARE * tot.sum():
                h[:, :, -1] = h.sum(2)
                h[:, :, :-1] = 0
            h = h.reshape(len(r["hist"]), NG * NC)
            h /= h.sum(1, keepdims=True)
            H.append(h)
            alt.extend(r["alt"])
            bprod.extend(r["prep"]["base"])
            bcur.extend(r["prep"]["base_cur"])
            off.append(n)
            n += len(r["hist"])
        self.H = np.vstack(H + [np.zeros((1, NG * NC))])
        self.alt = np.array(alt + [0.0])
        self.base_prod_seg = np.array(bprod + [0.0])
        self.base_cur_seg = np.array(bcur + [0.0])
        # "current" = the app the records were built with (base_cur and these constants)
        self.app = races[0].get("app") or {"fatigue_c": rs.FATIGUE_C, "fatigue_q": rs.FATIGUE_Q,
                                           "fatigue_fresh": rs.FATIGUE_FRESH, "night": rs.NIGHT_FACTOR}
        pairs = []
        for ri, r in enumerate(races):
            for lo, hi in groups:
                mem = [x for x in r["runners"] if lo * 3600 <= x["mv"][-1] < hi * 3600 and (runner_filter is None or runner_filter(r, x))]
                if len(mem) >= min_members:
                    pairs.append((ri, lo, hi, mem))
        self.pairs = pairs
        P = len(pairs)
        S = max(len(races[p[0]]["hist"]) for p in pairs)
        C = max(len(races[p[0]]["cps"]) for p in pairs)
        self.IDX = np.full((P, S), n)
        self.dist = np.zeros((P, S))
        self.fat_prod = np.ones((P, S))
        self.cpj = np.zeros((P, C), dtype=int)
        self.cpf = np.zeros((P, C))
        self.last = np.zeros(P, dtype=int)
        self.med = np.zeros(P)
        self.sh = np.zeros(P)
        self.clock = np.ones(P, dtype=bool)  # False: no start clock (LiveTrail), night not applied
        self.nrun = np.zeros(P)
        self.km = np.zeros(P)
        op, oi, oT, oa = [], [], [], []
        for p, (ri, lo, hi, mem) in enumerate(pairs):
            r = races[ri]
            ns = len(r["hist"])
            self.IDX[p, :ns] = off[ri] + np.arange(ns)
            self.dist[p, :ns] = r["prep"]["dist"]
            self.fat_prod[p, :ns] = r["fat_prod"]
            ends = r["prep"]["end"]
            for i, kmc in enumerate(r["prep"]["cp_km"]):
                j = 0
                while j < ns and ends[j] < kmc:
                    j += 1
                if j >= ns:
                    j, f = ns - 1, 1.0
                else:
                    pe_ = ends[j - 1] if j > 0 else 0.0
                    f = (kmc - pe_) / (ends[j] - pe_) if ends[j] > pe_ else 1.0
                self.cpj[p, i], self.cpf[p, i] = j, f
            nc = len(r["cps"])
            self.last[p] = nc - 1
            self.med[p] = statistics.median(x["mv"][-1] for x in mem)
            self.sh[p] = r["start_hour"]
            self.clock[p] = r.get("has_clock", True)
            self.nrun[p] = len(mem)
            self.km[p] = r["km"]
            for x in mem:
                for i in range(1, nc - 1):
                    a = x["mv"][i]
                    if a is not None:
                        op.append(p), oi.append(i), oT.append(x["mv"][-1]), oa.append(a)
        self.op, self.oi = np.array(op), np.array(oi)
        self.oT, self.oa = np.array(oT, dtype=float), np.array(oa, dtype=float)
        hrs = self.med / 3600
        # linear interpolation weights of each pair's level onto REF_H
        w = np.zeros((P, len(REF_H)))
        hc = np.clip(hrs, REF_H[0], REF_H[-1])
        k = np.clip(np.searchsorted(REF_H, hc) - 1, 0, len(REF_H) - 2)
        t = (hc - REF_H[k]) / (REF_H[k + 1] - REF_H[k])
        w[np.arange(P), k] = 1 - t
        w[np.arange(P), k + 1] = t
        self.w = w
        self.has_obs = np.bincount(self.op, minlength=P) > 0

    # -- simulation
    def base(self, x):
        th = curves(x)[:, None, :] * terrain(x)[None, :, None]  # NG x NC x L
        seg = (self.H @ th.reshape(NG * NC, -1)) * self.alt[:, None]  # Nseg x L
        return np.einsum("psl,pl->ps", seg[self.IDX], self.w)

    def predict(self, x=None, mode="fit"):
        """Moving time at each checkpoint (P x C) from the group's level."""
        if mode == "fit":
            base = self.base(x)
            c, q, rr, pp, fresh = x[I_C], x[I_Q], x[I_R], x[I_P], x[I_FRESH]
            night = x[I_NIGHT]
        elif mode == "current":
            base = self.base_cur_seg[self.IDX]
            ap_ = self.app
            c, q, rr, pp, fresh = ap_["fatigue_c"], ap_["fatigue_q"], ap_["fatigue_q"], 0.0, ap_["fatigue_fresh"]
            night = ap_["night"]
        else:  # legacy
            base = self.base_prod_seg[self.IDX]
            night = 1.08
        hours_fat = mode != "legacy"
        th = self.med / 3600  # the group's (target) finish, moving hours
        if hours_fat:
            scale = c * (th / 10) ** q * (self.km / 100) ** pp
        P, S = base.shape
        flat = np.full(P, 360.0)
        rows = np.arange(P)
        for _ in range(ITER):
            t = np.zeros(P)
            cum = np.zeros((P, S))
            for s in range(S):
                if hours_fat:
                    h = np.minimum(t / 3600, 200.0)
                    fat = np.clip(1 + scale * (h / th) ** rr - fresh * np.maximum(0.0, 1 - h / 3), 0.5, 5.0)
                else:
                    fat = self.fat_prod[:, s]
                hr = (self.sh + t / 3600) % 24
                nf = np.where((hr >= 21) | (hr < 6), night, np.where(((hr >= 20) & (hr < 21)) | ((hr >= 6) & (hr < 7)), 1 + (night - 1) * 0.375, 1.0))
                nf = np.where(self.clock, nf, 1.0)
                t = t + flat * base[:, s] * fat * nf * self.dist[:, s]
                cum[:, s] = t
            prev = np.where(self.cpj > 0, cum[rows[:, None], np.maximum(self.cpj - 1, 0)], 0.0)
            pred = prev + (cum[rows[:, None], self.cpj] - prev) * self.cpf
            fin = pred[rows, self.last]
            flat = flat * np.clip(self.med / np.maximum(fin, 1.0), 0.2, 5.0)
        return pred

    def pair_gaps(self, pred):
        """Mean |gap| (min) per pair and the per-observation gaps."""
        fin = pred[np.arange(len(pred)), self.last]
        g = np.abs(pred[self.op, self.oi] * self.oT / fin[self.op] - self.oa) / 60
        s = np.bincount(self.op, weights=g, minlength=len(pred))
        n = np.bincount(self.op, minlength=len(pred))
        return np.where(n > 0, s / np.maximum(n, 1), np.nan), g

    def score(self, pred):
        m, _ = self.pair_gaps(pred)
        ok = self.has_obs
        v = float(np.sum(m[ok] * self.nrun[ok]) / np.sum(self.nrun[ok]))
        return v if math.isfinite(v) else 1e4

    def table(self, pred, key):
        """{label: (runners, mean |gap|)} aggregated by key(pair index)."""
        m, _ = self.pair_gaps(pred)
        agg = {}
        for p in range(len(m)):
            if not self.has_obs[p]:
                continue
            k = key(p)
            a = agg.setdefault(k, [0.0, 0.0])
            a[0] += self.nrun[p]
            a[1] += self.nrun[p] * m[p]
        return {k: (int(v[0]), v[1] / v[0]) for k, v in agg.items()}

    def by_group(self, pred):
        return self.table(pred, lambda p: f"{self.pairs[p][1]}-{self.pairs[p][2]}h")

    def by_band(self, pred):
        def band(p):
            for lo, hi in BANDS:
                if lo <= self.km[p] < hi:
                    return f"{lo}-{min(hi, 180)} km"
        return self.table(pred, band)


# ---------------------------------------------------------------- fit


def bounds():
    lo = np.full(25, -np.inf)
    hi = np.full(25, np.inf)
    lo[0], hi[0] = -0.1, 0.05
    lo[1:5], hi[1:5] = math.log(1e-4), math.log(0.2)
    lo[5:10], hi[5:10] = math.log(0.005), math.log(3.0)
    lo[I_GAM], hi[I_GAM] = -1.5, 1.5
    lo[I_C], hi[I_C] = 0.0, 1.5
    lo[I_Q], hi[I_Q] = -0.5, 2.5
    lo[I_R], hi[I_R] = 0.3, 3.0
    lo[I_P], hi[I_P] = -1.0, 2.0
    lo[I_FRESH], hi[I_FRESH] = -0.1, 0.4
    lo[I_NIGHT], hi[I_NIGHT] = 1.0, 1.4
    ter_lo = {"F": 0.85, "T": 0.9, "T1": 0.9, "T2": 0.9, "T3": 0.9, "S": 0.9, "U": 0.8}
    ter_hi = {"F": 1.3, "T": 2.0, "T1": 2.0, "T2": 2.0, "T3": 2.0, "S": 2.0, "U": 1.5}
    lo[I_TER] = [math.log(ter_lo[t]) for t in TERRAIN]
    hi[I_TER] = [math.log(ter_hi[t]) for t in TERRAIN]
    return lo, hi


def penalty(x, lam_c, lam_t):
    """Curvature of the gradient curve (changes of the uphill slope, bends of the
    downhill) and terrain multipliers away from production's, in minutes of gap."""
    up_slopes = np.exp(x[5:10]) / np.diff(np.concatenate([[0.0], UP]))
    pc = 1000 * (np.sum(np.diff(up_slopes) ** 2) + np.sum(np.exp(x[1:5]) ** 2))
    pt = 100 * np.sum((x[I_TER] - np.log(TER_PROD)) ** 2)
    return lam_c * pc + lam_t * pt


def fit(prob, x0, form="general", lam_c=0.0, lam_t=0.0, maxiter=300, label="", verbose=True, polish=True):
    """Minimise mean |gap| + penalty over the form's parameters (L-BFGS-B, Powell polish)."""
    free = np.array(FORMS[form])
    lo, hi = bounds()
    if form == "hours":
        lo[I_Q] = lo[I_R]  # r = q must stay a positive power
    x = np.clip(x0.copy(), lo, hi)
    n = [0]
    t0 = time.time()

    def full(z):
        y = x.copy()
        y[free] = z
        if form == "hours":
            y[I_R], y[I_P] = y[I_Q], 0.0
        return y

    def f(z):
        n[0] += 1
        y = full(z)
        return prob.score(prob.predict(y)) + penalty(y, lam_c, lam_t)

    bnds = list(zip(lo[free], hi[free]))
    r = optimize.minimize(f, x[free], method="L-BFGS-B", bounds=bnds, options={"maxiter": maxiter, "eps": 2e-3})
    best = r
    if polish:
        r2 = optimize.minimize(f, r.x, method="Powell", bounds=bnds, options={"maxiter": 2, "xtol": 1e-3, "ftol": 1e-5})
        best = r2 if r2.fun < r.fun else r
    y = full(best.x)
    if verbose:
        print(f"  [{label or form}] lam_c {lam_c} lam_t {lam_t}: {n[0]} evals, {time.time() - t0:.0f}s, "
              f"objective {best.fun:.2f}, mean |gap| {prob.score(prob.predict(y)):.2f}", flush=True)
    return y


# ---------------------------------------------------------------- cross-validation (train events only)

CV_FORMS = ["hours", "general", "general+level"]
CV_LAM_C = [0.0, 0.03, 0.3]
CV_LAM_T = [0.0, 0.3, 3.0]
_CV = {}


def cv_folds(train, k=2):
    events = sorted({r["event"] for r in train})
    random.Random(SEED + 1).shuffle(events)
    events.sort(key=lambda e: -sum(1 for r in train if r["event"] == e))  # greedy balance by race count
    folds = [[] for _ in range(k)]
    for e in events:
        min(folds, key=lambda f: sum(1 for r in train if r["event"] in f)).append(e)
    return folds


def _cv_job(job):
    form, lam_c, lam_t, fi = job
    tr, va = _CV["folds"][fi]
    x = fit(tr, x0_from_production(), form, lam_c, lam_t, verbose=False, polish=False)
    return job, va.score(va.predict(x)), tr.score(tr.predict(x))


def cross_validate(train, say):
    import multiprocessing as mp

    folds = cv_folds(train)
    say(f"  inner CV folds (train events): {folds}")
    probs = []
    for i in range(len(folds)):
        tr = [r for r in train if r["event"] not in folds[i]]
        va = [r for r in train if r["event"] in folds[i]]
        probs.append((Problem(tr), Problem(va)))
    _CV["folds"] = probs
    jobs = [(f, lc, lt, i) for f in CV_FORMS for lc in CV_LAM_C for lt in CV_LAM_T for i in range(len(folds))]
    jobs += [("curcurve", 0.0, lt, i) for lt in CV_LAM_T for i in range(len(folds))]  # the curve is not fitted: no lam_c
    with mp.get_context("fork").Pool(min(4, os.cpu_count() or 1)) as pool:
        out = pool.map(_cv_job, jobs)
    res = {}
    for (f, lc, lt, i), va, tr in out:
        res.setdefault((f, lc, lt), []).append((va, tr))
    base = [(p[1].score(p[1].predict(None, "legacy")), p[1].score(p[1].predict(None, "current"))) for p in probs]
    say(f"  validation mean |gap| per fold: legacy {[round(b[0], 2) for b in base]}, current {[round(b[1], 2) for b in base]}")
    for key, v in sorted(res.items(), key=lambda kv: statistics.mean(a for a, _ in kv[1])):
        say(f"  {key[0]:14s} lam_c {key[1]:<4} lam_t {key[2]:<4} validation {statistics.mean(a for a, _ in v):6.2f} {[round(a, 2) for a, _ in v]}  (fit {statistics.mean(b for _, b in v):.2f})")
    best = min(res, key=lambda k: statistics.mean(a for a, _ in res[k]))
    return best, res


def _final_job(job):
    form, lam_c, lam_t = job
    return fit(_CV["train"], x0_from_production(), form, lam_c, lam_t, label=form)


# ---------------------------------------------------------------- split / reports


def split(races):
    test = set(TEST_EVENTS)
    return [r for r in races if r["event"] not in test], [r for r in races if r["event"] in test], sorted(test)


def fmt_table(title, cols, rows):
    """rows: {label: {col: (n, gap)}}"""
    out = [title, f"{'':16s}{'runners':>9s}" + "".join(f"{c:>14s}" for c in cols)]
    for lab, v in rows.items():
        n = next(iter(v.values()))[0]
        out.append(f"{lab:16s}{n:9d}" + "".join(f"{v[c][1]:14.1f}" if c in v else f"{'-':>14s}" for c in cols))
    return "\n".join(out)


def compare(prob, preds, how):
    tabs = {name: getattr(prob, how)(pred) for name, pred in preds.items()}
    labels = list(next(iter(tabs.values())).keys())
    order = [f"{lo}-{hi}h" for lo, hi in GROUPS] + [f"{lo}-{min(hi, 180)} km" for lo, hi in BANDS]
    labels = [lab for lab in order if lab in labels]
    rows = {lab: {name: tabs[name][lab] for name in preds if lab in tabs[name]} for lab in labels}
    rows["ALL"] = {name: (int(prob.nrun[prob.has_obs].sum()), prob.score(pred)) for name, pred in preds.items()}
    return rows


def guard(race, xs, group=(16, 21)):
    """Finishers of one race in a finish band (moving hours): gaps per checkpoint, each model."""
    prob = Problem([race], groups=[group])
    if not prob.pairs:
        return None
    out = {"race": race["id"], "group_h": list(group), "runners": int(prob.nrun[0]),
           "median_finish_h": round(prob.med[0] / 3600, 2), "checkpoints": []}
    per = {}
    for name, (x, mode) in xs.items():
        pred = prob.predict(x, mode)
        fin = pred[0, prob.last[0]]
        per[name] = (pred[0], fin, prob.score(pred))
        out[f"mean_abs_gap_{name}"] = round(prob.score(pred), 1)
    mem = prob.pairs[0][3]
    for i, c in enumerate(race["cps"][1:-1], start=1):
        row = {"cp": c["name"], "km": round(c["km_official"], 1)}
        for name, (pred, fin, _) in per.items():
            g = [(pred[i] * x["mv"][-1] / fin - x["mv"][i]) / 60 for x in mem if x["mv"][i] is not None]
            if g:
                row[f"{name}_median_signed"] = round(statistics.median(g), 1)
                row[f"{name}_mean_abs"] = round(statistics.mean(abs(v) for v in g), 1)
        out["checkpoints"].append(row)
    return out


def say_guard(say, g, models):
    say(f"  mean |gap|: " + ", ".join(f"{k} {g[f'mean_abs_gap_{k}']}" for k in models))
    say(f"  {'checkpoint':22s}{'km':>6s}   median signed gap (pred - actual), min: " + " / ".join(models) + "   | mean |gap|")
    for c in g["checkpoints"]:
        say(f"  {str(c['cp']):22s}{c['km']:6.1f}   " + " / ".join(f"{c.get(k + '_median_signed', float('nan')):6.1f}" for k in models)
            + "   | " + " / ".join(f"{c.get(k + '_mean_abs', float('nan')):5.1f}" for k in models))


def owner_runner(race, xs):
    """One runner (the owner's bib) planned at his OWN finish: the population model sets the
    shape only (the level comes from the athlete's race model), so the predicted finish is
    his by construction; what is scored is the time at each checkpoint."""
    prob = Problem([race], groups=[(0, 99)], runner_filter=lambda r, x: x["bib"] == OWNER_BIB, min_members=1)
    if not prob.pairs:
        return None
    x_run = prob.pairs[0][3][0]
    out = {"race": race["id"], "bib": OWNER_BIB, "actual_s": int(x_run["time_s"]), "moving_s": int(x_run["mv"][-1]), "checkpoints": []}
    preds = {}
    for name, (x, mode) in xs.items():
        pred = prob.predict(x, mode)[0]
        preds[name] = pred
        out[f"mean_abs_gap_{name}"] = round(prob.score(prob.predict(x, mode)), 1)
    for i, c in enumerate(race["cps"][1:-1], start=1):
        a = x_run["mv"][i]
        if a is None:
            continue
        row = {"cp": c["name"], "km": round(c["km_official"], 1), "actual_moving_s": int(a)}
        for name, pred in preds.items():
            row[f"{name}_s"] = int(round(pred[i]))
        out["checkpoints"].append(row)
    return out


def prev_edition_eval(test_races, xs):
    """Previous edition vs model vs blend on matched checkpoints of held-out races."""
    byid = {}
    for path in sorted((dataset.ROOT / "utmb").glob("*/*.json.gz")):
        byid[(path.parent.name.rsplit("_", 1)[0], path.parent.name.rsplit("_", 1)[1], path.name.replace(".json.gz", ""))] = path
    res = {"races": [], "tot": {}}
    agg = {}
    for r in test_races:
        stem = r["id"].split("/")[-1]
        prev_years = sorted(y for (e, y, s) in byid if e == r["event"] and s == stem and y < r["year"])
        if not prev_years:
            continue
        prev = dataset.load_utmb(byid[(r["event"], prev_years[-1], stem)])
        if not prev or len(prev["runners"]) < 10:
            continue
        names = pe.same_points(prev, r)
        if not names:
            continue
        shares = pe.shares(prev)
        prob = Problem([r])
        preds = {name: prob.predict(x, mode) for name, (x, mode) in xs.items()}
        entry = {"race": r["id"], "prev": f"{r['event']}_{prev_years[-1]}", "matched_cps": len(names), "of": len(r["cps"]) - 2}
        for p, (_, lo, hi, mem) in enumerate(prob.pairs):
            gl = f"{lo}-{hi}h"
            gaps = {k: [] for k in ["prev"] + [f"{m}" for m in xs] + [f"blend_{m}" for m in xs]}
            for x in mem:
                T = x["mv"][-1]
                for i, c in enumerate(r["cps"][1:-1], start=1):
                    a = x["mv"][i]
                    if a is None or c["name"] not in names:
                        continue
                    s = pe.predict_share(shares, c["name"], T / 3600)
                    if s is None:
                        continue
                    pp = s * T
                    gaps["prev"].append(abs(pp - a) / 60)
                    for m, pred in preds.items():
                        pm = pred[p, i] * T / pred[p, prob.last[p]]
                        gaps[m].append(abs(pm - a) / 60)
                        gaps[f"blend_{m}"].append(abs((pm + pp) / 2 - a) / 60)
            if not gaps["prev"]:
                continue
            for k, v in gaps.items():
                for lab in (gl, "ALL"):
                    a = agg.setdefault(lab, {}).setdefault(k, [0, 0.0])
                    a[0] += len(mem)
                    a[1] += len(mem) * statistics.mean(v)
            entry[gl] = {k: round(statistics.mean(v), 1) for k, v in gaps.items()}
        res["races"].append(entry)
    order = [f"{lo}-{hi}h" for lo, hi in GROUPS] + ["ALL"]
    res["tot"] = {lab: {k: (v[0], v[1] / v[0]) for k, v in agg[lab].items()} for lab in order if lab in agg}
    return res


def per_race(prob, preds):
    """{race id: (runners, {model: mean |gap|})} over the race's level groups (runner-weighted)."""
    gaps = {k: prob.pair_gaps(p)[0] for k, p in preds.items()}
    out = {}
    for p in np.where(prob.has_obs)[0]:
        rid = prob.races[prob.pairs[p][0]]["id"]
        d = out.setdefault(rid, [0.0, {k: 0.0 for k in preds}])
        d[0] += prob.nrun[p]
        for k in preds:
            d[1][k] += prob.nrun[p] * gaps[k][p]
    return {rid: (int(n), {k: v / n for k, v in s.items()}) for rid, (n, s) in out.items()}


def livetrail_eval(lt_races, models, say):
    """The models on LiveTrail courses (no GPS track: profile from section D+ / D-, no clock)."""
    prob = Problem(lt_races)
    preds = {k: prob.predict(x, m) for k, (x, m) in models.items()}
    cols = list(models)
    say(f"  {len(lt_races)} courses, {sum(len(r['runners']) for r in lt_races)} finishers, {len(prob.pairs)} (course, level group) pairs, "
        f"{len(prob.op)} checkpoint passages scored; events: {len({r['event'] for r in lt_races})}")
    say("\n" + fmt_table("LIVETRAIL mean |gap| (min) by level group", cols, compare(prob, preds, "by_group")))
    say("\n" + fmt_table("LIVETRAIL mean |gap| (min) by distance band", cols, compare(prob, preds, "by_band")))
    pr = per_race(prob, preds)
    res = {"courses": len(lt_races), "finishers": sum(len(r["runners"]) for r in lt_races), "events": len({r["event"] for r in lt_races}),
           "passages_scored": int(len(prob.op)), "mean_abs_gap": {k: round(prob.score(p), 2) for k, p in preds.items()}}
    if "current" in models:
        for k in models:
            if k != "current":
                res[f"courses_improved_{k}_vs_current"] = sum(v[k] < v["current"] for _, v in pr.values())
        say("  courses where each model beats current: " + ", ".join(f"{k} {res[f'courses_improved_{k}_vs_current']}/{len(pr)}" for k in models if k != "current"))
    res["courses_scored"] = len(pr)
    return res


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=FIT_DIR + "/fitdata.pkl")
    ap.add_argument("--livetrail", default=FIT_DIR + "/livetrail.pkl", help="LiveTrail courses (fit_data.py --livetrail); scored last")
    ap.add_argument("--params", default=str(HERE / "fitted_params.json"))
    ap.add_argument("--results", default=str(HERE / "fit_results.txt"))
    ap.add_argument("--form", choices=list(FORMS), help="skip the CV: fit this form with --lam-c / --lam-t")
    ap.add_argument("--lam-c", type=float, default=0.3)
    ap.add_argument("--lam-t", type=float, default=1.0)
    a = ap.parse_args()
    if not os.path.exists(a.data):
        raise SystemExit(f"{a.data} missing: run fit_data.py first")
    races = pickle.load(open(a.data, "rb"))
    train, test, test_events = split(races)
    lines = []

    def say(s=""):
        print(s, flush=True)
        lines.append(s)

    def band_counts(rs_):
        out = {}
        for lo, hi in BANDS:
            sel = [r for r in rs_ if lo <= r["km"] < hi]
            out[f"{lo}-{min(hi, 180)} km"] = (len(sel), sum(len(r["runners"]) for r in sel))
        return out

    say(f"DATASET: {len(races)} UTMB Live running races 40-180 km with a GPS track, {sum(len(r['runners']) for r in races)} finishers "
        f"(all those stored: up to {max(len(r['runners']) for r in races)} per race, the fastest third whole then an even sample by rank)")
    for nm, s in (("train", train), ("test", test)):
        say(f"  {nm}: {len(s)} races, {sum(len(r['runners']) for r in s)} finishers, events: {', '.join(sorted({r['event'] for r in s}))}")
        say("    by band (races, finishers): " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in band_counts(s).items()))

    ptr, pte = Problem(train), Problem(test)
    say(f"  train pairs (race x level group) {len(ptr.pairs)}, obs {len(ptr.op)}; test pairs {len(pte.pairs)}, obs {len(pte.op)}")

    x0 = x0_from_production()
    # sanity: the fast path at the current app's parameters is close to the app's own path
    say(f"\nSANITY train: legacy {ptr.score(ptr.predict(None, 'legacy')):.2f}, current (app path) {ptr.score(ptr.predict(None, 'current')):.2f}, "
        f"fast path at the current parameters {ptr.score(ptr.predict(x0)):.2f}")

    if a.form:
        best = (a.form, a.lam_c, a.lam_t)
        say(f"\nMODEL SELECTION skipped: {best}")
    else:
        say("\nMODEL SELECTION: 2-fold cross-validation by event inside TRAIN (test untouched)")
        best, _ = cross_validate(train, say)
        say(f"  selected: form {best[0]}, lam_c {best[1]}, lam_t {best[2]}")
    form, lam_c, lam_t = best

    say("\nFITTING on the whole train set")
    _CV["train"] = ptr
    jobs = [("hours", lam_c, lam_t), ("general", lam_c, lam_t), ("general+level", lam_c, lam_t), ("curcurve", 0.0, lam_t)]
    import multiprocessing as mp

    with mp.get_context("fork").Pool(4) as pool:
        xs = dict(zip(["fit_hours", "fit_general", "fit_general+level", "curcurve_fitrest"], pool.map(_final_job, jobs)))
    for k, (f_, lc, lt) in zip(xs, jobs):
        say(f"  {k}: form {f_} lam_c {lc} lam_t {lt}: train mean |gap| {ptr.score(ptr.predict(xs[k])):.2f}")
    sel = {"hours": "fit_hours", "general": "fit_general", "general+level": "fit_general+level", "curcurve": "curcurve_fitrest"}[form]
    models = {"legacy": (None, "legacy"), "current": (None, "current")}
    models.update({k: (x, "fit") for k, x in xs.items()})
    models["fit"] = models[sel]
    x_fit = xs[sel]
    say("\nTRAIN mean |gap| (min): " + ", ".join(f"{k} {ptr.score(ptr.predict(x, m)):.2f}" for k, (x, m) in models.items()))

    preds = {k: pte.predict(x, m) for k, (x, m) in models.items()}
    cols = [k for k in models if k != "fit"]
    say("\n" + fmt_table("HELD-OUT mean |gap| (min) by level group", cols, compare(pte, preds, "by_group")))
    say("\n" + fmt_table("HELD-OUT mean |gap| (min) by distance band", cols, compare(pte, preds, "by_band")))
    m_cur, _ = pte.pair_gaps(preds["current"])
    m_fit, _ = pte.pair_gaps(preds["fit"])
    ok = pte.has_obs
    say(f"  (race, group) pairs where the selected fit ({sel}) beats current: {int(np.sum(m_fit[ok] < m_cur[ok]))}/{int(ok.sum())}")
    pr = per_race(pte, {k: preds[k] for k in ("legacy", "current", "fit")})
    say(f"\nHELD-OUT per race: runners, legacy, current, selected fit ({sel})")
    for rid, (n, v) in sorted(pr.items()):
        say(f"  {rid:40s}{n:5d}{v['legacy']:8.1f}{v['current']:8.1f}{v['fit']:8.1f}")
    improved = sum(v["fit"] < v["current"] for _, v in pr.values())
    say(f"  races where the fit beats current: {improved}/{len(pr)}; beats legacy: {sum(v['fit'] < v['legacy'] for _, v in pr.values())}/{len(pr)}")

    gmodels = {k: models[k] for k in GUARD_MODELS}
    guards = {}
    for rid in ("utmb/transjeju_2025/100m", OWNER_RACE):
        race = next((r for r in races if r["id"] == rid), None)
        g = guard(race, gmodels) if race else None
        if g:
            guards[rid] = g
            say(f"\nGUARD {rid}, finishers 16-21 h moving ({g['runners']} runners, median {g['median_finish_h']} h)")
            say_guard(say, g, GUARD_MODELS)

    own_race = next((r for r in races if r["id"] == OWNER_RACE), None)
    own = owner_runner(own_race, {k: models[k] for k in ("legacy", "current", "fit")}) if own_race else None
    if own:
        say(f"\nOWNER {OWNER_RACE} bib {OWNER_BIB}: official {own['actual_s'] / 3600:.2f} h, moving {own['moving_s'] / 3600:.2f} h; "
            "plan shape at his own finish, mean |gap| at checkpoints: "
            + ", ".join(f"{k} {own[f'mean_abs_gap_{k}']}" for k in ("legacy", "current", "fit")))
        for c in own["checkpoints"]:
            say(f"  {str(c['cp']):22s}{c['km']:6.1f}  actual {c['actual_moving_s'] / 3600:6.2f} h  "
                + "  ".join(f"{k} {(c[f'{k}_s'] - c['actual_moving_s']) / 60:+6.1f}" for k in ("legacy", "current", "fit")))
    tj26 = next((r for r in races if r["id"] == OWNER_RACE), None)
    tj26_all = None
    if tj26:
        p26 = Problem([tj26])
        tj26_all = {k: round(p26.score(p26.predict(x, m)), 1) for k, (x, m) in models.items()}
        say(f"  {OWNER_RACE} all finishers by level group: " + ", ".join(f"{k} {v}" for k, v in tj26_all.items()))

    lt = None
    if a.livetrail and os.path.exists(a.livetrail):
        say("\nLIVETRAIL (extra validation, never used for selection: no GPS track, profile from section D+/D-, night not scored)")
        lt_races = pickle.load(open(a.livetrail, "rb"))
        lt = livetrail_eval(lt_races, {k: models[k] for k in ("legacy", "current", "fit_hours", "fit_general", "curcurve_fitrest")}, say)

    pv = prev_edition_eval(test, {k: models[k] for k in ("current", "fit")})
    say(f"\nPREVIOUS EDITION on held-out races with an earlier edition on matched checkpoints ({len(pv['races'])} races)")
    for e in pv["races"]:
        say(f"  {e['race']} <- {e['prev']}: {e['matched_cps']}/{e['of']} intermediate checkpoints matched")
    ks = ["current", "fit", "prev", "blend_current", "blend_fit"]
    say(f"  {'':12s}{'runners':>9s}" + "".join(f"{k:>18s}" for k in ks))
    for lab, v in pv["tot"].items():
        say(f"  {lab:12s}{v['prev'][0]:9d}" + "".join(f"{v[k][1]:18.1f}" for k in ks))
    say("  (fit = the selected model; blend = mean of the model's and the previous edition's time at each matched checkpoint)")

    summary = {
        "test_events": test_events, "held_out_races": len(pr), "held_out_runners": int(pte.nrun[pte.has_obs].sum()),
        "train_races": len(train), "train_finishers": sum(len(r["runners"]) for r in train),
        "fit_races": len(races), "fit_finishers": sum(len(r["runners"]) for r in races),
        "mean_abs_gap": {k: round(pte.score(p), 2) for k, p in preds.items()},
        "races_improved_vs_current": improved,
        "guards": {rid: {k: g[f"mean_abs_gap_{k}"] for k in GUARD_MODELS} | {"runners": g["runners"]} for rid, g in guards.items()},
        "owner": own, "transjeju_2026_all": tj26_all, "livetrail": lt,
        "per_race": {rid: {"runners": n} | {k: round(v_, 2) for k, v_ in v.items()} for rid, (n, v) in pr.items()},
    }
    params = {
        "about": "Fitted by scripts/race_data/fit_model.py on UTMB Live results (train events only); see fit_results.txt",
        "train_events": sorted({r["event"] for r in train}), "test_events": test_events,
        "selected": {"model": sel, "form": form, "lam_c": lam_c, "lam_t": lam_t},
        "fit": describe(x_fit),
        "variants": {k: describe(x) for k, x in xs.items()},
        "raw_x": {**{k: x.tolist() for k, x in xs.items()}, "names": NAMES},
        "validation": summary,
        "guards_16_21h": guards,
        "prev_edition": pv,
    }
    json.dump(params, open(a.params, "w"), indent=1, default=float)
    say("\nFITTED PARAMETERS (curve factors relative to flat, at a 20 h level unless noted; read at -20..30 %)")
    for k, x in xs.items():
        d = describe(x)
        say(f"  {k}: curve {dict(zip(d['knots_pct'], d['curve_at_20h']))}")
        say(f"     gamma_up {d['gamma_up']} gamma_down {d['gamma_down']}; fatigue c {d['fatigue']['c']} q {d['fatigue']['q']} r {d['fatigue']['r']} "
            f"p {d['fatigue']['p']} fresh {d['fatigue']['fresh']}; night {d['night']['night']}; terrain {d['terrain']}")
    for lvl, cv in params["variants"]["fit_general+level"]["curve_by_level_h"].items():
        say(f"  fit_general+level, curve at {lvl}: {cv}")
    say(f"  selected ({sel}) integer table: {params['fit']['integer_table_20h']}")
    open(a.results, "w").write("\n".join(lines) + "\n")
    print(f"\n-> {a.params}\n-> {a.results}")


if __name__ == "__main__":
    main()
