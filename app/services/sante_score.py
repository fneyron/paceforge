"""Santé v4: « Récupération », PaceForge's own 0–100 score (owner decisions
2026-10-07, « un score juste comme WHOOP, plus simple », and 2026-10-08:
past activities only, no check-in, no race; after the visual judges: the
state comes from the score, like WHOOP; v4.2, « une approche scientifique »:
every rule checked against the official texts, research_recovery.md §2–§3).
No brand metric enters it. No official body gives weights, a formula or
thresholds for such a score (Kellmann 2018: « multivariate approaches … on a
formal or informal level »; BASES 2023: these scores « require validation »):
every number below is (H) unless cited.

raw (0–100) is the weighted mean of the components present, each scored 0–100
only when its own data rule holds:
1. VFC · 7 nuits (≥ 3 usable nights in the last 7 and a band, full or
   « provisoire »): z = (ln of the 7-night mean − ln of the band's centre) /
   the band's SD of ln RMSSD; 100 from z ≥ −0.5 (the trials' convention:
   mean ± 0.5 SD), linear to 0 at z = −2.5 (H). Above the band = 100: never
   above, never praised (Plews 2013; Bellenger 2016).
2. FC de nuit · 7 nuits (the same rule): 100 while the mean is ≤ the band's
   median + 2 bpm, 40 at + 5, 0 at + 8, linear between (H; anchors: + 4 bpm
   on 2 nights, Alavi 2022's alert; ≈ + 4,5 bpm in a short overload, Bosquet
   2008; ≈ + 6 % when sick, Altini & Plews 2021).
   During the illness alert it reads the alert's own 2 nights, at most 39
   (its facts row red, « nettement au-dessus »): never green under the alert.
3. Sommeil · 24 h (a main night this morning; the naps of the 24 h before
   its wake count, nights.day_tst24): ≥ 7 h → 100 (AASM/SRS: Watson 2015a;
   NSF: Hirshkowitz 2015), 6 h → 60 (Craven 2022: sleep loss is ≤ 6 h per
   24 h), ≤ 4 h → 0, linear between (H); never less for a long night (no
   ceiling: Watson 2015a); with a 7-day mean (≥ 3 usable days) and a usual
   (the 24-h band's median) at most 100 − 2/3 point per minute of that mean
   under the usual (90 min under → 40, H).
4. Charge récente, only while a recovery window is open (sante_training's
   effort windows): 50 after a Longue, 30 after a Très longue, 20 after an
   Ultra (the lowest of the windows open). Outside a window it is no
   component at all: a constant 100 would only dilute the other signals
   (OECD/JRC 2008 on compensation); the week's volume is Activités'.
Weights (H): VFC 25, FC de nuit 25, Sommeil 30, Charge 20, renormalised over
the components present (the two heart signals together at most half: one
domain never counted twice, OECD/JRC 2008; sleep the largest single weight,
the recovery behaviour every consensus text names). Without a nightly
component (VFC, FC de nuit or Sommeil) no score, but during a recovery
window: the activities then say the athlete is recovering, so the score is
the window's cap (nothing measured says otherwise: raw is taken as 100 and
the caps bind), « estimé » (v4.3: its bar hatched in the 14-day card, the
word under the ring), the activities being all it knows (owner: « tu te
bases que sur les activités passées pour juger de la récupération »).
Caps on raw (H), the lowest binds: the nightly-HR illness alert (2 nights,
nights.illness_alert) → 39; a recovery window (sante_training.EFFORT_RULES:
35, 45 or 65 by class and day); a 24-h total under 6 h → 65 (6 h: Craven
2022; 65: PaceForge's); VFC under its band (z < −0.5) AND FC de nuit ≥ its
median + 3 bpm → 69 (Buchheit 2014, Table 2: rMSSD down with HR up,
« accumulated fatigue »); a red component (sub-score < 40) → 69, where FC de
nuit, Sommeil and Charge always count but VFC only when FC de nuit is
measured and its mean is > median + 2 bpm (rMSSD down with HR down is the
« saturation » of a well-trained athlete, « coping well with training »:
Buchheit 2014, Table 2; resting HRV « largely unaffected by overreaching »:
Bellenger 2016; Meeusen 2013); neither VFC nor FC de nuit in the score → 80
(sleep and load alone never say « fully recovered »).
score = the capped raw, rounded half up on the exact value (snapped to 1e-9:
64,5 is 65, never 64,4999…). The state is its band (sante_today.state):
≥ 70 « Bonne récupération », 40–69 « Récupération en cours », < 40
« Récupération faible » (H): the number and the state never disagree. The
main reason (`reason`: the alert, else the cap that binds, else the joint
VFC/FC pattern, else the lowest component) is data: the page prints the
alert's sentence only (v4.3, owner: « mets juste les scores »). A difference
of a few points means nothing (BASES 2023: unvalidated; the HRV moves ≈ 12 %
night to night, Buchheit 2014): the method fold says so.
No sub-score is shown (v4.4, owner: « Sommeil 100, Charge récente 20 …
on comprend rien »: « Détail du score » is gone): the page shows the score,
its state and plain facts (sante._top). A heart signal's colour is its place
against the normal, the same on its facts row and on its card's status line
(row_tone): green in it, orange out of it on the side that matters (VFC
under, FC de nuit over), red when its note is under 40 and counts for the 69
cap (never a red VFC that caps nothing), at least orange under the joint
cap, red under the alert, neutral on the other side (never praised).
History: the 13 days before today are recomputed from what is stored, each
from what was known that day, with the same rule (sante._history); nothing is
persisted.
"""
import math
import re
import statistics
from datetime import date

from app.services import viz
from app.services.nights import SHORT_DAY_MIN

BANDS = {"ok": (70, 100), "warn": (40, 69), "danger": (0, 39)}  # (H) the state is the band of the score
WEIGHTS = {"hrv": 25, "hr": 25, "sleep": 30, "load": 20}  # (H) research_recovery.md §3.2; Charge only in a window
ORDER = ("hrv", "hr", "sleep", "load")
NIGHTLY = ("hrv", "hr", "sleep")  # no score without one of them (but in a recovery window)
HRV_FULL_Z, HRV_ZERO_Z = -0.5, -2.5  # z of ln RMSSD: 100 from the band's floor (the trials' ± 0.5 SD), 0 at −2.5 (H)
HR_FULL_BPM, HR_MID_BPM, HR_ZERO_BPM = 2, 5, 8  # (H) over the band's median: 100, 40, 0 (Alavi 2022; Bosquet 2008)
HR_MID_SUB = 40  # (H) the FC de nuit sub-score at + 5 bpm
SLEEP_POINTS = ((240, 0), (360, 60), (420, 100))  # (H) 24-h minutes → sub-score (7 h: Watson 2015a; 6 h: Craven 2022)
SLEEP_DEBT = 2 / 3  # (H) points off per minute of the 7-day mean under the usual
CAP_ILL = 39  # (H) the nightly-HR illness alert: « Récupération faible »
CAP_RED, RED_SUB = 69, 40  # (H) a component under 40: never a green ring over a red contributor
CAP_JOINT, JOINT_HR_BPM = 69, 3  # (H) VFC under its band and FC de nuit ≥ median + 3 bpm (Buchheit 2014, Table 2)
CAP_SHORT = 65  # (H) a 24-h total under 6 h (the 6 h: Craven 2022; the 65: PaceForge's)
CAP_NO_HEART = 80  # (H) neither VFC nor FC de nuit in the score: sleep and Charge alone never make a 100
CAPS = ("ill", "effort", "short", "joint", "red", "no_heart")  # equal caps: the first names the reason
HEART = ("hrv", "hr")
HISTORY_DAYS = 14  # the Récupération card
HISTORY_NIGHTS = 160  # days of nights a past day reads: its alert episodes (67 days, each on a 60-day band)

# « Comment je calcule ta récupération » (v4.3, owner: « c'est trop d'explication, simplifie et synthétise, ne mets
# pas les citations »): 6 one-line bullets in plain words, no citation, no « (H) » (the heuristics stay marked in
# the code and the tests); one plain bullet on the cap after a big outing, the only outing Santé mentions (« Ne
# mentionne pas les sorties dans la partie Santé »: bullet 1 names « tes gros efforts récents », v4.4); the
# references of both folds are on /sante/sources (REFS, sante_sleep.REFS)
METHOD = [
    "Ton score sur 100 combine ton sommeil, ta VFC, ta FC de nuit et tes gros efforts récents.",
    "Chaque signal est comparé à ta propre normale, celle de tes 60 derniers jours (7 nuits au moins).",
    "Après une grosse sortie (3 h, 6 h, 10 h et plus), le score reste plafonné quelques jours, jusqu'à 2 semaines "
    "après un ultra.",
    "Une nuit sous 6 h ou une FC de nuit nettement haute font baisser ton état.",
    "70 et plus : bonne récupération ; 40 à 69 : en cours ; moins de 40 : faible.",
    "Une estimation : quelques points d'écart ne veulent rien dire.",
]
# the references both folds rest on, listed on /sante/sources (label, DOI or URL): « Textes officiels » (consensus,
# position stands, guidelines), then « Études »
REFS = [
    ("Textes officiels", [
        ("Kellmann 2018", "10.1123/ijspp.2017-0759"), ("Meeusen 2013", "10.1249/MSS.0b013e318279a10a"),
        ("Watson 2015a", "10.5665/sleep.4716"), ("Hirshkowitz 2015", "10.1016/j.sleh.2014.12.010"),
        ("Schwellnus 2016", "10.1136/bjsports-2016-096572"), ("Walsh 2021", "10.1136/bjsports-2020-102025"),
        ("BASES 2023", "https://westminsterresearch.westminster.ac.uk/item/wxx7y/"
                       "bases-expert-statement-methods-to-monitor-athletes-sleep"),
        ("Sammito 2024", "10.1186/s12995-024-00414-9"), ("Quigley 2024", "10.1111/psyp.14604"),
    ]),
    ("Études", [
        ("Buchheit 2014", "10.3389/fphys.2014.00073"), ("Plews 2013", "10.1123/ijspp.8.6.688"),
        ("Plews 2014", "10.1123/ijspp.2013-0455"), ("Alavi 2022", "10.1038/s41591-021-01593-2"),
        ("Bosquet 2008", "10.1136/bjsm.2007.042200"), ("Saw 2016", "10.1136/bjsports-2015-094758"),
    ]),
]


def typo(method: list[str]) -> list[str]:
    """French typography for a fold at 358 px: a no-break space ties a number to its unit (« 6 h », « 10 min »,
    « +5 bpm », « 12 % », « 1 500 m », « 2 semaines », « 7 nuits ») and the groups of a large number, so a line
    never ends on « 6 »; and one before « : » and « ; »."""
    def tie(t: str) -> str:
        t = re.sub(r" ([=≈+]) ", "\u00a0\\1\u00a0", t)  # « Paradoxal = REM », « N1 + N2 »
        t = re.sub(r"(\d) (?=\d{3}\b)", "\\1\u00a0", t)
        t = re.sub(r" ([:;])", "\u00a0\\1", t)
        return re.sub(r"(\d) (?=(?:h|min|bpm|ms|m|%|semaines?|jours?|nuits?)(?![\w]))", "\\1\u00a0", t)
    return [tie(b) for b in method]


def flat(method: list[str]) -> str:
    """A fold's text in one string, for reading it whole."""
    return " ".join(method)


def linked(refs: list) -> list:
    """[(group, [(label, href)])]: a DOI as its doi.org link, a plain URL as it is (BASES 2023, CTA/NSF)."""
    return [(group, [(name, link if link.startswith("http") else f"https://doi.org/{link}") for name, link in items])
            for group, items in refs]


def _lin(x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    """y0 up to x0, y1 from x1, linear between (x0 < x1)."""
    if x <= x0:
        return float(y0)
    if x >= x1:
        return float(y1)
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


# ── components ──────────────────────────────────────────────────────────────

def hrv_sub(mean: float, band: dict) -> float:
    """100 from z ≥ −0.5 (above the band too: never praised), 0 at z = −2.5."""
    z = (math.log(mean) - math.log(band["center"])) / band["sd"]
    return _lin(z, HRV_ZERO_Z, 0, HRV_FULL_Z, 100)


def hr_sub(mean: float, band: dict) -> float:
    """100 while the 7-night mean is ≤ the band's median + 2 bpm, 40 at + 5,
    0 at + 8, linear between (H; Alavi 2022, Bosquet 2008)."""
    d = mean - band["center"]
    return _lin(d, HR_FULL_BPM, 100, HR_MID_BPM, HR_MID_SUB) if d <= HR_MID_BPM \
        else _lin(d, HR_MID_BPM, HR_MID_SUB, HR_ZERO_BPM, 0)


def sleep_base(tst24: float) -> float:
    (a, ya), (b, yb), (c, yc) = SLEEP_POINTS
    return _lin(tst24, a, ya, b, yb) if tst24 < b else _lin(tst24, b, yb, c, yc)


def sleep_sub(tst24: float, mean7: float | None = None, usual: float | None = None) -> float:
    """≥ 7 h → 100, 6 h → 60, ≤ 4 h → 0, linear between; at most 100 − 2/3 per
    minute of the 7-day mean under the usual, when both are there. Never less
    for a long night: no ceiling (Watson 2015a)."""
    sub = sleep_base(tst24)
    if mean7 is not None and usual is not None:
        sub = min(sub, max(0.0, 100 - SLEEP_DEBT * max(0.0, usual - mean7)))
    return sub


def load_sub(window: dict | None) -> float:
    """During a recovery window, its class's Charge (50, 30 or 20, H)."""
    return float(window["load"]) if window else 100.0


def components(day: dict) -> tuple[list[dict], list[str]]:
    """([{key, sub, prov, red, joint, …}] present, [keys] missing) for one day
    (sante._assess): stats {hr, hrv: {value, normal, status, seen}}, tst24,
    sleep {mean7, usual, prov}, window. Charge récente is a component only
    while a window is open, and never « missing » outside one. `red`: the
    component counts for the 69 cap (VFC only with FC de nuit measured and
    over its median + 2 bpm); `joint`: VFC under its band with FC de nuit ≥
    median + 3 bpm (both rows)."""
    parts, absent = [], []
    for key, fn in (("hrv", hrv_sub), ("hr", hr_sub)):
        s = day["stats"][key]
        if s["value"] is not None and s["normal"]:
            sub, alert = fn(s["value"], s["normal"]), key == "hr" and bool(day["alert"])
            if alert:  # the alert's own 2 nights, red at most: never « dans ta normale » under the alert
                sub = min(sub, fn(statistics.fmean(day["alert"]["values"]), s["normal"]), RED_SUB - 1)
            parts.append({"key": key, "sub": sub, "prov": bool(s["normal"]["provisional"]), "alert": alert,
                          "status": s["status"], "delta": s["value"] - s["normal"]["center"]})
        else:
            absent.append(key)
    tst = day["tst24"]
    if tst is not None:
        sl = day["sleep"]
        sub = sleep_sub(tst, sl["mean7"], sl["usual"])
        parts.append({"key": "sleep", "sub": sub, "prov": bool(sl["prov"]), "tst24": tst,
                      "debt": sub < sleep_base(tst)})
    else:
        absent.append("sleep")
    w = day["window"]
    if w:
        parts.append({"key": "load", "sub": load_sub(w), "prov": False, "window": w})
    by = {p["key"]: p for p in parts}
    hrv, hr = by.get("hrv"), by.get("hr")
    up = hr["delta"] if hr else None  # FC de nuit's 7-night mean over its median (None: not measured)
    for p in parts:
        p["red"] = p["sub"] < RED_SUB and (p["key"] != "hrv" or (up is not None and up > HR_FULL_BPM))
        p["joint"] = False
    if hrv and hr and hrv["status"] == "below" and up >= JOINT_HR_BPM:
        hrv["joint"] = hr["joint"] = True
    total = sum(WEIGHTS[p["key"]] for p in parts)
    for p in parts:
        p["weight"] = WEIGHTS[p["key"]] / total if total else 0
    return parts, absent


def caps(day: dict, parts: list[dict]) -> list[tuple[float, str]]:
    """[(cap on raw, key)] of the rules that hold on this day (`parts`: its
    components): ill, effort, short, joint, red, no_heart (see the module
    docstring)."""
    out = []
    if day["alert"]:
        out.append((CAP_ILL, "ill"))
    if day["window"]:
        out.append((day["window"]["cap"], "effort"))
    if day["tst24"] is not None and day["tst24"] < SHORT_DAY_MIN:
        out.append((CAP_SHORT, "short"))
    if any(p["joint"] for p in parts):
        out.append((CAP_JOINT, "joint"))
    if any(p["red"] for p in parts):
        out.append((CAP_RED, "red"))
    if not any(p["key"] in HEART for p in parts):
        out.append((CAP_NO_HEART, "no_heart"))
    return out


def tone_of(value: float) -> str:
    """The state is the band of the score: ok ≥ 70, warn 40–69, danger < 40."""
    return next(tone for tone, (lo, _) in BANDS.items() if value >= lo)


def rounded(x: float) -> int:
    """Half up on the exact value (snapped to 1e-9: 64,5 is 65, never 64,4999… → 64)."""
    return int(math.floor(round(x, 9) + 0.5))


def reason(tone: str, binding: list[str], parts: list[dict], day: dict) -> str | None:
    """What the state's sentence names, None on a green day: the illness alert
    (whenever it holds), else the cap that binds (effort, short), else the
    joint VFC/FC pattern (whenever it holds: it explains the 69), else the
    lowest component (a red one under its cap, or the plain lowest): hrv, hr,
    sleep (« short » under 6 h), load (its recovery window: « effort »)."""
    if tone == "ok":
        return None
    if day["alert"]:
        return "ill"
    if binding and binding[0] in ("effort", "short"):
        return binding[0]
    if any(p.get("joint") for p in parts):
        return "joint"
    low = min(parts, key=lambda p: (p["sub"], ORDER.index(p["key"])))["key"]
    if low == "load":
        return "effort" if day["window"] else None
    if low == "sleep" and day["tst24"] is not None and day["tst24"] < SHORT_DAY_MIN:
        return "short"
    return low


def building(day: dict, absent: list[str]) -> list[str]:
    """The heart signals missing from the score because their normal is still being built: measured lately (in
    the 60 days of a band and the week after them) but no band yet (fewer than 7 usable nights, H)."""
    return [k for k in HEART if k in absent and day["stats"][k].get("seen") and not day["stats"][k]["normal"]]


def score_of(day: dict) -> dict:
    """{value, tone, raw, raw0, parts, absent, building, caps (binding keys,
    the lowest first), reason, measured, estimated} for one day, or {value:
    None, …} without a nightly component outside a recovery window (then no
    state either). In a window without a nightly component, raw is 100
    (nothing measured says otherwise) and the caps bind: the score is the
    window's cap, « estimé » (v4.3)."""
    parts, absent = components(day)
    measured = any(p["key"] in NIGHTLY for p in parts)
    if not measured and not day["window"]:
        return {"value": None, "tone": None, "parts": parts, "absent": absent, "building": building(day, absent),
                "caps": [], "reason": None, "measured": False, "estimated": False}
    raw0 = round(sum(p["sub"] * p["weight"] for p in parts), 9) if measured else 100.0  # 49,99999999999999 is 50
    held = caps(day, parts)
    raw = min([raw0] + [c for c, _ in held])
    value = rounded(raw)
    tone = tone_of(value)
    binding = [k for _, _, k in sorted((c, CAPS.index(k), k) for c, k in held if c < raw0)]
    return {"value": value, "tone": tone, "raw": raw, "raw0": raw0, "parts": parts, "absent": absent,
            "building": building(day, absent), "caps": binding, "reason": reason(tone, binding, parts, day),
            "measured": measured, "estimated": not measured}


# ── what the page draws ─────────────────────────────────────────────────────

ESTIMATED = "estimé"  # a score from a recovery window alone, no night measured (v4.3)
EST_READ = "estimé, sans nuit mesurée"  # its readout in the 14-day card (v4.3, owner: no outing named on Santé)


def ring(score: dict, state: dict | None, href: str | None = None) -> dict:
    """The Récupération ring, the only one (v4.4): the score, the arc in the
    state's colour; no label under it (the state's word next to it names it:
    « Récupération en cours »), only « estimé » when no night was measured
    (the window's cap alone, v4.3). `href`: the section it sums up, None when
    the page has none (a plain ring)."""
    v = score.get("value")
    if v is None:
        return viz.ring("recup", None, "—", None, tone="none", href=href,
                        aria="Récupération : pas de score ce matin")
    est = bool(score.get("estimated"))
    return viz.ring("recup", v / 100, str(v), None, ESTIMATED if est else None, tone=state["tone"],
                    href=href, aria=f"Récupération {v} sur 100" + (", estimée sans nuit mesurée" if est else "")
                    + f". {state['aria']}")


def heart_tone(key: str, status: str | None, sub: float, red: bool = True, joint: bool = False,
               alert: bool = False) -> str:
    """A heart signal's colour: its place against the normal, the same on its facts row and on its card's status
    line (v4.3, owner: « est-ce que c'est bien ou pas bien ? »): green in it; orange out of it on the side that
    matters (VFC under it, FC de nuit over it), red when its note is under 40 and counts for the 69 cap (a VFC
    that caps nothing is never red: no red row under a green ring); at least orange under the joint cap (both
    signals), red under the illness alert (FC de nuit); neutral out on the other side (VFC over it, FC de nuit
    under it: never praised, Plews 2013; Bellenger 2016)."""
    if alert:
        return "danger"
    bad = status == ("below" if key == "hrv" else "above")
    if bad or joint:
        return "danger" if sub < RED_SUB and red else "warn"
    if status in ("above", "below"):
        return "accent"
    return "ok"


def row_tone(p: dict) -> str:
    """A heart component's colour (`p`: one of score_of's parts, VFC or FC de nuit): heart_tone on its status,
    its note, whether it counts for the 69 cap, the joint cap and the alert."""
    return heart_tone(p["key"], p.get("status"), p["sub"], p.get("red", True), p.get("joint", False),
                      p.get("alert", False))


def history_card(history: list[tuple[date, dict | None, dict]], today: date) -> dict | None:
    """« Récupération · 14 jours »: one bar per day with a score, in its
    state's colour, hatched when it was estimated without a night measured
    (v4.3), with faint 40 and 70 lines labelled on the right (the bands:
    never colour alone), today's day on a disc; tap a bar → « 64 »
    « ◐ Récupération en cours » / « mer. 7 oct. » (« · estimé, sans nuit
    mesurée »): the date, the score and the state only (v4.3, owner: « mets
    juste les scores »). It rests on the mean of the days with a score
    (nothing selected: today's score is the ring's). None under 2 days with a
    score."""
    days = [d for d, _, _ in history]
    with_score = [s["value"] for _, _, s in history if s.get("value") is not None]
    if len(with_score) < 2:
        return None
    values, classes, tones, est, r, a = [], [], [], [], [], []
    for d, st, s in history:
        v = s.get("value")
        values.append(v)
        classes.append(s["tone"] if v is not None else "")
        tones.append(s["tone"] if v is not None else "")
        e = v is not None and bool(s.get("estimated"))
        est.append(e)
        if v is None:
            r.append(["—", "", f"{viz.d_short(d)} · pas de score"])
            a.append(f"{viz.d_long(d)} : pas de score")
            continue
        r.append([str(v), f"{st['glyph']} {st['word']}", viz.d_short(d) + (f" · {EST_READ}" if e else "")])
        a.append(f"{viz.d_long(d)} : {v} sur 100, {st['word'].lower()}" + (", estimé sans nuit mesurée" if e else "")
                 + ".")
    c = viz.day_bars("recuperation", days, values, readouts=r, arias=a, classes=classes, tones=tones, y_max=100,
                     lines=((70, "70"), (40, "40")), today=len(days) - 1 if days and days[-1] == today else None,
                     hatched=est,
                     summary=f"Récupération sur {len(days)} jours : {len(with_score)} jours avec un score")
    mean = rounded(sum(with_score) / len(with_score))
    return viz.rest(c, [str(mean), "en moyenne", ""],
                    f"Récupération sur les {len(days)} derniers jours : {mean} en moyenne. Choisis un jour pour son "
                    "score et son état.", back=len(days) - 1)
