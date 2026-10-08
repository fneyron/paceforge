"""Activités, top of the page (v3, moved from Santé › Entraînement and
Tendances): how the weeks go and heart rate at easy pace — training, never
recovery (v4.1, owner 2026-10-08: recovery lives on Santé only, like WHOOP
and Oura keep it on their home; « ce ne doit pas être redondant ni trop
compliqué »: no recovery line, no taper line, no fond et fatigue model here).
From the activities alone (sante_training), never a planned race, so it works
without a watch at night and without a race in the app.
partials/activity_training.html draws it on page 1 without a sport filter.

- A1 « Semaines » (open): 12 weeks of hours as Activités counts them (UTC
  Mondays, duplicates and false starts out), D+ as a thin second mark, the
  usual week as a band (P25–P75 of the active weeks of the last 26, H), ◆
  weeks holding a long outing (≥ 3 h or ≥ 1 500 m D+, H; a race is an outing
  like any other: no race flag, owner 2026-10-08), the single-run spike in the
  warn tone: a run > 10 % longer, on distance, than the longest of the 30 days
  before it, never a race (marked so on Strava) nor an effort of 6 h or more
  (an ultra planned in the app or not: marked ◆, never flagged; Frandsen 2025:
  1.5–2.3× overuse injuries; no weekly % flag, evidence row 15). Resting
  readout « semaine type : 7h40 » (the median of those weeks), « heures par
  semaine » before 8 active weeks; a week's own values only on tap (the list's
  week headings print them). A legend names every mark. One line at most:
  the spike of the last 10 days.
- A2 « FC en footing » (opens itself when flagged): one dot per flat easy run,
  its HR moved to the athlete's reference pace (one Theil–Sen slope over 12
  months), hot runs (≥ 25 °C, H) hollow and out of the normal; the normal is
  the median of the 28 days before ± 3 bpm (H; Nuuttila 2022's 3–4 bpm edge)
  with 3 runs at least (H); drawn only with ≥ 6 qualifying runs in the last 6
  weeks (H). « à surveiller » when the 2 latest runs, both in the last 14
  days, are each ≥ 3 bpm above the median of the runs of the 14 days before
  it (H; Nuuttila 2022: against the previous 2 weeks). One model
  (sante_training.easy_model / easy_watch), shared with Santé's « FC en
  footing » tile, which prints the bpm: here the line says it in words. HR
  alone is « not a clear marker of fatigue » (Buchheit 2014): never « fatigue ».
"""
import logging
import statistics
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import race_prep as rp
from app.services import sante_training as st
from app.services import viz
from app.services.viz import JOURS_L, MOIS, NNBSP, X1, d_long, hm, hm_long, num

logger = logging.getLogger(__name__)

WEEKS, USUAL_WEEKS, MIN_USUAL_WEEKS = 12, 26, 8  # (H)
SPIKE, SPIKE_DAYS, SPIKE_RECENT = 1.10, 30, 10  # Frandsen 2025; the line names a spike of the last 10 days (H)
FORM_DAYS = 120
EASY_DAYS = 182  # the dots of 6 months


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


def semaines(sessions: list[st.Session], today: date, now: datetime) -> dict | None:
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
    by_week: dict[date, list] = {}
    for s in sessions:
        by_week.setdefault(st.monday(s.start).date(), []).append(s)
    spk = {st.monday(s.start).date(): (s, ratio) for s, ratio in spikes(sessions, mondays[0])}
    longs = [m for m in mondays if m not in spk and any(
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
            ctx.append(f"{w['count']} activité{'s' if w['count'] > 1 else ''}")
            said.append(ctx[-1])
        if m in spk:
            s, ratio = spk[m]
            ctx.append(f"{viz.GLYPH['up']} sortie {round((ratio - 1) * 100)}{NNBSP}% plus longue")
            said.append(f"une sortie {round((ratio - 1) * 100)} % plus longue que ta plus longue du mois")
        elif m in longs and big:
            ctx.append(f"{viz.GLYPH['long']} sortie de {hm(big.minutes)}")
            said.append(f"une sortie longue de {hm_long(big.minutes)}")
        head = rp.week_label(m) + (" · en cours" if w["current"] else "")
        value = hm(w["minutes"]) if w["minutes"] else "aucune activité"
        if round(w["dplus"]) >= 1:
            value += f" · {viz.dplus(w['dplus'])}"  # as the list's week heading prints it
        r.append([head, value, " · ".join(ctx)])
        spoken = f"Semaine du {d_long(m)}" + (", en cours" if w["current"] else "") + " : "
        spoken += (hm_long(w["minutes"]) if w["minutes"] else "aucune activité")
        if round(w["dplus"]) >= 1:
            spoken += f", {int(round(w['dplus']))} mètres de dénivelé positif"
        a.append(spoken + "".join(f", {c}" for c in said))
        weeks_ago = (this_monday - m).days // 7
        href = f"#week-{w['key']}" if weeks_ago < 6 else rp.activities_href(m, this_monday)
        h.append({"href": href, "label": "Voir la semaine ›"} if ss else None)
    ev = viz.events(mondays, xs, longs=longs)
    c = {"n": n, "W": viz.W, "H": 168, "X1": X1, "cols": cols, "base": 132,
         "band": {"y": y(usual["hi"]), "h": round(y(usual["lo"]) - y(usual["hi"]), 1)} if usual else None,
         "spikes": [{"x": xs[mondays.index(m)]} for m in spk if m in mondays], **ev,
         "xt": [{"x": xs[i], "label": f"{m.day} {MOIS[m.month - 1]}"} for i, m in enumerate(mondays)
                if (n - 1 - i) % 3 == 0],
         "summary": "Heures par semaine sur 12 semaines, dénivelé en dessous" + (
             f", ta semaine type autour de {hm_long(round(usual['mid'] / 5) * 5)}" if usual else ""),
         **viz._data(xs, [], mondays, r, a, h=h)}
    # resting readout: a week's own hours only on tap (the list's week headings print them)
    if usual:
        mid = round(usual["mid"] / 5) * 5  # a median: to 5 min
        c = viz.rest(c, ["12 semaines", f"semaine type : {hm(mid)}", ""], f"12 semaines, ta semaine type : {hm_long(mid)}")
    else:
        c = viz.rest(c, ["12 semaines", "heures par semaine", "dénivelé en dessous"],
                  "12 semaines : heures par semaine, dénivelé en dessous. Choisis une semaine pour ses chiffres.")
    c["line"] = a1_line(sessions, today)
    return c


def a1_line(sessions, today: date) -> dict | None:
    """{text, href}: a spike of the last 10 days, else nothing (the recovery
    after a big outing is Santé's, a race's taper the race page's)."""
    recent = spikes(sessions, today - timedelta(days=SPIKE_RECENT - 1))
    if not recent:
        return None
    s, ratio = recent[-1]
    k = (today - s.day).days
    when = ("d'aujourd'hui" if k == 0 else "d'hier" if k == 1 else f"de {JOURS_L[s.day.weekday()]}" if k < 7
            else f"du {s.day.day} {MOIS[s.day.month - 1]}")
    return {"text": f"Sortie {when} {round((ratio - 1) * 100)}{NNBSP}% plus longue que ta plus longue du mois.",
            "href": f"/activity/{s.id}"}


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
    mins, secs = divmod(model["pace"], 60)
    c.update(flagged=flagged, pace=f"{mins}:{secs:02d}/km",
             line="Tes 2 dernières sorties faciles : cœur au-dessus de ta normale, à même allure." if flagged else None,
             summary=(f"FC en footing sur 6 mois, ramenée à {mins}:{secs:02d} par kilomètre : {len(points)} sortie"
                      f"{'s' if len(points) > 1 else ''} facile{'s' if len(points) > 1 else ''}"))
    return c


# ── the block ───────────────────────────────────────────────────────────────

async def training_top(db: AsyncSession, user_id: int, now: datetime | None = None) -> dict | None:
    """What partials/activity_training.html draws, or None (no session, or a
    failure: the list below never blanks for it). No planned race is read."""
    try:
        now = now or datetime.now(timezone.utc)
        today = await st.athlete_today(db, user_id, now)
        sessions = await st.load_sessions(db, user_id, today)
        if not sessions:
            return None
        weeks = semaines(sessions, today, now)
        if weeks is None:
            return None
        return {"weeks": weeks, "easy": footing(sessions, today, st.hr_max(sessions, today))}
    except Exception:
        logger.exception("Activités › training block failed for user %d", user_id)
        return None
