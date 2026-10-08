"""Santé v4 › the Sommeil section of the one page (Oura-like): « comment je dors ? ».

- Last night (the hero): bed → wake (the watch's detection, approximate,
  rounded to 5 min: de Zambotti 2024) and the naps its 24 h counts
  (« + sieste 2h20 », nights.day_naps: yesterday afternoon's too). Its 24-h
  total is the Sommeil ring's, printed once there; the hero prints it only
  for a night that is not this morning's (the ring is then empty).
- Its stages, like WHOOP and Oura, shown and never judged (owner,
  2026-10-08): one bar of the four phases (Éveil · Léger · Profond ·
  Paradoxal, calm colours that are no good/bad colours), each named with its
  minutes in a legend (colour never alone), « Phases estimées par la
  montre. » (Schyvens 2025: wrist κ 0.21–0.53; Lee 2023): no target, no word,
  no score. A Garmin night with real intervals also gets its hypnogram above
  it (lanes, the same colours). A night without stage minutes: its plain bar
  on a clock axis (bed → wake, the naps on their own lane: never in the
  night's timing, HR or HRV: Mollicone 2008).
- 14 nuits / 3 mois: one bar per day of 24-h sleep (the main night solid, the
  naps lighter on top), the ≈ 7 h line (Johnston 2020), a tiny legend (nuit ·
  sieste · 7 h); « 3 mois » only with a night 14 to 90 days old (else it would
  draw the 14 nights again), its 90 bars (each day's 24 h in one) faint under
  the 7-night mean (Oura's long ranges). Tap a bar → « 8h36 » / « nuit du mer. 7 au jeu. 8 · 22:40 →
  07:30 »; it rests on the range's mean (printed nowhere else: the latest
  night is the ring's).
- Habitudes: the median bedtime and wake of 28 days (5 nights at least, H),
  rounded to 5 min, and « Régularité ± 35 min », the SD of bedtime once 8
  nights are there (H; Fischer 2021). Time-zone nights and the nights after a
  big effort stay out; a « rendormi » wake leaves the wake median (H).
- The closed « Les chiffres de chaque nuit » table (30 nights: the accessible
  alternative of the charts) and the closed « Comment je lis tes nuits » fold.
The nights are drawn as they are: no race, no event marker, no tag glyph on a
chart (the table names the tags).
"""
from datetime import date, timedelta

from app.services import nights as nt
from app.services import viz

RANGES = {"14": (14, "14 nuits"), "90": (90, "3 mois")}
USUAL_NIGHTS = 5  # (H) nights of 28 days before a median bedtime and wake are shown
TABLE_DAYS = 30
REF_MIN = 7 * 60  # ≈ 7 h line (Johnston 2020)

METHOD = [
    "Je calcule ta FC et ta VFC de nuit sur les mesures brutes de ta montre, pendant ta nuit principale "
    "seulement : jamais un score de marque.",
    "Ta normale : tes nuits sans contexte (les 3 nuits après une grosse sortie, une sortie longue la veille ou "
    "intense le soir, l'altitude, un changement de fuseau) sur 60 jours (H), une par montre ; « provisoire » dès "
    "7 nuits (H), elle bouge encore jusqu'à 14 (H). L'alerte FC de nuit attend 14 nuits : une normale provisoire la "
    "ferait sonner pour du bruit (Quer 2021).",
    "La montre surestime le sommeil, davantage les mauvaises nuits : lis le sens, pas la taille (Chinoy 2021 ; "
    "Schyvens 2025). Ses heures de coucher et de lever sont approximatives (de Zambotti 2024).",
    "Tes siestes comptent dans le total sur 24 h ; une sieste que la montre n'a pas vue n'est pas zéro (Sargent "
    "2018 ; Chinoy 2023). Moins de 7 h par jour sur 2 semaines va avec plus de blessures (Johnston 2020).",
    "Régularité : de combien ton coucher bouge d'une nuit à l'autre, dès 8 nuits sur 28 (H ; Fischer 2021).",
    "Les phases (éveil, léger, profond, paradoxal) sont estimées par la montre, pas mesurées : accord faible à moyen "
    "avec le laboratoire (Schyvens 2025 ; Lee 2023). Elles sont montrées, jamais jugées.",
    "L'anneau Sommeil compte les 24 h avant ton réveil, siestes comprises (celle d'hier après-midi aussi) ; il est "
    "plein à 8 h (H) : vert dès 7 h (Johnston 2020), orange de 6 à 7 h, rouge sous 6 h (Craven 2022).",
]
REFS = [
    ("Chinoy 2021", "10.1093/sleep/zsaa291"), ("Schyvens 2025", "10.1093/sleepadvances/zpaf021"),
    ("de Zambotti 2024", "10.1093/sleep/zsad325"), ("Sargent 2018", "10.1080/07420528.2018.1466800"),
    ("Chinoy 2023", "10.2147/NSS.S395732"), ("Johnston 2020", "10.1016/j.jsams.2019.10.013"),
    ("Fischer 2021", "10.1093/sleep/zsab103"), ("Quer 2021", "10.1038/s41591-020-1123-x"),
    ("Lee 2023", "10.2196/50983"), ("Craven 2022", "10.1007/s40279-022-01706-y"),
]


def _measured(n) -> bool:
    return n.asleep is not None or bool(n.nap_min)


def offered_ranges(nights: dict, today: date) -> list[str]:
    """« 14 nuits », and « 3 mois » when a measured day is 14 to 90 days old."""
    mid = any(today - timedelta(days=90) < d <= today - timedelta(days=14) and _measured(n) for d, n in nights.items())
    return ["14"] + (["90"] if mid else [])


def choose(nights: dict, today: date, r: str | None) -> str:
    """The range drawn first: `r` when offered, else the first one holding a measured day."""
    offered = offered_ranges(nights, today)
    if r in offered:
        return r
    recent = any(today - timedelta(days=14) < d <= today and _measured(n) for d, n in nights.items())
    return "14" if recent or len(offered) == 1 else "90"


def _readout(n, d: date) -> tuple[list[str], str]:
    """[value, word, the night · its times] and the spoken sentence: « 8h36 » ·
    « nuit du mer. 7 au jeu. 8 · 22:40 → 07:30 »; with a nap « 8h10 » « nuit
    5h50 + sieste 2h20 » · « … · 23:35 → 05:40 »; a nap alone « 1h22 »
    « sieste seule » · « … · pas de nuit mesurée » (said plainly: no « ? »)."""
    label = viz.night_label(d)
    if n is None or not _measured(n):
        return ["—", "", f"{label} · pas de mesure"], f"{label} : pas de mesure"
    if n.asleep is None:
        return ([viz.hm(n.nap_min), "sieste seule", f"{label} · pas de nuit mesurée"],
                f"{label} : sieste de {viz.hm_long(n.nap_min)} seule, pas de nuit mesurée")
    times = f"{viz.clock(n.start)} → {viz.clock(n.end)}"
    said = (f"{label} : {viz.sleep_spoken(n.asleep, n.nap_min)}, couché vers {viz.clock(n.start)}, "
            f"levé vers {viz.clock(n.end)}")
    if n.nap_min:
        return [viz.hm(n.tst24), f"nuit {viz.hm(n.asleep)} + sieste {viz.hm(n.nap_min)}", f"{label} · {times}"], said
    return [viz.hm(n.asleep), "", f"{label} · {times}"], said


def bars(nights: dict, today: date, key: str) -> dict:
    """The 14-night or 3-month bars (see the module docstring)."""
    n_days = RANGES[key][0]
    days = [today - timedelta(days=n_days - 1 - i) for i in range(n_days)]
    values = [nights[d].asleep if d in nights else None for d in days]
    stack = [(nights[d].nap_min or None) if d in nights else None for d in days]
    if n_days > 14:  # 90 slivers: each day's 24 h in one faint bar, the 7-night mean is the mark
        values = [nights[d].tst24 if d in nights else None for d in days]
        stack = None
    r, a = [], []
    for d in days:
        read, said = _readout(nights.get(d), d)
        r.append(read)
        a.append(said)
    totals = [nights[d].tst24 for d in days if d in nights and nights[d].tst24 is not None]
    trend = None
    if n_days > 14:  # the long range: the 7-night mean over faint bars
        tst = [nights[d].tst24 if d in nights else None for d in days]
        trend = [viz.rolling(tst, i) for i in range(n_days)]
    c = viz.day_bars(f"sommeil-{key}", days, values, stack=stack, readouts=r, arias=a, trend=trend,
                     reference=(REF_MIN, f"7{viz.NNBSP}h"), min_top=9 * 60, today=len(days) - 1,
                     summary=f"Sommeil sur 24 h, {RANGES[key][1]} : {len(totals)} nuit{'s' if len(totals) > 1 else ''} "
                             f"mesurée{'s' if len(totals) > 1 else ''}")
    if totals:
        mean = round(sum(totals) / len(totals) / 5) * 5  # a mean of approximate times: to 5 min
        return viz.rest(c, [viz.hm(mean), "en moyenne", ""],
                        f"Sommeil sur 24 heures, {RANGES[key][1]} : en moyenne {viz.hm_long(mean)}, siestes "
                        "comprises. Choisis une nuit pour la sienne.")
    return viz.rest(c, ["—", "", ""], f"Sommeil, {RANGES[key][1]} : pas de nuit mesurée")


PHASES = (("awake", "Éveil"), ("light", "Léger"), ("deep", "Profond"), ("rem", "Paradoxal"))  # WHOOP's order
LEVEL = {"core": "light", "deep": "deep", "rem": "rem", "awake": "awake"}  # Garmin's sleepLevels kinds


def summed(intervals, start, end) -> dict | None:
    """{awake, light, deep, rem} minutes of real stage intervals [(kind, a, b)]
    cut to the main window [start, end]; None without a sleep stage."""
    out = {k: 0.0 for k, _ in PHASES}
    for kind, a, b in intervals or ():
        a, b = max(a, start), min(b, end)
        if kind in LEVEL and b > a:
            out[LEVEL[kind]] += (b - a).total_seconds() / 60
    out = {k: round(v) for k, v in out.items()}
    return out if out["light"] + out["deep"] + out["rem"] else None


def phases(stages: dict | None) -> dict | None:
    """The stages bar and its legend: [{key, name, min, hm}] in WHOOP's order
    (Éveil · Léger · Profond · Paradoxal; a phase of 0 min left out), and the
    spoken list; None without stage minutes."""
    if not stages:
        return None
    parts = [{"key": k, "name": nm, "min": stages[k], "hm": viz.hm(stages[k])} for k, nm in PHASES if stages.get(k)]
    if not parts:
        return None
    said = ", ".join(f"{p['name'].lower()} {viz.hm_long(p['min'])}" for p in parts)
    return {"parts": parts, "aria": f"Phases estimées par la montre : {said}."}


def hero(nights: dict, today: date, samples: dict | None = None) -> dict | None:
    """Last night (the latest main night of the last 90 days): its times, the
    naps its 24 h counts (nights.day_naps), its stages (the stages bar and
    legend; with real Garmin intervals, the hypnogram above it), else its plain
    bar on a clock axis; the 24-h total only when it is not this morning's
    (the ring prints this morning's)."""
    last = max((d for d, n in nights.items() if n.asleep is not None and today - timedelta(days=90) < d <= today),
               default=None)
    if last is None:
        return None
    n = nights[last]
    counted = nt.day_naps(nights, last)
    nap_min = sum(m for _, _, m in counted)
    naps = [(a, b) for a, b, _ in counted if a is not None]
    intervals = (samples or {}).get(last) if n.timeline else None
    ph = phases(n.stages or summed(intervals, n.start, n.end))
    draw = bool(intervals) or ph is None  # the hypnogram above the stages bar, else the plain bar without stages
    t = viz.timeline(n.start, n.end, stages=intervals, naps=naps) if draw else None
    times = f"{viz.clock(n.start)} → {viz.clock(n.end)}"
    nap = f"+ sieste{'s' if len(counted) > 1 else ''} {viz.hm(nap_min)}" if nap_min else None
    listed = t["out"] if t else naps  # the naps no lane draws: their times in words
    out_naps = [f"sieste {viz.clock(a)} → {viz.clock(b)}" for a, b in listed]
    aria = (f"{viz.night_label(last)} : couché vers {viz.clock(n.start)}, levé vers {viz.clock(n.end)}"
            + (f", sieste de {viz.hm_long(nap_min)}" if nap_min else ""))
    return {"day": last, "today": last == today, "label": "Cette nuit" if last == today else viz.night_label(last),
            "times": times, "nap": nap, "night": f"nuit {viz.hm(n.asleep)}" if nap_min else None,
            "total": None if last == today else f"{viz.hm(n.asleep + nap_min)} sur 24{viz.NNBSP}h",
            "timeline": t, "out_naps": out_naps, "stages": bool(intervals), "phases": ph, "aria": aria}


def habits(nights: dict, today: date) -> dict | None:
    """« Coucher 23:10 · Lever 07:20 » (medians of 28 days, 5 nights at least
    (H)) and « Régularité ± 35 min » (the SD of bedtime, 8 nights in 28 at
    least (H); Fischer 2021)."""
    t = nt.timing(nights, today)
    if t["n"] < USUAL_NIGHTS or t["bed"] is None:
        return None
    items = [("Coucher", nt.clock5(t["bed"]))]
    if t["wake"] is not None:
        items.append(("Lever", nt.clock5(t["wake"])))
    if t["regular_ok"] and t["bed_sd"] is not None:
        items.append(("Régularité", f"±{viz.NBSP}{max(5, int(round(t['bed_sd'] / 5)) * 5)}{viz.NBSP}min"))
    return {"stats": items, "n": t["n"]}


def rows(nights: dict, today: date) -> list[dict]:
    """« Les chiffres de chaque nuit », 30 nights, newest first: « — » when
    missing, the tags as words (the only place they are written)."""
    out = []
    for k in range(TABLE_DAYS):
        d = today - timedelta(days=k)
        n = nights.get(d)
        if not n or not (_measured(n) or n.hr is not None or n.hrv is not None):
            continue
        marks = [f"{viz.GLYPH['tag']} {nt.TAG_WORDS[t]}" for t in sorted(n.tags) if t != "race"]
        out.append({"date": viz.d_short(d), "iso": d.isoformat(),
                    "tst": viz.hm(n.tst24) if n.tst24 is not None else "—",
                    "night": viz.hm(n.asleep) if n.asleep is not None else "—",
                    "nap": viz.hm(n.nap_min) if n.nap_min else "—",
                    "times": f"{viz.clock(n.start)} → {viz.clock(n.end)}" if n.start else "—",
                    "hr": viz.num(n.hr) if n.hr is not None else "—",
                    "hrv": viz.num(n.hrv) if n.hrv is not None else "—",
                    "marks": " · ".join(marks) or "—"})
    return out


def sleep_section(nights: dict, today: date, r: str | None = None, samples: dict | None = None) -> dict:
    """Everything the Sommeil section draws. state: never (no night ever, one
    line) | old (nothing in 90 days) | ok."""
    base = {"method": METHOD, "refs": REFS}
    if not any(_measured(n) for d, n in nights.items() if d <= today):
        return {**base, "state": "never"}
    h = hero(nights, today, samples)
    offered = offered_ranges(nights, today)
    if h is None and "90" not in offered and not any(
            today - timedelta(days=14) < d <= today and _measured(n) for d, n in nights.items()):
        return {**base, "state": "old", "rows": rows(nights, today)}
    chosen = choose(nights, today, r)
    return {**base, "state": "ok", "hero": h, "ranges": [(k, RANGES[k][1]) for k in offered], "r": chosen,
            "bars": {k: bars(nights, today, k) for k in offered}, "habits": habits(nights, today),
            "rows": rows(nights, today)}
