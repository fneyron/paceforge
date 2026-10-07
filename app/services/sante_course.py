"""Santé › Course: the next race — countdown, phases, taper, « prêt pour la
distance ? », race-day freshness, race week (sleep and carbohydrates) — or the
recovery after the last one.

Science (Santé research notes; coaching repères are labelled as such):
- taper 8–14 days, volume −41 to −60 %, intensity and frequency kept (Bosquet
  2007): 14 days when the race lasts 3 h or more (or is unknown), 10 below;
  races of 10 h and more add an S-2 week at 80–90 % (repère). S-1 60–75 % of
  the base, S0 40–50 % without the race; base = mean hours of the 4 complete
  weeks before the taper starts (the last 4 before it has);
- freshness on race day: CTL/ATL run forward day by day with the mid targets
  (5 days a week, at the athlete's recent load per hour), read on the fatigue
  bands (TSB +5 to +25 % of CTL, Friel). A scenario, never a prediction;
- readiness repères: longest outing 40–50 % of the race (2h30–6 h), best
  weekly D+ 70 % of the race's (trail), 3 outings of 3 h in 8 weeks (races of
  6 h and more);
- carbohydrate loading 10–12 g/kg/day J-2 and J-1 for races over 90 min
  (Burke 2011; ACSM/AND/DC 2016), breakfast 1–2 g/kg 3 h before, 5–7 ml/kg
  of fluid at least 4 h before (ACSM);
- race-week sleep: 9 h from J-7; an early start moves bed and wake together,
  at most 60 min a day.
Weeks are Activités' weeks (sante_training.weeks), so a bar is that week's total.
"""
import math
import statistics
from datetime import date, datetime, time, timedelta, timezone

from app.services.sante import (
    EARLY_START_MIN,
    FALL_ASLEEP_MIN,
    RACE_NEED_MIN,
    RACE_WAKE_BEFORE_START,
    m_clock,
)
from app.services.sante_today import (
    FATIGUE_ZONES,
    WEEKDAYS,
    WEEKDAYS_LONG,
    dplus_txt,
    gauge,
    hm,
    num,
    signed,
)
from app.services.sante_training import (
    ATL_DAYS,
    BIKE,
    CTL_DAYS,
    FOOT,
    Session,
    fatigue_band,
    fatigue_pct,
    weeks,
)

MONTHS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
TAPER_LONG, TAPER_SHORT = 14, 10
LONG_S, ULTRA_S, VERY_LONG_S, CARBS_S = 3 * 3600, 6 * 3600, 10 * 3600, 90 * 60
TARGETS = {2: (0.80, 0.90), 1: (0.60, 0.75), 0: (0.40, 0.50)}  # weeks before the race → share of the base
CHART_DAYS = 35  # the taper chart opens at J-35
WEEK_OPEN_DAYS, WEEK_FIRST_DAYS = 10, 7  # race week: open at J-10, above the taper at J-7
TIMELINE_DAYS = 63  # the phase timeline spans at most 12 weeks (today − 14 d → race + 7 d)
TL_PHONE_PX = 326  # the timeline's width on a 358 px phone: segment labels are fitted to it
RECOVERY_DAYS, SHORT_RECOVERY_DAYS = 14, 7
REST_DAYS = (0, 4)  # the projection trains 5 days a week: Monday and Friday off
LONG_RUN_MIN, LONG_RUN_MAX = 150, 360
GENERIC_WAKE = 13 * 60  # 07:00 in minutes after 18:00 (no measured nights)
EARLIEST_BED = 2 * 60  # 20:00: earlier, sleep hardly comes (the last night is then a bit shorter)
EVE = ("Mal dormir la veille d'une course est courant et pèse peu après une bonne semaine. "
       "Au lit à ton heure.")


# ── words and numbers ───────────────────────────────────────────────────────

def _r(x: float) -> int:
    """Half up (Python's round() goes to even)."""
    return int(math.floor(x + 0.5))


def _to(x: float, step: int) -> int:
    return _r(x / step) * step


def _day(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def countdown(days: int) -> str:
    """Mes courses' wording (simulator._route_card): « J-9 » up to 14 days,
    then weeks, then months."""
    return ("aujourd'hui" if days == 0 else "demain" if days == 1 else f"J-{days}" if days <= 14
            else f"dans {round(days / 7)} sem." if days < 63 else f"dans {round(days / 30.4)} mois")


def date_txt(d: date, today: date) -> str:
    """« sam. 1 nov. » (the year when it is not this one)."""
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}" + (f" {d.year}" if d.year != today.year else "")


def _dm(d: date) -> str:  # charts: « 1/11 »
    return f"{d.day}/{d.month}"


def _ddmm(d: date) -> str:  # sentences: « 01/11 »
    return d.strftime("%d/%m")


def _on(d: date, today: date) -> str:
    """« demain », « samedi » within the week, else « le 20/10 »."""
    k = (d - today).days
    return "aujourd'hui" if k == 0 else "demain" if k == 1 else WEEKDAYS_LONG[d.weekday()] if 1 < k < 7 \
        else f"le {_ddmm(d)}"


def bar_h(h: float) -> str:
    """A bar's value: « 9 h » (the page's one chart format)."""
    return "0 h" if h <= 0 else "<1 h" if h < 0.5 else f"{_r(h)} h"


def range_txt(lo: float, hi: float, sep: str = "–") -> str:
    """Hours range: « 6–7 h » (« 35–45 min » for small weeks)."""
    if hi < 2:
        a, b = _to(lo * 60, 5), _to(hi * 60, 5)
        return f"{a} min" if a == b else f"{a}{sep}{b} min"
    a, b = _r(lo), _r(hi)
    return f"{a} h" if a == b else f"{a}{sep}{b} h"


def _m(v: float) -> str:  # « 2 380 m », spaced like dplus_txt
    return dplus_txt(v)[1:]


def _km(v: float) -> str:
    return f"{num(v)} km" if v >= 10 else f"{num(v, 1)} km"


# ── the race ────────────────────────────────────────────────────────────────

def sport(route) -> str:
    """foot | bike | tri | other (Route.sport_type, « trail » by default)."""
    st = (getattr(route, "sport_type", None) or "trail").lower()
    if st in ("tri", "triathlon"):
        return "tri"
    if st in ("bike", "velo", "vélo", "cycling", "gravel"):
        return "bike"
    return "foot" if st in ("trail", "run", "road", "route", "ultra", "marathon") else "other"


def is_trail(route) -> bool:
    """A foot race with 10 m of D+ per km or more."""
    km = route.total_distance_km or 0
    return sport(route) == "foot" and km > 0 and (route.total_elevation_gain or 0) >= 10 * km


def expected_s(route) -> tuple[int | None, bool]:
    """(seconds, estimated): the athlete's objective (target_time_s), else a
    rough estimate used only to pick the taper and the carbohydrates — never
    shown as a goal: trail (km + D+/100) at 7 km-effort an hour, road at
    11 km/h; other sports without an objective: unknown."""
    if getattr(route, "target_time_s", None):
        return int(route.target_time_s), False
    km = route.total_distance_km or 0
    if sport(route) != "foot" or km <= 0:
        return None, False
    hours = (km + (route.total_elevation_gain or 0) / 100) / 7 if is_trail(route) else km / 11
    return int(round(hours * 3600)), True



def race_duration(route, sessions=()) -> tuple[int | None, bool]:
    """(seconds, known) of a race already run: its result, else its session
    (that day, marked as a race or half the distance at least), else its
    objective; else the estimate (known=False: never printed as a time)."""
    res = getattr(route, "result_json", None) or {}
    if res.get("total_actual_s"):
        return int(res["total_actual_s"]), True
    rd = _day(getattr(route, "race_date", None))
    km = getattr(route, "total_distance_km", None) or 0
    # the race's own session: that day, covering most of the distance (not a warm-up)
    same = [x.minutes for x in sessions if rd and x.day == rd and (x.workout_type == 1 or x.km >= 0.5 * km)]
    if same:
        return int(max(same) * 60), True
    if getattr(route, "target_time_s", None):
        return int(route.target_time_s), True
    return expected_s(route)[0], False

def taper_days(exp_s: int | None) -> int:
    return TAPER_SHORT if exp_s is not None and exp_s < LONG_S else TAPER_LONG


def taper_start(race_day: date, exp_s: int | None) -> date:
    """The first day of the taper; races of 10 h and more start it with S-2."""
    start = race_day - timedelta(days=taper_days(exp_s))
    if exp_s is not None and exp_s >= VERY_LONG_S:
        start = min(start, race_day - timedelta(days=race_day.weekday() + 14))
    return start


def targets(base_h: float | None, exp_s: int | None) -> dict[int, tuple[float, float]]:
    """{weeks before the race: (low, high)} in hours (in shares of the base
    when the base is unknown)."""
    b = 1.0 if base_h is None else base_h
    return {k: (lo * b, hi * b) for k, (lo, hi) in TARGETS.items()
            if k < 2 or (exp_s is not None and exp_s >= VERY_LONG_S)}


def base_hours(wk: list[dict], start: date) -> float | None:
    """Mean hours of the 4 complete weeks before the taper's week (before
    this week while the taper is still ahead)."""
    end = min(start - timedelta(days=start.weekday()), wk[-1]["monday"])
    four = [w for w in wk if end - timedelta(weeks=4) <= w["monday"] < end]
    if not four or not any(w["minutes"] for w in four):
        return None
    return statistics.fmean(w["minutes"] for w in four) / 60


def _now(today: date, now: datetime | None = None) -> datetime:
    """The server's now, for the weeks: Activités' UTC Mondays (noon of the
    athlete's day when not given)."""
    return now or datetime.combine(today, time(12), tzinfo=timezone.utc)


# ── phase timeline (HTML, in %) ─────────────────────────────────────────────

def _edge(x: float, label: str) -> dict:
    return {"x": x, "label": label, "align": "start" if x < 8 else "end" if x > 92 else "mid"}


def timeline(today: date, start: date, race_day: date) -> dict:
    """Construction / Affûtage / race flag / Récup 7 days over today − 14 d →
    race + 7 d; a sentence instead when the race is beyond 12 weeks."""
    days = (race_day - today).days
    if days > TIMELINE_DAYS:
        return {"text": f"Affûtage {countdown((start - today).days)}, le {_ddmm(start)}."}
    lo, hi = today - timedelta(days=14), race_day + timedelta(days=7)
    span = (hi - lo).days

    def x(d: date) -> float:
        return round(min(100.0, max(0.0, (d - lo).days / span * 100)), 1)

    ts = max(start, lo)
    segs = []
    if ts > lo:
        segs.append({"cls": "build", "label": "Construction", "short": "Constr.", "l": 0.0, "w": x(ts)})
    segs.append({"cls": "taper", "label": "Affûtage", "short": None, "l": x(ts), "w": round(x(race_day) - x(ts), 1)})
    segs.append({"cls": "rec", "label": "Récup", "short": None, "l": x(race_day), "w": round(100 - x(race_day), 1)})
    now = x(today)
    for sg in segs:  # a label only where it fits, never under today's marker
        sg["tx"] = sg["text"] = None
        for text in filter(None, (sg["label"], sg["short"])):
            need = (6.6 * len(text) + 12) / TL_PHONE_PX * 100
            at = sg["l"]
            if at - 1 <= now <= at + need:
                at = now + 1
            if at + need <= sg["l"] + sg["w"]:
                sg["tx"], sg["text"] = round(at, 1), text
                break
    dates = ([_edge(x(start), _dm(start))] if start > lo else []) + [_edge(x(race_day), _dm(race_day))]
    label = (f"Construction jusqu'au {_dm(start)}, affûtage jusqu'à la course le {_dm(race_day)}, puis 7 jours de "
             "récupération." if start > lo else f"Affûtage jusqu'à la course le {_dm(race_day)}, puis 7 jours de "
             "récupération.")
    return {"segs": segs, "flag": x(race_day), "today": x(today), "dates": dates, "label": label}


# ── taper chart (SVG, fixed 360 × 170, never stretched) ─────────────────────

W, H, PAD, Y0, TOP, ROW_Y, X_Y = 360, 170, 2, 146, 30, 12, 162
MAX_COLS, MAX_PAST = 10, 6


def taper_chart(wk: list[dict], race_day: date, tg: dict, base_h: float) -> dict:
    """The last complete weeks (bars linking to Activités), this week as an
    outline, the weeks to come with their hatched target range, the base as a
    dashed line. Every value printed at 11 px."""
    race_monday = race_day - timedelta(days=race_day.weekday())
    this_monday = wk[-1]["monday"]
    cur_k = (race_monday - this_monday).days // 7
    n_past = max(0, min(MAX_PAST, MAX_COLS - 1 - cur_k))
    cols = [{"k": (race_monday - w["monday"]).days // 7, "monday": w["monday"], "h": w["minutes"] / 60,
             "minutes": w["minutes"], "href": w["href"], "current": w["current"]} for w in wk[len(wk) - 1 - n_past:]]
    cols += [{"k": cur_k - j, "monday": this_monday + timedelta(weeks=j), "h": None, "href": None, "current": False}
             for j in range(1, cur_k + 1)]
    vmax = max([c["h"] or 0 for c in cols] + [hi for _, hi in tg.values()] + [base_h]) or 1.0

    def y(v: float) -> float:
        return round(Y0 - v / vmax * (Y0 - TOP), 1)

    colw = (W - 2 * PAD) / len(cols)
    bw = round(min(24.0, colw * 0.6), 1)
    out = []
    for i, c in enumerate(cols):
        cx = round(PAD + colw * (i + 0.5), 1)
        col = {"cx": cx, "x": round(cx - bw / 2, 1), "w": bw, "k": c["k"], "current": c["current"],
               "xlabel": "S0" if c["k"] == 0 else f"S-{c['k']}", "strong": c["current"] or c["k"] == 0,
               "href": c["href"], "bar": None, "target": None}
        t = tg.get(c["k"])
        if t:
            col["target"] = {"x": round(cx - bw / 2 - 3, 1), "w": round(bw + 6, 1), "y": y(t[1]),
                             "h": max(round(y(t[0]) - y(t[1]), 1), 2.0), "label": range_txt(*t)}
        if c["h"] is not None:
            top = y(c["h"])
            cls = "pf-ct-warn" if t and c["h"] > t[1] * 1.05 else "pf-ct-ink" if c["current"] else ""
            col["bar"] = {"y": top, "h": round(Y0 - top, 1), "ly": round(top - 4, 1), "label": bar_h(c["h"]),
                          "cls": cls, "sr": f"Semaine du {_ddmm(c['monday'])}"
                          + (" (en cours)" if c["current"] else "") + f" : {hm(c['minutes'])}"}
        out.append(col)

    by = y(base_h)
    label = f"ta base {bar_h(base_h)}"
    lw = 6 * len(label)  # ≈ px at 11 px

    def clash(x1: float, x2: float) -> bool:
        return any(c["bar"] and x1 - bw <= c["cx"] <= x2 + bw and abs(c["bar"]["ly"] - (by - 4)) < 12 for c in out)

    right = not clash(W - PAD - lw, W - PAD) or clash(PAD, PAD + lw)
    return {"cols": out, "w": W, "h": H, "y0": Y0, "row_y": ROW_Y, "x_y": X_Y, "pad": PAD,
            "base_y": by, "base_label": label, "base_x": W - PAD if right else PAD,
            "base_anchor": "end" if right else "start"}


def local_week_h(sessions: list[Session], today: date, back: int = 0) -> float:
    """Hours of the athlete's own week (local Monday to Sunday), `back` weeks
    ago — this week so far for back=0. The chart's weeks are Activités' (UTC)."""
    monday = today - timedelta(days=today.weekday() + 7 * back)
    end = today if back == 0 else monday + timedelta(days=6)
    return sum(s.minutes for s in sessions if monday <= s.day <= end) / 60


def _taper_status(today: date, days: int, start: date, race_day: date, tg: dict, wk: list[dict] | None,
                  base_h: float | None, exp_s: int | None, sessions: list[Session] = ()) -> str | None:
    """One sentence, first match wins (no number printed on the chart)."""
    if days <= 1:
        return None
    race_monday = race_day - timedelta(days=race_day.weekday())
    this_monday = today - timedelta(days=today.weekday())
    cur_k = (race_monday - this_monday).days // 7
    ultra = exp_s is not None and exp_s >= ULTRA_S
    if today >= start:
        t = tg.get(cur_k)
        if base_h is None or not wk:
            return "Affûtage en cours : moins de volume, garde un peu d'intensité."
        if not t:
            return "Ton affûtage commence : allège dès maintenant, garde un peu d'intensité."
        so_far, elapsed = local_week_h(sessions, today), today.weekday() + 1
        left = min(7 - elapsed, days - 1) if cur_k == 0 else 7 - elapsed
        if so_far > t[1] or (elapsed >= 3 and so_far * 7 / elapsed > t[1] * 1.15):
            more = f", avec {left} jour{'s' if left > 1 else ''} à venir" if left > 0 else ""
            return f"Déjà au-dessus de ta cible cette semaine{more} : lève le pied."
        prev = tg.get(cur_k + 1)
        if prev:
            ph = local_week_h(sessions, today, 1)
            if ph > prev[1] * 1.05:
                return "La semaine dernière a dépassé ta cible : allège davantage cette semaine."
            if ph < prev[0]:
                return "Moins, c'est bien à ce stade : garde juste un peu d'intensité."
        return "Dans ta cible : garde un peu d'intensité, c'est elle qui entretient ta forme."
    left_w = (start - today).days // 7
    if days > 21 and left_w >= 1:
        s = (f"Encore {left_w} semaine{'s' if left_w > 1 else ''} de construction : c'est le moment de ta plus "
             "grosse sortie longue (pas plus de 10 % au-dessus de la précédente).")
        if ultra:
            last = race_day - timedelta(days=21)
            s += f" Pour un ultra, place-la au plus tard le {_ddmm(last)} (3 semaines avant, repère)."
        return s
    if ultra and today > race_day - timedelta(days=21):
        return (f"Ta dernière grosse sortie longue est derrière toi (3 semaines avant un ultra, repère) : semaine "
                f"normale, l'affûtage commence {_on(start, today)}.")
    return f"Dernière semaine de construction : l'affûtage commence {_on(start, today)}."


def _bullets(today: date, days: int, race_day: date, tg: dict, base_h: float | None, amounts: bool,
             fam: str) -> list[str]:
    """2–3 lines: what each coming taper week holds (hours only when no chart
    prints them)."""
    if days == 0:
        return []
    race_monday = race_day - timedelta(days=race_day.weekday())
    cur_k = (race_monday - (today - timedelta(days=today.weekday()))).days // 7
    easy = ("deux footings avec 4 accélérations" if fam == "foot"
            else "deux séances courtes avec quelques accélérations")

    def amount(k: int) -> str:
        lo, hi = tg[k]
        if base_h is None:
            return f"{_r(TARGETS[k][0] * 100)} à {_r(TARGETS[k][1] * 100)} % de ton volume habituel"
        return range_txt(lo, hi, " à ")

    out = []
    for k in sorted(tg, reverse=True):
        if k > cur_k:
            continue
        what = {2: "un peu moins que d'habitude, une sortie longue plus courte (repère)",
                1: "garde une séance rythmée courte (ex. 3 × 6 min allure course)",
                0: f"{easy}, repos ou 20 min faciles la veille"}[k]
        if amounts:
            what = amount(k) + (" sans la course" if k == 0 else "") + ", " + what
        head = "Semaine de la course" if k == 0 else f"Semaine du {_ddmm(race_monday - timedelta(weeks=k))}"
        out.append(f"{head} : {what}.")
    return out


# ── prêt pour la distance ? ─────────────────────────────────────────────────

def _pool(sessions: list[Session], fam: str) -> list[Session]:
    if fam == "foot":
        return [s for s in sessions if s.sport in FOOT]
    if fam == "bike":
        return [s for s in sessions if s.sport in BIKE]
    return list(sessions)


def word(v: float, ref: float) -> str:
    """bon (met) / juste (within 15 % below) / court."""
    return "bon" if v >= ref else "juste" if v >= 0.85 * ref else "court"


TONES = {"bon": "ok", "juste": "muted", "court": "warn"}
ADVICE = {"long": "Allonge ta sortie longue, de 10 % au plus d'une fois sur l'autre.",
          "dplus": "Ajoute du dénivelé dans tes sorties longues, surtout en descente.",
          "count": "Place une sortie de 3 h ou plus par semaine d'ici l'affûtage."}


def readiness(route, sessions: list[Session], today: date, exp_s: int | None, in_taper: bool,
              now: datetime | None = None) -> dict:
    """Three coaching repères over 8 weeks, as rows for the shared row macro."""
    fam = sport(route)
    pool = _pool(sessions, fam)
    recent = [s for s in pool if today - timedelta(days=56) < s.day <= today]
    href = f"/simulator/routes/{route.id}"
    goal_link = None if route.target_time_s else {
        "text": "Ajoute ton objectif de temps sur la page de la course pour comparer ta plus longue sortie",
        "href": href}
    if not recent:
        return {"rows": [], "empty": "Il me faut tes sorties des 8 dernières semaines (Strava) pour comparer ta "
                "préparation à la course.", "advice": None, "goal_link": None}
    rows = []
    trail = is_trail(route)
    if route.target_time_s:
        e = route.target_time_s / 60
        lo = min(LONG_RUN_MAX, max(LONG_RUN_MIN, 0.4 * e))
        hi = min(LONG_RUN_MAX, max(lo, 0.5 * e))
        big = max(recent, key=lambda s: s.minutes)
        w = word(big.minutes, lo)
        edges = [(lo, hm(lo))] + ([(hi, hm(hi))] if hi - lo >= 15 else [])
        rows.append({"key": "long", "label": "Plus longue sortie", "window": "8 sem.",
                     "value": hm(big.minutes) + (f" · {dplus_txt(big.dplus)}" if trail and big.dplus >= 50 else ""),
                     "ref": "repère : 40–50 % de ta course" if hi > lo else "repère pour ta course",
                     "word": w, "tone": TONES[w],
                     "gauge": gauge(big.minutes, 0, max(big.minutes, hi) * 1.25, band=(lo, hi), edges=edges),
                     "sub": "Au-delà de 12 h : deux sorties longues sur deux jours plutôt qu'une plus longue."
                     if route.target_time_s >= 12 * 3600 else None})
    if trail:
        wk = weeks(pool, _now(today, now), 9)[:-1]
        best = sorted((w["dplus"] for w in wk), reverse=True)[:4]
        v, ref = statistics.fmean(best), 0.7 * route.total_elevation_gain
        w = word(v, ref)
        rows.append({"key": "dplus", "label": "D+ par semaine", "window": "tes 4 meilleures sur 8", "value": _m(v),
                     "ref": "repère : 70 % du D+ de la course", "word": w, "tone": TONES[w],
                     "gauge": gauge(v, 0, max(v, ref) * 1.25, band=(ref, max(v, ref) * 1.25),
                                    edges=[(0, "0"), (ref, _m(ref))])})
    if exp_s is not None and exp_s >= ULTRA_S:
        n = sum(1 for s in recent if s.minutes >= 180)
        w = word(n, 3)
        rows.append({"key": "count", "label": "Sorties de 3 h et plus", "window": "8 sem.", "value": str(n),
                     "ref": "repère : 3 et plus pour un ultra", "word": w, "tone": TONES[w],
                     "gauge": gauge(n, 0, max(6, n + 1), band=(3, max(6, n + 1)), edges=[(0, "0"), (3, "3")])})
    if in_taper:
        advice = "Trop tard pour construire : tu cours avec ce fond. Mise sur la fraîcheur."
    elif not rows:
        advice = None
    else:
        short = [r["key"] for r in rows if r["word"] == "court"] or [r["key"] for r in rows if r["word"] == "juste"]
        advice = (" ".join(ADVICE[k] for k in short) if short
                  else "Ta préparation couvre la distance : garde le cap jusqu'à l'affûtage.")
    return {"rows": rows, "empty": None, "advice": advice, "goal_link": goal_link}


# ── fraîcheur le jour J (a scenario) ────────────────────────────────────────

def load_per_hour(sessions: list[Session], today: date) -> float | None:
    """The athlete's recent load per hour of training (28 days, else 56)."""
    for days in (28, 56):
        ss = [s for s in sessions if today - timedelta(days=days) < s.day <= today]
        hours = sum(s.minutes for s in ss) / 60
        if hours >= 3:
            return sum(s.load for s in ss) / hours
    return None


def project(ctl: float, atl: float, lph: float, first: date, race_day: date, base_h: float, tg: dict,
            done: dict[date, float] | None = None, which: str = "mid") -> int | None:
    """Fatigue % on the race morning (CTL and ATL at the end of the eve),
    starting from (ctl, atl) at the end of the day before `first`, if the
    athlete trains `base_h` a week until the taper, then the low / mid / high
    target of each taper week — 5 days a week (the 5 days before the race in
    race week) at `lph` load per hour. `done` = {monday: hours already done}."""
    done = done or {}
    race_monday = race_day - timedelta(days=race_day.weekday())
    s0 = set(sorted(race_monday + timedelta(days=i) for i in range(race_day.weekday()))[-5:])
    d = first
    while d < race_day:
        mon = d - timedelta(days=d.weekday())
        k = (race_monday - mon).days // 7
        t = tg.get(k)
        hours = base_h if not t else t[0] if which == "lo" else t[1] if which == "hi" else (t[0] + t[1]) / 2
        train = s0 if k == 0 else {mon + timedelta(days=i) for i in range(7) if i not in REST_DAYS}
        left = [x for x in train if x >= first]
        per_day = min(hours / 5, max(0.0, hours - done.get(mon, 0.0)) / len(left)) if left else 0.0
        load = per_day * lph if d in train else 0.0
        ctl += (load - ctl) / CTL_DAYS
        atl += (load - atl) / ATL_DAYS
        d += timedelta(days=1)
    return fatigue_pct(ctl, atl)


def freshness(tr: dict | None, sessions: list[Session], today: date, race_day: date, base_h: float | None,
              tg: dict, so_far_h: float) -> dict | None:
    days = (race_day - today).days
    if not tr or base_h is None or not 2 <= days <= CHART_DAYS:
        return None
    lph = load_per_hour(sessions, today)
    # nothing logged yet today: start from last evening and plan today too
    trained = any(s.day == today for s in sessions)
    first = today + timedelta(days=1) if trained else today
    series = tr.get("series") or {}
    ctl, atl = series.get(first - timedelta(days=1)) or (tr["ctl"], tr["atl"])
    if not lph or ctl <= 0:
        return None
    done = {today - timedelta(days=today.weekday()): so_far_h}
    pct = project(ctl, atl, lph, first, race_day, base_h, tg, done)
    if pct is None:
        return None
    key, w = fatigue_band(pct)
    if key == "fresh":
        advice = "C'est la zone visée pour une course (repère)."
    elif pct < -25:
        hi = project(ctl, atl, lph, first, race_day, base_h, tg, done, "hi")
        advice = ("Très reposé : vise le haut de tes fourchettes d'affûtage." if hi is not None and hi >= -25
                  else "Très reposé : garde un peu de volume.")
    else:
        lo = project(ctl, atl, lph, first, race_day, base_h, tg, done, "lo")
        advice = ("Encore chargé le jour J : vise le bas de tes fourchettes d'affûtage." if lo is not None and lo < -5
                  else "Encore chargé le jour J : allège davantage.")
    return {"pct": pct, "key": key, "word": w, "value": f"{signed(pct)} %",
            "line": f"si tu suis l'affûtage : {signed(pct)} % ({w})", "advice": advice,
            "gauge": gauge(pct, -40, 60, band=(-25, -5), zones=FATIGUE_ZONES, edges=[(-25, "−25"), (-5, "−5")])}


# ── semaine de course : dormir et manger ────────────────────────────────────

def _down5(m: float) -> int:
    return int(m // 5 * 5)


def _bed(wake: float, awake: float) -> int:
    """In bed early enough for 9 h of sleep before `wake`, not before 20:00."""
    return max(EARLIEST_BED, _down5(wake - RACE_NEED_MIN - awake - FALL_ASLEEP_MIN))


def _up5(m: float) -> int:
    return int(-(-m // 5) * 5)


def sleep_plan(route, race_day: date, today: date, usual: dict | None) -> dict | None:
    """9 h from J-7; for an early start (race wake = start − 2 h, 90 min or
    more before the usual wake) bed and wake move together, at most 60 min a
    day (rounded to 5 min), down to the race morning."""
    days = (race_day - today).days
    if days <= 0:
        return None
    since = (f"à partir de {WEEKDAYS_LONG[(race_day - timedelta(days=7)).weekday()]} (J‑7)" if days > 7
             else "dès ce soir")
    start = route.start_hour * 60 + (route.start_minute or 0) if route.start_hour is not None else None
    race_wake = (start - RACE_WAKE_BEFORE_START - 1080) % 1440 if start is not None and 4 * 60 <= start < 12 * 60 \
        else None
    wake = usual["wake"] if usual else GENERIC_WAKE
    awake = usual["awake"] if usual else 0
    gap = wake - race_wake if race_wake is not None else 0
    early = race_wake is not None and gap >= EARLY_START_MIN
    out = {"lead": None, "early": None, "rows": [], "eve": EVE, "note": None}
    if not usual:
        out["lead"] = f"Vise 9 h par nuit {since} : si tu te lèves à 7 h, au lit vers {m_clock(_bed(GENERIC_WAKE, 0))}."
        if early:
            n_wake = min(7, days)
            shift = min(60, _up5(gap / n_wake))
            first = race_day - timedelta(days=math.ceil(gap / shift) - 1)
            k = (first - today).days
            when = ("demain" if k <= 1 else WEEKDAYS_LONG[first.weekday()] if k < 7
                    else f"{WEEKDAYS_LONG[first.weekday()]} {_ddmm(first)}")
            out["early"] = (f"Le jour J, réveil {m_clock(race_wake)} : avance ton lever de {shift} min par jour "
                            f"dès {when}.")
        out["note"] = "Porte ta montre ces 7 nuits : je te dirai si tu as assez dormi."
        return out
    if not early:
        out["lead"] = (f"Vise 9 h par nuit {since} : au lit vers {m_clock(_bed(wake, awake))}. C'est cette "
                       "semaine de sommeil qui compte, plus que la dernière nuit.")
        return out
    n_wake = min(7, days)
    shift = min(60, _up5(gap / n_wake))
    out["lead"] = "Vise 9 h par nuit : c'est cette semaine de sommeil qui compte, plus que la dernière nuit."
    out["early"] = (f"Départ {m_clock((start - 1080) % 1440)} : réveil {m_clock(race_wake)}, {hm(gap)} plus tôt que "
                    f"d'habitude. Avance coucher et lever d'environ {shift} min par jour.")
    for i in range(1, n_wake + 1):
        evening = race_day - timedelta(days=n_wake - i + 1)
        w = race_wake if i == n_wake else max(race_wake, wake - shift * i)
        out["rows"].append({"day": WEEKDAYS[evening.weekday()], "bed": m_clock(_bed(w, awake)), "wake": m_clock(w)})
    return out


def food_plan(route, exp_s: int | None, weight_kg: float | None) -> dict | None:
    """Carbohydrate loading J-2 and J-1, breakfast, fluid — for races of 90
    min and more; the in-race intake lives on the race page (#nutrition)."""
    if exp_s is None:
        return None
    if exp_s < CARBS_S:
        return {"short": "Pas besoin de surcharge : mange normalement, petit-déj 2 à 3 h avant."}
    w = weight_kg if weight_kg and weight_kg > 0 else None
    href = f"/simulator/routes/{route.id}"
    out = {"short": None, "weight_link": None, "breakfast_link": None}
    if w:
        out["load"] = (f"Recharge J-2 et J-1 : 10 à 12 g de glucides par kg et par jour, soit {_to(10 * w, 10)} à "
                       f"{_to(12 * w, 10)} g pour tes {num(w)} kg. Réduis fibres et graisses la veille.")
    else:
        out["load"] = ("Recharge J-2 et J-1 : 10 à 12 g de glucides par kg et par jour. Réduis fibres et graisses la "
                       "veille.")
        out["weight_link"] = {"text": "Ajoute ton poids dans Réglages pour un chiffre en grammes", "href": "/settings"}
    out["test"] = "Teste-le à l'entraînement, avant une sortie longue, jamais pour la première fois avant la course."
    out["equiv"] = "100 g de glucides ≈ 130 g de pâtes sèches."
    start = route.start_hour * 60 + (route.start_minute or 0) if route.start_hour is not None else None
    at = f" (vers {m_clock((start - 180 - 1080) % 1440)})" if start is not None else ""
    if sport(route) == "tri":
        out["breakfast"] = None
        out["breakfast_link"] = {"text": "Petit-déjeuner : il est dans ton plan de triathlon", "href": href}
    elif w:
        out["breakfast"] = (f"Petit-déjeuner 3 h avant le départ{at} : {_to(w, 5)} à {_to(2 * w, 5)} g de glucides "
                            "(1 à 2 g/kg), ce que tu as déjà testé.")
    else:
        out["breakfast"] = (f"Petit-déjeuner 3 h avant le départ{at} : 1 à 2 g de glucides par kg, ce que tu as déjà "
                            "testé.")
    out["fluid"] = (f"Bois {_to(5 * w, 50)} à {_to(7 * w, 50)} ml (5–7 ml/kg) au moins 4 h avant le départ." if w
                    else "Bois 5 à 7 ml par kg au moins 4 h avant le départ.")
    return out


# ── after the race ──────────────────────────────────────────────────────────

def recovery(route, today: date, sessions=()) -> dict | None:
    """Up to J+14 after a race of 3 h or more (J+7 below): no intensity until
    J+8 (J+11 after 10 h), like Aujourd'hui."""
    if route is None:
        return None
    rd = _day(route.race_date)
    if rd is None:
        return None
    k = (today - rd).days
    dur = race_duration(route, sessions)[0]
    long = dur is None or dur >= LONG_S
    if not 1 <= k <= (RECOVERY_DAYS if long else SHORT_RECOVERY_DAYS):
        return None
    if long:
        free = rd + timedelta(days=(10 if dur is not None and dur >= VERY_LONG_S else 7) + 1)
        # the day-to-day advice (and the date to resume) is Aujourd'hui's
        text = ("Compare ton temps à ton plan dans le débrief, et regarde ta fatigue redescendre dans Entraînement."
                if today < free else
                "Tu peux reprendre l'intensité, progressivement : ta VFC et ta FC au repos mettent parfois 2 semaines "
                "à revenir.")
    else:
        text = "Course courte : 2 à 3 jours faciles suffisent, puis reprends normalement."
    return {"days": k, "title": f"Récupération · J+{k}", "name": route.name, "date": date_txt(rd, today),
            "text": text, "debrief_href": f"/simulator/routes/{route.id}", "fatigue_href": "?vue=entrainement"}


# ── the tab ─────────────────────────────────────────────────────────────────

def race_block(route, sessions: list[Session], tr: dict | None, today: date, weight_kg: float | None,
               usual: dict | None, now: datetime | None = None) -> dict | None:
    rd = _day(route.race_date)
    if rd is None or rd < today:
        return None
    days = (rd - today).days
    exp_s, estimated = expected_s(route)
    fam = sport(route)
    start = taper_start(rd, exp_s)
    wk = weeks(sessions, _now(today, now), 12) if sessions else None
    base_h = base_hours(wk, start) if wk else None
    tg = targets(base_h, exp_s)
    in_taper = today >= start
    phase = "race_week" if days <= WEEK_FIRST_DAYS else "taper" if in_taper else "build"

    facts = [_km(route.total_distance_km or 0)]
    if (route.total_elevation_gain or 0) >= 50:
        facts.append(dplus_txt(route.total_elevation_gain))
    if route.target_time_s:
        facts.append(f"objectif {hm(route.target_time_s / 60)}")
    start_txt = (f" · départ {route.start_hour:02d}:{(route.start_minute or 0):02d}"
                 if route.start_hour is not None else "")

    chart = taper_chart(wk, rd, tg, base_h) if base_h is not None and days <= CHART_DAYS else None
    why = ("course de 10 h et plus" if exp_s is not None and exp_s >= VERY_LONG_S
           else "course de 3 h et plus" if exp_s is not None and exp_s >= LONG_S
           else "course de moins de 3 h" if exp_s is not None else "durée inconnue")
    taper = {
        "days": (rd - start).days, "open": days <= CHART_DAYS,
        "why": why + (" (estimée sans objectif de temps)" if estimated else ""),
        "status": _taper_status(today, days, start, rd, tg, wk, base_h, exp_s, sessions),
        "chart": chart, "bullets": _bullets(today, days, rd, tg, base_h, chart is None, fam),
        "base": hm(base_h * 60) if base_h is not None and chart is None else None,
    }
    so_far = local_week_h(sessions, today)
    ready = readiness(route, sessions, today, exp_s, in_taper, now)
    ready["fresh"] = freshness(tr, sessions, today, rd, base_h, tg, so_far)
    week = {"open": days <= WEEK_OPEN_DAYS, "first": days <= WEEK_FIRST_DAYS,
            "sleep": sleep_plan(route, rd, today, usual), "food": food_plan(route, exp_s, weight_kg),
            "nutrition_href": f"/simulator/routes/{route.id}#nutrition"}
    return {
        "id": route.id, "name": route.name, "href": f"/simulator/routes/{route.id}", "days": days,
        "when": countdown(days), "date": date_txt(rd, today) + start_txt, "facts": " · ".join(facts),
        "estimated": estimated, "phase": phase, "timeline": timeline(today, start, rd), "taper": taper,
        "ready": ready, "week": week,
    }


def course_tab(next_race, last_race, sessions: list[Session], tr: dict | None, today: date,
               weight_kg: float | None, usual: dict | None, now: datetime | None = None) -> dict:
    """Everything the « Course » tab draws (partials/sante_course.html, as
    `course`), and the tab strip's sublabel."""
    race = race_block(next_race, sessions, tr, today, weight_kg, usual, now) if next_race is not None else None
    rec = recovery(last_race, today, sessions)
    if race and (race["days"] <= 14 or not rec):
        state, sublabel = race["phase"], race["when"]
    elif rec:
        state, sublabel = "recovery", f"récup J+{rec['days']}"
    else:
        state, sublabel = "none", None
    return {"state": state, "sublabel": sublabel, "race": race, "recovery": rec}
