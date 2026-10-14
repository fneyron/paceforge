"""Santé v4 › the Sommeil card of the one page (Oura-like): « comment je dors ? ».

- Last night (« Cette nuit », the card's first row): bed → wake (the watch's
  detection, approximate, rounded to 5 min, H: de Zambotti 2024) and the naps
  its 24 h counts (« + sieste 2h20 », nights.day_naps: yesterday afternoon's
  too; sleep is counted per 24 h, naps in: Watson 2015b; Hirshkowitz 2015) and
  its 24-h total « sur 9h00 de besoin »; a nap taken after that wake, which
  the next morning will count, is listed under it, out of the total
  (« sieste 14:00 → 15:00 · comptée dans ta prochaine nuit », nights.pending_naps:
  each nap in one 24 h) (2026-10-08: the Sommeil dial at the top
  says it as a percentage of that need, so its hours are printed here, once).
- The need is never asked (2026-10-09, owner: « Ne demande pas, ce doit être
  auto comme WHOOP »): 8 h for everyone (owner, 2026-10-14: scientific basis —
  the learnt base was the upper quartile of the athlete's own nights, so a
  sleeper as long as usual owed sleep for good; the effort's + 30 min and the
  quarter of the debt repaid each night had no study behind them): Hirshkowitz
  2015 and Watson 2015 set 7–9 h for adults, 8 h is their middle and the
  sleep of the athletes studied (Van Dongen 2003: 8.16 h kept performance
  stable). The sleep owed is said apart, never added to the need (deficit).
- Its stages, like WHOOP and Oura (the owner's request, a deliberate
  departure from research_sleep.md §b: shown for COROS and Garmin, no
  collapse, no setting), never judged: one bar of the four phases (Éveil ·
  Léger · Profond · Paradoxal, calm colours that are no good/bad colours,
  the raw minutes: the night's shape), each named in a legend with its
  minutes rounded to 10 min (H) (colour never alone); « Comment je calcule »
  (sante_score.METHOD) says the watch estimates them: the shape of the night,
  not its quality. No %, no target, no norm, no comparison, the main window only
  (watches classify 50–70 % of the night correctly: de Zambotti 2024; no body
  sets an ideal amount: Ohayon 2017). A Garmin night with real intervals also
  gets its hypnogram above it (lanes, the same colours). A night without
  stage minutes: its plain bar on a clock axis (bed → wake, the naps on their
  own lane: never in the night's timing, HR or HRV).
- 14 nuits / 3 mois: one bar per day of 24-h sleep (the main night solid, the
  naps its 24 h counts lighter on top: nights.day_naps, each nap on one bar
  only, the ring's and the score's figure; a day without a main night: its
  naps the next morning does not count, « sieste seule », the others on that
  morning's bar, its readout says so), the estimated need for each day (the same
  need as the dial), a legend (nuit · sieste · besoin estimé); « 3 mois » only with a night 14 to 90
  days old (else it would draw the 14 nights again), its 90 bars (each day's
  24 h in one) faint under the mean over the last seven calendar days, including
  days before the visible range. Tap a bar for its duration, date and need;
  at rest show the mean and the number of measured nights in that range. No
  ceiling, never a long night flagged (Watson 2015a).
- Habitudes: the median bedtime and wake of 28 days (5 nights at least, H),
  rounded to 5 min, and once 8 nights are there (H; ANSI/CTA/NSF-2052.1-A's
  « 1 week or more ») « 9 nuits sur 11 à moins d'1 h de ton coucher
  habituel » (the RU-SATED window: Ravyts 2021), no colour, no score.
  Every night counts (owner, 2026-10-08: none left out); a « rendormi » wake
  leaves the wake median (H).
- The closed « Les chiffres de chaque nuit » table (30 nights, their stage
  minutes too, to 10 min: the accessible alternative of the charts); the
  nights' method is a bullet of the page's one « Comment je calcule » fold
  (sante_score.METHOD; the audit, 2026-10-09: one fold, not two), its sources
  (REFS) on /sante/sources.
The nights are drawn as they are: no race, no event marker, no tag glyph on a
chart (the table names the tags).
"""
import math
import statistics
from datetime import date, timedelta

from app.services import nights as nt
from app.services import viz

RANGES = {"14": (14, "14 nuits"), "90": (90, "3 mois")}
USUAL_NIGHTS = 5  # (H) nights of 28 days before a median bedtime and wake are shown
TABLE_DAYS = 30
REF_MIN = 7 * 60  # lower threshold for sufficient sleep (Watson 2015a; Hirshkowitz 2015)
# owner, 2026-10-14: scientific basis — the need N is 8 h, never asked (owner, 2026-10-09: « Ne demande pas »), never
# learnt from the athlete's own nights, never raised by an effort or the sleep owed: the middle of the 7–9 h of
# Hirshkowitz 2015 and Watson 2015, Van Dongen 2003's 8.16 h (a need of 7 to 9 h set in Réglages: a proposal)
NEED_DEFAULT = 8 * 60  # (H) the middle of 7–9 h
# the sleep owed over the last 7 nights, said in hours, never added to the need: D7 = max(0, Σ_raced (N − naps) +
# (7 − r)/n · Σ (N − 24 h)) over the n nights measured and the r raced through (a night not measured is never 0 h, a
# raced night never scaled up; Van Dongen 2003: the deficit of a week of short nights adds up; Kitamura 2016: it takes
# days to repay), only from 4 measured nights (a sparse week says too little: Walsh 2021)
DEFICIT_DAYS, DEFICIT_MIN_NIGHTS = 7, 4  # (H)
# owner, 2026-10-14: scientific basis — a deficit under its own noise is « none »: D7 must exceed 1.28 standard errors of
# a 7-night estimate from n measured nights (σ_T·7/√n), σ_T shrunk towards 45 min (H, a night-to-night SD of the 24 h)
DEFICIT_Z, TST_SD_PRIOR = 1.28, 45.0  # (H)

# the nights' table's words for its marks (« À noter »): no outing named (v4.3), plain words (v4.4); the others are
# nights.TAG_WORDS'
WORDS = {**{t: "après un gros effort" for t in ("long", "big", "ultra")},
         "late": "effort intense le soir", "tz": "changement de fuseau", "late_nap": "après une sieste tardive"}
# the sources the nights' bullets of « Comment je calcule » rest on (label, DOI or URL), listed on /sante/sources
REFS = [
    ("Recommandations officielles", [
        ("Watson 2015a", "10.5665/sleep.4716"), ("Watson 2015b", "10.5665/sleep.4886"),
        ("Hirshkowitz 2015", "10.1016/j.sleh.2014.12.010"),
        ("CTA/NSF 2052.1‑A", "https://www.thensf.org/wp-content/uploads/2022/10/ANSI-CTA-NSF-2052.1-A-FINAL.pdf"),
        ("CTA/NSF 2052.3", "https://www.thensf.org/wp-content/uploads/2025/03/ANSI-CTA-NSF-2052.3-FINAL.pdf"),
        ("Janse van Rensburg 2021", "10.1007/s40279-021-01502-0"), ("Ohayon 2017", "10.1016/j.sleh.2016.11.006"),
        ("Walsh 2021", "10.1136/bjsports-2020-102025"), ("de Zambotti 2024", "10.1093/sleep/zsad325"),
    ]),
    ("Études scientifiques", [
        ("Van Dongen 2003", "https://pubmed.ncbi.nlm.nih.gov/12683469/"),
        ("Kitamura 2016", "https://pmc.ncbi.nlm.nih.gov/articles/PMC5075948/"),
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


def _readout(nights: dict, d: date) -> tuple[list[str], str]:
    """[value, word, the night · its times] and the spoken sentence: « 8h36 » ·
    « nuit du mer. 7 au jeu. 8 · 22:40 → 07:30 »; with a nap its 24 h counts
    « 8h10 » « nuit 5h50 + sieste 2h20 » · « … · 23:35 → 05:40 »; a nap alone
    « 1h22 » « sieste seule » · « … · nuit non enregistrée », « sieste comptée
    le lendemain » when the next morning's bar holds it (said plainly: no « ? »)."""
    n = nights.get(d)
    label = viz.night_label(d)
    if n is None or not _measured(n):
        return ["—", "", f"{label} · pas de mesure"], f"{label} : pas de mesure"
    if n.asleep is None:
        alone = nt.bar_nap_min(nights, d)
        if not alone:  # the next morning's 24 h counts it: drawn on that bar, never two
            return ([viz.hm(n.nap_min), "sieste comptée le lendemain", f"{label} · nuit non enregistrée"],
                    f"{label} : sieste de {viz.hm_long(n.nap_min)}, comptée le lendemain, nuit non enregistrée")
        return ([viz.hm(alone), "sieste seule", f"{label} · nuit non enregistrée"],
                f"{label} : sieste de {viz.hm_long(alone)} seule, nuit non enregistrée")
    nap = nt.bar_nap_min(nights, d)
    times = f"{viz.clock(n.start)} → {viz.clock(n.end)}"
    said = (f"{label} : {viz.sleep_spoken(n.asleep, nap)}, couché vers {viz.clock(n.start)}, "
            f"levé vers {viz.clock(n.end)}")
    if nap:
        return [viz.hm(n.asleep + nap), f"nuit {viz.hm(n.asleep)} + sieste {viz.hm(nap)}", f"{label} · {times}"], said
    return [viz.hm(n.asleep), "", f"{label} · {times}"], said


def bars(nights: dict, today: date, key: str) -> dict:
    """The 14-night or 3-month bars (see the module docstring)."""
    n_days = RANGES[key][0]
    days = [today - timedelta(days=n_days - 1 - i) for i in range(n_days)]
    tst = [nt.day_tst24(nights, d) for d in days]  # the 24 h before each wake, each nap once (day_naps)
    values = [nights[d].asleep if d in nights else None for d in days]
    stack = [nt.bar_nap_min(nights, d) or None for d in days]
    if n_days > 14:  # 90 slivers: each day's 24 h in one faint bar, the 7-night mean is the mark
        values = tst
        stack = None
    r, a = [], []
    needs = [NEED_DEFAULT] * n_days
    for i, d in enumerate(days):
        read, said = _readout(nights, d)
        if tst[i] is not None:
            read[2] += f" · besoin estimé {viz.hm(needs[i])}"
            said += f", besoin estimé {viz.hm_long(needs[i])}"
        r.append(read)
        a.append(said)
    totals = [v for v in tst if v is not None]
    trend, trend_sources = None, None
    if n_days > 14:  # the long range: the 7-night mean over faint bars
        averages = [nt.mean7(nights, "tst24", d) for d in days]
        trend = [m["value"] if m else None for m in averages]
        trend_sources = [nt.mean_source(nights, "tst24", d) for d in days]
        for i, m in enumerate(averages):
            if m:
                detail = f"moyenne 7 jours : {viz.hm(m['value'])} ({m['n']} nuits) · {viz.source_label(trend_sources[i])}"
                r[i][2] += f" · {detail}"
                a[i] += f", {detail}"
    c = viz.day_bars(f"sommeil-{key}", days, values, stack=stack, readouts=r, arias=a, trend=trend,
                     trend_sources=trend_sources,
                     targets=needs, lines=((0, "0 h"), (240, "4 h"), (480, "8 h")),
                     min_top=9 * 60, today=len(days) - 1,
                     summary=f"Sommeil sur 24 h, {RANGES[key][1]} : {len(totals)} nuit{'s' if len(totals) > 1 else ''} "
                             f"enregistrée{'s' if len(totals) > 1 else ''}")
    if totals:
        mean = round(sum(totals) / len(totals) / 5) * 5  # a mean of approximate times: to 5 min
        return viz.rest(c, [viz.hm(mean), "en moyenne", f"Sur {len(totals)} nuits mesurées · {RANGES[key][1]}"],
                        f"Sommeil sur 24 heures, {RANGES[key][1]} : {viz.hm_long(mean)} en moyenne, siestes "
                        "comprises. Choisis une nuit pour voir la sienne.")
    return viz.rest(c, ["—", "", ""], f"Sommeil, {RANGES[key][1]} : aucune nuit enregistrée")


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
    return {"parts": parts, "aria": f"Phases estimées par ta montre : {said}."}


def deficit(nights: dict, d: date, raced=frozenset()) -> dict:
    """The sleep owed over the last 7 nights (the mornings of d − 6 → d), in minutes, never added to the need
    (owner, 2026-10-14: scientific basis — it was summed over the measured nights only, so it halved when the watch
    was worn one night in two, then a quarter of it was added to the need):
    D7 = max(0, Σ_raced (8 h − naps) + (7 − r)/n_m · Σ_measured (8 h − 24 h)): a night spent racing (`raced`: an
    effort through 01:00 → 05:00, sante_training.raced_nights; Kishi 2024) counts its own sleep, never scaled up;
    the n_m measured nights stand for the 7 − r others (a night not measured is never 0 h); only from 4 measured
    nights (H). Under 1.28 · σ_T · 7/√n_m it is « none » (H: about 2h30 for σ_T 45 min and 7 nights): a sleeper who
    meets 8 h on average would show a positive D7 on half the mornings; σ_T is the athlete's SD of the 24 h over
    the baseline days (−67 → −8), shrunk towards 45 min as the bands are (nights.shrunk_sd).
    {minutes (to 10 min; None: not estimable), n (measured), raced, days, raw (D7 before the noise floor, min),
    floor (min), text}."""
    raced_gap, gaps = 0.0, []
    r = 0
    for k in range(DEFICIT_DAYS):
        x = d - timedelta(days=k)
        slept = nt.slept_before_wake(nights, x)
        if x in raced:
            r += 1
            own = slept if slept is not None else (sum(m for _, _, m in nt.day_naps(nights, x))
                                                    + nt.bar_nap_min(nights, x))
            raced_gap += NEED_DEFAULT - own
        elif slept is not None:
            gaps.append(NEED_DEFAULT - slept)
    n = len(gaps)
    out = {"n": n, "raced": r, "days": DEFICIT_DAYS}
    if n < DEFICIT_MIN_NIGHTS:
        return {**out, "minutes": None, "raw": None, "floor": None,
                "text": f"manque non estimable ({n}/{DEFICIT_DAYS} nuits)"}
    raw = max(0.0, raced_gap + (DEFICIT_DAYS - r) / n * sum(gaps))
    floor = DEFICIT_Z * tst_spread(nights, d) * DEFICIT_DAYS / math.sqrt(n)
    minutes = int(10 * math.floor(raw / 10 + 0.5)) if raw > floor else 0
    return {**out, "minutes": minutes, "raw": raw, "floor": floor,
            "text": f"manque estimé sur 7 jours{viz.NBSP}: {viz.hm(minutes)}" if minutes else
            "pas de manque sur 7 jours"}


def tst_spread(nights: dict, d: date) -> float:
    """σ_T: the SD of the athlete's 24 h over the baseline days (−67 → −8; nights.baseline_until), shrunk towards
    45 min (H) with the bands' prior weight: √((n·s² + 7·45²)/(n + 7)); 45 min without two of them."""
    until = nt.baseline_until(d)
    vals = [v for k in range(1, nt.BAND_DAYS + 1) if (v := nt.day_tst24(nights, until - timedelta(days=k))) is not None]
    s = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return nt.shrunk_sd(s, len(vals), TST_SD_PRIOR)


def hero_day(nights: dict, today: date) -> date | None:
    """The night the Sommeil card opens on: the latest main night of the last 90 days."""
    return max((d for d, n in nights.items() if n.asleep is not None and today - timedelta(days=90) < d <= today),
               default=None)


def hero(nights: dict, today: date, samples: dict | None = None) -> dict | None:
    """Last night (the latest main night of the last 90 days): its times, the
    naps its 24 h counts (nights.day_naps), its stages (the stages bar and
    legend; with real Garmin intervals, the hypnogram above it), else its plain
    bar on a clock axis; its 24-h total « besoin estimé 8h00 » (the Sommeil dial says it
    as a percentage of that need, its hours are printed here, once). `later`:
    the naps taken after that wake no morning counts yet (nights.pending_naps:
    the next morning's 24 h will), listed out of the total."""
    last = hero_day(nights, today)
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
    pending = [(a, b) for a, b, _ in nt.pending_naps(nights, last) if a is not None]
    later = None
    if pending:
        s = "s" if len(pending) > 1 else ""
        later = (f"sieste{s} " + ", ".join(f"{viz.clock(a)} → {viz.clock(b)}" for a, b in pending)
                 + f" · compté{'es' if s else 'e'} dans ta prochaine nuit")
        aria += f", {later.replace(' · ', ', ')}"
    return {"day": last, "today": last == today, "label": "Cette nuit" if last == today else viz.night_label(last),
            "source": n.source, "times": times, "nap": nap, "total": viz.hm(n.asleep + nap_min), "later": later,
            "need": f"besoin estimé {viz.hm(NEED_DEFAULT)}",
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
    words (the only place they are written: WORDS; the nights after an effort: « après un gros effort »)."""
    out = []
    for k in range(TABLE_DAYS):
        d = today - timedelta(days=k)
        n = nights.get(d)
        if not n or not (_measured(n) or n.hr is not None or n.hrv is not None):
            continue
        marks = [f"{viz.GLYPH['tag']} {w}" for w in nt.tag_words(n.tags, words=WORDS)]
        tst, nap = nt.day_tst24(nights, d), nt.bar_nap_min(nights, d)
        out.append({"date": viz.d_short(d), "iso": d.isoformat(),
                    "tst": viz.hm(tst) if tst is not None else "—",
                    "night": viz.hm(n.asleep) if n.asleep is not None else "—",
                    "nap": viz.hm(nap) if nap else "—",
                    "times": f"{viz.clock(n.start)} → {viz.clock(n.end)}" if n.start else "—",
                    "hr": viz.num(n.hr) if n.hr is not None else "—",
                    "hrv": viz.num(n.hrv) if n.hrv is not None else "—",
                    "phases": [stage_hm(n.stages[k]) if n.stages else "—" for k, _ in PHASES],
                    "marks": " · ".join(marks) or "—"})
    return out


def sleep_section(nights: dict, today: date, r: str | None = None, samples: dict | None = None,
                  raced=frozenset()) -> dict:
    """Everything the Sommeil section draws. state: never (no night ever, one
    line) | old (nothing in 90 days) | ok."""
    if not any(_measured(n) for d, n in nights.items() if d <= today):
        return {"state": "never"}
    h = hero(nights, today, samples)
    offered = offered_ranges(nights, today)
    if h is None and "90" not in offered and not any(
            today - timedelta(days=14) < d <= today and _measured(n) for d, n in nights.items()):
        return {"state": "old", "rows": rows(nights, today)}
    chosen = choose(nights, today, r)
    return {"state": "ok", "hero": h, "ranges": [(k, RANGES[k][1]) for k in offered], "r": chosen,
            "bars": {k: bars(nights, today, k) for k in offered}, "habits": habits(nights, today),
            "rows": rows(nights, today), "deficit": deficit(nights, today, raced)}
