"""Santé v4: « Récupération », PaceForge's own 0–100 score (owner decisions
2026-10-07, « un score juste comme WHOOP, plus simple », and 2026-10-08:
past activities only, no check-in, no race). No brand metric enters it.

The state (sante_today.state) comes first; the score is placed INSIDE the
band of its tone, so the number and the state never disagree:
    ok « Bien récupéré » → 70–100, warn « Récupération en cours » → 40–69,
    danger « À ménager » → 0–39;
    score = low + (high − low) × raw / 100, rounded half up on the exact value
    (raw and the placed value snapped to 1e-9 first: 6,5 is never 6,4999…).
raw (0–100) is the weighted mean of the components present, each scored 0–100
only when its own data rule holds (every threshold is (H) unless cited):
1. VFC · 7 nuits (≥ 3 usable nights in the last 7 and a band, full or
   « provisoire »): z = (ln of the 7-night mean − ln of the band's centre) /
   the band's SD of ln RMSSD; 100 from z ≥ −0.5, linear to 0 at z = −2.5.
   Above the band = 100: never above, never praised (Plews 2013; Le Meur
   2013; Bellenger 2016).
2. FC de nuit · 7 nuits (the same rule): 100 while the mean is ≤ the band's
   median + 2 bpm, linear to 0 at + 10 bpm.
3. Sommeil · 24 h (a main night this morning; the naps of the day count):
   ≥ 7 h → 100 (Johnston 2020), 6 h → 60 (Craven 2022: sleep loss is ≤ 6 h
   per 24 h), ≤ 4 h → 0, linear between; with a 7-day mean (≥ 3 usable days)
   and a usual (the 24-h band's median) at most 100 − 2/3 point per minute of
   that mean under the usual (90 min under → 40).
4. Charge récente (≥ 6 activities in 42 days, or a recovery window open):
   100; during a window (sante_training.EFFORT_RULES) 50 after a long effort
   (≥ 3 h, or ≥ 1 500 m D+ on foot), 30 after 6–10 h, 20 after ≥ 10 h.
Weights (H): VFC 30, FC de nuit 25, Sommeil 25, Charge 20, renormalised over
the components present. No score without a nightly component (VFC, FC de
nuit or Sommeil): the activities alone say nothing of recovery.
Caps on raw (H): the recovery window's (≥ 10 h: 40 on D+1 → D+3, then 65
until D+10; 6–10 h: 45 on D+1 → D+2, then 65 until D+5; long: 65 on D+1 →
D+2); the 7-night HRV under its band with the nightly HR ≥ its median + 3 bpm:
60; a 24-h total under 6 h: 65. The illness alert sets the tone (0–39).
A difference of a few points means nothing: the method fold says so.
History: the 13 days before today are recomputed from what is stored, each
from what was known that day (sante._history); nothing is persisted.
"""
import math
from datetime import date

from app.services import sante_today as td
from app.services import viz

BANDS = {"ok": (70, 100), "warn": (40, 69), "danger": (0, 39)}
WEIGHTS = {"hrv": 30, "hr": 25, "sleep": 25, "load": 20}  # (H)
ORDER = ("hrv", "hr", "sleep", "load")
NIGHTLY = ("hrv", "hr", "sleep")  # no score without one of them
NAMES = {"hrv": "VFC", "hr": "FC de nuit", "sleep": "Sommeil", "load": "Charge récente"}
SPOKEN = {"hrv": "VFC sur 7 nuits", "hr": "FC de nuit sur 7 nuits", "sleep": "sommeil sur 24 heures",
          "load": "charge récente"}
HRV_FULL_Z, HRV_ZERO_Z = -0.5, -2.5  # (H) z of ln RMSSD: 100 from the band's floor, 0 at −2.5
HR_FULL_BPM, HR_ZERO_BPM = 2, 10  # (H) the 7-night mean over the band's median
SLEEP_POINTS = ((240, 0), (360, 60), (420, 100))  # (H) 24-h minutes → sub-score (7 h: Johnston 2020; 6 h: Craven 2022)
SLEEP_DEBT = 2 / 3  # (H) points off per minute of the 7-day mean under the usual
MIN_LOAD_SESSIONS = 6  # (H) activities in 42 days before « Charge récente » counts outside a window
CAP_LOW_HRV, CAP_SHORT = 60, 65  # (H) caps on raw: HRV under its band with nightly HR up; a 24-h total < 6 h
HISTORY_DAYS = 14  # the Récupération card
HISTORY_NIGHTS = 160  # days of nights a past day reads: its alert episodes (67 days, each on a 60-day band)

METHOD = [
    "Ton état vient d'abord, de tes activités passées et de tes nuits seulement : FC de nuit nettement au-dessus "
    "de ta normale 2 nuits de suite → à ménager ; une grosse sortie récente, une VFC basse avec une FC de nuit "
    "haute, ou moins de 6 h de sommeil sur 24 h → récupération en cours ; sinon, bien récupéré.",
    "Le score se place dans la plage de cet état : 70–100 bien récupéré, 40–69 récupération en cours, 0–39 à "
    "ménager. Le chiffre et l'état ne peuvent pas se contredire.",
    "Il fait la moyenne de 4 signaux au plus, notés de 0 à 100, chacun seulement quand ses données suffisent : VFC "
    "sur 7 nuits (poids 30), FC de nuit sur 7 nuits (25), sommeil sur 24 h siestes comprises (25), charge récente "
    "(20). Les poids (H) se répartissent sur les signaux présents ; sans aucune nuit, pas de score.",
    "VFC (H) : pleine note dans ta normale ou au-dessus (au-dessus n'est jamais un bonus : Plews 2013), zéro à 2,5 "
    "écarts-types en dessous. FC de nuit (H) : pleine note jusqu'à +2 bpm sur ta médiane, zéro à +10.",
    "Sommeil (H) : pleine note dès 7 h (le seuil de Johnston 2020), 60 à 6 h (le seuil de Craven 2022), 0 à 4 h ; "
    "au plus 40 quand ta moyenne sur 7 jours est 90 min sous ton habitude.",
    "Grosses sorties (H), d'après leur durée arrêts compris, courses comprises : 10 h et plus → 10 jours de "
    "récupération (score plafonné à 40 les 3 premiers jours, puis à 65) ; 6 à 10 h → 5 jours (45, puis 65) ; 3 h "
    "ou 1 500 m D+ à pied → 2 jours (65). La charge récente vaut alors 20, 30 ou 50. Les 3 nuits après 6 h et "
    "plus restent hors de ta normale : la FC de nuit peut y monter très haut (Hynynen 2010).",
    "Plafonds (H) : VFC basse avec FC de nuit haute, 60 ; moins de 6 h sur 24 h, 65. L'alerte FC de nuit place le "
    "score entre 0 et 39.",
    "Une différence de quelques points ne veut rien dire : d'une nuit à l'autre la VFC varie d'environ 12 % "
    "(Buchheit 2014). Regarde l'état et la tendance sur 14 jours.",
    "Ta normale est « provisoire » de 7 à 14 nuits (H) : elle bouge encore. L'alerte FC de nuit attend 14 nuits "
    "pour rester spécifique (Quer 2021) : un graphique calme n'est pas un certificat de bonne santé. Aucun score "
    "de marque n'y entre : peu sont validés (Doherty 2025).",
]
REFS = [
    ("Plews 2013", "10.1007/s40279-013-0071-8"), ("Johnston 2020", "10.1016/j.jsams.2019.10.013"),
    ("Craven 2022", "10.1007/s40279-022-01706-y"), ("Hynynen 2010", "10.1055/s-0030-1249625"),
    ("Buchheit 2014", "10.3389/fphys.2014.00073"), ("Altini & Plews 2021", "10.3390/s21237932"),
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


def sleep_base(tst24: float) -> float:
    (a, ya), (b, yb), (c, yc) = SLEEP_POINTS
    return _lin(tst24, a, ya, b, yb) if tst24 < b else _lin(tst24, b, yb, c, yc)


def sleep_sub(tst24: float, mean7: float | None = None, usual: float | None = None) -> float:
    """≥ 7 h → 100, 6 h → 60, ≤ 4 h → 0, linear between; at most 100 − 2/3 per
    minute of the 7-day mean under the usual, when both are there."""
    sub = sleep_base(tst24)
    if mean7 is not None and usual is not None:
        sub = min(sub, max(0.0, 100 - SLEEP_DEBT * max(0.0, usual - mean7)))
    return sub


def load_sub(window: dict | None) -> float:
    """100; during a recovery window, its class's Charge (50, 30 or 20, H)."""
    return float(window["load"]) if window else 100.0


def components(day: dict) -> tuple[list[dict], list[str]]:
    """([{key, sub, prov, …}] present, [keys] missing) for one day
    (sante._assess): stats {hr, hrv: {value, normal}}, tst24, sleep {mean7,
    usual, prov}, window, sessions42."""
    parts, absent = [], []
    for key, fn in (("hrv", hrv_sub), ("hr", hr_sub)):
        s = day["stats"][key]
        if s["value"] is not None and s["normal"]:
            parts.append({"key": key, "sub": fn(s["value"], s["normal"]), "prov": bool(s["normal"]["provisional"]),
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
    if w or day["sessions42"] >= MIN_LOAD_SESSIONS:
        parts.append({"key": "load", "sub": load_sub(w), "prov": False, "window": w})
    else:
        absent.append("load")
    total = sum(WEIGHTS[p["key"]] for p in parts)
    for p in parts:
        p["weight"] = WEIGHTS[p["key"]] / total if total else 0
    return parts, absent


def caps(day: dict) -> list[tuple[float, str]]:
    """[(cap on raw, why)] of the rules that hold on this day."""
    out = []
    w = day["window"]
    if w:
        out.append((w["cap"], "grosse sortie"))
    s = day["stats"]
    if s["hrv"]["status"] == "below" and day["hr_up"]:
        out.append((CAP_LOW_HRV, "VFC basse et FC de nuit haute"))
    if day["tst24"] is not None and day["tst24"] < td.SHORT_DAY_MIN:
        out.append((CAP_SHORT, "nuit courte"))
    return out


def place(tone: str, raw: float) -> int:
    """The score inside the tone's band, rounded half up on the exact value
    (snapped to 1e-9: 39 × 16,67 % is 6,5 → 7, never 6,4999… → 6)."""
    lo, hi = BANDS[tone]
    return int(math.floor(round(lo + (hi - lo) * raw / 100, 9) + 0.5))


def score_of(day: dict, state: dict | None) -> dict:
    """{value, tone, raw, raw0, parts, absent, caps (binding)} for one day, or
    {value: None, parts, absent} (no state, or no nightly component)."""
    parts, absent = components(day)
    if state is None or not any(p["key"] in NIGHTLY for p in parts):
        return {"value": None, "tone": state["tone"] if state else None, "parts": parts, "absent": absent}
    raw0 = round(sum(p["sub"] * p["weight"] for p in parts), 9)  # 49,99999999999999 is 50
    held = caps(day)
    raw = min([raw0] + [c for c, _ in held])
    return {"value": place(state["tone"], raw), "tone": state["tone"], "raw": raw, "raw0": raw0, "parts": parts,
            "absent": absent, "caps": [w for c, w in held if c < raw0]}


def measured(day: dict) -> bool:
    """A nightly component holds today (the state's « at least one measured signal »)."""
    return any(p["key"] in NIGHTLY for p in components(day)[0])


# ── what the page draws ─────────────────────────────────────────────────────

def ring(score: dict, state: dict | None) -> dict:
    """The Récupération ring: the score, the arc in the state's colour, the
    state's short word under it (the state line below says it in full)."""
    v = score.get("value")
    if v is None:
        aria = f"Récupération : pas de score. {state['aria']}" if state else "Récupération : pas de score"
        return viz.ring("recup", None, "—", "Récupération", state["short"] if state else None,
                        tone=state["tone"] if state else "none", href="#contributeurs", aria=aria)
    return viz.ring("recup", v / 100, str(v), "Récupération", state["short"], tone=state["tone"],
                    href="#contributeurs", aria=f"Récupération {v} sur 100. {state['aria']}")


def _word(p: dict, state: dict | None) -> str:
    """The Contributeurs' short word: no number the rings, the state line or the cards print."""
    k = p["key"]
    prov = " (provisoire)" if p.get("prov") and k in ("hrv", "hr") else ""
    if k == "hrv":
        return {"below": "basse", "above": "au-dessus"}.get(p["status"], "dans ta normale") + prov
    if k == "hr":
        if p["delta"] >= td.HR_UP_BPM:
            return "haute" + prov
        return ("basse" if p["status"] == "below" else "dans ta normale") + prov
    if k == "sleep":
        t = p["tst24"]
        return ("courte" if t < td.SHORT_DAY_MIN else "plus courte que d'habitude" if p["debt"]
                else "un peu courte" if t < SLEEP_POINTS[2][0] else "suffisante")
    w = p.get("window")
    if not w:
        return "pas de grosse sortie"
    # the state line already says when: only the words here (each number printed once)
    return "grosse sortie" if state and state["key"] == "effort" else f"grosse sortie {td.ago_short(w['days'])}"


def sleep_tone(tst24: float) -> str:
    """The Sommeil ring's colour, and its Contributeurs bar's: ≥ 7 h ok
    (Johnston 2020), 6–7 h warn, < 6 h danger (Craven 2022)."""
    return "ok" if tst24 >= SLEEP_POINTS[2][0] else "warn" if tst24 >= td.SHORT_DAY_MIN else "danger"


def _tone(p: dict) -> str:
    """A Contributeurs bar's colour (its word says it too): the sleep row as
    the Sommeil ring, so one name never wears two colours; the others by
    their sub-score, on the score's bands (≥ 70, 40–69, < 40)."""
    if p["key"] == "sleep":
        return sleep_tone(p["tst24"])
    return next(tone for tone, (lo, _) in BANDS.items() if p["sub"] >= lo)


def contributors(score: dict, state: dict | None) -> dict | None:
    """Oura-like, always open: one row per component present (its name, a bar
    of its sub-score in its tone, a short word), the missing ones in one muted
    line; None without a score."""
    if score.get("value") is None:
        return None
    rows = []
    for p in sorted(score["parts"], key=lambda p: ORDER.index(p["key"])):
        word = _word(p, state)
        rows.append({"key": p["key"], "name": NAMES[p["key"]], "sub": round(p["sub"]), "word": word,
                     "tone": _tone(p), "aria": f"{SPOKEN[p['key']]} : {word}, {round(p['sub'])} sur 100"})
    missing = [NAMES[k] for k in ORDER if k in score["absent"]]
    return {"rows": rows, "absent": f"Pas encore dans le score : {', '.join(missing)}." if missing else None}


def history_card(history: list[tuple[date, dict | None, dict]], today: date) -> dict | None:
    """« Récupération · 14 jours »: one bar per day with a score, in its
    state's colour (and its word in the readout: never colour alone), today
    marked; tap a bar → [the day, « 59 · Récupération en cours », the
    sentence]. It rests on a hint, no number: today's score is the ring's.
    None under 2 days with a score."""
    days = [d for d, _, _ in history]
    with_score = [(d, st, s) for d, st, s in history if s.get("value") is not None]
    if len(with_score) < 2:
        return None
    values, classes, r, a = [], [], [], []
    for d, st, s in history:
        v = s.get("value")
        values.append(v)
        classes.append(s["tone"] if v is not None else "")
        if v is None:
            r.append([viz.d_short(d), "—", "pas de score"])
            a.append(f"{viz.d_long(d)} : pas de score")
            continue
        r.append([viz.d_short(d), f"{v} · {st['word']}", st["text"] or ""])
        a.append(f"{viz.d_long(d)} : récupération {v} sur 100, {st['word'].lower()}."
                 + (f" {st['text']}" if st["text"] else ""))
    c = viz.day_bars("recuperation", days, values, readouts=r, arias=a, classes=classes, y_max=100,
                     today=len(days) - 1 if days and days[-1] == today else None,
                     summary=f"Récupération sur {len(days)} jours : {len(with_score)} jours avec un score")
    return viz.rest(c, ["", "", "Touche un jour pour son score et son état."],
                    f"Récupération sur les {len(days)} derniers jours. Touche un jour pour son score et son état.",
                    back=len(days) - 1)
