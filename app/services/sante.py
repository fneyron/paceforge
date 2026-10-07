"""The Santé page (v3): two views, each answering one question.

- Aujourd'hui « que faire aujourd'hui ? » (sante_today): one action, at most
  2 linked drivers and one sentence, at most 3 tiles (FC de nuit, VFC,
  Sommeil; FC en footing when flagged or during « Reprise »), the check-in.
- Sommeil « comment je dors, et est-ce dans ma normale ? » (sante_sleep): the
  24-h amounts and bed → wake windows, the hypnogram from real intervals,
  « Cœur la nuit », the nights' table and the method fold.

Only PaceForge's own nightly values (nights.py), read against the athlete's
own band; no brand value is read. Weekly training, fond/fatigue and easy-pace
HR live on Activités; race preparation lives on the race page. A night without
the watch is a gap, never a zero. Each number is printed once.
"""
import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.services import nights as nt
from app.services import race_prep as rp
from app.services.health import WATCH_SOURCES, fmt_minutes

logger = logging.getLogger(__name__)

# ── page ────────────────────────────────────────────────────────────────────

HISTORY_DAYS = 400
WEEK = 7


async def health_page(db: AsyncSession, user_id: int, today: date | None = None, now: datetime | None = None,
                      weight_kg: float | None = None, r: str | None = None,
                      parts: tuple[str, ...] = ("today", "sleep")) -> dict:
    """Everything /sante draws. `has_data` is False when neither a watch nor a
    session ever arrived. `r`: Sommeil's range (14, 90, 365; else the first
    one holding data). `parts`: the views to build."""
    from app.services import sante_sleep as sl
    from app.services import sante_training as st

    now = now or datetime.now(timezone.utc)
    latest = (await db.execute(select(func.max(HealthMetric.date)).where(HealthMetric.user_id == user_id))).scalar()
    today = today or await athlete_today(db, user_id, now, latest)
    sessions = await st.load_sessions(db, user_id, today)
    peak = st.hr_max(sessions, today)
    next_race, last_race, past_races = await _races(db, user_id, today)
    # Routes and sessions marked as races (Strava), as Activités and « après la course » read them
    races = rp.all_races(past_races + ([next_race] if next_race else []), sessions)
    nights, feel = await nt.load_nights(db, user_id, today, days=HISTORY_DAYS, sessions=sessions, races=races,
                                        peak=peak)
    sources = set((await db.execute(select(HealthMetric.source).distinct().where(
        HealthMetric.user_id == user_id, HealthMetric.metric != "feel"))).scalars().all())
    watch_seen = sources & set(WATCH_SOURCES)
    out = {
        "has_data": bool(sources) or bool(sessions),  # older rows may come from another source (Apple Health)
        "has_watch_data": bool(sources),
        "today": today,
        "night_state": await _night_state(db, user_id, today, watch_seen),
    }
    if "today" in parts:
        out["auj"] = _today_view(nights, feel, sessions, peak, next_race, last_race, races, today, bool(sources),
                                 routes=past_races + ([next_race] if next_race else []))
    if "sleep" in parts:
        race_days = {d for d, _ in races}  # a race has its own flag
        longs = sorted({s.day for s in sessions if (s.minutes >= 180 or s.dplus >= 1500) and s.day not in race_days})
        # the hypnograms of the range shown only (none in « 1 an »)
        samples = await _timelines(db, user_id, nights, today) if sl.choose(nights, today, r) in ("14", "90") else {}
        out["som"] = sl.sleep_view(nights, today, r, races=races, longs=longs, samples=samples,
                                   alert=nt.illness_alert(nights, today, races) is not None)
    return out


def _today_view(nights, feel, sessions, peak, next_race, last_race, races, today: date, has_watch: bool,
                routes=None) -> dict:
    """The Aujourd'hui view: « Forme du jour », decision, tiles, the sparse
    line, the check-in. `routes`: the Routes raced in the past 12 months and
    the next one (the score's 14-day history reads each day's own race);
    default: `last_race` and `next_race`. The nights are not changed: their
    bands and alerts are computed once (nights.memo)."""
    with nt.memo():
        return _build_today(nt.freeze(nights), feel, sessions, peak, next_race, last_race, races, today, has_watch,
                           routes)


def _build_today(nights, feel, sessions, peak, next_race, last_race, races, today: date, has_watch: bool,
                routes=None) -> dict:
    from app.services import sante_score as sc
    from app.services import sante_today as td
    from app.services import sante_training as st

    routes = sorted((r for r in (routes if routes is not None else (last_race, next_race)) if r and rp.race_day(r)),
                    key=rp.race_day)
    model = st.easy_model(sessions, today, peak)  # the easy-pace model Activités › FC en footing draws
    deltas = st.easy_deltas(model)
    day = _decide_day(nights, feel, sessions, model, deltas, routes, races, today, has_watch)
    verdict, ctx = day["verdict"], day["ctx"]
    # the tiles, with their 14-day sparklines; R2/R3: they show the episode's nights, never decided on
    episode = verdict["rule"] in ("ill", "reprise")
    stats = {k: _night_stats(nights, k, today, episode=episode) for k in ("hr", "hrv")}
    stats["sleep"] = _sleep_stats(nights, today, ctx["short"])
    stats["easy"] = td.easy_stats(model, today, ctx["reprise"] is not None)
    tiles = td.make_tiles(stats, verdict["drivers"], ctx["reprise"] is not None)

    # « Forme du jour »: today's, and the last 14 days' recomputed from what is stored (nothing persisted)
    now = sc.score_of(day, nights)
    history = _history(nights, feel, sessions, routes, today, has_watch, model) + [(today, verdict, now)]
    score = sc.view(now, history, has_watch=has_watch, has_sessions=bool(sessions))

    # the nightly tiles that cannot show: one line, once
    any_night = any(n.asleep is not None or n.hr is not None or n.hrv is not None for n in nights.values())
    link = None
    if not any_night:
        line = ("Sans nuit mesurée, je juge sur tes séances et ton ressenti." if has_watch
                else "Sans montre, je juge sur tes séances et ton ressenti.")
        link = None if has_watch else ("/settings#coros", "Connecter une montre")
    else:
        line = td.sparse_line([k for k in ("hr", "hrv") if stats[k]["text"] is None], _seen7(nights, today))
        if line:
            link = ("/sante?vue=sommeil", "Sommeil")
    # a tile without its status word: the normal is still being built (said once, on Sommeil with its count)
    building = line is None and any(t["key"] in ("hr", "hrv", "sleep") and t["value"] and not t["word"] for t in tiles)
    return {"verdict": verdict, "tiles": tiles, "line": line, "line_link": link, "building": building,
            "feel": _feel_view(feel.get(today)), "score": score, "method": sc.METHOD, "refs": sc.REFS}


def _history(nights, feel, sessions, routes, today: date, has_watch: bool, today_model=None) -> list:
    """[(day, verdict, score)] of the 13 days before today, each as the page
    computed it on that day: from the rows stored by then only, so a past
    point never takes knowledge from a later day. A day's nights carry that
    day's tags: an alert episode's first night is « FC de nuit haute » only
    from the next morning, when the alert fires; a race known only once run
    (Strava) tags its J-7 → J-1 from the day it was run (Routes are planned,
    known ahead); « late » reads that day's HR bounds. A day reads its own
    easy-pace model (the runs logged by then), fitted only when a rule reads it.

    Cost: the nights are tagged once, as of today, without the alert; a day
    whose own tagging gives the same tags on its nights (no race run since
    that tags them, the same « late » nights) reads those, with that
    day's alert episodes on top; the bands and alerts are then shared between
    the days (nights.memo). Any other day is tagged anew. `today_model`:
    today's easy-pace model, shared with the days that have the same runs."""
    from app.services import sante_score as sc
    from app.services import sante_training as st

    recent = [(x, n) for x, n in nights.items() if x > today - timedelta(days=sc.HISTORY_NIGHTS)]
    races_now, peak_now = rp.all_races(routes, sessions), st.hr_max(sessions, today)
    base = {x: nt.retagged(n) for x, n in recent}  # every tag as of today but « FC de nuit haute »
    rest_now = nt.rest_hr(base, today)
    nt.tag_nights(base, sessions, races_now, feel, rest_now, peak_now)
    nt.freeze(base)
    by_day = defaultdict(list)
    for s in sessions:
        by_day[s.day].append(s)
    # the nights a session can tag « late » (if vigorous: that reads the day's HR bounds)
    late = {x: c for x, n in base.items()
            if (c := nt.late_candidates(n, by_day.get(x - timedelta(days=1), []) + by_day.get(x, [])))}
    tagged = {frozenset(): base}  # a day's alert episode nights → base with those nights tagged « alert »
    models = {}  # the easy runs' ids → their model: the days with the same runs share one Theil–Sen fit
    if today_model:
        models[frozenset(s.id for s in today_model["runs"])] = today_model

    def fitter(ss, d, peak):
        box = []

        def fit():
            if not box:
                key = frozenset(s.id for s in st.easy_runs(ss, peak)
                                if d - timedelta(days=st.EASY_FIT_DAYS) < s.day <= d)
                if key not in models:
                    models[key] = st.easy_model(ss, d, peak)
                box.append(models[key])
            return box[0]
        return fit

    out = []
    for k in range(sc.HISTORY_DAYS - 1, 0, -1):
        d = today - timedelta(days=k)
        ss = [s for s in sessions if s.day <= d]
        fd = {x: f for x, f in feel.items() if x <= d}
        races = rp.all_races(routes, ss)  # a race marked on Strava counts from the day it was run
        peak, rest = st.hr_max(ss, d), nt.rest_hr(base, d)
        # its nights tagged « race » today by a race run after it (Strava), and by none it knew of
        later = {rd for rd, _ in races_now} - {rd for rd, _ in races}
        moved = later and any(x <= d and any(abs((x - rd).days) <= nt.RACE_WINDOW for rd in later)
                              and not any(abs((x - rd).days) <= nt.RACE_WINDOW for rd, _ in races) for x in base)
        same = not moved and all(any(nt.vigorous(s, rest, peak) for s in c) == ("late" in base[x].tags)
                                 for x, c in late.items() if x <= d)
        if same:
            alert = frozenset(x for x in nt.alert_episodes(base, d, races) if x in base)
            if alert not in tagged:
                tagged[alert] = nt.freeze({x: nt.retagged(n, n.tags | {"alert"}) if x in alert else n
                                           for x, n in base.items()})
            nd = tagged[alert]
        else:
            nd = {x: nt.retagged(n) for x, n in recent if x <= d}
            nt.tag_nights(nd, ss, races, fd, rest, peak)
            nt.tag_alerts(nd, d, races)
            nt.freeze(nd)
        fit = fitter(ss, d, peak)
        past = _decide_day(nd, fd, ss, fit, lambda fit=fit: st.easy_deltas(fit()), routes, races, d, has_watch)
        out.append((d, past["verdict"], sc.score_of(past, nd)))
    return out


def _races_on(routes, d: date):
    """(the next race on or after `d`, the last one run in the 14 days before `d`), as _races reads them."""
    nxt = next((r for r in routes if rp.race_day(r) >= d), None)
    last = next((r for r in reversed(routes) if d - timedelta(days=14) <= rp.race_day(r) < d), None)
    return nxt, last


def _decide_day(nights, feel, sessions, model, deltas, routes, races, d: date, has_watch: bool) -> dict:
    """What the ladder reads on day `d` — today, or a past day of the score's
    history (_history: what was stored on that day) — and its verdict: {day,
    ctx, verdict, stats (the deciding 7-night HR and HRV), shown (what the
    tiles show: the same, or an illness episode's nights), tst24, big (R6's
    outing), load (the score's Charge: the same, the race itself left out)}.
    `model` and `deltas`: the easy-pace model and its deltas, or functions
    returning them (read only when « Reprise » or R8 needs them)."""
    from app.services import sante_today as td
    from app.services import sante_training as st

    next_race, last_race = _races_on(routes, d)
    nr = None
    if next_race:
        nr = {"days": (rp.race_day(next_race) - d).days, "name": next_race.name,
              "href": f"/simulator/routes/{next_race.id}#prep"}
    race_week = bool(nr and 1 <= nr["days"] <= WEEK)
    post = _post_race(last_race, sessions, d)
    if post and post.get("route_id"):
        post["href"] = f"/simulator/routes/{post['route_id']}#prep"
    alert = nt.illness_alert(nights, d, races)
    reprise = nt.reprise(nights, feel, d, races, deltas)
    f = feel.get(d)
    worse = bool(f and f.get("answered", True) and f.get("value") == 3)
    easy = st.easy_watch(model() if callable(model) else model, d) if worse else None  # R8 reads it with « moins bien »

    # R5: a main episode and a 24-h total under 6 h, never in race week nor on race morning (J-7 → J0: the race
    # eve is never flagged, Lastella 2014; Juliff 2015)
    short = None
    tst = nt.day_tst24(nights, d)
    if tst is not None and tst < nt.SHORT_DAY_MIN and not (nr and 0 <= nr["days"] <= WEEK):
        usual = _usual(nights, d - timedelta(days=1))
        # a « rendormi » morning (a nap ≤ 3 h after the wake, H) is no early wake: its 24-h total says the short night
        early = nt.early_wake(nights[d], usual) and not nights[d].resettled
        short = {"tst24": tst, "early": early, "tip": nt.nap_tip_hour(usual)}

    # R6 (H): the biggest outing on foot of the last 48 h, when ≥ 3 h or ≥ 1 500 m D+
    yday, d42 = d - timedelta(days=1), d - timedelta(days=42)
    outings = [s for s in sessions if yday <= s.day <= d and s.sport in st.FOOT
               and (s.minutes >= td.LEGS_MIN or s.dplus >= td.LEGS_DPLUS)]
    big = max(outings, key=lambda s: (s.minutes, s.dplus), default=None)
    # the score's Charge leaves a race day's outing out: after a race the cap does it (SCORE_SPEC §2.4)
    race_days = {rd for rd, _ in races}
    load = max((s for s in outings if s.day not in race_days), key=lambda s: (s.minutes, s.dplus), default=None)

    # what decides: the 7-night means without an illness episode's nights (evidence: « Excluded nights »)
    stats = {k: _night_stats(nights, k, d, spark=False) for k in ("hr", "hrv")}
    ctx = {"today": d, "next_race": nr, "post": post, "feel": feel.get(d), "alert": alert, "reprise": reprise,
           "short": short, "legs": {"big": big}, "hr": stats["hr"]["status"], "hrv": stats["hrv"]["status"],
           "easy": easy, "sessions42": sum(1 for s in sessions if d42 < s.day <= d),
           "has_watch": has_watch, "has_sessions": bool(sessions), "race_week": race_week}
    verdict = td.decide(ctx)
    shown = stats
    if verdict["rule"] in ("ill", "reprise"):
        shown = {k: _night_stats(nights, k, d, episode=True, spark=False) for k in ("hr", "hrv")}
    return {"day": d, "ctx": ctx, "verdict": verdict, "stats": stats, "shown": shown, "tst24": tst, "big": big,
            "load": load}


USUAL_NIGHTS = 5  # (H) nights of 28 days before a median onset and wake are used (R5's early wake, the nap tip)


def _usual(nights, until: date) -> dict:
    """The median onset and wake of the 28 days up to `until`, once 5 nights are there (H; else none)."""
    t = nt.timing(nights, until)
    return t if t["n"] >= USUAL_NIGHTS else {"bed": None, "wake": None}


def _night_stats(nights, metric: str, today: date, episode: bool = False, spark: bool = True) -> dict:
    """A nightly tile's numbers: the mean of the last 7 days' usable nights (3
    at least), its status against the band of the 60 days before the window,
    on the watch that mean reads (one band per watch, Dial 2025: a new watch
    has no status for 7 nights, a « provisoire » one until 14, H), the
    7-night means of the last 14 days for the sparkline (`spark`). The status
    is judged on the unrounded mean (median ± 3 bpm, H). An illness episode's
    nights stay out (they never decide); `episode` (R2/R3 only): they stay in,
    so the tile shows the episode. `normal`: the band itself (the score reads
    its centre and spread)."""
    ignore = nt.EPISODE if episode else ()
    m = nt.mean7(nights, metric, today, ignore=ignore)
    src = nt.mean_source(nights, metric, today)
    b = nt.band(nights, metric, today - timedelta(days=WEEK - 1), source=src) if src else None
    means = [(nt.mean7(nights, metric, today - timedelta(days=k), ignore=ignore) or {}).get("value")
             for k in range(13, -1, -1)] if spark else []
    label, unit, said = {"hr": ("FC de nuit · 7 nuits", "bpm", "battements par minute"),
                         "hrv": ("VFC · 7 nuits", "ms", "millisecondes")}[metric]
    st = nt.status(m["value"], b) if m and b else None
    text = f"{m['value']:.0f}" if m else None
    return {"label": label, "unit": unit, "text": text, "value": m["value"] if m else None, "status": st,
            "spoken": f"{text} {said}" if m else "", "means": means,
            "band": (b["lo"], b["hi"]) if b and m else None, "href": "/sante?vue=sommeil#coeur",
            "prov": bool(st and b["provisional"]), "normal": b if m else None}


def _sleep_stats(nights, today: date, short: dict | None) -> dict:
    """« Sommeil · 7 jours »: the mean 24-h total (naps included) of the
    usable days, else of every measured day (the race window included: shown,
    never judged); « Sommeil · 24 h » when R5 fires: this morning's total as
    Sommeil draws it (Night.tst24: the same number on both views; R5 also
    counts yesterday's late nap, day_tst24, without printing it)."""
    from app.services.viz import hm, hm_long

    base = {"unit": "", "href": "/sante?vue=sommeil#nuits"}
    if short:
        days = [today - timedelta(days=k) for k in range(13, -1, -1)]
        means = [nights[d].tst24 if d in nights else None for d in days]
        v = nights[today].tst24
        return {**base, "label": "Sommeil · 24 h", "text": hm(v), "value": v,
                "status": "short", "spoken": f"{hm_long(v)} sur 24 heures", "means": means, "band": None}
    usable = nt.mean7(nights, "tst24", today)
    shown = usable or nt.mean7(nights, "tst24", today, untagged=False)
    src = nt.mean_source(nights, "tst24", today)
    b = nt.band(nights, "tst24", today - timedelta(days=WEEK - 1), source=src) if usable and src else None
    means = [(nt.mean7(nights, "tst24", today - timedelta(days=k), untagged=bool(usable)) or {}).get("value")
             for k in range(13, -1, -1)]
    if not shown:
        return {**base, "label": "Sommeil · 7 jours", "text": None, "value": None, "status": None, "spoken": "",
                "means": means, "band": None}
    v = shown["value"]
    return {**base, "label": "Sommeil · 7 jours", "text": hm(v), "value": v, "status": nt.status(v, b) if b else None,
            "spoken": f"{hm_long(v)} par jour en moyenne, siestes comprises", "means": means,
            "band": (b["lo"], b["hi"]) if b else None, "prov": bool(b and b["provisional"])}


def _seen7(nights, today: date) -> dict:
    """What the last 7 nights hold, to say once why a nightly tile is missing."""
    week = [n for d, n in nights.items() if today - timedelta(days=WEEK - 1) <= d <= today
            and (n.hr is not None or n.hrv is not None)]
    words = [t for n in week for t in sorted(n.tags) if t in nt.EXCLUDING and t != "race"]
    common = statistics.mode(words) if words else None
    return {"measured": len(week), "race": sum(1 for n in week if "race" in n.tags),
            "nap": sum(1 for n in week if n.hr is not None and not n.usable("hr") and n.hr_nap_day),
            "tag": nt.TAG_WORDS[common] if common else None}


FEEL_WORDS = {1: "mieux", 2: "comme d'habitude", 3: "moins bien"}
WHY_WORDS = {"legs": "jambes", "fatigue": "fatigue", "sick": "malade", "stress": "stress"}


def _feel_view(f: dict | None) -> dict:
    """The check-in: asked, or « Noté : moins bien · jambes — modifier »."""
    f = f or {}
    answered = bool(f) and f.get("answered", True)
    value = f.get("value") if answered else None
    why = (f.get("why") or []) if value == 3 else []
    words = (([FEEL_WORDS[value]] if value else []) + [WHY_WORDS[w] for w in why]
             + (["alcool hier"] if f.get("alcohol") else []))
    return {"answered": answered, "value": value, "why": why, "alcohol": bool(f.get("alcohol")),
            "noted": " · ".join(words)}


TIMELINE_DAYS = 90  # the hypnograms of the 14-night and 3-month ranges only


async def _timelines(db: AsyncSession, user_id: int, nights, today: date) -> dict:
    """{day: [(stage, start, end)]}: the real stage intervals (Garmin
    sleepLevels, stored as the hypnogram's timeline) of the nights of the last
    90 days that have them, each matched to its own main window only."""
    from bisect import bisect_right

    from app.models.health import HealthSample

    days = sorted(d for d, n in nights.items() if n.timeline and n.start and d > today - timedelta(days=TIMELINE_DAYS))
    if not days:
        return {}
    lo = nights[days[0]].start - timedelta(hours=1)
    rows = (await db.execute(select(HealthSample.kind, HealthSample.start_at, HealthSample.end_at).where(
        HealthSample.user_id == user_id, HealthSample.metric == "sleep", HealthSample.source == "Garmin",
        HealthSample.start_at >= lo).order_by(HealthSample.start_at))).all()
    out: dict[date, list] = {}
    wins = sorted((nights[d].start, nights[d].end, d) for d in days)  # one night after the other: ends sorted too
    ends = [e for _, e, _ in wins]
    for kind, a, b in rows:
        j = bisect_right(ends, a)  # the first window ending after the interval starts
        if j < len(wins) and wins[j][0] < b:
            out.setdefault(wins[j][2], []).append((kind, a, b))
    return out


async def athlete_today(db: AsyncSession, user_id: int, now: datetime | None = None,
                        latest: date | None = None) -> date:
    """sante_training.athlete_today (a seam the tests move to a fixed day)."""
    from app.services.sante_training import athlete_today as today_of

    return await today_of(db, user_id, now, latest)


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


async def _races(db: AsyncSession, user_id: int, today: date):
    """(the next race, the last one run in the past 14 days, those of the past
    12 months) — race_prep.load_races: sports hidden right now left out."""
    routes = await rp.load_races(db, user_id, today)
    nxt = next((r for r in routes if rp.race_day(r) >= today), None)
    past = [r for r in routes if rp.race_day(r) < today]
    last = next((r for r in reversed(past) if rp.race_day(r) >= today - timedelta(days=14)), None)
    return nxt, last, past


# intensity comes back at J+3 after a race under 3 h, J+7 after a long one, J+10 after 10 h (H)
FREE_SHORT, FREE_LONG, FREE_VERY_LONG = 3, 7, 10


def _post_race(last_race, sessions, today: date) -> dict | None:
    """The days after a race (a Route raced, or a session marked as a race),
    or after an exceptional outing (6 h and 1.5 × the longest of the 60 days
    before), until intensity comes back on `free` (see above; H). Its
    duration is the real one when known (race_prep.race_duration)."""
    from app.services.sante_today import of_day

    cands = []  # (day, minutes, known, name, race, route, session)
    if last_race and rp.race_day(last_race):
        secs, known = rp.race_duration(last_race, sessions)
        cands.append((rp.race_day(last_race), secs / 60 if secs else None, known and bool(secs), last_race.name, True,
                      last_race, None))
    for s in sessions:
        if not 1 <= (today - s.day).days <= FREE_VERY_LONG:
            continue
        # the longest of the 60 days before is read for an outing of 6 h and more only
        if (s.workout_type == 1 and s.minutes >= 180) or (s.minutes >= 360 and s.minutes >= 1.5 * max(
                (x.minutes for x in sessions if s.day - timedelta(days=60) <= x.day < s.day), default=0)):
            cands.append((s.day, s.minutes, True, f"ta sortie {of_day(s.day, today)}", s.workout_type == 1, None, s))
    # the latest day first; on one day, the Route (named) before the session
    for day, minutes, known, name, race, route, session in sorted(
            cands, key=lambda c: (c[0], c[5] is not None, c[1] or 0), reverse=True):
        days = (today - day).days
        long = minutes is None or minutes >= 180
        back = (FREE_VERY_LONG if minutes and minutes >= 600 else FREE_LONG) if long else FREE_SHORT
        if 1 <= days < back:
            return {"day": day, "days": days, "minutes": minutes or 0, "known": known, "name": name,
                    "limit": back - 1, "long": long, "race": race, "free": day + timedelta(days=back),
                    "race_name": route.name if route else None, "route_id": route.id if route else None,
                    "session": session}
    return None
