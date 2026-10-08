"""Santé v4 › the Sommeil section of the one page (Oura-like): « comment je dors ? ».

- Last night (the hero): bed → wake (the watch's detection, approximate,
  rounded to 5 min, H: de Zambotti 2024) and the naps its 24 h counts
  (« + sieste 2h20 », nights.day_naps: yesterday afternoon's too; sleep is
  counted per 24 h, naps in: Watson 2015b; Hirshkowitz 2015). Its 24-h total
  is the Sommeil ring's, printed once there; the hero prints it only for a
  night that is not this morning's (the ring is then empty).
- Its stages, like WHOOP and Oura (the owner's request, a deliberate
  departure from research_sleep.md §b: shown for COROS and Garmin, no
  collapse, no setting), never judged: one bar of the four phases (Éveil ·
  Léger · Profond · Paradoxal, calm colours that are no good/bad colours,
  the raw minutes: the night's shape), each named in a legend with its
  minutes rounded to 10 min (H) (colour never alone), and « Estimées par la
  montre à partir du pouls et des mouvements : la forme de ta nuit, pas sa
  qualité. » No %, no target, no norm, no comparison, the main window only
  (watches classify 50–70 % of the night correctly: de Zambotti 2024; no body
  sets an ideal amount: Ohayon 2017). A Garmin night with real intervals also
  gets its hypnogram above it (lanes, the same colours). A night without
  stage minutes: its plain bar on a clock axis (bed → wake, the naps on their
  own lane: never in the night's timing, HR or HRV).
- 14 nuits / 3 mois: one bar per day of 24-h sleep (the main night solid, the
  naps lighter on top), the 7 h line (Watson 2015a: about habitual sleep), a
  tiny legend (nuit · sieste · 7 h); « 3 mois » only with a night 14 to 90
  days old (else it would draw the 14 nights again), its 90 bars (each day's
  24 h in one) faint under the 7-night mean (Oura's long ranges). Tap a bar →
  « 8h36 » / « nuit du mer. 7 au jeu. 8 · 22:40 → 07:30 »; it rests on the
  range's mean (printed nowhere else: the latest night is the ring's). No
  ceiling, never a long night flagged (Watson 2015a).
- Habitudes: the median bedtime and wake of 28 days (5 nights at least, H),
  rounded to 5 min, and once 8 nights are there (H; ANSI/CTA/NSF-2052.1-A's
  « 1 week or more ») « 9 nuits sur 11 à moins d'1 h de ton coucher
  habituel » (the RU-SATED window: Ravyts 2021), no colour, no score.
  Time-zone nights and the 4 nights after an ultra stay out; a « rendormi »
  wake leaves the wake median (H).
- The closed « Les chiffres de chaque nuit » table (30 nights, their stage
  minutes too, to 10 min: the accessible alternative of the charts) and the
  closed « Comment je lis tes nuits » fold (METHOD).
The nights are drawn as they are: no race, no event marker, no tag glyph on a
chart (the table names the tags).
"""
import math
from datetime import date, timedelta

from app.services import nights as nt
from app.services import viz

RANGES = {"14": (14, "14 nuits"), "90": (90, "3 mois")}
USUAL_NIGHTS = 5  # (H) nights of 28 days before a median bedtime and wake are shown
TABLE_DAYS = 30
REF_MIN = 7 * 60  # the 7 h line: habitual sleep (Watson 2015a; Hirshkowitz 2015)

# « Comment je lis tes nuits » (V42_BRIEF.md §C–§D): the same shape as the recovery fold, every PaceForge number
# marked (H), only the sources the research reports cite
METHOD = [
    ("Ce qui vient des textes officiels", [
        "Ton sommeil se compte sur 24 h, siestes comprises (Watson 2015b ; Hirshkowitz 2015).",
        "Les 7 h recommandées parlent de ton sommeil habituel, pas d'une nuit (Watson 2015a).",
        "Une longue nuit n'est jamais signalée : elle peut aider à récupérer (Watson 2015a).",
        "Régularité : la variation de ton coucher, sur une semaine au moins (CTA/NSF 2052.1‑A).",
        "Décalage : 1 nuit par fuseau vers l'est, une demie vers l'ouest (Janse van Rensburg 2021).",
        "Léger ≈ N1 + N2, Profond ≈ N3, Paradoxal = REM, stades du laboratoire (CTA/NSF 2052.3).",
        "Le sommeil léger occupe normalement la plus grande partie de la nuit.",
        "Aucune instance scientifique ne fixe de dose idéale de profond ou de paradoxal (Ohayon 2017).",
    ]),
    ("Ce qui est notre choix (H)", [
        "L'anneau Sommeil : les 24 h avant ton réveil, plein à 8 h, une échelle, pas un objectif.",
        "Aucun texte officiel ne donne ce 8 h ; besoin ressenti des athlètes : 8,3 h (Sargent 2021).",
        "Un jour sous 6 h est « court », score plafonné à 65 (6 h : Craven 2022 ; 65 : notre choix).",
        "Régularité : tes nuits à moins d'1 h de ton coucher habituel, dès 8 sur 28 (Ravyts 2021).",
        "Hors de ta normale : 1 nuit après 3 h, 3 après 6 h, 4 après 10 h ; l'altitude ; un fuseau.",
        "Après un ultra, tes 4 nuits restent aussi hors de tes horaires habituels (Fachan 2026).",
        "Sieste « tardive » après 16 h ou à moins de 7 h de ton coucher (Mograss 2022 ; Walsh 2021).",
        "Coucher et lever arrondis à 5 min, phases à 10 min.",
    ]),
    ("Ce que la montre ne sait pas", [
        "Ton sommeil est estimé par la montre, qui le surestime (Walsh 2021 ; de Zambotti 2024).",
        "Coucher et lever détectés par la montre, pas déclarés par toi (de Zambotti 2024).",
        "Phases : 50 à 70 % des moments de la nuit bien classés (de Zambotti 2024).",
        "Sur une nuit, profond ou paradoxal peut différer d'environ 1 h du labo (Chinoy 2021, Garmin).",
        "COROS : aucune étude publiée sur la précision de ses phases.",
    ]),
]
REFS = [
    ("Textes officiels", [
        ("Watson 2015a", "10.5665/sleep.4716"), ("Watson 2015b", "10.5665/sleep.4886"),
        ("Hirshkowitz 2015", "10.1016/j.sleh.2014.12.010"),
        ("CTA/NSF 2052.1‑A", "https://www.thensf.org/wp-content/uploads/2022/10/ANSI-CTA-NSF-2052.1-A-FINAL.pdf"),
        ("CTA/NSF 2052.3", "https://www.thensf.org/wp-content/uploads/2025/03/ANSI-CTA-NSF-2052.3-FINAL.pdf"),
        ("Janse van Rensburg 2021", "10.1007/s40279-021-01502-0"), ("Ohayon 2017", "10.1016/j.sleh.2016.11.006"),
        ("Walsh 2021", "10.1136/bjsports-2020-102025"), ("de Zambotti 2024", "10.1093/sleep/zsad325"),
    ]),
    ("Études", [
        ("Craven 2022", "10.1007/s40279-022-01706-y"), ("Sargent 2021", "10.1123/ijspp.2020-0896"),
        ("Ravyts 2021", "10.1080/15402002.2019.1701474"), ("Fachan 2026", "10.1016/j.sleepx.2026.100197"),
        ("Mograss 2022", "10.1111/jsr.13578"), ("Chinoy 2021", "10.1093/sleep/zsaa291"),
    ]),
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
STAGE_STEP = 10  # (H) the stages' minutes are printed to 10 min: the watch's estimate has no finer precision
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


def stage_hm(minutes: float) -> str:
    """A stage's minutes as printed, rounded to 10 min (H): « 1h10 », « 50 min », « < 10 min »."""
    m = int(math.floor(minutes / STAGE_STEP + 0.5)) * STAGE_STEP
    return viz.hm(m) if m else f"<{viz.NBSP}{STAGE_STEP}{viz.NBSP}min"


def stage_spoken(minutes: float) -> str:
    m = int(math.floor(minutes / STAGE_STEP + 0.5)) * STAGE_STEP
    return viz.hm_long(m) if m else f"moins de {STAGE_STEP} minutes"


def phases(stages: dict | None) -> dict | None:
    """The stages bar (the raw minutes: its shape) and its legend, each phase
    rounded to 10 min (H): [{key, name, min, hm}] in WHOOP's order (Éveil ·
    Léger · Profond · Paradoxal; a phase of 0 min left out), and the spoken
    list; None without stage minutes. No %, no target, no norm, no
    comparison: the watch's estimate, the shape of the night, not its
    quality."""
    if not stages:
        return None
    parts = [{"key": k, "name": nm, "min": stages[k], "hm": stage_hm(stages[k])} for k, nm in PHASES if stages.get(k)]
    if not parts:
        return None
    said = ", ".join(f"{p['name'].lower()} {stage_spoken(p['min'])}" for p in parts)
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
    """« Coucher 23:10 · Lever 07:20 » (medians of 28 days on the local clock,
    5 nights at least (H), rounded to 5 min: detected by the watch, de
    Zambotti 2024) and, once 8 nights are there (H; ANSI/CTA/NSF-2052.1-A's
    « 1 week or more »), « 9 nuits sur 11 à moins d'1 h de ton coucher
    habituel » (the RU-SATED window: Ravyts 2021): no colour, no score."""
    t = nt.timing(nights, today)
    if t["n"] < USUAL_NIGHTS or t["bed"] is None:
        return None
    items = [("Coucher", nt.clock5(t["bed"]))]
    if t["wake"] is not None:
        items.append(("Lever", nt.clock5(t["wake"])))
    regular = (f"{t['bed_near']} nuit{'s' if t['bed_near'] > 1 else ''} sur {t['n']} à moins d'1{viz.NBSP}h de ton "
               "coucher habituel" if t["regular_ok"] else None)
    return {"stats": items, "n": t["n"], "regular": regular}


def rows(nights: dict, today: date) -> list[dict]:
    """« Les chiffres de chaque nuit », 30 nights, newest first: « — » when
    missing, each night's stage minutes (the watch's estimate), the tags as
    words (the only place they are written)."""
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
                    "phases": [stage_hm(n.stages[k]) if n.stages else "—" for k, _ in PHASES],
                    "marks": " · ".join(marks) or "—"})
    return out


def sleep_section(nights: dict, today: date, r: str | None = None, samples: dict | None = None) -> dict:
    """Everything the Sommeil section draws. state: never (no night ever, one
    line) | old (nothing in 90 days) | ok."""
    from app.services.sante_score import linked

    base = {"method": METHOD, "refs": linked(REFS)}
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
