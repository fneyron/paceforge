"""Santé › Aujourd'hui: « Forme du jour », PaceForge's own 0–100 score (owner
decision 2026-10-07: « un score juste comme WHOOP, plus simple »; no brand
metric enters it).

The action stays the evidence's: the ladder (sante_today.decide) decides it
and its tone; the score is placed INSIDE the band of that tone, so the number
and the action can never disagree:
    ok → 70–100 « bon », easy → 40–69 « moyen », rest → 0–39 « bas »,
    score = low + (high − low) × raw / 100, rounded half up (on the exact
    value: raw and the placed value are snapped to 1e-9 first, so a tie such
    as 6,5 is never read as 6,4999…).
raw (0–100) is the weighted mean of the components present, each scored 0–100
only when its own data rule holds (every threshold is (H) unless cited):
1. VFC · 7 nuits (≥ 3 usable nights in the last 7 and a band, full or
   « provisoire »): z = (ln of the 7-night mean − ln of the band's centre) /
   the band's SD of ln RMSSD; 100 from z ≥ −0.5 (the band's floor), linear to
   0 at z = −2.5. Above the band = 100: never above, never praised (Plews
   2013; Le Meur 2013; Bellenger 2016).
2. FC de nuit · 7 nuits (the same rule): Δ = the 7-night mean − the band's
   median; 100 while Δ ≤ +2 bpm, linear to 0 at +10 bpm.
3. Sommeil · 24 h (a main episode this morning; naps count, nights.day_tst24):
   ≥ 7 h → 100 (Johnston 2020), 6 h → 60 (Craven 2022: sleep loss is ≤ 6 h
   per 24 h), ≤ 4 h → 0, linear between; with a 7-day mean (≥ 3 usable days)
   and a usual (the 24-h band's median) it is at most 100 − 2/3 point per
   minute of that mean under the usual (90 min under → 40). Not on race
   morning: the race eve is never judged (Lastella 2014; evidence row 23).
4. Charge (≥ 6 sessions in 42 days): 100; an outing on foot ≥ 3 h or
   ≥ 1 500 m D+ in the last 48 h (the ladder's legs rule) → 50; ≥ 6 h → 30.
   An outing on a race day is left out: after a race the cap does it, not
   this (sante._decide_day's `load`).
5. Ressenti (the check-in answered today): mieux 100 · comme d'habitude 85 ·
   moins bien 50; jambes, fatigue, stress −10 each; malade → 10; « alcool
   hier » −15 (Pietilä 2018); kept within 0–100.
Weights (H): VFC 30, FC 25, Sommeil 20, Charge 15, Ressenti 10, renormalised
over the components present.
Caps on raw, the ladder's own rules (they only matter when that rule did not
already set the tone): « Reprise » ≤ 60; after a race or an exceptional
outing J+1 → J+3 ≤ 40, then until intensity comes back (J+7, J+10 after
≥ 10 h; sante._post_race) ≤ 65; R7 (VFC under the band AND FC de nuit above
it or « moins bien », never in race week) ≤ 60; a short night (24-h total
< 6 h, R5) ≤ 65. « malade » or the illness alert set the tone to rest (R2).
No score: tone unknown (« Pas encore d'avis »; the spec's « connecte ta
montre ou Strava » only for an athlete with neither), or no signal at all
(a race in 0–2 days, say, with nothing measured: no number to place). One
signal is a score « basé sur 1 signal sur 5 ».
A difference of a few points means nothing: the method fold says so.
History: the last 14 days are recomputed from what is stored (nights,
check-ins, sessions, races; sante._history), each from the rows of its own
day and before only, as the page computed it that day; nothing is persisted —
no new table, and a night synced late corrects its own day.
"""
import math
import statistics
from datetime import date, timedelta

from app.services import nights as nt
from app.services import viz

BANDS = {"ok": (70, 100), "easy": (40, 69), "rest": (0, 39)}
WORDS = {"ok": "bon", "easy": "moyen", "rest": "bas"}
TONE_CLS = {"ok": "ok", "easy": "warn", "rest": "danger"}
WEIGHTS = {"hrv": 30, "hr": 25, "sleep": 20, "load": 15, "feel": 10}  # (H)
ORDER = ("hrv", "hr", "sleep", "load", "feel")
SHORT_NAMES = {"hrv": "VFC", "hr": "FC de nuit", "sleep": "Sommeil", "load": "Charge", "feel": "Ressenti"}
NAMES = {"hrv": "VFC · 7 nuits", "hr": "FC de nuit · 7 nuits", "sleep": "Sommeil · 24 h", "load": "Charge · 48 h",
         "feel": "Ressenti"}
ARIA_NAMES = {"hrv": "VFC", "hr": "FC de nuit", "sleep": "sommeil", "load": "charge", "feel": "ressenti"}
SPOKEN = {"hrv": "VFC sur 7 nuits", "hr": "FC de nuit sur 7 nuits", "sleep": "sommeil sur 24 heures",
          "load": "charge des dernières 48 heures", "feel": "ressenti"}
HRV_FULL_Z, HRV_ZERO_Z = -0.5, -2.5  # (H) z of ln RMSSD: 100 from the band's floor, 0 at −2.5
HR_FULL_BPM, HR_ZERO_BPM = 2, 10  # (H) the 7-night mean over the band's median
SLEEP_POINTS = ((240, 0), (360, 60), (420, 100))  # (H) 24-h minutes → sub-score (7 h: Johnston 2020; 6 h: Craven 2022)
SLEEP_DEBT = 2 / 3  # (H) points off per minute of the 7-day mean under the usual
LOAD_BIG, LOAD_HUGE, HUGE_MIN = 50, 30, 360  # (H) an outing ≥ 3 h or ≥ 1 500 m D+ in 48 h; ≥ 6 h
MIN_LOAD_SESSIONS = 6  # (H) sessions in 42 days, as the ladder's « séance prévue »
FEEL_BASE = {1: 100, 2: 85, 3: 50}  # (H) mieux · comme d'habitude · moins bien
FEEL_WHY, FEEL_SICK, FEEL_ALCOHOL = 10, 10, 15  # (H) per reason off; « malade »; « alcool hier » off (Pietilä 2018)
CAP_REPRISE, CAP_POST_EARLY, CAP_POST, CAP_LOW_HRV, CAP_SHORT = 60, 40, 65, 60, 65  # (H) caps on raw
POST_EARLY_DAYS = 3  # (H) J+1 → J+3 after a race or an exceptional outing
HISTORY_DAYS = 14  # the sparkline
NO_WATCH, RACE_EVE = "pas de montre", "veille de course"
NOT_COUNTED = ("normale en construction", "jours avec sieste", RACE_EVE)  # and « nuits … » (a tag, the race window)
HISTORY_NIGHTS = 160  # days of nights a past day reads: its alert episodes (67 days, each on a 60-day band)

METHOD = [
    "L'action vient d'abord, de règles tirées des études et de choix de PaceForge (H) ; le score se place dans sa "
    "plage : 70–100 « bon » (séance prévue), 40–69 « moyen » (facile), 0–39 « bas » (repos). Le chiffre et l'action "
    "ne peuvent pas se contredire.",
    "Il fait la moyenne de 5 signaux au plus, notés de 0 à 100, chacun seulement quand ses données suffisent : VFC sur "
    "7 nuits (poids 30), FC de nuit sur 7 nuits (25), sommeil sur 24 h siestes comprises (20), charge des 48 dernières "
    "heures (15), ton ressenti du matin (10). Les poids (H) se répartissent sur les signaux présents.",
    "VFC (H) : pleine note dans ta normale ou au-dessus (au-dessus n'est jamais un bonus : Plews 2013), zéro à 2,5 "
    "écarts-types en dessous. FC de nuit (H) : pleine note jusqu'à +2 bpm sur ta médiane, zéro à +10.",
    "Sommeil (H) : pleine note dès 7 h (le seuil de Johnston 2020), 60 à 6 h (le seuil de Craven 2022), 0 à 4 h ; au "
    "plus 40 quand ta moyenne sur 7 jours est 90 min sous ton habitude (2/3 de point en moins par minute). Le matin "
    "de la course, la nuit de la veille ne compte pas : elle ne dit rien de ta course (Lastella 2014).",
    "Charge (H) : 50 après 3 h ou 1 500 m D+ sur 48 h, 30 après 6 h. La course elle-même n'y entre pas : le plafond "
    "d'après course s'en charge.",
    "Ressenti (H) : ta propre note compte (Saw 2016) ; mieux 100, comme d'habitude 85, moins bien 50, −10 par raison "
    "(jambes, fatigue, stress), malade 10 ; alcool hier −15 (Pietilä 2018).",
    "Plafonds (H), les règles de l'action : reprise, jours après une course, VFC basse avec FC haute ou « moins "
    "bien », nuit de moins de 6 h. Malade ou alerte FC de nuit : plage repos.",
    "Une différence de quelques points ne veut rien dire : d'une nuit à l'autre la VFC varie d'environ 12 % "
    "(Buchheit 2014). Regarde l'action et la tendance sur 14 jours.",
    "Ta normale est « provisoire » de 7 à 14 nuits (H) : elle bouge encore. L'alerte FC de nuit attend 14 nuits pour "
    "rester spécifique (Quer 2021). Aucun score de marque n'y entre : peu sont validés (Doherty 2025).",
]
REFS = [
    ("Plews 2013", "10.1007/s40279-013-0071-8"), ("Johnston 2020", "10.1016/j.jsams.2019.10.013"),
    ("Lastella 2014", "10.1080/17461391.2012.660505"),
    ("Craven 2022", "10.1007/s40279-022-01706-y"), ("Saw 2016", "10.1136/bjsports-2015-094758"),
    ("Pietilä 2018", "10.2196/mental.9519"), ("Buchheit 2014", "10.3389/fphys.2014.00073"),
    ("Quer 2021", "10.1038/s41591-020-1123-x"), ("Doherty 2025", "10.1515/teb-2025-0001"),
]


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
    """100 while the 7-night mean is ≤ the band's median + 2 bpm, 0 at + 10 bpm."""
    return _lin(mean - band["center"], HR_FULL_BPM, 100, HR_ZERO_BPM, 0)


def sleep_sub(tst24: float, mean7: float | None = None, usual: float | None = None) -> float:
    """≥ 7 h → 100, 6 h → 60, ≤ 4 h → 0, linear between; at most 100 − 2/3 per
    minute of the 7-day mean under the usual, when both are there."""
    (a, ya), (b, yb), (c, yc) = SLEEP_POINTS
    sub = _lin(tst24, a, ya, b, yb) if tst24 < b else _lin(tst24, b, yb, c, yc)
    if mean7 is not None and usual is not None:
        sub = min(sub, max(0.0, 100 - SLEEP_DEBT * max(0.0, usual - mean7)))
    return sub


def load_sub(big) -> float:
    """100; 50 after an outing ≥ 3 h or ≥ 1 500 m D+ in the last 48 h; 30 after ≥ 6 h."""
    if big is None:
        return 100.0
    return float(LOAD_HUGE if big.minutes >= HUGE_MIN else LOAD_BIG)


def feel_sub(feel: dict) -> float:
    """mieux 100 · comme d'habitude 85 · moins bien 50; −10 per jambes, fatigue,
    stress; malade → 10; « alcool hier » −15; within 0–100."""
    why = set(feel.get("why") or []) if feel["value"] == 3 else set()
    sub = FEEL_SICK if "sick" in why else FEEL_BASE[feel["value"]] - FEEL_WHY * len(why & {"legs", "fatigue", "stress"})
    if feel.get("alcohol"):
        sub -= FEEL_ALCOHOL
    return float(max(0, min(100, sub)))


def _why_night(nights, metric: str, d: date, stat: dict) -> str:
    """Why a nightly component is missing, in a few words."""
    if stat["value"] is not None:
        return "normale en construction"
    week = [n for n in (nights.get(d - timedelta(days=k)) for k in range(6, -1, -1))
            if n and n.value(metric) is not None]
    if len(week) < nt.MIN_MEAN_NIGHTS:
        return "pas assez de nuits"
    if any("race" in n.tags for n in week):
        return "nuits autour de la course"
    if metric == "hr" and any(n.hr_nap_day and not n.usable("hr") for n in week):
        return "jours avec sieste"
    tags = [t for n in week for t in sorted(n.tags) if t in nt.EXCLUDING]
    return f"nuits {nt.TAG_WORDS[statistics.mode(tags)]}" if tags else "pas assez de nuits"


def components(day: dict, nights) -> tuple[list[dict], list[tuple[str, str]]]:
    """([{key, sub, weight, prov}] present, [(key, why)] missing) for one day
    (sante._decide_day): the nightly ones read what the tiles show. Without
    a watch the nightly ones are missing for that one reason."""
    d, ctx, stats = day["day"], day["ctx"], day["shown"]
    parts, absent = [], []
    watch = ctx.get("has_watch", True)
    for key, fn in (("hrv", hrv_sub), ("hr", hr_sub)):
        s = stats[key]
        if s["value"] is not None and s["normal"]:
            parts.append({"key": key, "sub": fn(s["value"], s["normal"]), "prov": s["normal"]["provisional"]})
        else:
            absent.append((key, _why_night(nights, key, d, s) if watch else NO_WATCH))
    tst = day["tst24"]
    if tst is not None and (ctx.get("next_race") or {}).get("days") == 0:
        absent.append(("sleep", RACE_EVE))  # race morning: the race eve is never judged (Lastella 2014)
    elif tst is not None:
        m7 = nt.mean7(nights, "tst24", d)
        src = nt.mean_source(nights, "tst24", d)
        b = nt.band(nights, "tst24", d - timedelta(days=6), source=src) if m7 and src else None
        parts.append({"key": "sleep", "sub": sleep_sub(tst, m7["value"] if m7 else None, b["center"] if b else None),
                      "prov": bool(b and b["provisional"])})
    else:
        n = nights.get(d)
        absent.append(("sleep", NO_WATCH if not watch else "nuit incomplète ?" if n and n.nap_min
                       else "pas de nuit ce matin"))
    if ctx["sessions42"] >= MIN_LOAD_SESSIONS:
        parts.append({"key": "load", "sub": load_sub(day["load"]), "prov": False})
    else:
        absent.append(("load", "pas assez de séances"))
    f = ctx.get("feel")
    if f and f.get("answered", True):
        parts.append({"key": "feel", "sub": feel_sub(f), "prov": False})
    else:
        absent.append(("feel", "pas encore répondu"))
    total = sum(WEIGHTS[p["key"]] for p in parts)
    for p in parts:
        p["weight"] = WEIGHTS[p["key"]] / total if total else 0
    return parts, absent


def caps(day: dict) -> list[tuple[float, str]]:
    """[(cap on raw, why)] of the ladder's rules that hold on this day."""
    ctx = day["ctx"]
    f = ctx.get("feel") if (ctx.get("feel") or {}).get("answered", True) else None
    worse = bool(f and f["value"] == 3)
    out = []
    if ctx.get("reprise"):
        out.append((CAP_REPRISE, "reprise"))
    post = ctx.get("post")
    if post:
        what = "après la course" if post.get("race") else "après ta grosse sortie"
        out.append((CAP_POST_EARLY if post["days"] <= POST_EARLY_DAYS else CAP_POST, what))
    if not ctx.get("race_week") and ctx.get("hrv") == "below" and (ctx.get("hr") == "above" or worse):
        out.append((CAP_LOW_HRV, "VFC sous ta normale"))
    if ctx.get("short"):
        out.append((CAP_SHORT, "nuit courte"))
    return out


def place(tone: str, raw: float) -> int:
    """The score inside the tone's band, rounded half up on the exact value
    (snapped to 1e-9: 39 × 16,67 % is 6,5 → 7, never 6,4999… → 6)."""
    lo, hi = BANDS[tone]
    return int(math.floor(round(lo + (hi - lo) * raw / 100, 9) + 0.5))


def score_of(day: dict, nights) -> dict:
    """{value, tone, raw, raw0, parts, absent, caps (binding)} for one day
    (sante._decide_day), or {value: None, why} without a score."""
    tone = day["verdict"]["tone"]
    if tone not in BANDS:
        return {"value": None, "why": "unknown", "tone": tone}
    parts, absent = components(day, nights)
    if not parts:  # nothing measured (a race in 0–2 days with no data, say): no number to place
        return {"value": None, "why": "none", "tone": tone, "parts": parts, "absent": absent}
    raw0 = round(sum(p["sub"] * p["weight"] for p in parts), 9)  # 49,99999999999999 is 50
    held = caps(day)
    raw = min([raw0] + [c for c, _ in held])
    binding = [w for c, w in held if c < raw0]
    return {"value": place(tone, raw), "tone": tone, "raw": raw, "raw0": raw0, "parts": parts, "absent": absent,
            "caps": binding}


# ── what the page draws ─────────────────────────────────────────────────────

def _shares(parts: list[dict]) -> list[int]:
    """Each weight as a whole percentage, the largest remainders rounded up so they make 100."""
    raw = [p["weight"] * 100 for p in parts]
    out = [int(math.floor(r)) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)[:100 - sum(out)]:
        out[i] += 1
    return out


def _names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} et {names[-1]}"


def _absent_line(absent: list[tuple[str, str]]) -> str | None:
    """One muted line: « pas mesuré : FC de nuit (pas assez de nuits) · Ressenti
    (pas encore répondu) » for what has no value; « pas compté : VFC et FC de
    nuit (normale en construction) » for what the tiles show but the score
    does not read yet (or never: the race eve)."""
    if not absent:
        return None
    heads: dict[str, dict[str, list[str]]] = {"pas mesuré": {}, "pas compté": {}}
    for key, why in absent:
        head = "pas compté" if why in NOT_COUNTED or why.startswith("nuits ") else "pas mesuré"
        heads[head].setdefault(why, []).append(SHORT_NAMES[key])
    return " · ".join(f"{head} : " + " · ".join(f"{_names(names)} ({why})" for why, names in groups.items())
                      for head, groups in heads.items() if groups)


def _none_line(s: dict, has_watch: bool, has_sessions: bool) -> str:
    """The one line without a score. Tone unknown: the spec's « connecte ta
    montre ou Strava » only for an athlete with neither; one with Strava or a
    watch is told what is missing once, by the action's sentence."""
    if s["why"] == "unknown":
        return ("Pas encore de score." if has_watch or has_sessions
                else "Pas encore de score : connecte ta montre ou Strava.")
    return "Pas de score aujourd'hui : rien de mesuré."


def spark(history: list[tuple[date, dict, dict]]) -> dict | None:
    """The last 14 days' scores (only the days with one), tap a day → its score
    and action; None under 2 such days. Its resting readout prints no score:
    today's is the gauge's."""
    days = [d for d, _, _ in history]
    points = [{"day": d, "value": s["value"], "word": WORDS[s["tone"]], "action": v["headline"]}
              for d, v, s in history if s.get("value") is not None]
    if len(points) < 2:
        return None
    c = viz.score_days(days, points)
    n = len(points)
    return viz.rest(c, [f"{len(days)} derniers jours", "touche un jour", ""],
                    f"Forme du jour sur les {len(days)} derniers jours : {n} jours avec un score. Touche un jour pour "
                    "son score et son action.", back=n - 1)


def view(s: dict, history: list[tuple[date, dict, dict]], *, has_watch: bool, has_sessions: bool) -> dict:
    """Everything the score block draws: the half-gauge, the word, « basé sur
    N signaux sur 5 » and the names as chips, the breakdown (one bar per
    component present and its weight; the values themselves are the tiles'
    and Sommeil's, printed once there), the missing ones in one muted line,
    the binding cap, the 14-day sparkline; or the one line without a score."""
    if s.get("value") is None:
        return {"value": None, "line": _none_line(s, has_watch, has_sessions)}
    tone, v = s["tone"], s["value"]
    parts = sorted(s["parts"], key=lambda p: ORDER.index(p["key"]))
    shares = _shares(parts)
    rows = []
    for p, share in zip(parts, shares):
        rows.append({"key": p["key"], "label": NAMES[p["key"]], "sub": round(p["sub"]), "share": share,
                     "prov": p["prov"],
                     "aria": f"{SPOKEN[p['key']]} : {round(p['sub'])} sur 100"
                             + (", normale provisoire" if p["prov"] else "") + f", poids {share} %"})
    n = len(parts)
    names = [SHORT_NAMES[p["key"]] for p in parts]
    base = f"basé sur {n} signa{'ux' if n > 1 else 'l'} sur {len(WEIGHTS)}"
    aria = (f"Forme du jour {v} sur 100, {WORDS[tone]} : {base} ({', '.join(ARIA_NAMES[p['key']] for p in parts)}). "
            "Touche pour le détail.")
    return {"value": v, "tone": tone, "cls": TONE_CLS[tone], "word": WORDS[tone],
            "gauge": viz.gauge(v), "base": base, "chips": names, "rows": rows, "aria": aria,
            "absent_line": _absent_line(s["absent"]),
            "cap_line": f"Plafonné : {', '.join(s['caps'])}." if s["caps"] else None,
            "spark": spark(history)}
