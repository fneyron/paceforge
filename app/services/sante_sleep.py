"""Santé › Sommeil: « comment je dors, et est-ce dans ma normale ? ».

One range toggle (14 nuits · 3 mois · 1 an, ?r=) scopes the whole view:
- the Nuits figure: 24-h amounts (night solid, nap lighter and hatched, < 6 h
  outlined; ≈ 7 h line, Johnston 2020; the athlete's usual ± 30 min (H) and
  the 7-night mean line once there) above the bed → wake windows (main window
  only; times detected by the watch, approximate, rounded to 5 min: de
  Zambotti 2024; naps as their own segments, never in the timing);
- the selected night's hypnogram, from real Garmin intervals only (shape, not
  a measure: Schyvens 2025; Lee 2023);
- « Cœur la nuit »: VFC above, FC de nuit below, one axis, one scrub, one
  readout of per-night values and « normale x–y » (the 7-night means are
  printed once, on Aujourd'hui); respiration as a slim third panel only while
  the HR alert is on (Budig 2022: it supports, never starts, an alert);
- the closed « Les chiffres de chaque nuit » table (the accessible
  alternative), and the closed « Comment je lis tes nuits » fold.
At most one sentence per figure. Empty states: no night ever → the wear
advice; nothing in the range → « Rien sur 3 mois · ta dernière série : … » and
the range that holds it. « 1 an » (weekly marks) shows only once 30 measured
nights are older than 90 days (H).
"""
import statistics
from datetime import date, datetime, time, timedelta

from app.services import nights as nt
from app.services import viz

RANGES = {"14": (14, "14 nuits", "14 jours"), "90": (90, "3 mois", "3 mois"), "365": (365, "1 an", "1 an")}
YEAR_OLD_NIGHTS = 30  # (H) measured nights older than 90 days before « 1 an » is offered
SERIES_GAP = 21  # days (H): a longer gap ends a series of measured nights
VALID_MAIN = 180  # min asleep (H): a night that counts in the coverage
MAX_PERIOD = timedelta(hours=16)  # (H)
MAX_HYPNOGRAMS = 14  # the last 14 real nights of the range (payload)
WEAR = ("Porte ta montre au moins 3 nuits par semaine, des jours variés, et toutes les nuits de J-14 à J+14 "
        "autour d'une course.")

METHOD = [
    "Je calcule ta FC et ta VFC de nuit sur les mesures brutes de ta montre, pendant ta nuit principale "
    "seulement : jamais un score de marque.",
    "Ta normale : au moins 14 nuits sans contexte (course J-7 → J+7, alcool, fuseau, altitude, longue séance, "
    "maladie) sur 60 jours, une par montre. Les seuils marqués (H) sont des choix de PaceForge.",
    "La montre surestime le sommeil, davantage les mauvaises nuits : lis le sens, pas la taille (Chinoy 2021 ; "
    "Schyvens 2025). Ses heures de coucher et de lever sont approximatives (de Zambotti 2024).",
    "Tes siestes comptent dans le total sur 24 h ; une sieste que la montre n'a pas vue n'est pas zéro (Sargent "
    "2018 ; Chinoy 2023). Moins de 7 h par jour sur 2 semaines va avec plus de blessures (Johnston 2020).",
    "L'alerte FC rate beaucoup de maladies : un graphique calme n'est pas un certificat de bonne santé (Quer 2021).",
    "Les stades donnent une allure, pas une mesure (Schyvens 2025 ; Lee 2023).",
    "Aujourd'hui : des jambes lourdes après 3 h ou 1 500 m D+, ou « moins bien » seul, rendent la séance facile. "
    "Ce sont des choix (H) : ton ressenti compte (Saw 2016).",
]
REFS = [
    ("Chinoy 2021", "10.1093/sleep/zsaa291"), ("Schyvens 2025", "10.1093/sleepadvances/zpaf021"),
    ("de Zambotti 2024", "10.1093/sleep/zsad325"), ("Sargent 2018", "10.1080/07420528.2018.1466800"),
    ("Chinoy 2023", "10.2147/NSS.S395732"), ("Johnston 2020", "10.1016/j.jsams.2019.10.013"),
    ("Quer 2021", "10.1038/s41591-020-1123-x"), ("Lee 2023", "10.2196/50983"),
    ("Saw 2016", "10.1136/bjsports-2015-094758"), ("Plews 2014", "10.1123/ijspp.2013-0455"),
]


def _measured(n) -> bool:
    return n.asleep is not None or bool(n.nap_min)


def _valid(n) -> bool:
    return n.asleep is not None and n.asleep >= VALID_MAIN and n.end - n.start <= MAX_PERIOD


def _dm(d: date) -> str:
    """« 9 oct. »."""
    return f"{d.day} {viz.MOIS[d.month - 1]}"


def last_series(days: list[date]) -> tuple[date, date]:
    """The last run of measured days with no gap over SERIES_GAP days."""
    end = start = days[-1]
    for d in reversed(days[:-1]):
        if (start - d).days > SERIES_GAP:
            break
        start = d
    return start, end


def offered_ranges(nights: dict, today: date) -> list[str]:
    old = sum(1 for d, n in nights.items() if n.asleep is not None and d <= today - timedelta(days=90))
    return ["14", "90"] + (["365"] if old >= YEAR_OLD_NIGHTS else [])


def sleep_view(nights: dict, today: date, r: str | None, *, races=(), longs=(), samples=None,
               alert: bool = False) -> dict:
    """Everything the Sommeil view draws for range `r` (else the first range
    holding data). state: never | empty | ok."""
    days_with = sorted(d for d, n in nights.items() if d <= today and _measured(n))
    base = {"method": METHOD, "refs": REFS, "wear": WEAR}
    if not days_with:
        return {**base, "state": "never"}
    offered = offered_ranges(nights, today)

    def holds(key):
        return days_with[-1] > today - timedelta(days=RANGES[key][0])

    chosen = r if r in offered else next((k for k in offered if holds(k)), "14")
    base.update(ranges=[(k, RANGES[k][1]) for k in offered], r=chosen)
    if not holds(chosen):
        a, b = last_series(days_with)
        go = next((k for k in offered if b > today - timedelta(days=RANGES[k][0])), None)
        return {**base, "state": "empty", "empty": f"Rien sur {RANGES[chosen][2]} · ta dernière série : {_dm(a)} → {_dm(b)}",
                "go": (go, f"Voir {RANGES[go][1]}") if go else None}
    if chosen == "365":
        return {**base, "state": "ok", **_year(nights, today, races, longs, alert)}
    n_days = RANGES[chosen][0]
    first = today - timedelta(days=n_days - 1)
    if chosen != "14":  # the axis starts at the first data point
        first = max(first, next(d for d in days_with if d >= first))
    days = [first + timedelta(days=i) for i in range((today - first).days + 1)]
    return {**base, "state": "ok", **_daily(nights, days, today, chosen, races, longs, samples or {}, alert)}


def _tags(nights, days) -> dict:
    return {d: [nt.TAG_WORDS[t] for t in sorted(nights[d].tags)] for d in days if d in nights and nights[d].tags}


def _usual(nights, today: date) -> dict | None:
    """Median onset and wake (≥ 5 nights) and their spread (≥ 8 nights) over 28 days (H)."""
    t = nt.timing(nights, today)
    if t["n"] < 5:
        return None
    return t


def _coverage(nights, days, chosen) -> str:
    k = sum(1 for d in days if d in nights and _valid(nights[d]))
    s = "s" if k > 1 else ""
    return f"{k} nuit{s} mesurée{s} sur {14 if chosen == '14' else RANGES[chosen][1]}"


def _building(nights, today: date) -> str | None:
    """« Ta normale se construit : 3 nuits sur 14 hors course. » while neither HR nor HRV has a band."""
    until = today + timedelta(days=1)
    have = [n for d, n in nights.items() if today - timedelta(days=nt.BAND_DAYS) < d <= today
            and (n.hr is not None or n.hrv is not None)]
    if not have or nt.band(nights, "hr", until) or nt.band(nights, "hrv", until):
        return None
    k = max(nt.band_count(nights, "hr", until), nt.band_count(nights, "hrv", until))
    race = " hors course" if any("race" in n.tags for n in have) else ""
    return f"Ta normale se construit : {k} nuit{'s' if k > 1 else ''} sur 14{race}."


def _amount_line(nights, today: date) -> str | None:
    if nt.quiet_short_sleep(nights, today):
        return "Moins de 7 h en moyenne sur tes nuits mesurées des 14 derniers jours."
    m = nt.mean7(nights, "tst24", today)
    b = nt.band(nights, "tst24", today - timedelta(days=6))
    if m and b and m["value"] < b["lo"]:
        return "Nuits plus courtes que d'habitude cette semaine."
    return None


def _timing_line(usual: dict | None) -> str | None:
    """Regularity as minutes only, once 8 nights are there (H; Fischer 2021); a
    « rendormi » morning leaves the wake out (its spread then goes unsaid)."""
    if not usual or not usual.get("regular_ok"):
        return None
    r5 = lambda sd: max(5, int(round(sd / 5)) * 5)  # noqa: E731
    out = f"D'une nuit à l'autre, ton coucher bouge de ± {r5(usual['bed_sd'])} min"
    if usual.get("wake_sd"):
        out += f", ton lever de ± {r5(usual['wake_sd'])} min"
    return out + "."


def _heart_line(nights, today: date, alert: bool) -> str | None:
    if alert:
        return "FC de nuit nettement au-dessus de ta normale 2 nuits de suite."
    k = 0
    while k < 60:
        d = today - timedelta(days=k)
        m, b = nt.mean7(nights, "hrv", d), nt.band(nights, "hrv", d - timedelta(days=6))
        if not (m and b and m["value"] < b["lo"]):
            break
        k += 1
    return f"VFC sous ta normale depuis {k} nuits." if k >= 3 else None


def _panels(nights, days, alert: bool, weekly=None) -> list[dict]:
    """VFC above, FC de nuit below (respiration while the HR alert is on):
    per-night values (never judged one by one), the rolling band of the 60
    days before each night, the 7-night mean line (the trend the band is
    for)."""
    def vals(metric):
        if weekly:
            return [weekly[d].get(metric) for d in days]
        return [(nights[d].value(metric) if d in nights else None) for d in days]

    def bands(metric):
        out = []
        for d in days:
            b = nt.band(nights, metric, d + timedelta(days=7) if weekly else d)
            if b and metric == "resp":
                b = {**b, "lo": b["center"] - 1, "hi": b["center"] + 1}  # (H) median ± 1 breath/min
            out.append((b["lo"], b["hi"]) if b and b.get("lo") is not None else None)
        return out

    def means(metric):
        if weekly:
            return [None] * len(days)
        return [(nt.mean7(nights, metric, d) or {}).get("value") for d in days]

    def judge(metric):
        # one night is never judged (Buchheit 2014: ≈ 12 % night to night; the band is the 7-night mean's):
        # its dot stays plain, the readout prints its value and « normale x–y »; a weekly median is
        # judged, on usable weeks
        return [True] * len(days) if weekly else [False] * len(days)

    panels = [
        {"name": "VFC", "unit": "ms", "unit_long": "millisecondes", "values": vals("hrv"), "band": bands("hrv"),
         "mean": means("hrv"), "judge": judge("hrv"), "min_span": 20},
        {"name": "FC", "unit": "bpm", "unit_long": "battements par minute", "values": vals("hr"), "band": bands("hr"),
         "mean": means("hr"), "judge": judge("hr"), "min_span": 8},
    ]
    if alert and not weekly and any(v is not None for v in vals("resp")):
        panels.append({"name": "Resp.", "unit": "/min", "unit_long": "respirations par minute", "values": vals("resp"),
                       "band": bands("resp"), "mean": [None] * len(days), "judge": judge("resp"), "min_span": 4,
                       "digits": 1, "h": 48})
    return panels


def _last(days, ok) -> int:
    return max((i for i, d in enumerate(days) if ok(d)), default=len(days) - 1)


def _daily(nights, days, today, chosen, races, longs, samples, alert) -> dict:
    tags = _tags(nights, days)
    usual = _usual(nights, today)
    sel = _last(days, lambda d: d in nights and _measured(nights[d]))
    band = [(b["lo"], b["hi"]) if (b := nt.band(nights, "tst24", d)) else None for d in days]
    mean = [(nt.mean7(nights, "tst24", d) or {}).get("value") for d in days]
    chart = viz.nights_chart(days, nights, usual=usual, races=races, longs=longs, tags=tags, link="#hyp", sel=sel,
                             mean=mean, band=band)
    hyps = []
    for i, d in enumerate(days):
        n = nights.get(d)
        if n and n.timeline and samples.get(d):
            h = viz.hypnogram(samples[d], n.start, n.end)
            if h:
                h["aria"] = f"Forme de la {viz.night_label(d)}, estimée par la montre : une allure, pas une mesure"
                hyps.append((i, h))
    hyps = hyps[-MAX_HYPNOGRAMS:]
    heart = None
    if any(d in nights and (nights[d].hr is not None or nights[d].hrv is not None) for d in days):
        hsel = _last(days, lambda d: d in nights and (nights[d].hr is not None or nights[d].hrv is not None))
        heart = viz.band_chart("coeur", days, _panels(nights, days, alert), races=races, longs=longs, tags=tags,
                               sel=hsel, title="Cœur la nuit")
    return {"weekly": False, "coverage": _coverage(nights, days, chosen), "building": _building(nights, today),
            "nights": chart, "amount_line": _amount_line(nights, today), "timing_line": _timing_line(usual),
            "hyps": hyps, "hyp_sel": sel, "hyp_has_sel": any(i == sel for i, _ in hyps),
            "heart": heart, "heart_line": _heart_line(nights, today, alert) if heart else None,
            "rows": _rows(nights, days, races)}


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _year(nights, today, races, longs, alert) -> dict:
    """« 1 an »: one mark per week (weeks with ≥ 3 nights): the mean 24-h
    amount, the median bed → wake window, the weekly medians of VFC and FC."""
    lo = today - timedelta(days=364)
    first = min(d for d, n in nights.items() if d >= lo and _measured(n))
    weeks = []
    m = _monday(first)
    while m <= today:
        weeks.append(m)
        m += timedelta(days=7)
    pseudo, weekly, labels = {}, {}, []
    for m in weeks:
        wd = [m + timedelta(days=k) for k in range(7) if m + timedelta(days=k) <= today]
        mains = [nights[d] for d in wd if d in nights and nights[d].asleep is not None]
        per = {}
        for metric in ("hr", "hrv"):
            v = [nights[d].value(metric) for d in wd if d in nights and nights[d].value(metric) is not None]
            per[metric] = statistics.median(v) if len(v) >= 3 else None
        weekly[m] = per
        k = len(mains)
        if k >= 3:
            ref = datetime.combine(m - timedelta(days=1), time(0))
            bed = statistics.median(viz._axis_min(n.start, n.day) for n in mains)
            wake = statistics.median(viz._axis_min(n.end, n.day) for n in mains)
            nap = statistics.fmean(n.nap_min for n in mains)
            pseudo[m] = nt.Night(day=m, start=ref + timedelta(minutes=bed), end=ref + timedelta(minutes=wake),
                                 asleep=round(statistics.fmean(n.asleep for n in mains)),
                                 naps=[(None, None, round(nap))] if nap >= 1 else [])
        labels.append((f"sem. du {viz.d_short(m)}" + (f" · moyenne de {k} nuits" if k >= 3 else ""),
                       f"semaine du {viz.d_long(m)}" + (f", moyenne de {k} nuits" if k >= 3 else "")))
    races_w = [(_monday(d), name) for d, name in races if d >= weeks[0]]
    longs_w = sorted({_monday(d) for d in longs if d >= weeks[0]})
    sel = _last(weeks, lambda m: m in pseudo)
    chart = viz.nights_chart(weeks, pseudo, races=races_w, longs=longs_w, sel=sel, mean=[None] * len(weeks),
                             slot_labels=labels, unit_word="semaines")
    heart = None
    if any(weekly[m]["hr"] is not None or weekly[m]["hrv"] is not None for m in weeks):
        hsel = _last(weeks, lambda m: weekly[m]["hr"] is not None or weekly[m]["hrv"] is not None)
        hl = [(f"sem. du {viz.d_short(m)}", f"semaine du {viz.d_long(m)}") for m in weeks]
        heart = viz.band_chart("coeur", weeks, _panels(nights, weeks, alert, weekly), races=races_w, longs=longs_w,
                               sel=hsel, title="Cœur la nuit, médianes par semaine", slot_labels=hl, unit_word="semaines")
    days = [lo + timedelta(days=i) for i in range(365)]
    return {"weekly": True, "coverage": _coverage(nights, days, "365"), "building": _building(nights, today),
            "nights": chart, "amount_line": None, "timing_line": None, "hyps": [], "hyp_sel": None,
            "hyp_has_sel": False, "heart": heart, "heart_line": _heart_line(nights, today, alert) if heart else None,
            "rows": _rows(nights, days, races)}


def _rows(nights, days, races) -> list[dict]:
    """« Les chiffres de chaque nuit », newest first: « — » when missing, tags as words."""
    race_of = {d: name for d, name in races}
    out = []
    for d in reversed(days):
        n = nights.get(d)
        if not n or not (_measured(n) or n.hr is not None or n.hrv is not None):
            continue
        marks = ([f"{viz.GLYPH['race']} {race_of[d]}"] if d in race_of else []) + \
            [f"{viz.GLYPH['tag']} {nt.TAG_WORDS[t]}" for t in sorted(n.tags)]
        out.append({"date": viz.d_short(d), "iso": d.isoformat(),
                    "tst": viz.hm(n.tst24) if n.tst24 is not None else "—",
                    "night": viz.hm(n.asleep) if n.asleep is not None else "—",
                    "nap": viz.hm(n.nap_min) if n.nap_min else "—",
                    "times": f"{viz.clock(n.start)} → {viz.clock(n.end)}" if n.start else "—",
                    "hr": viz.num(n.hr) if n.hr is not None else "—",
                    "hrv": viz.num(n.hrv) if n.hrv is not None else "—",
                    "marks": " · ".join(marks) or "—"})
    return out
