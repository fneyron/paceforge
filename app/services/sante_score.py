"""PaceForge's daily state and its « indice nocturne »: a rule-based state on the validated HRV-guided reading
(Plews 2013; Vesterinen 2016; Javaloyes 2019; Nuuttila 2022), never a weighted 0–100 index with caps (a composite
score has no validated basis: Doherty 2025).

Needs: last night measured, a heart series (RMSSD, else night HR; never Apple's SDNN) with a full baseline (14
nights from day −67 to day −8) and 3 nights at least in the last 7, last night among them (nights.trend). Never a
state from sleep alone or from activities alone.

- Primary marker: the VFC (RMSSD), else the FC de nuit.
- Red (danger): the night-HR illness alert (nights.illness_alert), or D+0 and D+1 after an Ultra.
- Orange (warn), each reason listed: the primary marker's 7-day trend outside its smallest worthwhile change
  (z ≤ −T for the VFC, z ≥ +T for the FC de nuit, T: nights.threshold); the breathing flag
  (nights.resp_up_nights); under 6 h of sleep in the 24 h (Craven 2022); an effort window open (C7).
  The FC de nuit outside while the VFC is inside shows on its own row, never changes the state.
- Green (ok): none.

The number the Récupération dial reads (`value`) is an « indice nocturne », not a recovery percentage: 100 × the
mean, over the heart series used, of s_VFC = clip((z + 2.5)/2, 0, 1) and s_FC = clip((2.5 − z)/2, 0, 1) — 100 at
or above the smallest worthwhile change on the good side, 0 at 2.5 SD on the bad one (H) — held to ≤ 69 when
orange, ≤ 39 when red, ≥ 70 when green (H), so the number and the state never disagree. Insufficient data: a
reason, never a number (`missing`). Scores are rounded half up. Daily history is recomputed from the stored events
up to each assessed day, nothing dated after it. No physiological recovery deadline is invented. Details:
docs/sante-estimates.md and docs/recovery-cumulative.md; the dated decisions behind the rules: docs/decisions.md.
"""
import math
import re
import statistics
from datetime import date

from app.services import viz
from app.services.nights import SHORT_DAY_MIN

HEART = ("hrv", "hr")
ORDER = ("hrv", "hr", "sleep")
BAD = {"hrv": "below", "hr": "above"}  # the side that matters: VFC under its usual values, FC de nuit over them
# the indice nocturne: s = clip((2.5 ± z)/2, 0, 1) (H: 100 from the smallest worthwhile change, 0 at 2.5 SD)
Z_FLOOR, Z_SPAN = 2.5, 2.0  # (H)
HOLD = {"ok": (70, 100), "warn": (0, 69), "danger": (0, 39)}  # (H) the number never contradicts the state
HISTORY_DAYS = 14  # the Récupération card
HISTORY_NIGHTS = 160  # days of nights a past day reads: its baseline (days −67 → −8), its alert's, with room

# « Comment je calcule »: one method fold, a few bullets for the three dials in their order, the stages and the
# margin; plain words, no citation, no « (H) »; sentences of 15 words at most, « tes valeurs habituelles », never
# « ta normale »; nothing the state no longer does. The references are on /sante/sources.
METHOD = [
    "Sommeil compare tes 24 h, siestes comprises, à un besoin de 8 h. "
    "Le manque des 7 dernières nuits est indiqué à part. Ta journée commence au réveil.",
    "Récupération compare 7 jours de VFC et de FC de nuit à tes valeurs habituelles. "
    "Elles couvrent 60 jours, sans la dernière semaine. "
    "Un effort récent, un sommeil court ou une respiration rapide sont signalés. "
    "Ce bilan ne prédit pas ta date de récupération.",
    "La VFC mesure les variations entre battements cardiaques. La FC de nuit est ton pouls moyen nocturne. "
    "Les graphiques précisent si les éveils sont exclus.",
    "Entraînement compare tes 7 derniers jours à ta semaine habituelle. Une minute compte davantage quand ton "
    "pouls est haut.",
    "Ta montre estime tes phases : la forme de ta nuit, pas sa qualité. Mes pourcentages sont des estimations "
    "aussi : quelques points d'écart ne veulent rien dire.",
]

# the references the method rests on (with sante_sleep.REFS), listed on /sante/sources (label, DOI or URL):
# « Recommandations officielles » (consensus, position stands, guidelines), then « Études scientifiques »
REFS = [
    ("Recommandations officielles", [
        ("Kellmann 2018", "10.1123/ijspp.2017-0759"), ("Meeusen 2013", "10.1249/MSS.0b013e318279a10a"),
        ("Watson 2015a", "10.5665/sleep.4716"), ("Hirshkowitz 2015", "10.1016/j.sleh.2014.12.010"),
        ("Schwellnus 2016", "10.1136/bjsports-2016-096572"), ("Walsh 2021", "10.1136/bjsports-2020-102025"),
        ("BASES 2023", "https://westminsterresearch.westminster.ac.uk/item/wxx7y/"
                       "bases-expert-statement-methods-to-monitor-athletes-sleep"),
        ("Impellizzeri 2019 · charge interne et externe", "10.1123/ijspp.2018-0935"),
        ("Impellizzeri 2020 · limites du ratio aigu:chronique", "10.1123/ijspp.2019-0864"),
        ("Sammito 2024", "10.1186/s12995-024-00414-9"), ("Quigley 2024", "10.1111/psyp.14604"),
    ]),
    ("Études scientifiques", [
        ("Plews 2013a · moyenne glissante sur 7 jours", "10.1123/ijspp.8.6.688"),
        ("Plews 2013b · VFC et adaptation à l’entraînement", "10.1007/s40279-013-0071-8"),
        ("Buchheit 2014", "10.3389/fphys.2014.00073"), ("Plews 2014", "10.1123/ijspp.2013-0455"),
        ("Vesterinen 2016 · entraînement guidé par la VFC", "10.1249/MSS.0000000000000910"),
        ("Javaloyes 2019 · entraînement guidé par la VFC", "10.1123/ijspp.2018-0122"),
        ("Nuuttila 2022 · VFC et FC nocturnes", "https://pmc.ncbi.nlm.nih.gov/articles/PMC9473708/"),
        ("Hecksteden 2017 · repères individualisés", "10.1123/ijspp.2016-0120"),
        ("Shaffer et Ginsberg 2017 · mesures de la VFC", "https://pmc.ncbi.nlm.nih.gov/articles/PMC5624990/"),
        ("Miller 2020 · respiration nocturne", "https://pmc.ncbi.nlm.nih.gov/articles/PMC7728254/"),
        ("Alavi 2022", "10.1038/s41591-021-01593-2"),
        ("Doherty 2025 · limites des scores composites", "10.1515/teb-2025-0001"),
        ("Dial 2025 · validation des mesures nocturnes", "10.14814/phy2.70527"),
        ("Xu 2026 · VFC optique et ECG", "10.3390/s26165192"),
        ("Morton, Fitz-Clarke et Banister 1990 · charge cardiaque", "https://pubmed.ncbi.nlm.nih.gov/2246166/"),
        ("Busso 2003 · modèle forme-fatigue", "10.1249/01.MSS.0000074465.13621.37"),
        ("Williams 2017 · moyennes exponentielles de charge", "10.1136/bjsports-2016-096589"),
        ("Imbach 2022 · limites des modèles forme-fatigue", "10.1186/s40798-022-00426-x"),
        ("Petersen 2007 · récupération après un marathon", "10.1007/s00421-007-0504-x"),
        ("Vesterinen 2014 · FC à allure fixe", "10.1519/JSC.0000000000000349"),
        ("Saw 2016", "10.1136/bjsports-2015-094758"),
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


def rounded(x: float) -> int:
    """Half up on the exact value (snapped to 1e-9: 64,5 is 65, never 64,4999… → 64)."""
    return int(math.floor(round(x, 9) + 0.5))


# ── the daily state ─────────────────────────────────────────────────────────

def is_sdnn(trend: dict | None) -> bool:
    return bool(trend and trend.get("source") and "(SDNN sommeil)" in trend["source"])


def usable(metric: str, trend: dict | None) -> bool:
    """A heart series the state reads: last night measured on it, a baseline (full from 14 nights; « provisoire »
    from 7, read all the same and said so: the score shows while the repères build), 3 nights in the 7 days, never
    Apple's SDNN (descriptive only, no RMSSD rule applies to it)."""
    return bool(trend and trend["last"] and trend["z"] is not None and trend["baseline"]
                and not (metric == "hrv" and is_sdnn(trend)))


def sub(metric: str, z: float) -> float:
    """s_VFC = clip((z + 2.5)/2, 0, 1), s_FC = clip((2.5 − z)/2, 0, 1)."""
    return min(1.0, max(0.0, (Z_FLOOR + (z if metric == "hrv" else -z)) / Z_SPAN))


# the reasons, as data (their wording on the page is a GUI proposal): key → label, and the sentence of each
LABELS = {"ill": "FC de nuit nettement plus haute depuis 2 nuits", "effort": "Effort récent",
          "hrv": "VFC plus basse que d'habitude", "hr": "FC de nuit plus haute que d'habitude",
          "resp": "Respiration plus rapide depuis 2 nuits", "short": "Sommeil court"}
TEXTS = {"hrv": "Ça arrive avec la fatigue, le stress, l'alcool ou un début de maladie.",
         "hr": "Ça arrive avec la fatigue, la chaleur, l'alcool ou un début de maladie.",
         "resp": "Ta respiration nocturne est plus rapide que d'habitude. Ça arrive au début d'une infection.",
         "short": "Moins de 6 h de sommeil sur 24 h."}


# why there is no state when last night was measured elsewhere (missing_reason; its wording is a GUI proposal)
ELSEWHERE = {"device": "dernière nuit mesurée par une autre montre que d'habitude",
             "method": "dernière nuit mesurée avec un autre calcul que d'habitude"}


def _reason(key: str, tone: str, text: str | None = None) -> dict:
    return {"key": key, "tone": tone, "label": LABELS[key], "text": text if text is not None else TEXTS.get(key)}


def missing_reason(day: dict) -> dict:
    """Why there is no state (C8: a reason, never a number): {key, label}: no night (« pas de bilan »), a baseline
    under 14 nights (« repères en construction (n/14) »), under 3 nights this week (« pas assez de nuits cette
    semaine (n/3) »), the heart measured last night on another series only (ELSEWHERE: another watch, another
    calculation), the heart not measured last night (« pas de bilan »)."""
    from app.services.nights import MIN_BAND_NIGHTS, MIN_MEAN_NIGHTS

    if not day.get("night_measured"):
        return {"key": "no_night", "label": "pas de bilan"}
    trends = day.get("trends") or {}
    measured = [m for m in HEART if (t := trends.get(m)) and t["last"] and not (m == "hrv" and is_sdnn(t))]
    if not measured:
        elsewhere = day.get("elsewhere") or {}
        if elsewhere:  # last night measured, but on another series than its usual values' (another watch or calculation)
            kind = "device" if "device" in elsewhere.values() else "method"
            return {"key": "elsewhere", "label": ELSEWHERE[kind], "kind": kind}
        return {"key": "no_heart", "label": "pas de bilan"}
    t = trends[measured[0]]
    if not t["baseline"]:
        n = t.get("baseline_n", 0)
        return {"key": "building", "label": f"repères en construction ({n}/{MIN_BAND_NIGHTS})", "n": n,
                "required": MIN_BAND_NIGHTS, "metric": measured[0]}
    return {"key": "few", "label": f"pas assez de nuits cette semaine ({t['n']}/{MIN_MEAN_NIGHTS})", "n": t["n"],
            "required": MIN_MEAN_NIGHTS, "metric": measured[0]}


def score_of(day: dict) -> dict:
    """The day's state and its indice nocturne (the module docstring) from sante._assess's day: {value, tone,
    primary, parts [{key, z, T, s, status, n, value, centre}], reasons [{key, tone, label, text}], reason (the first
    one's key), missing (why no state, else None), measured, caps ([]: no cap any more), absent, building}."""
    trends = day.get("trends") or {}
    used = [m for m in HEART if usable(m, trends.get(m))]
    absent = [m for m in HEART if m not in used]
    building = [m for m in HEART if (t := trends.get(m)) and not (m == "hrv" and is_sdnn(t)) and not t["baseline"]]
    out = {"value": None, "tone": None, "primary": None, "parts": [], "reasons": [], "reason": None,
           "missing": None, "measured": False, "caps": [], "absent": absent, "building": building}
    if not day.get("night_measured") or not used:
        out["missing"] = missing_reason(day)
        return out
    parts = []
    for m in used:
        t = trends[m]
        parts.append({"key": m, "z": t["z"], "T": t["T"], "s": sub(m, t["z"]), "status": t["status"], "n": t["n"],
                      "value": t["value"], "centre": t["baseline"]["center"]})
    primary = used[0]  # the VFC, else the FC de nuit
    reasons = []
    if day.get("alert"):
        reasons.append(_reason("ill", "danger"))
    w = day.get("window")
    if w and w["tone"] == "danger":
        reasons.append(_reason("effort", "danger", w["text"]))
    if trends[primary]["status"] == BAD[primary]:
        reasons.append(_reason(primary, "warn"))
    if day.get("resp"):
        reasons.append(_reason("resp", "warn"))
    tst = day.get("tst24")
    if tst is not None and tst < SHORT_DAY_MIN:
        reasons.append(_reason("short", "warn"))
    if w and w["tone"] == "warn":
        reasons.append(_reason("effort", "warn", w["text"]))
    tone = "danger" if any(r["tone"] == "danger" for r in reasons) else "warn" if reasons else "ok"
    lo, hi = HOLD[tone]
    value = min(hi, max(lo, rounded(100 * statistics.fmean(p["s"] for p in parts))))
    out.update(value=value, tone=tone, primary=primary, parts=parts, reasons=reasons,
               reason=reasons[0]["key"] if reasons else None, measured=True)
    return out


def row_tone(metric: str, trend: dict | None, alert: bool = False) -> str | None:
    """A heart signal's colour on its card: red under the illness alert (FC de nuit), orange out of its usual values
    on the side that matters (VFC under, FC de nuit over: shown even when it changes nothing, the secondary
    marker), neutral on the other side (never praised: Plews 2013), green in them; None without a comparison."""
    if alert and metric == "hr":
        return "danger"
    status = (trend or {}).get("status")
    if status is None:
        return None
    if status == BAD[metric]:
        return "warn"
    return "accent" if status in ("above", "below") else "ok"


def quality(day: dict) -> dict:
    """« Mesures utilisées »: each signal's place in the day's state — availability, not a statistical confidence
    percentage or a validation seal. Respiration is listed once the watch measures it (its flag is a reason of the
    state)."""
    from app.services.nights import MIN_BAND_NIGHTS, MIN_MEAN_NIGHTS

    score, trends = day["score"], day.get("trends") or {}
    used = {p["key"] for p in score["parts"]}
    rows = [{"name": "Sommeil", "state": "Utilisé" if day.get("tst24") is not None else "Absent pour cette nuit"}]
    for key, label in (("hr", "FC de nuit"), ("hrv", "VFC")):
        t = trends.get(key)
        if key in used:
            state = "Utilisée"
        elif key in (day.get("elsewhere") or {}):
            state = ("Mesurée par une autre montre cette nuit" if day["elsewhere"][key] == "device"
                     else "Mesurée avec un autre calcul cette nuit")
        elif t is None or not t["last"]:
            state = "Absente pour cette nuit"
        elif key == "hrv" and is_sdnn(t):
            state = "Mesurée, hors bilan (SDNN)"
        elif not t["baseline"]:
            state = f"Repères en construction ({t.get('baseline_n', 0)}/{MIN_BAND_NIGHTS})"
        else:
            state = f"Pas assez de nuits cette semaine ({t['n']}/{MIN_MEAN_NIGHTS})"
        rows.append({"name": label, "state": state})
    resp = day.get("resp_ref")
    if resp is not None:
        rows.append({"name": "Respiration", "state": "Utilisée" if resp["ready"] else
                     "Absente pour cette nuit" if not resp["last"] else
                     f"Repères en construction ({resp['n']}/{MIN_BAND_NIGHTS})"})
    seen = [k for k in HEART if trends.get(k) and not (k == "hrv" and is_sdnn(trends[k]))]
    partial = bool(score["parts"]) and len(score["parts"]) < len(seen)
    # a state read against a provisional baseline (7–13 nights, or a series younger than its lagged window) says so
    provisional = any(trends[k]["baseline"]["provisional"] for k in used if trends.get(k) and trends[k]["baseline"])
    label = "Bilan partiel" if partial else "Repères provisoires" if provisional else "Données du bilan"
    return {"label": label, "partial": partial, "provisional": provisional, "rows": rows}


# ── what the page draws ─────────────────────────────────────────────────────

DIAL_WORDS = {"ok": "bonne", "warn": "en cours", "danger": "faible"}  # the state's word under « Récupération »
NO_SCORE = "pas de score"  # the dial's word without a state (the line under the dials says why)
NO_MEASURE = "pas de mesure ce jour-là"  # a day without a state: no bar, no dot, its readout says it


def pct(v: int) -> str:
    """The indice as the page prints it (a percentage, as WHOOP and Oura do): « 65 % », the same number, a no-break
    space (the display font has no narrow one)."""
    return f"{v}{viz.NBSP}%"


def dial(score: dict, state: dict | None, href: str | None = None) -> dict:
    """The Récupération dial, the middle one of three (like WHOOP's, kept simple): the indice nocturne (« 65 % »,
    the « % » smaller), its arc in its state's colour, its name under it
    and the state's word (« bonne », « en cours », « faible »: the name and the word say the state, never colour
    alone); « — » and « pas de score » without a state (an empty dial, like WHOOP). `href`: its card below, None
    when the page has none (a plain dial)."""
    v = score.get("value")
    if v is None:
        return viz.ring("recup", None, "—", "Récupération", NO_SCORE, tone="none", href=href,
                        aria="Récupération : pas de score ce matin.")
    word = DIAL_WORDS[state["tone"]]
    return viz.ring("recup", v / 100, str(v), "Récupération", word, tone=state["tone"], unit="%", href=href,
                    note="bilan du jour", aria=f"Récupération {pct(v)}, {word}.")


def history_card(history: list[tuple[date, dict | None, dict]], today: date) -> dict | None:
    """« Récupération · 14 jours »: one bar per day with a state, its indice in the state's colour, with faint 40
    and 70 lines labelled on the right (never colour alone), today's day on a disc; nothing on a day without a state
    (no point either), whose readout says « pas de mesure ce jour-là »; tap a bar → « 64 % » « ◐ Récupération en
    cours » / « mer. 7 oct. »: the date, the indice and the state only. Nothing selected, no number: the card's
    title rests
    in the readout's place. None under 2 days with a state."""
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
                     summary=f"Historique de récupération : {len(with_score)} scores quotidiens sur {len(days)} jours")
    title = f"Récupération · {len(days)} jours"
    c = viz.rest(c, ["", f"Historique · {len(days)} jours", ""], f"Historique des scores quotidiens sur {len(days)} jours. Choisis un jour pour voir son "
                 "score et son état.", back=len(days) - 1)
    return {**c, "title": title}
