"""Daily nightly-signal index: an interpretation of received measurements, not recovery clearance.

Needs the assessed night, at least three measured nights in seven on a cardiac series, and a usable reference:
14 nights from day −67 to −8, or a provisional reference from seven nights (including a recent fallback while
that lagged window builds). Apple's SDNN stays separate and never enters this RMSSD index.

Each usable cardiac series contributes independently. Red means the existing night-HR illness alert; orange
means low RMSSD, high night HR, the breathing flag or less than six hours of sleep in the measured 24 h.
The existing personal thresholds and acute-alert rules are unchanged. No reason means usual signals.
Recorded efforts and training load remain contextual: their windows neither add reasons nor cap this index.

The displayed /100 is 100 × the mean of s_VFC = clip((z + 2.5)/2, 0, 1) and s_FC = clip((2.5 − z)/2, 0, 1),
held to ≤ 69 when orange, ≤ 39 when red, ≥ 70 when green (H). This descriptive scale is not clinically
validated. Missing data give a reason rather than a number. History is computed using events up to each day.
Details: docs/sante-estimates.md and docs/recovery-cumulative.md; the dated decisions behind the rules:
docs/decisions.md.
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
HISTORY_DAYS = 14  # the Signaux nocturnes card
HISTORY_NIGHTS = 160  # days of nights a past day reads: its baseline (days −67 → −8), its alert's, with room

# « Comment je calcule »: one method fold, a few bullets for the three dials in their order, the stages and the
# margin; plain words, no citation, no « (H) »; sentences of 15 words at most, « tes valeurs habituelles », never
# « ta normale »; nothing the state no longer does. The references are on /sante/sources.
METHOD = [
    "Sommeil compare tes 24 h, siestes comprises, au repère de 8 h. "
    "Ce repère ne mesure pas ton besoin personnel. L’écart estimé sur 7 jours est indiqué à part.",
    "Les signaux nocturnes comparent VFC et FC de nuit à tes valeurs habituelles. "
    "La tendance porte sur 7 jours. La référence couvre 60 jours, sans la dernière semaine. "
    "Au début, une référence récente reste provisoire. "
    "Un sommeil court ou une respiration rapide sont signalés. Les efforts récents restent un contexte séparé. "
    "Ce bilan ne prédit pas ta date de récupération.",
    "La VFC mesure les variations entre battements cardiaques. La FC de nuit est ton pouls moyen nocturne. "
    "Les graphiques précisent si les éveils sont exclus.",
    "Entraînement compare tes 7 derniers jours à ta semaine habituelle. Une minute compte davantage quand ton "
    "pouls est haut.",
    "Ta montre estime tes phases : la forme de ta nuit, pas sa qualité. L’indice nocturne n’est pas un "
    "pourcentage de récupération.",
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
TEXTS = {"hrv": "Une VFC plus basse que tes habitudes sur plusieurs nuits peut accompagner une fatigue ou un stress accru. "
                "À lire avec ton sommeil, ta FC nocturne et ton ressenti.",
         "hr": "Ça arrive avec la fatigue, la chaleur, l'alcool ou un début de maladie.",
         "resp": "Une respiration nocturne plus rapide que d’habitude est un signal à croiser avec ta FC et ton ressenti. "
                 "Elle ne permet pas, à elle seule, d’en identifier la cause.",
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
    from app.services.nights import MIN_MEAN_NIGHTS, reference_min_nights

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
        required = reference_min_nights(measured[0], t.get("source"))
        return {"key": "building", "label": f"repères en construction ({n}/{required})", "n": n,
                "required": required, "metric": measured[0]}
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
    for metric in used:
        if trends[metric]["status"] == BAD[metric] and not (metric == "hr" and day.get("alert")):
            reasons.append(_reason(metric, "warn"))
    if day.get("resp"):
        reasons.append(_reason("resp", "warn"))
    tst = day.get("tst24")
    if tst is not None and tst < SHORT_DAY_MIN:
        reasons.append(_reason("short", "warn"))
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
    from app.services.nights import MIN_BAND_NIGHTS, MIN_MEAN_NIGHTS, reference_min_nights

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
            state = f"Repères en construction ({t.get('baseline_n', 0)}/{reference_min_nights(key, t.get('source'))})"
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

DIAL_WORDS = {"ok": "habituels", "warn": "à surveiller", "danger": "inhabituels"}
NO_SCORE = "pas de score"  # the dial's word without a state (the line under the dials says why)
NO_MEASURE = "pas de mesure ce jour-là"  # a day without a state: no bar, no dot, its readout says it


def pct(v: int) -> str:
    """The indice as the page prints it (a percentage, as WHOOP and Oura do): « 65 % », the same number, a no-break
    space (the display font has no narrow one)."""
    return f"{v}{viz.NBSP}%"


def index_label(v: int) -> str:
    return f"{v}{viz.NBSP}/100"


def dial(score: dict, state: dict | None, href: str | None = None) -> dict:
    """The Récupération dial, the middle one of three (like WHOOP's, kept simple): the indice nocturne (« 65 % »,
    the « % » smaller), its arc in its state's colour, its name under it
    and the state's word (« bonne », « en cours », « faible »: the name and the word say the state, never colour
    alone); « — » and « pas de score » without a state (an empty dial, like WHOOP). `href`: its card below, None
    when the page has none (a plain dial)."""
    v = score.get("value")
    if v is None:
        return viz.ring("recup", None, "—", "Récupération", NO_SCORE, tone="none", href=href,
                        aria="Récupération : pas d’indice nocturne pour cette nuit.")
    word = DIAL_WORDS[state["tone"]]
    return viz.ring("recup", v / 100, str(v), "Récupération", word, tone=state["tone"], unit="/100", href=href,
                    note="indice nocturne", aria=f"Récupération : indice nocturne {v} sur 100, {word}.")


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
        r.append([index_label(v), f"{st['glyph']} {st['word']}", viz.d_short(d)])
        a.append(f"{viz.d_long(d)} : indice {v} sur 100, {st['word'].lower()}.")
    c = viz.day_bars("recuperation", days, values, readouts=r, arias=a, classes=classes, tones=tones, y_max=100,
                     lines=((70, "70"), (40, "40"), (0, "0")), today=len(days) - 1 if days and days[-1] == today else None,
                     summary=f"Historique des signaux nocturnes : {len(with_score)} indices sur {len(days)} jours")
    title = f"Signaux nocturnes · {len(days)} jours"
    c = viz.rest(c, [index_label(rounded(statistics.fmean(with_score))), "en moyenne", ""], f"Historique des scores quotidiens sur {len(days)} jours. Choisis un jour pour voir son "
                 "score et son état.", back=len(days) - 1)
    return {**c, "title": title, "average": index_label(rounded(statistics.fmean(with_score)))}
