"""Santé v4: one page (owner, 2026-10-08: « mets tout dans un seul », « plus
de graphiques »). Top to bottom:
- the top (v4.4, owner: « C'est toujours pas compréhensible : Sommeil 100,
  Charge récente 20 avec les donuts au-dessus, on comprend rien »: one
  answer, then plain facts, like Apple's Vitals and Garmin's recovery time):
  ONE ring, Récupération (the 0–100 score as a percentage, « 65 % », in its
  state's colour, sante_score; « estimé » under it when no night was
  measured), the state's word and glyph next to it, one sentence only for the
  illness alert (sante_today; v4.3, owner: « Ne mentionne pas les sorties
  dans la partie Santé, ça complexifie »: no activity named); then a short
  list of facts, one row each — a dot and the name on the left, the value
  and a plain word on the right (never colour alone), a link to its section
  below when there is one (owner: « Mets des pourcentages plutôt que des
  valeurs (comme WHOOP / Oura) »): Sommeil (the last 24 h, naps in,
  nights.day_tst24, as a percentage of an 8-h need, at most 100 % (H), its
  hours printed in the Sommeil section; « suffisant » from 7 h, green
  (Watson 2015a's habitual amount read on one day, H), « un peu court » from
  6 h (H), « court » under 6 h (Craven 2022)), Effort récent (only while a
  recovery window is open: « encore N jours », never a percentage), VFC
  (7 nuits) and FC de nuit (7 nuits) (their card's status: the 7-night mean
  as a signed percentage of the usual value and its word, « comme
  d'habitude » at 0 %, « en construction » without usual values). No
  sub-score anywhere, no Sommeil or Charge ring (the week's volume is
  Activités'), no « Détail du score »;
- the cards « Récupération · 14 jours », the Sommeil section (sante_sleep),
  « VFC · 30 nuits », « FC de nuit · 30 nuits », each night card opening on
  one status line, the last 7 nights against the normal the score reads
  (v4.3, owner: « est-ce que c'est bien ou pas bien ? »): the activities
  themselves are Activités' (v4.1: no « Charge · 14 jours » card);
- the closed folds (how the recovery is computed, how the nights are read,
  each ending on a link to /sante/sources; the nights' table).
From past activities and the nights only: no planned race, no check-in, no
training prescription, no sync status (Réglages': the page syncs on its own
when it opens and reloads quietly when something new arrived). Only
PaceForge's own nightly values (nights.py), read against the athlete's own
band; no brand value is read. A night without the watch is a gap, never a
zero. Each number is printed once on the page, but a night signal's
percentage: its card's status line, repeated on its facts row (one
statement, its summary at the top and its detail on the card).
"""
import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from functools import partial

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.services import nights as nt
from app.services import sante_score as sc
from app.services import sante_sleep as sl
from app.services import sante_today as td
from app.services import sante_training as st
from app.services import viz
from app.services.health import WATCH_SOURCES, fmt_minutes

logger = logging.getLogger(__name__)

HISTORY_DAYS = 400
CARD_NIGHTS = 30  # the VFC and FC de nuit cards
SPARSE_NIGHTS, SPARSE_DAYS = 10, 14  # fewer measured nights in the 30: the axis spans them (14 days at least)


async def health_page(db: AsyncSession, user_id: int, today: date | None = None, now: datetime | None = None,
                      r: str | None = None) -> dict:
    """Everything /sante draws. `has_data` is False when neither a watch nor an
    activity ever arrived. `r`: the sleep bars' range (14 or 90; else the
    first holding a night)."""
    now = now or datetime.now(timezone.utc)
    latest = (await db.execute(select(func.max(HealthMetric.date)).where(HealthMetric.user_id == user_id))).scalar()
    today = today or await athlete_today(db, user_id, now, latest)
    sessions = await st.load_sessions(db, user_id, today)
    peak = st.hr_max(sessions, today)
    # Garmin's day altitudes: where the athlete slept, first (R2)
    day_alt = await nt.day_altitudes(db, user_id, today - timedelta(days=HISTORY_DAYS), today)
    # the nights' tags read the efforts' classes only
    nights = await nt.load_nights(db, user_id, today, days=HISTORY_DAYS, sessions=sessions,
                                  efforts=st.efforts(sessions), peak=peak, day_alt=day_alt)
    # a Longue run like a race (R3): its HR against the athlete's bounds as of its day (the nights' resting HR)
    rest_of = partial(nt.rest_hr, nights)
    # a dawn finish: D on the day before its first morning
    efforts = nt.anchor_efforts(nights, st.efforts(sessions, rest_of))
    sources = set((await db.execute(select(HealthMetric.source).distinct().where(
        HealthMetric.user_id == user_id, HealthMetric.metric != "feel"))).scalars().all())
    out = {
        "has_data": bool(sources) or bool(sessions),  # older rows may come from another source (Apple Health)
        "has_watch_data": bool(sources),
        "today": today,
        "night_state": await _night_state(db, user_id, today, sources & set(WATCH_SOURCES)),
    }
    if not out["has_data"]:
        out["day_label"] = viz.d_short(today)
        return out
    with nt.memo():  # the nights no longer change: their bands and alerts are computed once
        nt.freeze(nights)
        day = _assess(nights, sessions, efforts, today)
        history = (_history(nights, sessions, efforts, today, rest_of, day_alt)
                   + [(today, day["state"], day["score"])])
        out["recup"] = sc.history_card(history, today)
        hero = sl.hero(nights, today)
        samples = await _timeline(db, user_id, nights, hero["day"]) if hero else {}
        out["sleep"] = sl.sleep_section(nights, today, r, samples)
        out["vfc"] = _night_card(nights, "hrv", today, day)
        out["fc"] = _night_card(nights, "hr", today, day)
        out.update(_top(day, efforts, bool(sources), out))
    out["day_label"] = viz.d_short(today)
    out["method"] = sc.typo(sc.METHOD)
    return out


# ── one day: its state and its score ────────────────────────────────────────

def _night_stat(nights, metric: str, d: date, ignore=()) -> dict:
    """The 7-night mean of the usable nights (3 at least, Plews 2014; Lau 2022)
    and the band it is read against: the 60 days before its window, on the
    watch that mean reads (one band per watch, Dial 2025; « provisoire » from 7
    to 13 nights, H); the band even without a mean (« ta normale se
    construit » is said only when there is none). The status is judged on the
    unrounded mean. `seen`: the metric measured in the band's 60 days or the
    week after them. `ignore`: tags kept in the mean (an alert episode's own
    nights, when the alert is the state: the score then reads them, never the
    band)."""
    m = nt.mean7(nights, metric, d, ignore=ignore)
    src = nt.mean_source(nights, metric, d)
    b = nt.band(nights, metric, d - timedelta(days=6), source=src)
    v = m["value"] if m else None
    lo = d - timedelta(days=nt.BAND_DAYS + 6)
    seen = src is not None and any(lo <= x <= d and n.value(metric) is not None for x, n in nights.items())
    return {"value": v, "normal": b, "status": nt.status(v, b) if b and v is not None else None, "seen": seen}


def _assess(nights, sessions, efforts, d: date) -> dict:
    """What day `d` reads — today, or a past day of the 14-day card (_history:
    the rows and activities known that day) — and its score and state (the
    band of the score). While an alert episode is open (a night of it in the
    7-night window, the alert firing or not) the nightly components read the
    episode's own nights (the band stays the normal before it): the score
    shows the episode, never « dans ta normale » the morning the alert stops."""
    alert = nt.illness_alert(nights, d)
    episode = alert or any("alert" in n.tags for x in range(nt.WEEK_DAYS)
                           if (n := nights.get(d - timedelta(days=x))) is not None)
    # an episode's nights are read even 5 to 7 nights after a ≥ 20-h ultra (out of the band only: the alert fires)
    ignore = (*nt.EPISODE, "ultra_tail") if episode else ()
    stats = {k: _night_stat(nights, k, d, ignore=ignore) for k in ("hr", "hrv")}
    tst = nt.day_tst24(nights, d)  # the 24 h before this morning's wake: the Sommeil row's number and the score's
    m7 = nt.mean7(nights, "tst24", d)
    usual = nt.band(nights, "tst24", d - timedelta(days=6), source=nt.mean_source(nights, "tst24", d)) if m7 else None
    day = {"day": d, "stats": stats, "tst24": tst,
           "sleep": {"mean7": m7["value"] if m7 else None, "usual": usual["center"] if usual else None,
                     "prov": bool(usual and usual["provisional"])},
           "window": st.effort_window(efforts, d), "alert": alert}
    score = sc.score_of(day)
    day.update(score=score, state=td.state(score, day))
    return day


def _history(nights, sessions, efforts, today: date, rest_of=None, day_alt=None) -> list:
    """[(day, state, score)] of the 13 days before today, each as the page
    computed it on that day: from the nights and the activities known then
    only — an activity is known once it ended (it is uploaded then), so one
    still running at midnight belongs to the next day — and the efforts as
    that day saw them (st.efforts on those activities: a chain across
    midnight joins a later day, so a past day may have seen a smaller effort). A night's
    tags as of that day are today's (an effort tags the nights after it, a
    session the night after it: both known by then) but for « sortie intense
    le soir » (it reads that day's HR bounds) and « FC de nuit haute » (an
    alert episode's first night is tagged only from the next morning). The
    nights are tagged once, as of today, without the alert episodes; a day
    whose « late » tags and efforts are the same, with no activity still
    running at its midnight, reads them, with that day's own alert episodes on
    top, and shares their bands (nights.memo); any other day is tagged anew
    from what it knew. `rest_of`: what the efforts were computed with
    (st.efforts): a Longue's heart-rate test reads the bounds as of its own
    day, the same whichever later day reads it; `day_alt`: Garmin's day
    altitudes the nights were tagged with (a night reads the day before)."""
    recent = {x: n for x, n in nights.items() if x > today - timedelta(days=sc.HISTORY_NIGHTS)}
    base = {x: nt.retagged(n) for x, n in recent.items()}
    nt.tag_activities(base, sessions, efforts, nt.rest_hr(base, today), st.hr_max(sessions, today), day_alt)
    nt.freeze(base)
    by_day = defaultdict(list)
    for s in sessions:
        by_day[s.day].append(s)
    ends = {s.id: st.local_end(s) for s in sessions}
    late = {x: c for x, n in base.items()
            if (c := nt.late_candidates(n, by_day.get(x - timedelta(days=1), []) + by_day.get(x, [])))}
    tagged = {frozenset(): base}
    out = []
    for k in range(sc.HISTORY_DAYS - 1, 0, -1):
        d = today - timedelta(days=k)
        midnight = datetime.combine(d + timedelta(days=1), time(0))
        ss = [s for s in sessions if ends[s.id] <= midnight]  # finished by the end of `d`
        known = {s.id for s in ss}
        # the efforts as that day saw them: today's that it knew whole; computed anew when one was only partly
        # known (a chain across midnight: that day saw a smaller effort)
        same = not any(e.ids & known and not e.ids <= known for e in efforts)
        efs = [e for e in efforts if e.ids <= known] if same else nt.anchor_efforts(base, st.efforts(ss, rest_of))
        running = any(s.day <= d and s.id not in known for s in sessions)
        peak, rest = st.hr_max(ss, d), nt.rest_hr(base, d)
        if not running and same and all(any(nt.vigorous(s, rest, peak) for s in c) == ("late" in base[x].tags)
                                         for x, c in late.items() if x <= d):
            alert = frozenset(x for x in nt.alert_episodes(base, d) if x in base)
            if alert not in tagged:
                tagged[alert] = nt.freeze({x: nt.retagged(n, n.tags | {"alert"}) if x in alert else n
                                           for x, n in base.items()})
            nd = tagged[alert]
        else:
            nd = {x: nt.retagged(n) for x, n in recent.items() if x <= d}
            nt.tag_activities(nd, ss, efs, rest, peak, day_alt)
            nt.tag_alerts(nd, d)
            nt.freeze(nd)
        past = _assess(nd, ss, efs, d)
        out.append((d, past["state"], past["score"]))
    return out


# ── the top: the Récupération ring, the state, the facts ───────────────────

# the Sommeil row's word and colour from this morning's 24 h (H): 7 h and more (Watson 2015a), 6 to 7 h, under 6 h
# (Craven 2022)
SLEEP_WORDS = ((sl.REF_MIN, "suffisant", "ok"), (nt.SHORT_DAY_MIN, "un peu court", "accent"), (0, "court", "warn"))
SLEEP_NEED = 8 * 60  # (H) the Sommeil row's 100 %: an 8-h need (the old ring's full turn; Sargent 2021: 8,3 h)
NO_NIGHT_WORD = "pas enregistré"
EFFORT_ORANGE = 65  # a recovery window's cap from which « Effort récent » wears orange (its 65 days); red under it
NIGHT_FACTS = {"hrv": ("vfc", "VFC"), "hr": ("fc", "FC de nuit")}
ALERT_WORD = "nettement plus haute depuis 2 nuits"  # the FC de nuit row under the illness alert
SAME = "comme d'habitude"  # a 7-night mean at its usual value, to the percent: « 0 % » said in words


def _fact(key: str, name: str, value: str | None, word: str, tone: str, href: str | None,
          qual: str | None = None) -> dict:
    """One row of the facts: a dot in `tone` (ok, accent: neutral, warn, danger, none: grey) and the name on the
    left (`qual` under it, muted: what the percentage is of, « sur 8 h », « 7 nuits »), the value and the word
    on the right, one above the other (never colour alone); `href`: its section on the page, None when there is
    none (a plain row)."""
    return {"key": key, "name": name, "qual": qual, "value": value, "word": word, "tone": tone, "href": href}


def sleep_fact(tst24: int | None, href: str | None) -> dict | None:
    """« Sommeil »: the 24 h before this morning's wake, naps in (nights.day_tst24: the score's figure), as a
    percentage of an 8-h need, at most 100 % (v4.4, owner: « Mets des pourcentages plutôt que des valeurs »;
    the hours themselves are the Sommeil section's, printed once), and a word from the hours: 7 h or more
    « suffisant » (green; AASM/SRS: Watson 2015a), 6 to 7 h « un peu court » (neutral), under 6 h « court »
    (warm: Craven 2022); no night this morning: « pas enregistré » (grey). None without a Sommeil section (no
    night ever measured)."""
    if href is None:
        return None
    if tst24 is None:
        return _fact("sommeil", "Sommeil", None, NO_NIGHT_WORD, "none", href)
    word, tone = next((w, t) for lo, w, t in SLEEP_WORDS if tst24 >= lo)
    return _fact("sommeil", "Sommeil", sc.pct(min(100, sc.rounded(100 * tst24 / SLEEP_NEED))), word, tone, href,
                 qual=f"sur {SLEEP_NEED // 60}{viz.NBSP}h")


def effort_fact(efforts, day: dict) -> dict | None:
    """« Effort récent », only while a recovery window is open (sante_training.effort_window): « encore N
    jours », N the days from today to the last day of the windows open (« dernier jour » on it); never the
    activity, its date or its hours (v4.3: no activity named on Santé), never a percentage (the window is a step,
    not a curve). A red dot while the window caps the score at 35 or 45 (its first days), orange at 65. No
    section of the page sums it up: a plain row."""
    w = day["window"]
    if not w:
        return None
    d = day["day"]
    last = max(x["until"] for e in efforts if (x := st.effort_window([e], d)))
    n = (last - d).days
    word = "dernier jour" if n <= 0 else f"encore {n} jour{'s' if n > 1 else ''}"
    return _fact("effort", "Effort récent", None, word, "warn" if w["cap"] >= EFFORT_ORANGE else "danger", None)


def night_fact(card: dict | None, metric: str, day: dict) -> dict | None:
    """« VFC (7 nuits) » / « FC de nuit (7 nuits) »: its card's status (card_status: one source of truth), its
    percentage and its word, never its explanation, in its colour: « −8 % plus basse que d'habitude », « +2 %
    dans tes valeurs habituelles », « comme d'habitude » at 0 %; « en construction » and « trop peu de nuits pour
    comparer » (grey) with no percentage; under the illness alert the FC de nuit row says « nettement plus haute
    depuis 2 nuits » (red). Links to its card; None without one (the watch never measured it in 30 days)."""
    if card is None:
        return None
    key, name = NIGHT_FACTS[metric]
    s = card["status"]
    return _fact(key, name, s["value"], s["word"], s["tone"] or "none", f"#{key}", qual="7 nuits")


def _top(day: dict, efforts, has_watch: bool, page: dict) -> dict:
    """The Récupération ring (a link to its 14 days, a plain ring when the page has none), the state and the
    facts under it — Sommeil, Effort récent (in a recovery window), VFC and FC de nuit (each with its card) —
    only with a state (no score: the line says why, the cards below show what there is)."""
    state, score = day["state"], day["score"]
    sleep_href = "#sommeil" if (page.get("sleep") or {}).get("state", "never") != "never" else None
    facts = [f for f in (sleep_fact(day["tst24"], sleep_href), effort_fact(efforts, day),
                         night_fact(page.get("vfc"), "hrv", day), night_fact(page.get("fc"), "hr", day)) if f]
    return {"ring": sc.ring(score, state, "#recuperation" if page.get("recup") else None), "state": state,
            "line": None if state else td.no_state_line(has_watch),
            "connect": not has_watch,  # no watch: how to add the nights, under the line or under the state
            "facts": facts if state else [], "score": score}


# ── the cards ───────────────────────────────────────────────────────────────

CARDS = {"hrv": ("vfc", "VFC", "ms", "millisecondes"), "hr": ("fc", "FC de nuit", "bpm", "battements par minute")}


def _span(metric: str, values: list, bands: list) -> float:
    """The y axis's least span: VFC ≥ 20 ms and ≥ 30 % of its median (± 15 %),
    FC de nuit ≥ 14 bpm, and the normal never more than half the height (a
    few ms or bpm must not look like a cliff)."""
    widest = max((b[1] - b[0] for b in bands if b), default=0)
    if metric == "hrv":
        vals = [v for v in values if v is not None]
        return max(20.0, 0.3 * statistics.median(vals) if vals else 0.0, 2 * widest)
    return max(14.0, 2 * widest)


# each night card's status line (v4.3): its last 7 nights against the usual values the score reads, in the words
# and the colour of its facts row (sante_score.row_tone); what it often means, only out of them on the side that
# matters; without usual values yet, how many nights until they exist (v4.4, owner: « les explications en
# français ne sont pas claires »: « tes valeurs habituelles », « d'habitude », never « ta normale »)
STATUS = {"in": "dans tes valeurs habituelles", "below": "plus basse que d'habitude",
          "above": "plus haute que d'habitude"}
BAD = {"hrv": "below", "hr": "above"}  # VFC under its normal, FC de nuit over it: the side that matters
MEANING = {"hrv": "Ça arrive avec la fatigue, le stress, l'alcool ou un début de maladie.",
           "hr": "Ça arrive avec la fatigue, la chaleur, l'alcool ou un début de maladie."}
NO_MEAN = "Trop peu de nuits qui comptent ces 7 derniers jours pour comparer."
FEW = "trop peu de nuits pour comparer"  # its row's word
BUILDING = "en construction"  # a signal without usual values yet: its row's word, its card's first words
COUNTS = "Les nuits en voyage, en altitude ou après un gros effort ne comptent pas."  # while the band builds
# the cards' legend (v4.4, owner: « comment matérialiser que c'est en cours de construction dans le graphique ? »):
# a filled dot counts toward the usual values, a hollow one does not; the band solid, or dashed while provisional
LEGEND_DOTS = {"both": [("is-dot", "compte"), ("is-out", "ne compte pas (voyage, gros effort, altitude)")],
               "in": [("is-dot", "nuit")], "out": [("is-out", "ne compte pas (voyage, gros effort, altitude)")]}
LEGEND_MEAN = ("is-mean", "moyenne sur 7 nuits")
LEGEND_BAND = ("is-band", "tes valeurs habituelles")
LEGEND_PROV = ("is-band-prov", "valeurs habituelles (provisoires)")


def nights_to_normal(nights, metric: str, d: date) -> int:
    """How many nights until the normal the score reads on `metric` exists, every coming night being an
    ordinary one: the first j ≥ 1 for which the band day d + j reads (the 60 days before its 7-night window, on
    the latest watch: 7 usable nights at least, H) holds 7 nights — those already slept and the coming ones."""
    src = nt.mean_source(nights, metric, d)
    known = [x for x, n in nights.items() if x <= d and n.usable(metric) and n.source_of(metric) == src]
    for j in range(1, nt.BAND_DAYS + 8):
        until = d + timedelta(days=j - 6)  # the band of day d + j: the nights before d + j − 6
        lo = until - timedelta(days=nt.BAND_DAYS)
        if sum(1 for x in known if lo <= x < until) + max(0, (until - d).days - 1) >= nt.MIN_PROVISIONAL_NIGHTS:
            return j
    return nt.MIN_PROVISIONAL_NIGHTS


def signed_pct(value: float, centre: float) -> int:
    """How far a 7-night mean is from the usual value (the band's centre: the median, the geometric mean for
    VFC), in whole percent (half up)."""
    return sc.rounded(100 * (value - centre) / centre)


def card_status(nights, metric: str, day: dict) -> dict:
    """{key, value, word, text, tone, meaning}, the card's status line and its facts row (one source of truth):
    the 7-night mean as a signed percentage against the usual value (v4.4, owner: « Mets des pourcentages plutôt
    que des valeurs (comme WHOOP / Oura) »: « −8 % », « +3 % », « comme d'habitude » at 0 %) and its place against
    the usual values, « dans tes valeurs habituelles » / « plus basse que d'habitude » / « plus haute que
    d'habitude » (key: in, below, above) with a dot in its colour (sante_score.row_tone: green in them, orange or
    red out of them on the side that matters, neutral on the other side, never praised), and « Ça arrive avec … »
    only out of them on the side that matters. Under the illness alert the FC de nuit reads the alert's 2 nights,
    as the score does: their percentage, « nettement plus haute depuis 2 nuits ». Without a band (none): « en
    construction », how many nights until there is one (nights_to_normal) and which nights do not count; a plain
    line when the week holds under 3 usable nights (few). `text`: the line in words."""
    s = day["stats"][metric]
    if not s["normal"]:
        n = nights_to_normal(nights, metric, day["day"])
        when = f"dans {n} nuits" if n > 1 else "après ta prochaine nuit"
        return {"key": "none", "value": None, "word": BUILDING, "tone": None, "meaning": COUNTS,
                "text": f"En construction : tes valeurs habituelles seront prêtes {when}, si tu portes ta montre."}
    part = next((p for p in day["score"]["parts"] if p["key"] == metric), None)
    if s["value"] is None or part is None:
        return {"key": "few", "value": None, "word": FEW, "text": NO_MEAN, "tone": None, "meaning": None}
    tone = sc.row_tone(part)
    status = BAD[metric] if tone in ("warn", "danger") else s["status"]
    centre = s["normal"]["center"]
    if metric == "hr" and day["alert"]:  # the alert's own 2 nights, as the score reads them
        p, word = signed_pct(statistics.fmean(day["alert"]["values"]), centre), ALERT_WORD
    else:
        p = signed_pct(s["value"], centre)
        word = SAME if p == 0 and status == "in" else STATUS[status]
    value = None if p == 0 else f"{viz.signed(p)}{viz.NBSP}%"
    return {"key": status, "value": value, "word": word, "text": f"{value} {word}" if value else word, "tone": tone,
            "meaning": MEANING[metric] if status == BAD[metric] else None}


def _night_card(nights, metric: str, today: date, day: dict) -> dict | None:
    """« VFC · 30 nuits » / « FC de nuit · 30 nuits » (viz.night_card): None
    without a night measured in the 30 days. With fewer than 10 measured
    nights the axis spans them (from the first, 14 days at least), not 30
    nights with a few dots against the right edge. Each day's band is the
    normal its 7-night mean is read against (the 60 days before that week, on
    the watch the mean reads: v4.3, one normal for the card, its status line
    and the score), its status line opens the card (card_status). v4.4: a
    night that does not count toward the usual values (Night.usable: time
    zone, after a big effort, altitude, an alert episode…) is a hollow dot,
    the provisional band is dashed; the legend names only what is drawn."""
    days = [today - timedelta(days=CARD_NIGHTS - 1 - i) for i in range(CARD_NIGHTS)]
    values = [nights[d].value(metric) if d in nights else None for d in days]
    seen = [i for i, v in enumerate(values) if v is not None]
    if not seen:
        return None
    if len(seen) < SPARSE_NIGHTS:
        keep = max(SPARSE_DAYS, CARD_NIGHTS - seen[0])
        days, values = days[-keep:], values[-keep:]
    bands, prov = [], []
    for d in days:
        b = nt.band(nights, metric, d - timedelta(days=6), source=nt.mean_source(nights, metric, d))
        bands.append((b["lo"], b["hi"]) if b else None)
        prov.append(bool(b and b["provisional"]))
    means = [(nt.mean7(nights, metric, d) or {}).get("value") for d in days]
    counts = [v is None or nights[d].usable(metric) for d, v in zip(days, values, strict=True)]
    key, name, unit, unit_long = CARDS[metric]
    c = viz.night_card(key, days, values, band=bands, prov=prov, mean=means, unit=unit, unit_long=unit_long,
                       name=name, min_span=_span(metric, values, bands), counts=counts)
    c["status"] = card_status(nights, metric, day)
    out = {d["out"] for d in c["dots"]}
    c["legend"] = (LEGEND_DOTS["both" if len(out) > 1 else "out" if out == {True} else "in"]
                   + ([LEGEND_MEAN] if c["mean"] else []) + ([LEGEND_BAND] if c["band"] else [])
                   + ([LEGEND_PROV] if c["band_prov"] else []))
    return c


# ── reading ─────────────────────────────────────────────────────────────────

async def _timeline(db: AsyncSession, user_id: int, nights, d: date) -> dict:
    """{d: [(stage, start, end)]}: the real stage intervals (Garmin
    sleepLevels) of the night `d` when it has them, cut to its main window."""
    from app.models.health import HealthSample

    n = nights.get(d)
    if not n or not n.timeline or not n.start:
        return {}
    rows = (await db.execute(select(HealthSample.kind, HealthSample.start_at, HealthSample.end_at).where(
        HealthSample.user_id == user_id, HealthSample.metric == "sleep", HealthSample.source == "Garmin",
        HealthSample.start_at >= n.start - timedelta(hours=1), HealthSample.start_at < n.end)
        .order_by(HealthSample.start_at))).all()
    segs = [(k, a, b) for k, a, b in rows if b > n.start and a < n.end]
    return {d: segs} if segs else {}


async def athlete_today(db: AsyncSession, user_id: int, now: datetime | None = None,
                        latest: date | None = None) -> date:
    """sante_training.athlete_today (a seam the tests move to a fixed day)."""
    return await st.athlete_today(db, user_id, now, latest)


async def _night_state(db: AsyncSession, user_id: int, today: date, seen: set[str]) -> dict[str, dict]:
    """Per watch that ever sent something: last night received, not yet, or
    not worn (today's daytime data came but no night). Drives how soon the
    page syncs again."""
    rows = (await db.execute(select(HealthMetric.metric, HealthMetric.source, HealthMetric.value).where(
        HealthMetric.user_id == user_id, HealthMetric.date == today,
        HealthMetric.metric.in_(("sleep", "steps", "hr_day"))))).all()
    out = {}
    for src in ("COROS", "Garmin"):
        if src not in seen:
            continue
        sleep = next((v for m, s, v in rows if m == "sleep" and s == src and v), None)
        if sleep:
            out[src] = {"state": "received", "txt": fmt_minutes(sleep)}
        elif any(s == src and m in ("steps", "hr_day") for m, s, _ in rows):
            out[src] = {"state": "none"}
        else:
            out[src] = {"state": "pending"}
    return out
