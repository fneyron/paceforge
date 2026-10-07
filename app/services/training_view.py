"""Activités, top of the page (v3, moved from Santé › Entraînement and
Tendances): how the weeks go, fond and fatigue, heart rate at easy pace. From
the sessions alone (sante_training), so it works without a watch at night.
partials/activity_training.html draws it on page 1 without a sport filter.

- A1 « Semaines » (open): 12 weeks of hours as Activités counts them (UTC
  Mondays, duplicates and false starts out), D+ as a thin second mark, the
  usual week as a band (P25–P75 of the active weeks of the last 26, H), ⚑ race
  weeks, ◆ weeks holding a long outing (≥ 3 h or ≥ 1 500 m D+, H), the
  single-run spike in the warn tone: a run > 10 % longer, on distance, than
  the longest of the 30 days before it, races excluded (Frandsen 2025: 1.5–
  2.3× overuse injuries; no weekly % flag, evidence row 15). Resting readout
  « semaine type : 7h40 » (the median of those weeks); a week's own values
  only on tap. One line at most: the spike, else « Affûtage pour {course} › »
  during the taper, else « Récupération après {course} : volume bas, c'est
  voulu. » after a race.
- A2 « Fond et fatigue » (closed): fond (42-day EWMA) and fatigue (7-day) of
  the session load over 120 days, two directly labelled lines, no y numbers,
  the readout is the date only: no ratio, no %, no « au-dessus / proche /
  sous » (the acute:chronic ratio is dropped: Lolli 2019; Impellizzeri 2020,
  2021; the constants are a convention, H; Hellard 2006). 42 days first (H).
- A3 « FC en footing » (opens itself when flagged): one dot per flat easy run,
  its HR moved to the athlete's reference pace (one Theil–Sen slope over 12
  months), hot runs (≥ 25 °C, H) hollow and out of the normal; the normal is
  the median of the 28 days before ± 3 bpm (H; Nuuttila 2022's 3–4 bpm edge)
  with 3 runs at least (H). « à surveiller » when the 2 latest runs, both in
  the last 14 days, are each ≥ 3 bpm above it (H; Nuuttila 2022). HR alone is
  « not a clear marker of fatigue » (Buchheit 2014): never « fatigue ».
"""
import json
import logging
import statistics
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.services import race_prep as rp
from app.services import sante_training as st
from app.services import viz
from app.services.viz import JOURS_L, MOIS, NNBSP, X1, d_long, hm, hm_long, num, signed

logger = logging.getLogger(__name__)

WEEKS, USUAL_WEEKS, MIN_USUAL_WEEKS = 12, 26, 8  # (H)
SPIKE, SPIKE_DAYS, SPIKE_RECENT = 1.10, 30, 10  # Frandsen 2025; the line names a spike of the last 10 days (H)
FORM_DAYS = 120
EASY_DAYS, EASY_FIT_DAYS = 182, 365
EASY_WINDOW, EASY_MIN, EASY_BPM, FLAG_DAYS = 28, 3, 3, 14  # (H)
HOT_C = 25  # (H)


def _dplus(v: float) -> str:
    """« 2 400 m D+ »."""
    return f"{int(round(v)):,}".replace(",", NNBSP) + f"{NNBSP}m D+"


def _rest(c: dict, read: list[str], aria: str) -> dict:
    """A resting readout shown until a touch (pf-viz.js D.rest): nothing selected."""
    data = json.loads(c["data"].replace("<\\/", "</"))
    data["rest"], data["restA"], data["sel"] = read, aria, len(data["x"])
    c["data"] = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    c.update(read=read, aria_now=aria, sel=len(data["x"]), rest=True)
    return c


# ── A1 Semaines ─────────────────────────────────────────────────────────────

def is_race(s: st.Session, race_days: set[date]) -> bool:
    return s.workout_type == 1 or s.day in race_days


def spikes(sessions: list[st.Session], race_days: set[date], since: date) -> list[tuple[st.Session, float]]:
    """Runs since `since` more than 10 % longer (distance) than the longest
    run of the 30 days before each, races excluded (Frandsen 2025)."""
    runs = sorted((s for s in sessions if s.sport in st.RUNS and s.km > 0), key=lambda s: s.start)
    first = min((s.day for s in sessions), default=None)
    out = []
    for s in runs:
        if s.day < since or is_race(s, race_days) or (s.day - first).days < SPIKE_DAYS:
            continue  # a race, or a run without 30 days of history before it (H): nothing to compare with
        before = [x.km for x in runs if s.day - timedelta(days=SPIKE_DAYS) <= x.day < s.day]
        if before and s.km > SPIKE * max(before):
            out.append((s, s.km / max(before)))
    return out


def semaines(sessions: list[st.Session], routes: list, today: date, now: datetime) -> dict | None:
    wk = st.weeks(sessions, now, WEEKS)
    if not any(w["count"] for w in wk):
        return None
    hist = [w for w in st.weeks(sessions, now, USUAL_WEEKS + 1)[:-1] if w["count"]]
    usual = None
    if len(hist) >= MIN_USUAL_WEEKS:
        q = statistics.quantiles([w["minutes"] for w in hist], n=4)
        usual = {"lo": q[0], "hi": q[2], "mid": statistics.median(w["minutes"] for w in hist)}
    mondays = [w["monday"] for w in wk]
    this_monday = mondays[-1]
    race_days = {rp.race_day(r) for r in routes}
    by_week: dict[date, list] = {}
    for s in sessions:
        by_week.setdefault(st.monday(s.start).date(), []).append(s)
    flags = []  # (monday, name): Routes first, then sessions marked as races
    for r in routes:
        m = rp.race_day(r) - timedelta(days=rp.race_day(r).weekday())
        if mondays[0] <= m <= this_monday and m not in dict(flags):
            flags.append((m, r.name))
    for s in sessions:
        m = st.monday(s.start).date()
        if s.workout_type == 1 and mondays[0] <= m <= this_monday and m not in dict(flags):
            flags.append((m, s.name or "course"))
    flagged = dict(flags)
    spk = {st.monday(s.start).date(): (s, ratio) for s, ratio in spikes(sessions, race_days, mondays[0])}
    longs = [m for m in mondays if m not in flagged and m not in spk and any(
        s.minutes >= rp.LONG_OUTING_MIN or s.dplus >= rp.LONG_OUTING_DPLUS for s in by_week.get(m, []))]

    n = len(wk)
    xs = viz.slot_x(n)
    bw = round(min(18, X1 / n * 0.62), 1)
    vmax = max([w["minutes"] for w in wk] + ([usual["hi"]] if usual else []) + [60]) * 1.08
    y = viz.scale(0, vmax, 14, 132)
    smax = max([w["dplus"] for w in wk] + [1])
    cols, r, a, h = [], [], [], []
    for i, w in enumerate(wk):
        m = w["monday"]
        col = {"i": i, "x": round(xs[i] - bw / 2, 1), "w": bw, "cx": xs[i], "cur": w["current"]}
        if w["minutes"]:
            col.update(y=y(w["minutes"]), h=round(132 - y(w["minutes"]), 1))
        if w["dplus"]:
            col["dplus"] = round(max(8 * w["dplus"] / smax, 1), 1)
        cols.append(col)
        ss = by_week.get(m, [])
        big = max(ss, key=lambda s: s.minutes, default=None)
        ctx, said = [], []  # printed (glyph + word), spoken
        if w["count"]:
            ctx.append(f"{w['count']} séance{'s' if w['count'] > 1 else ''}")
            said.append(ctx[-1])
        if m in flagged:
            ctx.append(f"{viz.GLYPH['race']} {flagged[m]}")
            said.append(f"course : {flagged[m]}")
        if m in spk:
            s, ratio = spk[m]
            ctx.append(f"{viz.GLYPH['long']} sortie {round((ratio - 1) * 100)}{NNBSP}% plus longue")
            said.append(f"une sortie {round((ratio - 1) * 100)} % plus longue que ta plus longue du mois")
        elif m in longs and big:
            ctx.append(f"{viz.GLYPH['long']} sortie de {hm(big.minutes)}")
            said.append(f"une sortie longue de {hm_long(big.minutes)}")
        head = rp.week_label(m) + (" · en cours" if w["current"] else "")
        value = hm(w["minutes"]) if w["minutes"] else "aucune séance"
        if w["dplus"] >= 1:
            value += f" · {_dplus(w['dplus'])}"
        r.append([head, value, " · ".join(ctx)])
        spoken = f"Semaine du {d_long(m)}" + (", en cours" if w["current"] else "") + " : "
        spoken += (hm_long(w["minutes"]) if w["minutes"] else "aucune séance")
        if w["dplus"] >= 1:
            spoken += f", {int(round(w['dplus']))} mètres de dénivelé positif"
        a.append(spoken + "".join(f", {c}" for c in said))
        weeks_ago = (this_monday - m).days // 7
        href = f"#week-{w['key']}" if weeks_ago < 6 else rp.activities_href(m, this_monday)
        h.append({"href": href, "label": "Voir la semaine ›"} if ss else None)
    ev = viz.events(mondays, xs, races=flags, longs=longs)
    c = {"n": n, "W": viz.W, "H": 168, "X1": X1, "cols": cols, "base": 132,
         "band": {"y": y(usual["hi"]), "h": round(y(usual["lo"]) - y(usual["hi"]), 1)} if usual else None,
         "spikes": [{"x": xs[mondays.index(m)]} for m in spk if m in mondays], **ev,
         "xt": [{"x": xs[i], "label": f"{m.day} {MOIS[m.month - 1]}"} for i, m in enumerate(mondays)
                if (n - 1 - i) % 3 == 0],
         "summary": "Heures par semaine sur 12 semaines, dénivelé en dessous" + (
             f", ta semaine type autour de {hm_long(usual['mid'])}" if usual else ""),
         **viz._data(xs, [], mondays, r, a, h=h)}
    if usual:
        c = _rest(c, ["12 semaines", f"semaine type : {hm(usual['mid'])}", ""],
                  f"12 semaines, ta semaine type : {hm_long(usual['mid'])}")
    c["line"] = a1_line(sessions, routes, race_days, today)
    return c


def a1_line(sessions, routes: list, race_days: set[date], today: date) -> dict | None:
    """{text, href}: the spike, else the taper, else the quiet line after a race."""
    recent = spikes(sessions, race_days, today - timedelta(days=SPIKE_RECENT - 1))
    if recent:
        s, ratio = recent[-1]
        k = (today - s.day).days
        when = ("d'aujourd'hui" if k == 0 else "d'hier" if k == 1 else f"de {JOURS_L[s.day.weekday()]}" if k < 7
                else f"du {s.day.day} {MOIS[s.day.month - 1]}")
        return {"text": f"Sortie {when} {round((ratio - 1) * 100)}{NNBSP}% plus longue que ta plus longue du mois.",
                "href": f"/activity/{s.id}"}
    nxt = next((r for r in routes if rp.race_day(r) >= today), None)
    if nxt is not None:
        start, rd = rp.taper_weeks(rp.race_day(nxt))
        if start <= today <= rd:
            return {"text": f"Affûtage pour {nxt.name} ›", "href": f"/simulator/routes/{nxt.id}#prep"}
    for r in reversed(routes):
        rd = rp.race_day(r)
        if rd < today:
            if (today - rd).days <= rp.recovery_days(r, sessions):
                return {"text": rp.fr(f"Récupération après {r.name} : volume bas, c'est voulu."),
                        "href": f"/simulator/routes/{r.id}#prep"}
            break
    return None


# ── A2 Fond et fatigue ──────────────────────────────────────────────────────

def fond_fatigue(sessions: list[st.Session], routes: list, today: date) -> dict:
    """{wait: sentence} before 6 weeks, else the two-line figure."""
    series = st.fitness(st.daily_loads(sessions), today)
    first = min(series) if series else today
    have = (today - first).days
    if not series or have < st.MIN_HISTORY_DAYS or len(sessions) < st.MIN_SESSIONS:
        left = max(1, st.MIN_HISTORY_DAYS - have) if series else None
        return {"wait": "Il faut 6 semaines de séances" + (f" : encore {left} jour{'s' if left > 1 else ''}."
                                                            if left and len(sessions) >= st.MIN_SESSIONS else ".")}
    lo = max(today - timedelta(days=FORM_DAYS - 1), first + timedelta(days=st.MIN_HISTORY_DAYS))
    days = [lo + timedelta(days=i) for i in range((today - lo).days + 1)]
    n = len(days)
    xs = viz.slot_x(n)
    lines = [("fond", [series[d][0] for d in days], ""), ("fatigue", [series[d][1] for d in days], "is-accent")]
    # the scale fits the two curves (no zero, no y number: a relative picture, never read as a ratio)
    vlo, vhi = viz.span_of([v for _, vals, _ in lines for v in vals], 1)
    y = viz.scale(vlo, vhi, 18, 120)
    out = [{"name": nm, "cls": cls, "path": viz.paths(xs, [y(v) for v in vals]), "end": y(vals[-1])}
           for nm, vals, cls in lines]
    # each name right-aligned on its curve's end, above the upper one and under the lower one
    up, down = sorted(out, key=lambda o: o["end"])
    up["ly"] = round(max(up["end"] - 6, 10), 1)
    down["ly"] = round(max(down["end"] + 14, up["ly"] + 13), 1)
    races = [(rp.race_day(r), r.name) for r in routes if days[0] <= rp.race_day(r) <= today]
    r = [["", viz.d_short(d), ""] for d in days]
    a = [d_long(d) for d in days]
    return {"n": n, "W": viz.W, "H": 140, "X1": X1, "series": out, "xt": viz.x_labels(days, xs),
            **viz.events(days, xs, races=races), "grid": [round(18 + k * (120 - 18) / 3, 1) for k in range(4)],
            "summary": f"Fond et fatigue sur {n} jours : deux courbes de ta charge, sans échelle",
            **viz._data(xs, [[y(v) for v in vals] for _, vals, _ in lines], days, r, a)}


# ── A3 FC en footing ────────────────────────────────────────────────────────

def flat_easy(sessions: list[st.Session], peak: float) -> list[st.Session]:
    """sante_training.easy_runs without its heat filter: hot runs are drawn
    (hollow), only kept out of the normal."""
    return [s for s in sessions
            if s.sport in st.RUNS and s.hr and s.speed and s.km >= 5 and 25 <= s.minutes <= 150
            and s.dplus <= 12 * s.km and s.hr <= 0.82 * peak and s.workout_type not in (1, 3)]


def is_hot(s: st.Session) -> bool:
    return s.temp is not None and s.temp >= HOT_C


def footing(sessions: list[st.Session], today: date, peak: float) -> dict | None:
    runs = sorted((s for s in flat_easy(sessions, peak) if s.day > today - timedelta(days=EASY_FIT_DAYS)),
                  key=lambda s: s.start)
    cool = [s for s in runs if not is_hot(s)]
    shown = [s for s in runs if s.day > today - timedelta(days=EASY_DAYS)]
    if len(cool) < st.MIN_FIT_RUNS or not shown:
        return None
    _, b = st.theil_sen([s.speed for s in cool], [s.hr for s in cool])
    pace = round(1000 / statistics.median(s.speed for s in cool) / 5) * 5  # s/km, to 5 s
    ref = 1000 / pace
    value = {s.id: s.hr - b * (s.speed - ref) for s in runs}
    cdays = [s.day for s in cool]

    def centre(d: date) -> float | None:
        """The median of the cool runs of the 28 days before `d` (3 at least)."""
        prior = [value[s.id] for s in cool[bisect_left(cdays, d - timedelta(days=EASY_WINDOW)):bisect_left(cdays, d)]]
        return statistics.median(prior) if len(prior) >= EASY_MIN else None

    lo = today - timedelta(days=EASY_DAYS - 1)
    days = [lo + timedelta(days=i) for i in range(EASY_DAYS)]
    band = []
    for d in days:
        m = centre(d)
        band.append((m - EASY_BPM, m + EASY_BPM) if m is not None else None)
    points = []
    for s in shown:
        m = centre(s.day)
        points.append({"day": s.day, "value": value[s.id], "hot": is_hot(s),
                       "label": f"normale {num(m - EASY_BPM)}–{num(m + EASY_BPM)}" if m is not None else "",
                       "href": {"href": f"/activity/{s.id}", "label": "Ouvrir la sortie ›"}})
    c = viz.dots(days, points, band=band, min_span=10)
    # two runs the same day: one slot, the later one stays selected by default
    last2 = [s for s in cool if s.day > today - timedelta(days=FLAG_DAYS)][-2:]
    deltas = [value[s.id] - centre(s.day) for s in last2 if centre(s.day) is not None]
    flagged = len(deltas) == 2 and all(dl >= EASY_BPM for dl in deltas)
    mins, secs = divmod(pace, 60)
    c.update(flagged=flagged, pace=f"{mins}:{secs:02d}/km",
             line=(f"{signed(statistics.fmean(deltas), 0, 'bpm')} sur tes 2 dernières sorties, à même allure."
                   if flagged else None),
             summary=(f"FC en footing sur 6 mois, ramenée à {mins}:{secs:02d} par kilomètre : {len(points)} sortie"
                      f"{'s' if len(points) > 1 else ''} facile{'s' if len(points) > 1 else ''}"))
    return c


# ── the block ───────────────────────────────────────────────────────────────

async def _rest_hr(db: AsyncSession, user_id: int, today: date) -> float:
    """The athlete's resting HR for the session load: the median nightly HR of
    60 days (sante_training.hr_rest), 50 without nights."""
    rows = (await db.execute(select(HealthMetric.date, HealthMetric.value).where(
        HealthMetric.user_id == user_id, HealthMetric.metric == "hr_night",
        HealthMetric.date > today - timedelta(days=60), HealthMetric.date <= today))).all()
    return st.hr_rest({d: v for d, v in rows if v}, {}, today)


async def training_top(db: AsyncSession, user_id: int, now: datetime | None = None) -> dict | None:
    """What partials/activity_training.html draws, or None (no session, or a
    failure: the list below never blanks for it)."""
    try:
        now = now or datetime.now(timezone.utc)
        today = await rp.athlete_today(db, user_id, now)
        sessions = await st.load_sessions(db, user_id, today)
        if not sessions:
            return None
        peak = st.hr_max(sessions, today)
        st.set_loads(sessions, await _rest_hr(db, user_id, today), peak)
        routes = [r for r in await rp.load_races(db, user_id, today)]
        past = [r for r in routes if rp.race_day(r) <= today]
        weeks = semaines(sessions, routes, today, now)
        if weeks is None:
            return None
        return {"weeks": weeks, "form": fond_fatigue(sessions, past, today), "easy": footing(sessions, today, peak)}
    except Exception:
        logger.exception("Activités › training block failed for user %d", user_id)
        return None
