"""Activités, top of the page (v3, moved from Santé › Entraînement and
Tendances): how the weeks go and heart rate at easy pace — training, never
recovery (v4.1, owner 2026-10-08: recovery lives on Santé only, like WHOOP
and Oura keep it on their home; « ce ne doit pas être redondant ni trop
compliqué »: no recovery line, no taper line, no fond et fatigue model here).
From the activities alone, never a planned race, so it works without a watch
at night and without a race in the app. partials/activity_training.html
draws it on page 1, the sport filter (Tout · Course · Trail · Vélo · Autre)
right under « Semaines », filtering the chart and the list alike.

- A1 « Semaines » (v4.3, owner: « Base-toi sur l'état de l'art, là c'est
  incompréhensible le schéma, il y a trop d'infos » — Strava's weekly
  progress, Garmin Connect's weekly volume): ONE measure at a time, a
  segmented control « Durée · Distance · D+ » (Distance only when some
  activity has one, D+ only when the 12 weeks hold some: an activity
  without D+ adds 0), « Durée » by default (every sport counts). 12 weekly
  bars and nothing else: the current week lighter (hatched, « en cours » in
  its readout and the legend), one dashed line at the average of the
  complete weeks, labelled « moy. ». The weeks as the list's headings count
  them (week_totals: UTC Mondays, the same sport filter, its duplicates and
  false starts out, moving time): a week's own total only on a tap (its
  heading prints it); the resting readout is the average, « 12h20 par
  semaine en moyenne », « sur 11 semaines » (the complete weeks since the
  first activity). No band, no ◆, no ▲, no D+ marks: the single-run spike
  (a run > 10 % longer, on distance, than the longest of the 30 days before
  it, never a race nor an effort of 6 h or more; Frandsen 2025: 1.5–2.3×
  overuse injuries) is a worded tag on its row in the list, « plus longue
  que d'habitude » (spike_ids). The toggle and the filter work without JS
  (a GET form, links).
- A2 « FC en footing » (opens itself when flagged; under « Tout », « Course »
  and « Trail »: its runs are runs): one dot per flat easy run,
  its HR moved to the athlete's reference pace (one Theil–Sen slope over 12
  months), hot runs (≥ 25 °C felt, H: sante_training.is_hot) hollow and out of the normal; the normal is
  the median of the 28 days before ± 3 bpm (H; Nuuttila 2022's 3–4 bpm edge)
  with 3 runs at least (H); drawn only with ≥ 6 qualifying runs in the last 6
  weeks (H). « à surveiller » when the 2 latest runs, both in the last 14
  days, are each ≥ 3 bpm above the median of the runs of the 14 days before
  it (H; Nuuttila 2022: against the previous 2 weeks); for 21 days after an
  ultra (sante_training.after_ultra, H) the same runs are annotated « après
  ultra » instead, the fold left closed (Chambers 1998, n = 8: HR at fixed
  speeds higher to day 25 after 90 km). One model (sante_training.easy_model
  / easy_watch), shared with Santé's « FC en footing » tile, which prints the
  bpm: here the line says it in words. HR alone is « not a clear marker of
  fatigue » (Buchheit 2014): never « fatigue ».
"""
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import String, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.services import race_prep as rp
from app.services import sante_training as st
from app.services import viz
from app.services.activity_dedupe import SPORT_GROUPS, find_duplicate_ids, is_false_start
from app.services.viz import MOIS, NBSP, X1, d_long, hm, hm_long, num

logger = logging.getLogger(__name__)

WEEKS = 12
SPIKE, SPIKE_DAYS = 1.10, 30  # Frandsen 2025
FORM_DAYS = 120
EASY_DAYS = 182  # the dots of 6 months
FLAG_LINE = "Tes 2 dernières sorties faciles : cœur au-dessus de ta normale, à même allure."
AFTER_ULTRA = "après ultra"  # the fold's note instead of « à surveiller », 21 days after an ultra (H)
SPIKE_TAG = "plus longue que d'habitude"  # the list row's tag of a single-run spike (v4.3)
# « Durée · Distance · D+ »: (key, the toggle's word, the figure's title, the spoken unit)
MEASURES = (("duree", "Durée", "Durée par semaine"), ("distance", "Distance", "Distance par semaine"),
            ("dplus", "D+", "Dénivelé positif par semaine"))
FLOOR = {"duree": 60, "distance": 10, "dplus": 100}  # the axis never shorter: a light week is no tall bar
FOOTING_SPORTS = (None, "run", "trail")  # « FC en footing » under these filters: its dots are runs


# ── A1 Semaines ─────────────────────────────────────────────────────────────

def is_race(s: st.Session) -> bool:
    """A race, from the activity alone: marked so on Strava, or an effort of 6
    h or more (sante_training.effort_of: an ultra, planned in the app or not)."""
    return s.workout_type == 1 or bool((e := st.effort_of(s)) and e.big)


def spikes(sessions: list[st.Session], since: date) -> list[tuple[st.Session, float]]:
    """Runs since `since` more than 10 % longer (distance) than the longest
    run of the 30 days before each, races excluded (Frandsen 2025)."""
    runs = sorted((s for s in sessions if s.sport in st.RUNS and s.km > 0), key=lambda s: s.start)
    first = min((s.day for s in sessions), default=None)
    out = []
    for s in runs:
        if s.day < since or is_race(s) or (s.day - first).days < SPIKE_DAYS:
            continue  # a race, or a run without 30 days of history before it (H): nothing to compare with
        before = [x.km for x in runs if s.day - timedelta(days=SPIKE_DAYS) <= x.day < s.day]
        if before and s.km > SPIKE * max(before):
            out.append((s, s.km / max(before)))
    return out


async def spike_ids(db: AsyncSession, user_id: int, since: date, now: datetime | None = None) -> set[int]:
    """The activities of the list (since `since`) that are a single-run spike (spikes): their rows say « plus
    longue que d'habitude » (v4.3: no ▲ on the chart any more). Never fails the list."""
    try:
        today = await st.athlete_today(db, user_id, now)
        return {s.id for s, _ in spikes(await st.load_sessions(db, user_id, today), since)}
    except Exception:
        logger.exception("Activités › spikes failed for user %d", user_id)
        return set()


def _sport_where(sport: str | None):
    """The list's sport filter (dashboard._sport_filter), as a where clause."""
    if sport in SPORT_GROUPS:
        return Activity.sport_type.in_(SPORT_GROUPS[sport])
    if sport == "other":
        return Activity.sport_type.not_in([t for types in SPORT_GROUPS.values() for t in types])
    return None


async def week_totals(db: AsyncSession, user_id: int, sport: str | None,
                      now: datetime) -> tuple[list[dict], date | None]:
    """The last 12 weeks, oldest first, exactly as the list's week headings count them (dashboard._week_groups):
    UTC Mondays, the same sport filter, the duplicates among those activities and the false starts left out, the
    moving time; {monday, current, count, minutes, km, dplus}. And the Monday of the first activity of that
    filter ever (the average reads the complete weeks since)."""
    this_monday = st.monday(now)
    lo = this_monday - timedelta(weeks=WEEKS - 1)
    has_splits = and_(Activity.splits_metric.is_not(None), cast(Activity.splits_metric, String).not_in(("null", "[]")))
    where = [Activity.user_id == user_id]
    if (f := _sport_where(sport)) is not None:
        where.append(f)
    rows = (await db.execute(select(Activity.id, Activity.start_date, Activity.sport_type, Activity.distance,
                                    Activity.moving_time, Activity.total_elevation_gain, has_splits)
                             .where(*where, Activity.start_date >= lo,
                                    Activity.start_date < this_monday + timedelta(weeks=1)))).all()
    first = (await db.execute(select(func.min(Activity.start_date)).where(*where))).scalar()

    class _Row:  # what activity_dedupe reads
        def __init__(self, r):
            self.id, self.start_date, self.sport_type, self.distance, self.moving_time = r[0], st._utc(r[1]), r[2], \
                r[3] or 0, r[4] or 0
            self.dplus, self.splits_metric = r[5] or 0, bool(r[6])

    acts = [_Row(r) for r in rows]
    skip = find_duplicate_ids(acts)
    out = []
    for i in range(WEEKS):
        m = lo + timedelta(weeks=i)
        week = [a for a in acts if a.id not in skip and not is_false_start(a) and st.monday(a.start_date) == m]
        out.append({"monday": m.date(), "current": m == this_monday, "count": len(week),
                    "minutes": sum(a.moving_time for a in week) / 60, "km": sum(a.distance for a in week) / 1000,
                    "dplus": sum(a.dplus for a in week)})
    return out, (st.monday(first).date() if first else None)


def _big(v: float, key: str) -> str:
    """A week's value as the readout prints it (a plain no-break space: the display font has no narrow one)."""
    if key == "duree":
        return hm(v)
    if key == "distance":
        return f"{num(v)}{NBSP}km"
    return "+" + f"{int(round(v)):,}".replace(",", NBSP) + f"{NBSP}m"


def _said(v: float, key: str) -> str:
    if key == "duree":
        return hm_long(v)
    if key == "distance":
        return f"{num(v)} kilomètre{'s' if round(v) > 1 else ''}"
    return f"{int(round(v))} mètre{'s' if round(v) > 1 else ''} de dénivelé positif"


def _avg_rounded(v: float, key: str) -> float:
    """A mean: durations to 5 min, distances to 1 km, climbs to 10 m."""
    return round(v / 5) * 5 if key == "duree" else round(v) if key == "distance" else round(v / 10) * 10


def _chart(weeks: list[dict], key: str, title: str, n_avg: int, sport: str | None, this_monday: date) -> dict:
    """One measure's 12 bars (see the module docstring)."""
    field = {"duree": "minutes", "distance": "km", "dplus": "dplus"}[key]
    values = [w[field] for w in weeks]
    done = [v for w, v in zip(weeks, values) if not w["current"]][-n_avg:] if n_avg else []
    avg = sum(done) / len(done) if done else None
    n = len(weeks)
    xs = viz.slot_x(n)
    bw = round(min(18, X1 / n * 0.62), 1)
    vmax = max(values + ([avg] if avg else []) + [FLOOR[key]]) * 1.08
    y = viz.scale(0, vmax, 14, 132)
    cols, r, a, h = [], [], [], []
    for i, (w, v) in enumerate(zip(weeks, values)):
        m = w["monday"]
        col = {"i": i, "x": round(xs[i] - bw / 2, 1), "w": bw, "cx": xs[i], "cur": w["current"]}
        if v > 0:
            col.update(y=y(v), h=round(132 - y(v), 1))
        cols.append(col)
        head = rp.week_label(m)
        if not w["count"]:
            r.append([head, "—", "aucune activité"])
            a.append(f"Semaine du {d_long(m)}" + (", en cours" if w["current"] else "") + " : aucune activité")
        else:
            r.append([head, _big(v, key), "en cours" if w["current"] else ""])
            a.append(f"Semaine du {d_long(m)}" + (", en cours" if w["current"] else "") + f" : {_said(v, key)}")
        # the week in the list below (page 1 holds the last 6 weeks), the filter kept
        weeks_ago = (this_monday - m).days // 7
        href = f"#week-{m.isoformat()}" if weeks_ago < 6 else (
            f"/activities?page={weeks_ago // 6 + 1}" + (f"&sport={sport}" if sport else "") + f"#week-{m.isoformat()}")
        h.append({"href": href, "label": "Voir la semaine ›"} if w["count"] else None)
    c = {"key": f"semaines-{key}", "n": n, "W": viz.W, "H": 156, "X1": X1, "cols": cols, "base": 132,
         "avg": y(avg) if avg else None, "cur": bool(cols[-1].get("h")),
         "xt": [{"x": xs[i], "label": f"{w['monday'].day} {MOIS[w['monday'].month - 1]}"}
                for i, w in enumerate(weeks) if (n - 1 - i) % 3 == 0],
         "summary": f"{title} sur 12 semaines, la semaine en cours plus claire"
                    + (", une ligne à la moyenne des semaines complètes" if avg else ""),
         "title": title, **viz._data(xs, [], [w["monday"] for w in weeks], r, a, h=h)}
    # resting readout: the average (each week's own total is its list heading's: printed there, on a tap here)
    if avg is not None:
        mean = _avg_rounded(avg, key)
        over = f"sur {n_avg} semaine{'s' if n_avg > 1 else ''}"
        return viz.rest(c, [over, _big(mean, key), "par semaine en moyenne"],
                        f"{_said(mean, key)} par semaine en moyenne, {over} complète{'s' if n_avg > 1 else ''}. "
                        "Choisis une semaine pour son total.", back=n - 1)
    return viz.rest(c, ["12 semaines", title, ""], f"{title} sur 12 semaines. Choisis une semaine pour son total.",
                    back=n - 1)


def semaines(weeks: list[dict], first: date | None, measure: str | None, sport: str | None = None) -> dict | None:
    """« Semaines »: the measures offered, the one shown (`measure` when offered, else « Durée ») and one chart
    per measure offered (the toggle switches them in place, ?m= without JS); None without any activity in the
    12 weeks (for this filter). The average reads the complete weeks since the first activity's (H)."""
    if not any(w["count"] for w in weeks):
        return None
    offered = [k for k, _, _ in MEASURES if k == "duree" or (k == "distance" and any(w["km"] > 0 for w in weeks))
               or (k == "dplus" and any(round(w["dplus"]) >= 1 for w in weeks))]
    this_monday = weeks[-1]["monday"]
    since = max(weeks[0]["monday"], first) if first else weeks[0]["monday"]
    n_avg = sum(1 for w in weeks if not w["current"] and w["monday"] >= since)
    charts = {k: _chart(weeks, k, title, n_avg, sport, this_monday) for k, _, title in MEASURES if k in offered}
    return {"measures": [(k, label) for k, label, _ in MEASURES if k in offered],
            "m": measure if measure in offered else "duree", "charts": charts}


# ── A2 FC en footing ────────────────────────────────────────────────────────

def footing(sessions: list[st.Session], today: date, peak: float) -> dict | None:
    """The dots of 6 months against the rolling normal (sante_training.easy_model,
    the model Santé's « FC en footing » tile reads too); « à surveiller »
    is sante_training.easy_watch. Its number (the bpm over the normal) is
    printed once, on Santé's tile: the line here says it in words."""
    model = st.easy_model(sessions, today, peak)
    if not st.line_ok(model, today):  # ≥ 6 qualifying runs in 6 weeks (H, evidence row 16)
        return None
    shown = [s for s in model["runs"] if s.day > today - timedelta(days=EASY_DAYS)]
    if not shown:
        return None
    value, centre = model["value"], model["centre"]
    lo = today - timedelta(days=EASY_DAYS - 1)
    days = [lo + timedelta(days=i) for i in range(EASY_DAYS)]
    band = []
    for d in days:
        m = centre(d)
        band.append((m - st.EASY_BPM, m + st.EASY_BPM) if m is not None else None)
    points = []
    for s in shown:
        m = centre(s.day)
        points.append({"day": s.day, "value": value[s.id], "hot": st.is_hot(s),
                       "label": f"normale {num(m - st.EASY_BPM)}–{num(m + st.EASY_BPM)}" if m is not None else "",
                       "href": {"href": f"/activity/{s.id}", "label": "Ouvrir la sortie ›"}})
    c = viz.dots(days, points, band=band, min_span=10)
    watch = st.easy_watch(model, today)
    flagged = bool(watch and watch["flag"])
    # for 21 days after an ultra a raised easy-pace HR is « après ultra », never flagged (Chambers 1998, H)
    ultra = flagged and st.after_ultra(st.efforts(sessions), watch["deltas"][-1][0]) is not None
    flagged = flagged and not ultra
    mins, secs = divmod(model["pace"], 60)
    line = FLAG_LINE if flagged else f"{FLAG_LINE[:-1]}, {AFTER_ULTRA}." if ultra else None
    c.update(flagged=flagged, after_ultra=ultra, pace=f"{mins}:{secs:02d}/km", line=line,
             summary=(f"FC en footing sur 6 mois, ramenée à {mins}:{secs:02d} par kilomètre : {len(points)} sortie"
                      f"{'s' if len(points) > 1 else ''} facile{'s' if len(points) > 1 else ''}"))
    return c


# ── the block ───────────────────────────────────────────────────────────────

async def training_top(db: AsyncSession, user_id: int, now: datetime | None = None, sport: str | None = None,
                       measure: str | None = None) -> dict | None:
    """What partials/activity_training.html draws for the sport filter `sport` (None: every sport), or None on a
    failure (the list below never blanks for it): « Semaines » (None without an activity in its 12 weeks) and
    « FC en footing » (under « Tout », « Course » and « Trail »). No planned race is read."""
    try:
        now = now or datetime.now(timezone.utc)
        weeks, first = await week_totals(db, user_id, sport, now)
        easy = None
        if sport in FOOTING_SPORTS:
            today = await st.athlete_today(db, user_id, now)
            sessions = await st.load_sessions(db, user_id, today)
            easy = footing(sessions, today, st.hr_max(sessions, today)) if sessions else None
        return {"weeks": semaines(weeks, first, measure, sport), "easy": easy}
    except Exception:
        logger.exception("Activités › training block failed for user %d", user_id)
        return None
