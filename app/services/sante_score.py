"""Santé v4: « Récupération », PaceForge's own 0–100 score (owner decisions
2026-10-07, « un score juste comme WHOOP, plus simple », and 2026-10-08:
past activities only, no check-in, no race; after the visual judges: the
state comes from the score, like WHOOP). No brand metric enters it.

raw (0–100) is the weighted mean of the components present, each scored 0–100
only when its own data rule holds (every threshold is (H) unless cited):
1. VFC · 7 nuits (≥ 3 usable nights in the last 7 and a band, full or
   « provisoire »): z = (ln of the 7-night mean − ln of the band's centre) /
   the band's SD of ln RMSSD; 100 from z ≥ −0.5, linear to 0 at z = −2.5.
   Above the band = 100: never above, never praised (Plews 2013; Le Meur
   2013; Bellenger 2016).
2. FC de nuit · 7 nuits (the same rule): 100 while the mean is ≤ the band's
   median + 2 bpm, linear to 0 at + 10 bpm.
   During the illness alert the row reads the alert's own 2 nights, at most
   39 (red, « nettement haute »): never a green bar under the alert.
3. Sommeil · 24 h (a main night this morning; the naps of the 24 h before
   its wake count, nights.day_tst24): ≥ 7 h → 100 (Johnston 2020), 6 h → 60
   (Craven 2022: sleep loss is ≤ 6 h per 24 h), ≤ 4 h → 0, linear between;
   with a 7-day mean (≥ 3 usable days) and a usual (the 24-h band's median)
   at most 100 − 2/3 point per minute of that mean under the usual (90 min
   under → 40).
4. Charge récente (≥ 6 activities in 42 days, or a recovery window open):
   100; during a window (sante_training.EFFORT_RULES) 50 after a long effort
   (≥ 3 h, or ≥ 1 500 m D+ on foot), 30 after 6–10 h, 20 after ≥ 10 h (the
   lowest of the windows open).
Weights (H): VFC 30, FC de nuit 25, Sommeil 25, Charge 20, renormalised over
the components present. Without a nightly component (VFC, FC de nuit or
Sommeil) no score, but during a recovery window: the activities then say the
athlete is recovering, so the score is the window's cap (nothing measured
says otherwise: raw is taken as 100 and the caps bind), « Récupération en
cours », the sentence naming the activity (owner: « tu te bases que sur les
activités passées pour juger de la récupération »).
Caps on raw (H), the lowest binds: the nightly-HR illness alert (2 nights,
nights.illness_alert) → 39; a recovery window (≥ 10 h: 40 on D+0 → D+3, then
65 until D+10; 6–10 h: 45 on D+0 → D+2, then 65 until D+5; long: 65 on D+0 →
D+2); a component under 40 → 69 (a red contributor never sits under a green
ring); neither VFC nor FC de nuit in the score → 80 (sleep and load alone never
say « fully recovered »: a watch worn some nights only would make every rested
morning a 100); a 24-h total under 6 h → 65.
score = the capped raw, rounded half up on the exact value (snapped to 1e-9:
64,5 is 65, never 64,4999…). The state is its band (sante_today.state):
≥ 70 « Bien récupéré », 40–69 « Récupération en cours », < 40 « À ménager »:
the number and the state never disagree. The state's sentence names the main
reason (`reason`): the alert, else the cap that binds, else the lowest
component. A difference of a few points means nothing: the method fold says so.
The Contributeurs' words follow their bar's band (≥ 70 green, 40–69 orange,
< 40 red): « dans ta normale » or « un peu haute / basse » on a green bar,
« haute / basse » on an orange one, « nettement … » on a red one.
History: the 13 days before today are recomputed from what is stored, each
from what was known that day, with the same rule (sante._history); nothing is
persisted.
"""
import math
import statistics
from datetime import date

from app.services import sante_today as td
from app.services import viz
from app.services.nights import SHORT_DAY_MIN

BANDS = {"ok": (70, 100), "warn": (40, 69), "danger": (0, 39)}  # the state is the band of the score
WEIGHTS = {"hrv": 30, "hr": 25, "sleep": 25, "load": 20}  # (H)
ORDER = ("hrv", "hr", "sleep", "load")
NIGHTLY = ("hrv", "hr", "sleep")  # no score without one of them (but in a recovery window)
NAMES = {"hrv": "VFC", "hr": "FC de nuit", "sleep": "Sommeil", "load": "Charge récente"}
SPOKEN = {"hrv": "VFC sur 7 nuits", "hr": "FC de nuit sur 7 nuits", "sleep": "sommeil sur 24 heures",
          "load": "charge récente"}
HRV_FULL_Z, HRV_ZERO_Z = -0.5, -2.5  # (H) z of ln RMSSD: 100 from the band's floor, 0 at −2.5
HR_FULL_BPM, HR_ZERO_BPM = 2, 10  # (H) the 7-night mean over the band's median
SLEEP_POINTS = ((240, 0), (360, 60), (420, 100))  # (H) 24-h minutes → sub-score (7 h: Johnston 2020; 6 h: Craven 2022)
SLEEP_DEBT = 2 / 3  # (H) points off per minute of the 7-day mean under the usual
MIN_LOAD_SESSIONS = 6  # (H) activities in 42 days before « Charge récente » counts outside a window
CAP_ILL = 39  # (H) the nightly-HR illness alert: « À ménager »
CAP_RED, RED_SUB = 69, 40  # (H) a component under 40: never a green ring over a red contributor
CAP_SHORT = 65  # (H) a 24-h total under 6 h (Craven 2022)
CAP_NO_HEART = 80  # (H) neither VFC nor FC de nuit in the score: sleep and Charge alone never make a 100
CAPS = ("ill", "effort", "short", "red", "no_heart")  # equal caps: the first names the reason
HEART = ("hrv", "hr")
HISTORY_DAYS = 14  # the Récupération card
HISTORY_NIGHTS = 160  # days of nights a past day reads: its alert episodes (67 days, each on a 60-day band)

METHOD = [
    "Ton score est la moyenne de tes signaux, chacun noté sur 100 (la barre de chaque contributeur) : VFC sur 7 "
    "nuits, FC de nuit sur 7 nuits, sommeil sur 24 h siestes comprises, charge récente. Les poids (H) : 30, 25, 25, "
    "20, répartis sur les signaux présents ; sans aucune nuit, pas de score, sauf pendant la récupération d'une "
    "grosse sortie : ton score est alors son plafond. Ton état en découle : 70 et plus, bien récupéré ; 40 à 69, "
    "récupération en cours ; moins de 40, à ménager.",
    "Notes (H) : VFC pleine dans ta normale ou au-dessus (jamais un bonus : Plews 2013), nulle 2,5 écarts-types sous "
    "elle ; FC de nuit pleine jusqu'à +2 bpm sur ta médiane, nulle à +10 ; sommeil plein dès 7 h (Johnston 2020), "
    "60 à 6 h (Craven 2022), 0 à 4 h, moins quand ta semaine dort bien sous ton habitude ; charge récente pleine "
    "sans grosse sortie, 50, 30 ou 20 pendant la récupération d'une sortie de 3 h, 6 h, 10 h et plus.",
    "Plafonds (H) : FC de nuit nettement au-dessus de ta normale 2 nuits de suite, 39 ; un signal sous 40, 69 ; "
    "moins de 6 h de sommeil sur 24 h, 65 ; sans VFC ni FC de nuit, 80 : une longue nuit seule ne fait pas un 100.",
    "Grosses sorties (H), à leur durée arrêts compris, courses comprises, deux activités à moins de 30 min d'écart "
    "comptant pour une : 10 h et plus → score plafonné à 40 dès la fin et les 3 jours qui suivent, puis à 65 "
    "jusqu'au 10e ; 6 à 10 h → 45, puis 65 jusqu'au 5e ; 3 h ou 1 500 m D+ à pied → 65 pendant 2 jours. Une fin "
    "dans la nuit compte du matin même. Les 3 nuits (H) qui suivent 6 h et plus restent hors de ta normale "
    "(Hynynen 2010).",
    "Une différence de quelques points ne veut rien dire : la VFC varie d'environ 12 % d'une nuit à l'autre "
    "(Buchheit 2014). Ta normale est « provisoire » de 7 à 13 nuits (H), pleine à 14 ; l'alerte FC de nuit attend "
    "14 nuits (Quer 2021). L'anneau Charge fait le tour au double de ta semaine habituelle, la médiane de tes 12 "
    "dernières (H). Aucun score de marque n'y entre (Doherty 2025).",
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
    if w or day["sessions42"] >= MIN_LOAD_SESSIONS:
        parts.append({"key": "load", "sub": load_sub(w), "prov": False, "window": w})
    else:
        absent.append("load")
    total = sum(WEIGHTS[p["key"]] for p in parts)
    for p in parts:
        p["weight"] = WEIGHTS[p["key"]] / total if total else 0
    return parts, absent


def caps(day: dict, parts: list[dict]) -> list[tuple[float, str]]:
    """[(cap on raw, key)] of the rules that hold on this day (`parts`: its
    components): ill, effort, short, red, no_heart (see the module docstring)."""
    out = []
    if day["alert"]:
        out.append((CAP_ILL, "ill"))
    if day["window"]:
        out.append((day["window"]["cap"], "effort"))
    if day["tst24"] is not None and day["tst24"] < SHORT_DAY_MIN:
        out.append((CAP_SHORT, "short"))
    if any(p["sub"] < RED_SUB for p in parts):
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
    lowest component (a red one under its cap, or the plain lowest): hrv, hr,
    sleep (« short » under 6 h), load (its recovery window: « effort »)."""
    if tone == "ok":
        return None
    if day["alert"]:
        return "ill"
    if binding and binding[0] in ("effort", "short"):
        return binding[0]
    low = min(parts, key=lambda p: (p["sub"], ORDER.index(p["key"])))["key"]
    if low == "load":
        return "effort" if day["window"] else None
    if low == "sleep" and day["tst24"] is not None and day["tst24"] < SHORT_DAY_MIN:
        return "short"
    return low


def score_of(day: dict) -> dict:
    """{value, tone, raw, raw0, parts, absent, caps (binding keys, the lowest
    first), reason, measured} for one day, or {value: None, …} without a
    nightly component outside a recovery window (then no state either). In a
    window without a nightly component, raw is 100 (nothing measured says
    otherwise) and the caps bind: the score is the window's cap."""
    parts, absent = components(day)
    measured = any(p["key"] in NIGHTLY for p in parts)
    if not measured and not day["window"]:
        return {"value": None, "tone": None, "parts": parts, "absent": absent, "caps": [], "reason": None,
                "measured": False}
    raw0 = round(sum(p["sub"] * p["weight"] for p in parts), 9) if measured else 100.0  # 49,99999999999999 is 50
    held = caps(day, parts)
    raw = min([raw0] + [c for c, _ in held])
    value = rounded(raw)
    tone = tone_of(value)
    binding = [k for _, _, k in sorted((c, CAPS.index(k), k) for c, k in held if c < raw0)]
    return {"value": value, "tone": tone, "raw": raw, "raw0": raw0, "parts": parts, "absent": absent,
            "caps": binding, "reason": reason(tone, binding, parts, day), "measured": measured}


# ── what the page draws ─────────────────────────────────────────────────────

def ring(score: dict, state: dict | None, href: str | None = None) -> dict:
    """The Récupération ring: the score, the arc in the state's colour; under
    it only the label (the state's word is the title just below). `href`:
    the section it sums up, None when the page has none (a plain ring)."""
    v = score.get("value")
    if v is None:
        return viz.ring("recup", None, "—", "Récupération", tone="none", href=href,
                        aria="Récupération : pas de score ce matin")
    return viz.ring("recup", v / 100, str(v), "Récupération", tone=state["tone"], href=href,
                    aria=f"Récupération {v} sur 100. {state['aria']}")


def _word(p: dict, state: dict | None) -> str:
    """The Contributeurs' short word: no number the rings, the state line or
    the cards print. VFC and FC de nuit follow their bar's band (the sub-score
    on the score's bands): green « dans ta normale », « au-dessus » (VFC) or
    « un peu basse / haute » (out of the normal, still ≥ 70), orange « basse /
    haute », red « nettement basse / haute »; the illness alert's FC de nuit
    is « nettement haute ». Never « haute » on a green bar."""
    k = p["key"]
    prov = " (provisoire)" if p.get("prov") and k in ("hrv", "hr") else ""
    band = tone_of(p["sub"])
    if k == "hrv":
        if band != "ok":
            return ("basse" if band == "warn" else "nettement basse") + prov
        return {"below": "un peu basse", "above": "au-dessus"}.get(p["status"], "dans ta normale") + prov
    if k == "hr":
        if p.get("alert") or band == "danger":
            return "nettement haute" + prov
        if band == "warn":
            return "haute" + prov
        if p["delta"] >= td.HR_UP_BPM:
            return "un peu haute" + prov
        return ("basse" if p["status"] == "below" else "dans ta normale") + prov
    if k == "sleep":
        t = p["tst24"]
        return ("court" if t < SHORT_DAY_MIN else "plus court que d'habitude" if p["debt"]
                else "un peu court" if t < SLEEP_POINTS[2][0] else "suffisant")
    w = p.get("window")
    if not w:
        return "pas de grosse sortie"
    # the state line already says when: only the words here (each number printed once)
    return "grosse sortie" if state and state["key"] == "effort" else f"grosse sortie {td.ago_short(w['ago'])}"


def sleep_tone(tst24: float) -> str:
    """The Sommeil ring's colour, and its Contributeurs bar's: ≥ 7 h ok
    (Johnston 2020), 6–7 h warn, < 6 h danger (Craven 2022)."""
    return "ok" if tst24 >= SLEEP_POINTS[2][0] else "warn" if tst24 >= SHORT_DAY_MIN else "danger"


SEVERITY = ("ok", "warn", "danger")


def _tone(p: dict) -> str:
    """A Contributeurs bar's colour (its word says it too): the sleep row as
    the Sommeil ring, so one name never wears two colours, or worse when the
    week sleeps well under the usual (its sub-score's then, as its word
    says); Charge récente neutral, as the Charge ring (it describes the
    activities, never warns); the others by their sub-score, on the score's
    bands (≥ 70, 40–69, < 40)."""
    if p["key"] == "load":
        return "accent"
    if p["key"] == "sleep":
        ring = sleep_tone(p["tst24"])
        return max(ring, tone_of(p["sub"]), key=SEVERITY.index) if p["debt"] else ring
    return "danger" if p.get("alert") else tone_of(p["sub"])


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
    state's colour, with faint 40 and 70 lines labelled on the right (the
    bands: never colour alone), today's day on a disc; tap a bar → « 64 »
    « ◐ Récupération en cours » / « mer. 7 oct. · the sentence ». It rests on
    the mean of the days with a score (nothing selected: today's score is
    the ring's). None under 2 days with a score."""
    days = [d for d, _, _ in history]
    with_score = [s["value"] for _, _, s in history if s.get("value") is not None]
    if len(with_score) < 2:
        return None
    values, classes, tones, r, a = [], [], [], [], []
    for d, st, s in history:
        v = s.get("value")
        values.append(v)
        classes.append(s["tone"] if v is not None else "")
        tones.append(s["tone"] if v is not None else "")
        if v is None:
            r.append(["—", "", f"{viz.d_short(d)} · pas de score"])
            a.append(f"{viz.d_long(d)} : pas de score")
            continue
        r.append([str(v), f"{st['glyph']} {st['word']}", viz.d_short(d) + (f" · {st['short']}" if st["short"] else "")])
        # a past day is said as its readout prints it: no « il y a » (it would count from that day, not today)
        said = st["text"] if d == today else (f"{st['short']}." if st["short"] else None)
        a.append(f"{viz.d_long(d)} : récupération {v} sur 100, {st['word'].lower()}." + (f" {said}" if said else ""))
    c = viz.day_bars("recuperation", days, values, readouts=r, arias=a, classes=classes, tones=tones, y_max=100,
                     lines=((70, "70"), (40, "40")), today=len(days) - 1 if days and days[-1] == today else None,
                     summary=f"Récupération sur {len(days)} jours : {len(with_score)} jours avec un score")
    mean = rounded(sum(with_score) / len(with_score))
    return viz.rest(c, [str(mean), "en moyenne", ""],
                    f"Récupération sur les {len(days)} derniers jours : {mean} en moyenne. Choisis un jour pour son "
                    "score et son état.", back=len(days) - 1)
