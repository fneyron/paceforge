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
    races = [(rp.race_day(x), x.name) for x in past_races + ([next_race] if next_race else [])]
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
        out["auj"] = _today_view(nights, feel, sessions, peak, next_race, last_race, races, today, bool(sources))
    if "sleep" in parts:
        race_days = {d for d, _ in races}  # a race has its own flag
        longs = sorted({s.day for s in sessions if (s.minutes >= 180 or s.dplus >= 1500) and s.day not in race_days})
        samples = await _timelines(db, user_id, nights)
        out["som"] = sl.sleep_view(nights, today, r, races=races, longs=longs, samples=samples,
                                   alert=nt.illness_alert(nights, today, races) is not None)
    return out


def _today_view(nights, feel, sessions, peak, next_race, last_race, races, today: date, has_watch: bool) -> dict:
    """The Aujourd'hui view: decision, tiles, the sparse line, the check-in."""
    from app.services import sante_today as td
    from app.services import sante_training as st

    nr = None
    if next_race:
        nr = {"days": (rp.race_day(next_race) - today).days, "name": next_race.name,
              "href": f"/simulator/routes/{next_race.id}#prep"}
    race_week = bool(nr and 1 <= nr["days"] <= WEEK)
    post = _post_race(last_race, sessions, today)
    if post and post.get("route_id"):
        post["href"] = f"/simulator/routes/{post['route_id']}#prep"
    alert = nt.illness_alert(nights, today, races)
    model = st.easy_model(sessions, today, peak)  # the easy-pace model Activités › FC en footing draws
    reprise = nt.reprise(nights, feel, today, races, st.easy_deltas(model))
    easy = st.easy_watch(model, today)

    # R5: a main episode and a 24-h total under 6 h, never in race week
    short = None
    tst = nt.day_tst24(nights, today)
    if tst is not None and tst < nt.SHORT_DAY_MIN and not race_week:
        usual = _usual(nights, today - timedelta(days=1))
        short = {"tst24": tst, "early": nt.early_wake(nights[today], usual), "tip": nt.nap_tip_hour(usual)}

    # R6 (H): the biggest outing on foot of the last 48 h, when ≥ 3 h or ≥ 1 500 m D+
    big = max((s for s in sessions if s.sport in st.FOOT and today - timedelta(days=1) <= s.day <= today
               and (s.minutes >= td.LEGS_MIN or s.dplus >= td.LEGS_DPLUS)),
              key=lambda s: (s.minutes, s.dplus), default=None)

    stats = {k: _night_stats(nights, k, today) for k in ("hr", "hrv")}
    stats["sleep"] = _sleep_stats(nights, today, short)
    stats["easy"] = td.easy_stats(model, today, reprise is not None)
    f_today = feel.get(today)
    ctx = {"today": today, "next_race": nr, "post": post, "feel": f_today, "alert": alert, "reprise": reprise,
           "short": short, "legs": {"big": big}, "hr": stats["hr"]["status"], "hrv": stats["hrv"]["status"],
           "easy": easy, "sessions42": sum(1 for s in sessions if s.day > today - timedelta(days=42)),
           "has_watch": has_watch, "has_sessions": bool(sessions)}
    verdict = td.decide(ctx)
    tiles = td.make_tiles(stats, verdict["drivers"], reprise is not None)

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
            "feel": _feel_view(f_today)}


def _usual(nights, until: date) -> dict:
    """The median onset and wake of the 28 days up to `until`, once 5 nights are there (else none)."""
    t = nt.timing(nights, until)
    return t if t["n"] >= 5 else {"bed": None, "wake": None}


def _night_stats(nights, metric: str, today: date) -> dict:
    """A nightly tile's numbers: the mean of the last 7 days' usable nights (3
    at least), its status against the band of the 60 days before the window,
    the 7-night means of the last 14 days for the sparkline."""
    # the nights of an illness episode stay out of the band, not out of the tile that shows the episode
    m = nt.mean7(nights, metric, today, ignore=("ill",))
    b = nt.band(nights, metric, today - timedelta(days=WEEK - 1))
    means = [(nt.mean7(nights, metric, today - timedelta(days=k), ignore=("ill",)) or {}).get("value")
             for k in range(13, -1, -1)]
    label, unit, said = {"hr": ("FC de nuit · 7 nuits", "bpm", "battements par minute"),
                         "hrv": ("VFC · 7 nuits", "ms", "millisecondes")}[metric]
    st = None
    if m and b:
        if metric == "hr":  # on the whole bpm the tile prints: median + 3 is « au-dessus » (H)
            v, c = round(m["value"]), round(b["center"])
            st = "above" if v >= c + nt.HR_BAND_BPM else "below" if v <= c - nt.HR_BAND_BPM else "in"
        else:
            st = nt.status(m["value"], b)
    text = f"{m['value']:.0f}" if m else None
    return {"label": label, "unit": unit, "text": text, "value": m["value"] if m else None, "status": st,
            "spoken": f"{text} {said}" if m else "", "means": means,
            "band": (b["lo"], b["hi"]) if b and m else None, "href": "/sante?vue=sommeil#coeur"}


def _sleep_stats(nights, today: date, short: dict | None) -> dict:
    """« Sommeil · 7 jours »: the mean 24-h total (naps included) of the
    usable days, else of every measured day (the race window included: shown,
    never judged); « Sommeil · 24 h » (the morning's total) when R5 fires."""
    from app.services.viz import hm, hm_long

    base = {"unit": "", "href": "/sante?vue=sommeil#nuits"}
    if short:
        days = [today - timedelta(days=k) for k in range(13, -1, -1)]
        means = [nt.day_tst24(nights, d) if d == today else (nights[d].tst24 if d in nights else None) for d in days]
        return {**base, "label": "Sommeil · 24 h", "text": hm(short["tst24"]), "value": short["tst24"],
                "status": "short", "spoken": f"{hm_long(short['tst24'])} sur 24 heures", "means": means, "band": None}
    usable = nt.mean7(nights, "tst24", today)
    shown = usable or nt.mean7(nights, "tst24", today, untagged=False)
    b = nt.band(nights, "tst24", today - timedelta(days=WEEK - 1)) if usable else None
    means = [(nt.mean7(nights, "tst24", today - timedelta(days=k), untagged=bool(usable)) or {}).get("value")
             for k in range(13, -1, -1)]
    if not shown:
        return {**base, "label": "Sommeil · 7 jours", "text": None, "value": None, "status": None, "spoken": "",
                "means": means, "band": None}
    v = shown["value"]
    return {**base, "label": "Sommeil · 7 jours", "text": hm(v), "value": v, "status": nt.status(v, b) if b else None,
            "spoken": f"{hm_long(v)} par jour en moyenne, siestes comprises", "means": means,
            "band": (b["lo"], b["hi"]) if b else None}


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


async def _timelines(db: AsyncSession, user_id: int, nights) -> dict:
    """{day: [(stage, start, end)]}: the real stage intervals (Garmin
    sleepLevels, stored as the hypnogram's timeline) of the nights that have
    them, clipped to nothing else than their own main window."""
    from app.models.health import HealthSample

    days = [d for d, n in nights.items() if n.timeline and n.start]
    if not days:
        return {}
    lo = nights[min(days)].start - timedelta(hours=1)
    rows = (await db.execute(select(HealthSample.kind, HealthSample.start_at, HealthSample.end_at).where(
        HealthSample.user_id == user_id, HealthSample.metric == "sleep", HealthSample.source == "Garmin",
        HealthSample.start_at >= lo).order_by(HealthSample.start_at))).all()
    out: dict[date, list] = {}
    wins = sorted((nights[d].start, nights[d].end, d) for d in days)
    for kind, a, b in rows:
        for s, e, d in wins:
            if a < e and b > s:
                out.setdefault(d, []).append((kind, a, b))
                break
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
        before = max((x.minutes for x in sessions if s.day - timedelta(days=60) <= x.day < s.day), default=0)
        if (s.workout_type == 1 and s.minutes >= 180) or (s.minutes >= 360 and s.minutes >= 1.5 * before):
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
