"""Santé › Entraînement: how the weeks go, and what follows a big one.

- one sentence first (taper, a long-outing jump, a weekly jump, a third rising
  week, detraining, or steady weeks), from the load of complete weeks;
- 12 weeks as bars (hours, the longest outing inside, a jump in orange), each
  linking to that week on Activités, with the week's sleep and HRV under them
  when the watch measured enough nights;
- the fatigue % day by day on the same x axis, in five named bands;
- « chez toi » links, pre-registered and counted: a weekly jump or a long
  outing → heart rate at easy pace in the days after (Strava only), weekly
  hours → sleep (watch nights). Shown only when the difference is above the
  smallest worthwhile change AND |Welch t| ≥ 2.5 (conservative: several
  hypotheses are tested), with the counts and « pas forcément cause ».
Load-to-injury claims are never made (Impellizzeri 2020); the long-outing
jump uses Frandsen 2025 (> 10 % above the longest of the previous 30 days).
"""
import math
import statistics
from datetime import date, timedelta

from app.services.sante_today import hm, num, of_day, signed
from app.services.sante_training import Session

JUMP = 1.3
SPIKE = 1.10
MONTHS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")

# bars: viewBox 360 × H, plot x 8..352
X0, X1, BAR_W = 8, 352, 20
B_TOP, B_BOT = 24, 160


def _hours_label(minutes: float) -> str:
    return "<1 h" if 0 < minutes < 45 else f"{round(minutes / 60)} h"


def _complete(weeks: list[dict]) -> list[dict]:
    return [w for w in weeks if not w["current"]]


def _jump(weeks: list[dict], i: int) -> float | None:
    """Week i's hours against the mean of the 3 weeks before (None without
    3 previous weeks with sessions) — the hours the bars show."""
    prev = weeks[i - 3:i] if i >= 3 else []
    if len(prev) < 3 or not all(w["count"] for w in prev):
        return None
    mean = statistics.fmean(w["minutes"] for w in prev)
    return weeks[i]["minutes"] / mean if mean > 0 else None


def spike(sessions: list[Session], today: date, days: int = 10,
          race_days: set[date] = frozenset()) -> tuple[Session, float] | None:
    """The longest outing of the last `days` days when it is > 10 % longer
    than the longest of the 30 days before it (90 min at least; a race is
    not a training jump)."""
    recent = [s for s in sessions if s.day > today - timedelta(days=days) and s.minutes >= 90
              and s.day not in race_days]
    big = max(recent, key=lambda s: s.minutes, default=None)
    if not big:
        return None
    before = [s.minutes for s in sessions if big.day - timedelta(days=30) <= s.day < big.day]
    if not before or big.minutes <= SPIKE * max(before):
        return None
    return big, big.minutes / max(before)


def header(weeks: list[dict], sessions: list[Session], tr: dict | None, today: date, taper: str | None,
           quiet: bool = False, race_days: set[date] = frozenset()) -> str:
    """`quiet`: tapering or just after a race — no jump, no detraining."""
    done = _complete(weeks)
    active = [w for w in done if w["count"]]
    if not sessions:
        return "Connecte Strava dans Réglages : tes semaines s'afficheront ici."
    if len(active) < 4:
        return f"Il faut 4 semaines d'activités pour lire tes semaines (tu en as {len(active)})."
    if taper:
        return taper
    if quiet:
        return "Tu récupères de ta course : des semaines plus légères sont normales."
    sp = spike(sessions, today, race_days=race_days)
    if sp:
        s, r = sp
        return (f"Ta sortie {of_day(s.day, today)} ({hm(s.minutes)}) : {round((r - 1) * 100)} % plus longue que ta plus "
                "longue du mois. C'est ce type de saut qui pèse, plus que le volume de la semaine.")
    last = len(weeks) - 2  # the last complete week
    j = _jump(weeks, last)
    if j and j >= JUMP:
        w = weeks[last]
        mean = statistics.fmean(x["minutes"] for x in weeks[last - 3:last])
        lo, hi = round(mean / 60), round(mean * 1.1 / 60)
        aim = f"{lo} à {hi} h" if hi > lo else f"{lo} h"
        return (f"Semaine du {w['monday'].strftime('%d/%m')} : {signed(round((j - 1) * 100))} % d'un coup "
                f"({hm(w['minutes'])} contre {hm(mean)} en moyenne). Vise {aim} cette semaine.")
    rising = [_jump(weeks, i) for i in (last - 2, last - 1, last)]
    if all(r and r > 1.05 for r in rising) and tr and tr["pct"] > 10:
        return ("3e semaine de hausse : une semaine plus légère (−30 %) t'aidera à l'absorber. C'est un repère "
                "d'entraîneur, pas une règle.")
    last4 = statistics.fmean(w["minutes"] for w in done[-4:])
    before = [w["minutes"] for w in done[-12:-4]]
    if before and statistics.fmean(before) > 0 and last4 < 0.6 * statistics.fmean(before):
        drop = round((1 - last4 / statistics.fmean(before)) * 100)
        return f"Ton volume a baissé de {drop} % sur 4 semaines : ton fond baisse si ça dure."
    med = statistics.median(w["minutes"] for w in done[-8:])
    return f"Semaines régulières autour de {_hours_label(med)} : continue, ou ajoute 10 % pour progresser."


def bars(weeks: list[dict], sessions: list[Session], nights: dict[date, float], hrv: dict[date, float],
         hrv_band: tuple[float, float] | None, race_days: set[date] = frozenset()) -> dict:
    """The 12-week chart in a fixed 360-wide viewBox (scaled, never stretched)."""
    n = len(weeks)
    step = (X1 - X0) / n
    top = max([w["minutes"] for w in weeks] + [60]) * 1.15
    done = _complete(weeks)
    med = statistics.median(w["minutes"] for w in done) if done else None

    def y(m: float) -> float:
        return round(B_BOT - m / top * (B_BOT - B_TOP), 1)

    cols = []
    for i, w in enumerate(weeks):
        cx = X0 + step * (i + 0.5)
        j = _jump(weeks, i)
        jump = bool(j and j >= JUMP and not w["current"])
        long_jump = False
        if w["longest"] and w["longest_day"] and w["longest_day"] not in race_days:  # a race is no training jump
            before = [s.minutes for s in sessions
                      if w["longest_day"] - timedelta(days=30) <= s.day < w["longest_day"]]
            long_jump = bool(before) and w["longest"] > SPIKE * max(before) and w["longest"] >= 90
        state = []
        days = [w["monday"] + timedelta(days=k) for k in range(7)]
        sl = [nights[d] for d in days if d in nights]
        if len(sl) >= 3:
            state.append(("sleep", hm(statistics.fmean(sl)).replace(" min", "′")))
        hv = [hrv[d] for d in days if d in hrv]
        if len(hv) >= 3 and hrv_band:
            m = statistics.fmean(hv)
            state.append(("hrv", "▼" if m < hrv_band[0] else "▲" if m > hrv_band[1] else "●"))
        cols.append({
            "x": round(cx - BAR_W / 2, 1), "cx": round(cx, 1), "y": y(w["minutes"]),
            "h": round(B_BOT - y(w["minutes"]), 1), "ly": y(w["longest"]), "lh": round(B_BOT - y(w["longest"]), 1),
            "label": _hours_label(w["minutes"]) if w["minutes"] else "", "jump": jump, "long_jump": long_jump,
            "current": w["current"], "href": w["href"],
            "date": "en cours" if w["current"] else f"{w['monday'].day}/{w['monday'].month}",
            "show_date": w["current"] or (n - 1 - i) % 2 == 0,
            "title": (f"Semaine du {w['monday'].strftime('%d/%m')} : {hm(w['minutes'])}"
                      + (f", plus longue sortie {hm(w['longest'])}" if w["longest"] else "")
                      + (f", {num(w['dplus'])} m D+" if w["dplus"] >= 50 else "")),
            "sleep": next((v for k, v in state if k == "sleep"), None),
            "hrv": next((v for k, v in state if k == "hrv"), None),
        })
    has_sleep = any(c["sleep"] for c in cols)
    has_hrv = any(c["hrv"] for c in cols)
    # sleep labels alternate on two lines: twelve « 7h44 » don't fit side by side
    height = 184 + (31 if has_sleep else 0) + (18 if has_hrv else 0)
    return {"cols": cols, "w": 360, "h": height, "base": B_BOT, "bw": BAR_W,
            "median": {"y": y(med), "label": _hours_label(med)} if med else None,
            "has_sleep": has_sleep, "has_hrv": has_hrv, "sleep_y": 198, "hrv_y": 198 + (31 if has_sleep else 0),
            "any_jump": any(c["jump"] or c["long_jump"] for c in cols)}


# fatigue chart: viewBox 360 × 150, y −40..+60 → 10..130
F_TOP, F_BOT, F_LO, F_HI = 10, 130, -40, 60
F_BANDS = ((30, 60, "z-warn", "très chargé"), (10, 30, "z-accent", "construction"), (-5, 10, None, "équilibré"),
           (-25, -5, "z-ok", "frais"), (-40, -25, "z-soft", "très reposé"))


def fatigue_chart(tr: dict, first_monday: date, today: date, races: list[date]) -> dict | None:
    """84 days on the bars' x axis (a day is a seventh of a week slot)."""
    hist = {d: v for d, v in tr["history"].items() if d >= first_monday}
    if len(hist) < 14:
        return None
    days = 84
    step = (X1 - X0) / days

    def x(d: date) -> float:
        return round(X0 + ((d - first_monday).days + 0.5) * step, 1)

    def y(v: float) -> float:
        v = min(F_HI, max(F_LO, v))
        return round(F_BOT - (v - F_LO) / (F_HI - F_LO) * (F_BOT - F_TOP), 1)

    path, pen = [], False
    for k in range(days):
        d = first_monday + timedelta(days=k)
        if d in hist and d <= today:
            path.append(f"{'L' if pen else 'M'}{x(d)} {y(hist[d])}")
            pen = True
        else:
            pen = False
    months = []
    d = first_monday
    while d <= first_monday + timedelta(days=days - 1):
        if d.day == 1 or d == first_monday:
            if d.day <= 7 or d == first_monday:
                months.append({"x": x(d), "label": MONTHS[d.month - 1]})
        d += timedelta(days=1)
    months = [m for i, m in enumerate(months) if i == 0 or m["x"] - months[i - 1]["x"] > 30]
    bands = [{"y": y(hi), "h": round(y(lo) - y(hi), 1), "cls": cls, "label": label, "ly": round((y(hi) + y(lo)) / 2 + 4, 1)}
             for lo, hi, cls, label in F_BANDS]
    tone = "warn" if tr["key"] == "loaded" else "ok" if tr["key"] == "fresh" else "ink"
    return {"w": 360, "h": 150, "line": " ".join(path), "bands": bands, "months": months,
            "today": {"x": x(today), "y": y(tr["pct"]), "tone": tone} if today in hist else None,
            "races": [{"x": x(r)} for r in races if first_monday <= r <= today], "x0": X0, "x1": X1}


def fatigue_line(tr: dict, today: date, quiet: bool = False) -> str | None:
    hist, series = tr["history"], tr["series"]
    last14 = [hist[d] for d in (today - timedelta(days=k) for k in range(14)) if d in hist]
    if len(last14) < 14:
        return None
    ctl_now = series[today][0]
    then = series.get(today - timedelta(days=84))
    change = round((ctl_now / then[0] - 1) * 100) if then and then[0] > 0 else None
    high = sum(1 for v in last14 if v > 30)
    low = sum(1 for v in last14 if v < -25)
    if high >= 7:
        return f"{high} jours sur les 14 derniers au-dessus de +30 % : c'est long. Prévois 4 à 5 jours faciles."
    if low >= 10:
        if quiet:  # tapering or recovering: being rested is the point
            return None
        return f"Très reposé depuis {low} jours sur 14 : ton fond baisse" + (f" ({signed(change)} % en 12 semaines)."
                                                                          if change is not None else ".")
    if change is not None:
        if all(-5 <= v <= 10 for v in last14) and abs(change) <= 3:
            return "Charge stable : ton fond ne bouge plus. Pour progresser, ajoute environ 10 % ou une sortie longue."
        if change >= 5:
            return f"Bonne construction : ton fond a monté de {change} % en 12 semaines."
        if change <= -5 and not quiet:
            return f"Ton fond a baissé de {abs(change)} % en 12 semaines."
    return None


# ── « chez toi » ────────────────────────────────────────────────────────────

SWC_HR, SWC_SLEEP, T_MIN = 2.0, 20.0, 2.5
MIN_BIN, MIN_WEEKS_BIN, MIN_WEEKS = 8, 6, 16


def welch(a: list[float], b: list[float]) -> tuple[float, float]:
    """(mean difference a − b, Welch t)."""
    d = statistics.fmean(a) - statistics.fmean(b)
    se = math.sqrt(statistics.variance(a) / len(a) + statistics.variance(b) / len(b))
    return d, (d / se if se > 0 else 0.0)


def insights(sessions: list[Session], runs: list[Session], resid: dict[int, float], weeks26: list[dict],
             nights: dict[date, float], today: date) -> dict:
    """{"cards": [...max 3], "locked": [...]}: pre-registered links only."""
    cards, locked, nulls = [], [], 0
    lo = today - timedelta(days=180)

    # H2: a long outing → easy-pace HR on the 3 days after
    longs = {s.day for s in sessions if s.day >= lo and (s.minutes >= 180 or s.dplus >= 1500)}
    rr = [s for s in runs if s.id in resid and s.day >= lo and s.day not in longs]
    after = [resid[s.id] for s in rr if any(0 < (s.day - d).days <= 3 for d in longs)]
    other = [resid[s.id] for s in rr if not any(0 < (s.day - d).days <= 3 for d in longs)]
    if len(after) >= MIN_BIN and len(other) >= MIN_BIN:
        d, t = welch(after, other)
        if abs(d) >= SWC_HR and abs(t) >= T_MIN:
            cards.append({"key": "h2", "score": abs(t),
                          "text": (f"Les 3 jours après une sortie de plus de 3 h ({len(longs)} en 6 mois), ta FC à "
                                   f"allure facile est {'plus haute' if d > 0 else 'plus basse'} qu'à l'habitude."
                                   + (" Garde ces jours-là faciles." if d > 0 else "")),
                          "bars": _pair(f"dans les 3 jours après ({len(after)} sorties)", d,
                                        f"les autres jours ({len(other)} sorties)", 0.0, "bpm"),
                          "foot": "chez toi, 6 derniers mois · lien observé, pas forcément cause"})
        elif abs(d) < 1 and nulls < 1:
            nulls += 1
            cards.append({"key": "h2", "score": 0,
                          "text": (f"Après tes sorties de plus de 3 h, ta FC à allure facile ne monte pas les jours "
                                   f"suivants ({len(after)} contre {len(other)} sorties) : tu les absorbes bien."),
                          "bars": None, "foot": "chez toi, 6 derniers mois"})
    else:
        locked.append({"text": "Effet de tes sorties longues sur ta FC à allure facile",
                       "have": min(len(after), MIN_BIN), "need": MIN_BIN,
                       "unit": "sorties faciles dans les 3 jours après une sortie longue"})

    # H1: a weekly jump → easy-pace HR the week after
    after_w, other_w = [], []
    for i in range(3, len(weeks26) - 1):
        j = _jump(weeks26, i)
        nxt = weeks26[i + 1]
        vals = [resid[s.id] for s in runs if s.id in resid and nxt["monday"] <= s.day < nxt["monday"] + timedelta(days=7)]
        if j is None or not vals:
            continue
        (after_w if j >= JUMP else other_w).append(statistics.fmean(vals))
    if len(after_w) >= MIN_WEEKS_BIN and len(other_w) >= MIN_WEEKS_BIN and len(after_w) + len(other_w) >= MIN_WEEKS:
        d, t = welch(after_w, other_w)
        if abs(d) >= SWC_HR and abs(t) >= T_MIN:
            cards.append({"key": "h1", "score": abs(t),
                          "text": ("Après une semaine à +30 % d'heures, ta FC à allure facile monte la semaine "
                                   "suivante : chez toi, la hausse se paie la semaine d'après." if d > 0 else
                                   "Après une semaine à +30 % d'heures, ta FC à allure facile baisse la semaine "
                                   "suivante : tu absorbes bien tes grosses semaines."),
                          "bars": _pair(f"la semaine d'après ({len(after_w)} fois)", d,
                                        f"après les autres ({len(other_w)} semaines)", 0.0, "bpm"),
                          "foot": "chez toi, 6 derniers mois · lien observé, pas forcément cause"})
        elif abs(d) < 1 and nulls < 1:
            nulls += 1
            cards.append({"key": "h1", "score": 0,
                          "text": (f"Tes grosses semaines ne font pas monter ta FC la semaine d'après ({len(after_w)} "
                                   f"contre {len(other_w)} semaines) : tu les absorbes."),
                          "bars": None, "foot": "chez toi, 6 derniers mois"})
    else:
        locked.append({"text": "Effet de tes grosses semaines", "have": min(len(after_w), MIN_WEEKS_BIN),
                       "need": MIN_WEEKS_BIN, "unit": "semaines à +30 % suivies de sorties faciles"})

    # H3: weekly hours → weekly sleep (weeks with 3 nights or more)
    rows = []
    for w in weeks26:
        if w["current"]:
            continue
        sl = [nights[w["monday"] + timedelta(days=k)] for k in range(7) if w["monday"] + timedelta(days=k) in nights]
        if len(sl) >= 3:
            rows.append((w["minutes"], statistics.fmean(sl)))
    if len(rows) >= MIN_WEEKS:
        med = statistics.median(m for m, _ in rows)
        big = [s for m, s in rows if m > med]
        small = [s for m, s in rows if m <= med]
        if len(big) >= MIN_WEEKS_BIN and len(small) >= MIN_WEEKS_BIN:
            d, t = welch(big, small)
            if abs(d) >= SWC_SLEEP and abs(t) >= T_MIN:
                cards.append({"key": "h3", "score": abs(t),
                              "text": (f"Tes semaines de plus de {_hours_label(med)} sont aussi tes semaines de nuits "
                                       f"{'plus courtes' if d < 0 else 'plus longues'}."),
                              "bars": _pair(f"grosses semaines ({len(big)})", statistics.fmean(big),
                                            f"les autres ({len(small)} semaines)", statistics.fmean(small), "min"),
                              "foot": "chez toi, 6 derniers mois · lien observé, pas forcément cause"})
    else:
        locked.append({"text": "Effet de tes heures sur ton sommeil", "have": len(rows), "need": MIN_WEEKS,
                       "unit": "semaines avec 3 nuits mesurées"})
    cards.sort(key=lambda c: -c["score"])
    return {"cards": cards[:3], "locked": locked}


def _pair(label_a: str, a: float, label_b: str, b: float, unit: str) -> list[dict]:
    """Two horizontal bars on one scale, values printed."""
    def txt(v):
        return hm(v) if unit == "min" else f"{signed(v)} {unit}"
    if unit == "min":
        lo = min(a, b) * 0.8
        span = max(a, b) - lo or 1
        w = [round((v - lo) / span * 100) for v in (a, b)]
    else:
        top = max(abs(a), abs(b), 1)
        w = [round(abs(v) / top * 100) for v in (a, b)]
    return [{"label": label_a, "value": txt(a), "w": max(w[0], 2), "hot": True},
            {"label": label_b, "value": txt(b), "w": max(w[1], 2), "hot": False}]
