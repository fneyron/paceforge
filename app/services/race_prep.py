"""Race page › « Préparation » (#prep): what the evidence backs around one race,
moved from Santé › Course (v3). partials/race_prep.html draws it.

Shown from J-42 to J0 (H), on the race page only (simulator_route.html): v4.3
(owner: « Enlève la partie récupération sous le plan d'une course »), nothing
after the race day — recovery is Santé's.
- Affûtage: Activités' weeks S-6 → S0 (UTC Mondays, duplicates out), hours
  done as bars, the taper J-14 → J-1 against the −41 to −60 % band of the
  base (Bosquet 2007: about 2 weeks, volume −41–60 %, intensity and frequency
  kept; Wang 2023: tapers of ≤ 21 days all worked, best at 8–14 days). The
  14 days fall on 2 or 3 calendar weeks: a week's target is the base at its
  usual rate for its days before J-14, plus the band for its days inside
  J-14 → J-1 (each day 1/7 of the week; the race day and after are not
  counted), so a race on any weekday compares like with like. No event longer than 40 km was
  studied: for an ultra it is an extrapolation, said in the figure's summary.
  Base = mean hours of the 4 complete weeks before the taper's first week
  (before this week while the taper is still ahead). S0 counts the days
  before the race only.
- Tes nuits, J-14 → J-1: 24-h sleep (main night solid, naps stacked and
  hatched), against your usual + 30 to 60 min (H, below the doses of Mah 2011
  and Arnal 2016; Cunha 2023) on J-7 → J-2 only (the banking week: Walsh
  2021, « even just 1 week »), drawn only when a usual exists (≥ 7 measured
  days in the 60 days before J-14, nights.band; « provisoire » under 14, H).
  « Vise 30 à 60 min de plus par jour, siestes comprises. » from J-7 to J-1,
  and once, from J-7 to J0, Walsh 2021's race-eve reassurance (EVE_LINE). The
  race eve is never flagged (Lastella 2014; Juliff 2015): no target on it, no
  outline anywhere on this page.
- Semaine de course (J-10 → J0): carbohydrate loading J-2 and J-1, 10–12
  g/kg/day for races over 90 min (Burke 2011; ACSM/AND/DC 2016), breakfast
  1–2 g/kg 3 h before, 5–7 ml/kg of fluid at least 4 h before (ACSM).
- Course chaude (J-14 → J-1): one line when the saved forecast is hot (heat
  factor ≥ 1.06 ≈ 25 °C, H): acclimatise over 1–2 weeks of repeated heat
  sessions (Racinais 2015), most of it in the first week (Périard 2015).
Each number is printed once across Santé, Activités and this page: the two
figures open on a resting readout (viz.rest) — the window, the base and the
target — and a week's hours (Activités' week headings) or a night's values
(Santé › Sommeil) show only on a tap.
« Prêt pour la distance ? » is gone: no evidence row backs it; the post-race
« Cœur la nuit » J+1 → J+14 and the driving line after a night race went with
the recovery part (v4.3).
"""
import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models.route import Route
from app.services import nights as nt
from app.services import sante_training as st
from app.services import viz
from app.services.health import feel_of
from app.services.viz import MOIS, NNBSP, X1, d_long, hm, hm_long, num

logger = logging.getLogger(__name__)

PREP_BEFORE = 42  # the section shows J-42 → J0 (H)
NIGHT_DAYS = 14  # J-14 → J-1
EXTEND = (30, 60)  # usual + 30–60 min a day of 24-h sleep (H)
BANK_DAYS = 7  # the sleep-banking line and target from J-7 (Walsh 2021: « even just 1 week » improves performance)
EVE_LINE = ("Une nuit agitée avant une course est courante : si tu as bien dormi la semaine d'avant, elle ne devrait "
            "pas peser sur ta course.")  # Walsh 2021, Box 2
TAPER_SHARE = (0.40, 0.59)  # the taper weeks at 40–59 % of the base: volume −41 to −60 % (Bosquet 2007)
TAPER_DAYS = 14  # J-14 → J-1: about 2 weeks (Bosquet 2007; Wang 2023)
CHART_WEEKS = 7  # S-6 → S0
CARBS_DAYS = 10  # the race-week carbohydrates from J-10
HOT_DAYS = 14  # the hot-race line J-14 → J-1
HOT_FACTOR = 1.06  # (H) weather.compute_heat_factor at ≈ 25 °C
LONG_S, CARBS_S = 3 * 3600, 90 * 60
LONG_OUTING_MIN, LONG_OUTING_DPLUS = 180, 1500  # ◆ a long outing (H)
NBH = "‑"  # non-breaking hyphen: « J‑12 » never wraps


# ── races (Santé reads them too) ────────────────────────────────────────────

def race_day(route) -> date | None:
    try:
        return date.fromisoformat(str(route.race_date)[:10]) if route is not None and route.race_date else None
    except (TypeError, ValueError):
        return None


# what the readers of load_races use: never the course, plan or weather JSON (≈ 1 MB per 100-mile course);
# a page that needs those has its own route fully loaded (the identity map keeps it whole)
RACE_COLUMNS = (Route.id, Route.user_id, Route.name, Route.race_date, Route.start_hour, Route.start_minute,
                Route.sport_type, Route.total_distance_km, Route.total_elevation_gain, Route.target_time_s,
                Route.result_json)


async def load_races(db: AsyncSession, user_id: int, today: date) -> list[Route]:
    """The dated races of the last 12 months and to come, by date (sports
    hidden right now left out), their light columns only (RACE_COLUMNS)."""
    from app.features import hidden_sports

    q = select(Route).options(load_only(*RACE_COLUMNS)).where(
        Route.user_id == user_id, Route.race_date.is_not(None),
        Route.race_date >= (today - timedelta(days=365)).isoformat())
    if hidden_sports():
        q = q.where(Route.sport_type.notin_(hidden_sports()))
    return [r for r in (await db.execute(q.order_by(Route.race_date))).scalars().all() if race_day(r)]


def all_races(routes, sessions=()) -> list[tuple[date, str]]:
    """[(day, name)] by day: the dated Routes, and the sessions marked as a race
    (Strava workout_type 1, as Activités and Santé's « après la course » read
    them) on a day no Route holds: one per day. What the J-7 → J+7 window and
    the « not after a race » rule of the nights read."""
    out = {race_day(r): r.name for r in routes if race_day(r)}
    for s in sorted(sessions, key=lambda s: s.start):
        if s.workout_type == 1 and s.day not in out:
            out[s.day] = s.name or "course"
    return sorted(out.items())


def sport(route) -> str:
    """foot | bike | tri | other (Route.sport_type, « trail » by default)."""
    s = (getattr(route, "sport_type", None) or "trail").lower()
    if s in ("tri", "triathlon"):
        return "tri"
    if s in ("bike", "velo", "vélo", "cycling", "gravel"):
        return "bike"
    return "foot" if s in ("trail", "run", "road", "route", "ultra", "marathon") else "other"


def expected_s(route) -> int | None:
    """The athlete's objective, else a rough estimate used only to pick the
    carbohydrates (never shown): trail (km + D+/100) at 7 km-effort an hour,
    road at 11 km/h; other sports without an objective: unknown."""
    if getattr(route, "target_time_s", None):
        return int(route.target_time_s)
    km = route.total_distance_km or 0
    if sport(route) != "foot" or km <= 0:
        return None
    trail = (route.total_elevation_gain or 0) >= 10 * km
    hours = (km + (route.total_elevation_gain or 0) / 100) / 7 if trail else km / 11
    return int(round(hours * 3600))


def taper_weeks(rd: date) -> tuple[date, date]:
    """(J-14, the race day): the taper Activités' A1 line names."""
    return rd - timedelta(days=TAPER_DAYS), rd


def taper_days(m: date, rd: date) -> tuple[int, int]:
    """(days before J-14, days in J-14 → J-1) of the week of Monday `m`, the race day and after left out."""
    lo, nxt = rd - timedelta(days=TAPER_DAYS), m + timedelta(days=7)
    return max(0, (min(nxt, lo) - m).days), max(0, (min(nxt, rd) - max(m, lo)).days)


# ── formats ─────────────────────────────────────────────────────────────────

def j_label(k: int) -> str:
    """-12 → « J‑12 », 0 → « J0 », 3 → « J+3 »."""
    return f"J{NBH}{-k}" if k < 0 else "J0" if k == 0 else f"J+{k}"


def fr(text: str | None) -> str | None:
    """French typography for a printed sentence: a no-break space before « : »
    and « % », « J‑2 » never split at its hyphen."""
    if not text:
        return text
    return text.replace(" : ", "\u00a0: ").replace(" %", f"{NNBSP}%").replace("J-", f"J{NBH}")


def week_label(m: date) -> str:
    return f"sem. du {m.day} {MOIS[m.month - 1]}"


def range_hm(lo: float, hi: float) -> str:
    """Minutes → « 3h40–5h20 » (to 5 min)."""
    return f"{hm(round(lo / 5) * 5)}–{hm(round(hi / 5) * 5)}"


def clock(m: int) -> str:
    """Minutes after midnight → « 06:00 »."""
    m %= 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _to(x: float, step: int) -> int:
    return int(round(x / step)) * step


def activities_href(m: date, this_monday: date) -> str:
    """The week on Activités (6 weeks a page, newest first)."""
    page = (this_monday - m).days // 7 // 6 + 1
    return f"/activities?page={page}#week-{m.isoformat()}"


# ── Affûtage ────────────────────────────────────────────────────────────────

def taper(sessions: list[st.Session], route, rd: date, today: date, now: datetime,
          race_days: frozenset = frozenset()) -> dict | None:
    """Weeks S-6 → S0 as bars against the taper band; None without a session
    in those weeks nor a base. The base leaves races out (a race week is no
    training volume to taper from, H); the bars are Activités' totals."""
    race_monday = rd - timedelta(days=rd.weekday())
    mondays = [race_monday - timedelta(weeks=CHART_WEEKS - 1 - i) for i in range(CHART_WEEKS)]
    this_monday = st.monday(now).date()
    by: dict[date, list] = defaultdict(list)
    for s in sessions:
        if s.day < rd:  # S0 without the race (nor anything after it)
            by[st.monday(s.start).date()].append(s)
    taper_from = rd - timedelta(days=TAPER_DAYS)
    share = {m: taper_days(m, rd) for m in mondays}  # (days before J-14, days in J-14 → J-1)
    end = min(next(m for m in mondays if share[m][1]), this_monday)
    four = [sum(s.minutes for s in by.get(end - timedelta(weeks=k), [])
                if s.workout_type != 1 and s.day not in race_days) for k in range(1, 5)]
    base = statistics.fmean(four) if any(four) else None
    done = [sum(s.minutes for s in by.get(m, [])) if m <= this_monday else None for m in mondays]
    if base is None and not any(done):
        return None
    # a taper week's target: its days before J-14 at the base rate, its taper days in the band (1/7 a day)
    target = {m: (base * (pre + TAPER_SHARE[0] * k) / 7, base * (pre + TAPER_SHARE[1] * k) / 7)
              for m, (pre, k) in share.items() if k} if base else {}

    n = len(mondays)
    xs = viz.slot_x(n)
    bw = round(min(18, X1 / n * 0.62), 1)
    vmax = max([v or 0 for v in done] + [hi for _, hi in target.values()] + [base or 0, 60]) * 1.08
    y = viz.scale(0, vmax, 14, 132)
    base_weeks = {end - timedelta(weeks=k) for k in range(1, 5)} if base else set()
    cols, r, a, h, longs = [], [], [], [], []
    for i, m in enumerate(mondays):
        v, t = done[i], target.get(m)
        k = CHART_WEEKS - 1 - i
        col = {"i": i, "x": round(xs[i] - bw / 2, 1), "w": bw, "cx": xs[i], "cur": m == this_monday,
               "label": "S0" if k == 0 else f"S{NBH}{k}"}
        if v:
            col.update(y=y(v), h=round(132 - y(v), 1))
        if t:
            col["tgt"] = {"x": round(xs[i] - bw / 2 - 3, 1), "w": round(bw + 6, 1), "y": y(t[1]),
                          "h": max(round(y(t[0]) - y(t[1]), 1), 2.0)}
        cols.append(col)
        ss = by.get(m, [])
        big = max((s for s in ss if s.minutes >= LONG_OUTING_MIN or s.dplus >= LONG_OUTING_DPLUS),
                  key=lambda s: s.minutes, default=None)
        if big:
            longs.append(m)
        ctx = []
        if k == 0:
            ctx.append("sans la course")
        if t:
            ctx.append(f"cible {range_hm(*t)}" + (f" · {share[m][1]} jours d'affûtage" if share[m][1] < 7 else ""))
        elif m in base_weeks:
            ctx.append("dans ta base")
        if big:
            ctx.append(f"{viz.GLYPH['long']} sortie de {hm(big.minutes)}")
        head = week_label(m) + (" · en cours" if m == this_monday else "")
        value = "à venir" if v is None else hm(v) if v else "0 min"
        r.append([head, value, " · ".join(ctx)])
        spoken = f"Semaine du {d_long(m)}" + (", en cours" if m == this_monday else "") + " : "
        spoken += "à venir" if v is None else hm_long(v) if v else "rien"
        if t:
            spoken += f", cible {hm_long(round(t[0] / 5) * 5)} à {hm_long(round(t[1] / 5) * 5)}"
            spoken += f", dont {share[m][1]} jours d'affûtage" if share[m][1] < 7 else ""
        a.append(spoken + ("" if not big else f", une sortie de {hm_long(big.minutes)}"))
        h.append({"href": activities_href(m, this_monday), "label": "Voir dans Activités ›"} if ss else None)
    sel = next((i for i, m in enumerate(mondays) if m == this_monday), n - 1 if this_monday > mondays[-1] else 0)
    ev = viz.events(mondays, xs, longs=longs)
    ultra = (route.total_distance_km or 0) > 40  # no taper study beyond 40 km (Bosquet 2007; Wang 2023)
    summary = ("Heures par semaine jusqu'à la course, et la cible des 2 semaines d'affûtage : 41 à 60 % de volume en "
               "moins que ta base." + (" Au-delà de 40 km, aucune étude : c'est une extrapolation." if ultra else ""))
    out = {"n": n, "W": viz.W, "H": 168, "X1": X1, "cols": cols, "base": 132,
           "base_y": y(base) if base else None, **ev, "summary": summary,
           **viz._data(xs, [], mondays, r, a, h=h, sel=sel)}
    out["sentence"] = fr(taper_sentence(sessions, rd, today, now, target, taper_from))
    # resting readout: this week's hours are Activités' (its week heading prints them)
    back = sel  # this week (the latest one lived)
    if base:
        t = (base * TAPER_SHARE[0], base * TAPER_SHARE[1])  # a full week of the taper
        out = viz.rest(out, [f"S{NBH}6 → S0", f"base : {hm(round(base / 5) * 5)} par semaine",
                             f"cible J{NBH}14 → J{NBH}1 : {range_hm(*t)} par semaine"],
                       f"Affûtage, 7 semaines jusqu'à la course : ta base {hm_long(round(base / 5) * 5)} par semaine, cible "
                       f"de J-14 à J-1 {hm_long(round(t[0] / 5) * 5)} à {hm_long(round(t[1] / 5) * 5)} par semaine, au "
                       "prorata des jours d'une semaine entamée. Choisis une semaine pour ses heures.", back=back)
    else:
        out = viz.rest(out, [f"S{NBH}6 → S0", "heures par semaine", ""],
                       "Affûtage, 7 semaines jusqu'à la course, heures par semaine. Choisis une semaine pour ses heures.",
                       back=back)
    return out


def taper_sentence(sessions, rd: date, today: date, now: datetime, target: dict, taper_from: date) -> str | None:
    """One sentence (≤ 90 characters), or none."""
    days = (rd - today).days
    if days <= 0:
        return None
    if today < taper_from:
        return (f"Affûtage dès le {viz.d_short(taper_from)} : 41 à 60 % de volume en moins, même intensité."
                if days <= 28 else None)
    this_monday = st.monday(now).date()
    t = target.get(this_monday)
    so_far = sum(s.minutes for s in sessions if st.monday(s.start).date() == this_monday and s.day < rd)
    if t and so_far > t[1]:
        return "Déjà au-dessus de ta cible cette semaine : lève le pied, garde l'intensité."
    return "Moins de volume, même intensité : c'est elle qui entretient ta forme."


# ── Tes nuits, J-14 → J-1 ───────────────────────────────────────────────────

AMOUNT = (16, 120)


def night_bars(nights: dict, rd: date, today: date) -> dict:
    """24-h sleep J-14 → J-1 (night solid, naps stacked and hatched), the usual
    + 30–60 min band from J-7 to J-2 (the banking week) when a usual exists.
    {"empty": True} when the watch never measured a night."""
    if not any(n.asleep is not None or n.nap_min for n in nights.values()):
        return {"empty": True}
    days = [rd - timedelta(days=NIGHT_DAYS - i) for i in range(NIGHT_DAYS)]
    b = nt.band(nights, "tst24", days[0])
    usual = b["center"] if b else None
    goal = (usual + EXTEND[0], usual + EXTEND[1]) if usual else None
    prov = " (provisoire)" if b and b["provisional"] else ""  # a usual from 7 to 13 days (H)
    n = len(days)
    xs = viz.slot_x(n)
    slot = X1 / n
    bw = round(min(14, slot * 0.62), 1)
    seen = [d for d in days if d in nights and d <= today]
    nap_of = {d: nt.bar_nap_min(nights, d) for d in seen}  # the naps of the 24 h before each wake, as Santé's bars
    vals = [nt.day_tst24(nights, d) or nap_of[d] for d in seen] + [goal[1] if goal else 0, 8 * 60]
    y = viz.scale(0, max(vals) * 1.05, AMOUNT[0], AMOUNT[1])
    cols, r, a = [], [], []
    for i, d in enumerate(days):
        k = (d - rd).days
        night = nights.get(d) if d <= today else None
        col = {"i": i, "x": round(xs[i] - bw / 2, 1), "w": bw, "cx": xs[i]}
        main, nap = (night.asleep, nap_of[d]) if night else (None, None)
        if d > today:
            col["future"] = True
        elif main is not None:
            col["night"] = {"y": y(main), "h": round(AMOUNT[1] - y(main), 1)}
            if nap:
                col["nap"] = {"y": y(main + nap), "h": round(y(main) - y(main + nap), 1)}
        elif nap:  # a nap-only day: the nap alone (no 24-h total)
            col["nap"] = {"y": y(nap), "h": round(AMOUNT[1] - y(nap), 1)}
        else:
            col["miss"] = True
        cols.append(col)
        ctx = [j_label(k)]
        banked = goal and -BANK_DAYS <= k < -1  # the target: J-7 → J-2, never the race eve
        if k == -1:
            ctx.append("veille de course")
        elif banked:
            ctx.append(f"cible {range_hm(*goal)}{prov}")
        if night:
            ctx += [f"{viz.GLYPH['tag']} {w}" for w in nt.tag_words(night.tags)]
        if d > today:
            r.append([viz.night_label(d), "à venir", " · ".join(ctx)])
            a.append(f"{viz.night_label(d)}, {j_label(k)} : à venir")
            continue
        if not night:
            ctx.append("pas de montre cette nuit")
        read, said = (viz.sleep_readout(main, nap), viz.sleep_spoken(main, nap)) if night else ("—", "pas de mesure")
        if night and main is None and not nap and night.nap_min:  # its nap is on the next morning's bar (each nap once)
            read = f"Sieste {viz.hm(night.nap_min)} · comptée le lendemain"
            said = f"sieste {hm_long(night.nap_min)}, comptée le lendemain"
        r.append([viz.night_label(d), read, " · ".join(ctx)])
        spoken = f"{viz.night_label(d)}, {j_label(k)} : " + said
        if banked:
            spoken += f", cible {hm_long(round(goal[0] / 5) * 5)} à {hm_long(round(goal[1] / 5) * 5)}"
        a.append(spoken)
    past = [i for i, d in enumerate(days) if d <= today]
    sel = past[-1] if past else 0
    measured = sum(1 for d in days if d in nights and d <= today and nights[d].asleep is not None)
    out = {"n": n, "W": viz.W, "H": 142, "X1": X1, "cols": cols, "amount": AMOUNT,
           "ticks": [{"y": y(m), "label": f"{m // 60}{NNBSP}h"} for m in range(240, int(max(vals) * 1.05) + 1, 120)],
           "goal": ({"x": round(slot * (n - BANK_DAYS), 1), "w": round(slot * (BANK_DAYS - 1), 1), "y": y(goal[1]),
                     "h": round(y(goal[0]) - y(goal[1]), 1)} if goal else None),
           "xt": [{"x": xs[i], "label": j_label((d - rd).days)} for i, d in enumerate(days) if (n - 1 - i) % 3 == 0],
           "summary": (f"Sommeil sur 24 heures de J-14 à J-1 : {measured} nuit{'s' if measured > 1 else ''} mesurée"
                       f"{'s' if measured > 1 else ''} sur {len(past)}"
                       + (f", cible {hm_long(round(goal[0] / 5) * 5)} à {hm_long(round(goal[1] / 5) * 5)} par jour"
                          " de J-7 à J-2" if goal else "")),
           **viz._data(xs, [], days, r, a, sel=sel)}
    # resting readout: the last night's total is Santé › Sommeil's (printed once, there)
    if goal:
        return viz.rest(out, [f"J{NBH}14 → J{NBH}1", f"cible {range_hm(*goal)} par jour",
                              "normale provisoire" if prov else ""],
                        f"Sommeil sur 24 heures de J-14 à J-1 : cible {hm_long(round(goal[0] / 5) * 5)} à "
                        f"{hm_long(round(goal[1] / 5) * 5)} par jour de J-7 à J-2, siestes comprises. Choisis une "
                        "nuit pour la sienne.", back=sel)
    return viz.rest(out, [f"J{NBH}14 → J{NBH}1", "sommeil sur 24 h", ""],
                    "Sommeil sur 24 heures de J-14 à J-1, siestes comprises. Choisis une nuit pour la sienne.", back=sel)


# ── Semaine de course : manger ──────────────────────────────────────────────

def food_plan(route, exp_s: int | None, weight_kg: float | None) -> dict | None:
    """Carbohydrate loading J-2 and J-1, breakfast and fluid, for races of 90
    min and more."""
    if exp_s is None:
        return None
    if exp_s < CARBS_S:
        return {"short": fr("Pas besoin de surcharge : mange normalement, petit-déj 2 à 3 h avant.")}
    w = weight_kg if weight_kg and weight_kg > 0 else None
    out = {"short": None, "weight_link": None}
    if w:
        out["load"] = (f"Recharge J-2 et J-1 : 10 à 12 g de glucides par kg et par jour, soit {_to(10 * w, 10)} à "
                       f"{_to(12 * w, 10)} g pour tes {num(w)} kg. Réduis fibres et graisses la veille.")
    else:
        out["load"] = ("Recharge J-2 et J-1 : 10 à 12 g de glucides par kg et par jour. Réduis fibres et graisses la "
                       "veille.")
        out["weight_link"] = {"text": "Ajoute ton poids dans Réglages pour un chiffre en grammes ›",
                              "href": "/settings"}
    out["equiv"] = "100 g de glucides ≈ 130 g de pâtes sèches."
    out["test"] = "Teste-le à l'entraînement, avant une sortie longue, jamais pour la première fois avant la course."
    start = route.start_hour * 60 + (route.start_minute or 0) if route.start_hour is not None else None
    at = f" (vers {clock(start - 180)})" if start is not None else ""
    out["breakfast"] = (f"Petit-déjeuner 3 h avant le départ{at} : {_to(w, 5)} à {_to(2 * w, 5)} g de glucides "
                        "(1 à 2 g/kg), ce que tu as déjà testé." if w else
                        f"Petit-déjeuner 3 h avant le départ{at} : 1 à 2 g de glucides par kg, ce que tu as déjà "
                        "testé.")
    out["fluid"] = (f"Bois {_to(5 * w, 50)} à {_to(7 * w, 50)} ml (5–7 ml/kg) au moins 4 h avant le départ." if w
                    else "Bois 5 à 7 ml par kg au moins 4 h avant le départ.")
    return {k: fr(v) if isinstance(v, str) else v for k, v in out.items()}


def hot(route) -> bool:
    """The saved forecast (or climate estimate) says hot: heat factor ≥ 1.06 (H)."""
    w = getattr(route, "weather_json", None) or {}
    try:
        return float(w.get("heat_factor") or 1.0) >= HOT_FACTOR or float(w.get("temperature_c") or 0) >= 25
    except (TypeError, ValueError):
        return False


# ── the section ─────────────────────────────────────────────────────────────

async def _nights(db: AsyncSession, user_id: int, today: date, lo: date, sessions, races) -> dict:
    """The nights from `lo` to today, tagged as Santé tags them (nights.load_nights: « sortie intense le soir »
    from the laps or splits too, the 3 nights after an effort of 6 h or more), plus the check-in's « alcool » and
    « malade » this page keeps; the alert never fires on the nights after a race (`races`)."""
    rows = await nt.read_rows(db, user_id, lo, today)
    feel = {d: feel_of(v, det) for d, (v, det, _) in rows.get("feel", {}).items()}
    nights = nt.build_nights(rows, today)
    rest = st.hr_rest({d: n.hr for d, n in nights.items() if n.hr}, {}, today)
    await nt.load_segments(db, nights, sessions)
    nt.tag_nights(nights, sessions, feel, rest, st.hr_max(sessions, today),
                  await nt.day_altitudes(db, user_id, lo, today))
    nt.tag_efforts(nights, nt.anchor_efforts(nights, st.efforts(sessions)))
    nt.tag_alerts(nights, today, races)
    return nights


async def prep_context(db: AsyncSession, user, route, now: datetime | None = None) -> dict | None:
    """What partials/race_prep.html draws for this race, or None (no date,
    outside J-42 → J0, or any failure: the race page never blanks for it)."""
    try:
        return await _prep(db, user, route, now)
    except Exception:
        logger.exception("Race preparation failed for route %s", getattr(route, "id", None))
        return None


async def _prep(db: AsyncSession, user, route, now: datetime | None) -> dict | None:
    rd = race_day(route)
    if rd is None:
        return None
    now = now or datetime.now(timezone.utc)
    today = await st.athlete_today(db, user.id, now)
    k = (today - rd).days
    if not -PREP_BEFORE <= k <= 0:  # nothing after the race day: recovery is Santé's (v4.3)
        return None
    sessions = await st.load_sessions(db, user.id, today)
    out = {"route_id": route.id, "k": k, "taper": None, "nights": None, "food": None, "hot": None,
           "bank": -BANK_DAYS <= k < 0, "eve": EVE_LINE if -BANK_DAYS <= k <= 0 else None,
           "title": "Jour J" if k == 0 else f"Affûtage · {j_label(k)}"}
    races = all_races(await load_races(db, user.id, today), sessions)
    out["taper"] = taper(sessions, route, rd, today, now, frozenset(d for d, _ in races))
    if -k <= NIGHT_DAYS:
        nights = await _nights(db, user.id, today, rd - timedelta(days=NIGHT_DAYS + nt.BAND_DAYS + 1),
                               sessions, races)
        out["nights"] = night_bars(nights, rd, today)
    if -k <= CARBS_DAYS:
        out["food"] = food_plan(route, expected_s(route), getattr(user, "weight_kg", None))
    if 1 <= -k <= HOT_DAYS and hot(route):
        out["hot"] = fr("Course chaude : quelques sorties à la chaleur sur 1 à 2 semaines t'y préparent.")
    return out
