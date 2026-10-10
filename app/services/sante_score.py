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
1. VFC · 7 nuits (≥ 3 measured nights in the last 7 and a band, full or
   « provisoire »: every measured night counts in both, last night included,
   nights.normal): z = (ln of the 7-night mean − ln of the band's centre) /
   the band's SD of ln RMSSD; 100 from z ≥ −0.5 (the trials' convention:
   mean ± 0.5 SD), linear to 0 at z = −2.5 (H). Above the band = 100: never
   above, never praised (Plews 2013; Bellenger 2016).
2. FC de nuit · 7 nuits (the same rule): 100 while the mean is ≤ the band's
   median + 2 bpm, 40 at + 5, 0 at + 8, linear between (H; anchors: + 4 bpm
   on 2 nights, Alavi 2022's alert; ≈ + 4,5 bpm in a short overload, Bosquet
   2008; ≈ + 6 % when sick, Altini & Plews 2021).
   During the illness alert it reads the alert's own 2 nights, at most 39
   (its row red, « nettement plus haute »): never green under the alert.
3. Sommeil · 24 h (a main night this morning; the naps of the 24 h before
   its wake count, nights.day_tst24): read against the day's estimated need
   (sante_sleep.sleep_need). At least 7/8 of it → 100, 3/4 → 60, 1/2 or
   less → 0, linear between (H). For an 8-h need these are 7 h, 6 h, 4 h;
   they move with the estimated need. No separate sleep-debt penalty in
   this component: the need already includes it.
Weights (H): VFC 25, FC de nuit 25, Sommeil 30, renormalised over the
components present (when all three exist: 31.25 %, 31.25 %, 37.5 %;
the heart signals together account for 62.5 %). The recent efforts are no
component of the mean (2026-10-08, owner: « Fais comme WHOOP »: its recovery
is body signals and sleep, the strain kept out; « Charge récente » is gone):
their recovery windows cap the score (below), the owner's « Un effort récent,
il faut le prendre en compte et afficher la fatigue quand même »; the
« Effort récent » row says it on every day of a window). Without a nightly
component usable on that day (VFC, FC de nuit or Sommeil) no score at all, like WHOOP (owner,
2026-10-09: « Mets un cadran vide, oui »): the dial is empty, the 14-day card
draws nothing that day, never a score estimated from the activities alone.
Caps on raw (H), the lowest binds: the nightly-HR illness alert (2 nights,
nights.illness_alert) → 39; an effort precaution interpolated through the
anchors in sante_training.EFFORT_RULES and gradually released to 100; a
continuous sleep precaution relative to the estimated base (sleep_limit),
replacing the old fixed 65 below 6 h and sleep's duplicate red cap;
VFC under its band (z < −0.5) AND FC de nuit ≥ its
median + 3 bpm → 69 (Buchheit 2014, Table 2: rMSSD down with HR up,
« accumulated fatigue »); a red cardiac component (sub-score < 40) → 69, where FC de
nuit always counts but VFC only when FC de nuit is measured and
its mean is > median + 2 bpm (rMSSD down with HR down is the
« saturation » of a well-trained athlete, « coping well with training »:
Buchheit 2014, Table 2; resting HRV « largely unaffected by overreaching »:
Bellenger 2016; Meeusen 2013); neither VFC nor FC de nuit in the score → 80
(sleep alone never says « fully recovered »).
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
on comprend rien »: « Détail du score » is gone): the page shows the score on
its dial with its state's word (dial) and plain rows (sante.night_row). A
heart signal's colour is its place against the normal, the same on its row
and on its card's status line (row_tone): green in it, orange out of it on
the side that matters (VFC under, FC de nuit over), red when its note is
under 40 and counts for the 69 cap (never a red VFC that caps nothing), at
least orange under the joint cap, red under the alert, neutral on the other
side (never praised).
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
WEIGHTS = {"hrv": 25, "hr": 25, "sleep": 30}  # (H) research_recovery.md §3.2; no Charge (WHOOP: strain kept out)
ORDER = ("hrv", "hr", "sleep")
NIGHTLY = ("hrv", "hr", "sleep")  # no score without one of them (like WHOOP: no night, no score)
HRV_FULL_Z, HRV_ZERO_Z = -0.5, -2.5  # z of ln RMSSD: 100 from the band's floor (the trials' ± 0.5 SD), 0 at −2.5 (H)
HR_FULL_BPM, HR_MID_BPM, HR_ZERO_BPM = 2, 5, 8  # (H) over the band's median: 100, 40, 0 (Alavi 2022; Bosquet 2008)
HR_MID_SUB = 40  # (H) the FC de nuit sub-score at + 5 bpm
# the 24 h against the day's need (sante_sleep.sleep_need) → sub-score: ½ → 0, ¾ → 60, ⅞ → 100, i.e. 4 h, 6 h and
# 7 h for an 8-h need (7 h: Watson 2015a; 6 h: Craven 2022); the sleep owed is in the need (never counted twice)
SLEEP_SHARES = ((0.5, 0), (0.75, 60), (0.875, 100))  # (H)
CAP_ILL = 39  # (H) the nightly-HR illness alert: « Récupération faible »
CAP_RED, RED_SUB = 69, 40  # (H) a component under 40: never a green dial over a red contributor
CAP_JOINT, JOINT_HR_BPM = 69, 3  # (H) VFC under its band and FC de nuit ≥ median + 3 bpm (Buchheit 2014, Table 2)
CAP_SHORT = 65  # (H) the continuous sleep guard's anchor at 3/4 of the estimated base
CAP_NO_HEART = 80  # (H) neither VFC nor FC de nuit in the score: sleep alone never makes a 100
CAP_RESP = 69  # (H) the breathing rate over its usual line 2 nights in a row (nights.resp_up_nights): never green
CAPS = ("ill", "effort", "short", "joint", "resp", "red", "no_heart")  # equal caps: the first names the reason
HEART = ("hrv", "hr")
HISTORY_DAYS = 14  # the Récupération card
HISTORY_NIGHTS = 160  # days of nights a past day reads: its bands (60 days) and its alert's (the 60 before), with room

# « Comment je calcule » (the audit, 2026-10-09: « Il y a deux explications à déplier, dont 8 points pour la
# première »): the page's one method fold, a few bullets for the three dials in their order (Sommeil, Récupération,
# Entraînement), the stages and the margin; the older « Comment je calcule ta récupération » (8 bullets) and
# « Comment je lis tes nuits » (5, sante_sleep) merged, without what the page already says (the 70 / 40 bands are
# the chart's lines and the dial's word, a short night is the Sommeil dial's word). v4.3 (owner: « c'est trop
# d'explication, simplifie et synthétise, ne mets pas les citations »): plain words, no citation, no « (H) » (the
# heuristics stay marked in the code and the tests), no outing named (« Ne mentionne pas les sorties dans la partie
# Santé »); v4.4 (owner: « les explications en français ne sont pas claires »): sentences of 15 words at most, VFC
# and FC de nuit each said in one plain sentence, « tes valeurs habituelles » instead of « ta normale ». The
# references are on /sante/sources (REFS, sante_sleep.REFS)
METHOD = [
    "Sommeil compare tes 24 h, siestes comprises, à un besoin estimé. La base part de 8 h et s’adapte avec "
    "assez de nuits comparables. L’effort et le manque de sommeil estimé s’y ajoutent. Ta journée commence au réveil.",
    "Récupération combine ton sommeil, ta VFC et ta FC de nuit. L’effet d’un sommeil court varie avec sa durée. "
    "La prudence après effort diminue chaque jour. Elle ne prédit pas ta date de récupération.",
    "La VFC mesure les variations entre battements cardiaques. La FC de nuit est ton pouls moyen nocturne. "
    "Les graphiques précisent si les éveils sont exclus. Je compare ces mesures à tes 60 derniers jours.",
    "Entraînement compare tes 7 derniers jours à ta semaine habituelle. Une minute compte davantage quand ton "
    "pouls est haut.",
    "Ta montre estime tes phases : la forme de ta nuit, pas sa qualité. Mes pourcentages sont des estimations "
    "aussi : quelques points d'écart ne veulent rien dire.",
]


def explanation(day: dict) -> list[str]:
    """Explain the effective limits, without suggesting a measured recovery percentage."""
    score = day["score"]
    if score["value"] is None:
        return ["Aucune nouvelle nuit exploitable pour ce bilan. Les anciennes tendances ne créent pas un nouveau score."]
    notes = ["Cet indice estime ta récupération ; il ne mesure pas un pourcentage de réparation du corps."]
    labels = {"effort": "la prudence après l’effort récent", "short": "le sommeil court par rapport à ta base estimée", "ill": "la FC nocturne élevée",
              "joint": "la tendance combinée de la VFC et de la FC", "resp": "la respiration nocturne élevée",
              "red": "un signal de récupération bas", "no_heart": "l’absence de tendance cardiaque exploitable"}
    limiting = [labels[k] for cap, k in caps(day, score["parts"]) if cap == score["raw"] and cap < score["raw0"]]
    if limiting:
        verb = "limitent" if len(limiting) > 1 else "limite"
        notes.append(f"Pour ce bilan, {' et '.join(limiting)} {verb} l’indice à {pct(score['value'])}.")
    if "effort" in score["caps"]:
        notes.append("La prudence liée à l’effort diminue progressivement avec les jours. Elle ne fixe pas une date de récupération complète.")
    if "short" in score["caps"]:
        notes.append("L’effet du sommeil évolue progressivement avec sa durée, sans changement brutal à 6 h.")
    if any(p["prov"] for p in score["parts"]):
        notes.append("Comparaison provisoire : tes références cardiaques reposent encore sur peu de nuits.")
    missing = [{"hrv": "VFC", "hr": "FC de nuit", "sleep": "sommeil"}[k] for k in score["absent"]]
    if missing:
        notes.append("Calcul sans " + " ni ".join(missing) + " exploitable pour cet indice.")
    return notes
# the references the fold rests on (with sante_sleep.REFS), listed on /sante/sources (label, DOI or URL):
# « Recommandations officielles » (consensus, position stands, guidelines), then « Études scientifiques »
REFS = [
    ("Recommandations officielles", [
        ("Kellmann 2018", "10.1123/ijspp.2017-0759"), ("Meeusen 2013", "10.1249/MSS.0b013e318279a10a"),
        ("Watson 2015a", "10.5665/sleep.4716"), ("Hirshkowitz 2015", "10.1016/j.sleh.2014.12.010"),
        ("Schwellnus 2016", "10.1136/bjsports-2016-096572"), ("Walsh 2021", "10.1136/bjsports-2020-102025"),
        ("BASES 2023", "https://westminsterresearch.westminster.ac.uk/item/wxx7y/"
                       "bases-expert-statement-methods-to-monitor-athletes-sleep"),
        ("Sammito 2024", "10.1186/s12995-024-00414-9"), ("Quigley 2024", "10.1111/psyp.14604"),
    ]),
    ("Études scientifiques", [
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


def sleep_sub(tst24: float, need: float = 480) -> float:
    """The 24 h against the day's need (minutes): ⅞ of it or more → 100, ¾ → 60, ½ or less → 0, linear between
    (7 h, 6 h and 4 h for an 8-h need). Never less for a long night: no ceiling (Watson 2015a)."""
    (a, ya), (b, yb), (c, yc) = ((share * need, y) for share, y in SLEEP_SHARES)
    return _lin(tst24, a, ya, b, yb) if tst24 < b else _lin(tst24, b, yb, c, yc)


def components(day: dict) -> tuple[list[dict], list[str]]:
    """([{key, sub, prov, red, joint, …}] present, [keys] missing) for one day
    (sante._assess): stats {hr, hrv: {value, normal, status, seen}}, tst24,
    need {total, …} (the 24 h are read against it); the recovery window is no component (it caps
    the score: caps). `red`: the component counts for the 69 cap (VFC only
    with FC de nuit measured and over its median + 2 bpm); `joint`: VFC under
    its band with FC de nuit ≥ median + 3 bpm (both rows)."""
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
        parts.append({"key": "sleep", "sub": sleep_sub(tst, day["need"]["total"]), "prov": False, "tst24": tst})
    else:
        absent.append("sleep")
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


def sleep_limit(slept: float | None, base: float) -> float | None:
    """Continuous precaution (H), relative to the estimated base, not debt.

    0 at no sleep, 40 at half the base, 65 at three quarters, 100 at
    seven eighths (at least 7 h). These are product anchors, not validated
    physiological percentages. Sleep already contributes to the weighted
    index; this single guard replaces both the 6-h step and sleep's red cap.
    """
    if slept is None:
        return None
    enough = max(7 * 60, base * 7 / 8)
    if slept >= enough:
        return None
    anchors = ((0, 0), (base / 2, 40), (base * 3 / 4, CAP_SHORT), (enough, 100))
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if slept <= x1:
            return _lin(slept, x0, y0, x1, y1)
    return None


def caps(day: dict, parts: list[dict]) -> list[tuple[float, str]]:
    """[(cap on raw, key)] of the rules that hold on this day (`parts`: its
    components): ill, effort, short, joint, red, no_heart (see the module
    docstring)."""
    out = []
    if day["alert"]:
        out.append((CAP_ILL, "ill"))
    if day["window"]:
        out.append((day["window"]["cap"], "effort"))
    if (limit := sleep_limit(day["tst24"], day["need"]["base"])) is not None:
        out.append((limit, "short"))
    if any(p["joint"] for p in parts):
        out.append((CAP_JOINT, "joint"))
    if day.get("resp"):
        out.append((CAP_RESP, "resp"))
    if any(p["red"] for p in parts if p["key"] != "sleep"):
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
    breathing rate's 69 when it binds, else the lowest component (a red one
    under its cap, or the plain lowest): hrv, hr, sleep (« short » under 6 h)."""
    if tone == "ok":
        return None
    if day["alert"]:
        return "ill"
    if binding and binding[0] in ("effort", "short"):
        return binding[0]
    if any(p.get("joint") for p in parts):
        return "joint"
    if "resp" in binding:
        return "resp"
    low = min(parts, key=lambda p: (p["sub"], ORDER.index(p["key"])))["key"]
    if low == "sleep" and day["tst24"] is not None and day["tst24"] < SHORT_DAY_MIN:
        return "short"
    return low


def building(day: dict, absent: list[str]) -> list[str]:
    """The heart signals missing from the score because their normal is still being built: measured in the 60 days
    of a band but no band yet (fewer than 7 measured nights, H)."""
    return [k for k in HEART if k in absent and day["stats"][k].get("seen") and not day["stats"][k]["normal"]]


def score_of(day: dict) -> dict:
    """{value, tone, raw, raw0, parts, absent, building, caps (binding keys,
    the lowest first), reason, measured} for one day, or {value: None, …}
    without a nightly component (then no state either), recovery window or
    not (owner, 2026-10-09: « Mets un cadran vide, oui », like WHOOP)."""
    parts, absent = components(day)
    # A rolling mean is historical context, not evidence of a new night.
    measured = any(p["key"] in day["measured_components"] for p in parts)
    if not measured:
        return {"value": None, "tone": None, "parts": parts, "absent": absent, "building": building(day, absent),
                "caps": [], "reason": None, "measured": False}
    raw0 = round(sum(p["sub"] * p["weight"] for p in parts), 9)  # 49,99999999999999 is 50
    held = caps(day, parts)
    raw = min([raw0] + [c for c, _ in held])
    value = rounded(raw)
    tone = tone_of(value)
    binding = [k for _, _, k in sorted((c, CAPS.index(k), k) for c, k in held if c < raw0)]
    return {"value": value, "tone": tone, "raw": raw, "raw0": raw0, "parts": parts, "absent": absent,
            "building": building(day, absent), "caps": binding, "reason": reason(tone, binding, parts, day),
            "measured": True}


# ── what the page draws ─────────────────────────────────────────────────────

DIAL_WORDS = {"ok": "bonne", "warn": "en cours", "danger": "faible"}  # the state's word under « Récupération »
NO_SCORE = "pas de score"  # the dial's word without a score (the line under the dials says why)
NO_MEASURE = "pas de mesure ce jour-là"  # a day without a score: no bar, no dot, its readout says it (2026-10-08)


def pct(v: int) -> str:
    """The score as the page prints it (v4.4, owner: « Mets des pourcentages plutôt que des valeurs (comme WHOOP /
    Oura) »): « 65 % », the same number, a no-break space (the display font has no narrow one)."""
    return f"{v}{viz.NBSP}%"


def dial(score: dict, state: dict | None, href: str | None = None) -> dict:
    """The Récupération dial, the middle one of three (2026-10-08, owner: « Fais comme WHOOP, ça doit rester
    simple »): the score as a percentage (« 65 % », the « % » smaller), its arc in its state's colour, its name
    under it and the state's word (« bonne », « en cours », « faible »: the name and the word say the state,
    never colour alone); « — » and « pas de score » without a score (no night measured: an empty dial, like
    WHOOP). `href`: its card below, None when the page has none (a plain dial)."""
    v = score.get("value")
    if v is None:
        return viz.ring("recup", None, "—", "Récupération", NO_SCORE, tone="none", href=href,
                        aria="Récupération : pas de score ce matin.")
    word = DIAL_WORDS[state["tone"]]
    return viz.ring("recup", v / 100, str(v), "Récupération", word, tone=state["tone"], unit="%", href=href,
                    aria=f"Récupération {pct(v)}, {word}.")


def heart_tone(key: str, status: str | None, sub: float, red: bool = True, joint: bool = False,
               alert: bool = False) -> str:
    """A heart signal's colour: its place against the normal, the same on its row and on its card's status
    line (v4.3, owner: « est-ce que c'est bien ou pas bien ? »): green in it; orange out of it on the side that
    matters (VFC under it, FC de nuit over it), red when its note is under 40 and counts for the 69 cap (a VFC
    that caps nothing is never red: no red row under a green dial); at least orange under the joint cap (both
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
    state's colour, with faint 40 and 70 lines labelled on the right (the bands:
    never colour alone), today's day on a disc; nothing on a day without a
    score (owner, 2026-10-08: « s'il n'y a pas de mesure tu ne mets rien, pas
    de point »; a night not recorded has no score), whose readout says « pas de
    mesure ce jour-là »; tap a bar → « 64 % » « ◐ Récupération en cours » /
    « mer. 7 oct. »: the date, the score and the state only (v4.3, owner:
    « mets juste les scores »). Nothing selected, no number: today's score is
    the dial's, and the 14 days' mean decided nothing and looked like it (the
    audit, 2026-10-09: « 65 % en moyenne sur 14 jours » next to the dial's
    64 %); the card's title (`title`) rests in the readout's place instead, so
    no empty block is reserved and a tapped day's readout takes its place.
    None under 2 days with a score."""
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
            r.append(["—", "", f"{viz.d_short(d)} · {NO_MEASURE}"])
            a.append(f"{viz.d_long(d)} : {NO_MEASURE}.")
            continue
        r.append([pct(v), f"{st['glyph']} {st['word']}", viz.d_short(d)])
        a.append(f"{viz.d_long(d)} : {pct(v)}, {st['word'].lower()}.")
    c = viz.day_bars("recuperation", days, values, readouts=r, arias=a, classes=classes, tones=tones, y_max=100,
                     lines=((70, pct(70)), (40, pct(40))), today=len(days) - 1 if days and days[-1] == today else None,
                     summary=f"Récupération sur {len(days)} jours : {len(with_score)} jours avec un score")
    title = f"Récupération · {len(days)} jours"
    c = viz.rest(c, ["", title, ""], f"Récupération des {len(days)} derniers jours. Choisis un jour pour voir son "
                 "score et son état.", back=len(days) - 1)
    return {**c, "title": title}
