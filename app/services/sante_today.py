"""Santé › Aujourd'hui: one decision for today and the signals it rests on.

The decision and the rows read the same signal objects, so they can never
disagree. Rules (first match wins; science in the Santé research notes):
1. illness (resting HR well above normal two nights running);
2. race recovery (a race of 3 h or more in the last 7–14 days);
3. race in 0–2 days;
4. « pas d'intensité » only when HRV is low AND resting HR is up (Buchheit
   2014; HRV alone is not specific, Bellenger 2016); a single strong signal
   only asks for an easy day; a low HRV in the 48 h after a long session is
   expected (Stanley 2013) and race-week HRV dips are normal (Stanley 2015);
5. legs: a long or steep outing in the last 48 h;
6. accumulated load, or heart rate running high at easy pace;
7. a mild signal, a short night, or a big week;
8./9. otherwise train as planned (hard allowed when the nights say so).
The watch's own score is shown last, as a second opinion with what it is
based on; it never changes the decision. Load never reads as injury risk.
"""
import statistics
from datetime import date, timedelta

from app.services.health import fmt_minutes
from app.services.viz import hm  # the house duration format (older modules import it from here)

WEEKDAYS_LONG = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
WEEKDAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")


def num(v: float, digits: int = 0) -> str:
    out = f"{v:.{digits}f}".replace(".", ",").replace("-", "−")
    return out


def signed(v: float, digits: int = 0) -> str:
    return ("+" if v > 0 else "") + num(v, digits) if round(v, digits) != 0 else "0"


def dplus_txt(m: float) -> str:
    return f"+{int(round(m, -1)):,} m".replace(",", " ")


def of_day(d: date, today: date) -> str:
    """« d'hier », « de samedi », « du 28/09 »."""
    w = when(d, today)
    return ("d'hier" if w == "hier" else "d'aujourd'hui" if w == "aujourd'hui" else "du " + w[3:]
            if w.startswith("le ") else "de " + w)


def when(d: date, today: date) -> str:
    """« hier », « samedi », « le 28/09 »."""
    k = (today - d).days
    return ("aujourd'hui" if k == 0 else "hier" if k == 1 else WEEKDAYS_LONG[d.weekday()] if k < 7
            else f"le {d.strftime('%d/%m')}")


# ── the gauge: everything in % of the track, drawn as HTML (no stretched SVG) ──

def gauge(value: float, lo: float, hi: float, band: tuple[float, float] | None = None,
          zones: list[tuple[float, float, str]] | None = None, edges: list[tuple[float, str]] | None = None) -> dict:
    span = hi - lo or 1

    def pct(v: float) -> float:
        return round(min(100.0, max(0.0, (v - lo) / span * 100)), 1)

    out = {"dot": pct(value), "zones": [], "edges": [], "band": None}
    for a, b, cls in zones or []:
        out["zones"].append({"l": pct(a), "w": round(pct(b) - pct(a), 1), "cls": cls})
    if band:
        out["band"] = {"l": pct(band[0]), "w": max(round(pct(band[1]) - pct(band[0]), 1), 1.0)}
    for v, label in edges or []:
        x = pct(v)
        out["edges"].append({"x": x, "label": label, "align": "start" if x < 8 else "end" if x > 92 else "mid"})
    return out


# ── rows ────────────────────────────────────────────────────────────────────

FATIGUE_ZONES = [(-40, -25, "z-soft"), (-25, -5, "z-ok"), (-5, 10, "z-none"), (10, 30, "z-accent"), (30, 60, "z-warn")]
FATIGUE_TONES = {"rested": "muted", "fresh": "ok", "balanced": "muted", "build": "muted", "loaded": "warn"}


def fatigue_row(tr: dict) -> dict:
    pct = tr["pct"]
    return {"key": "fatigue", "label": "Fatigue", "window": "7 j vs ton fond", "value": f"{signed(pct)} %",
            "ref": "0 = ton fond", "word": tr["word"], "tone": FATIGUE_TONES[tr["key"]],
            "gauge": gauge(pct, -40, 60, zones=FATIGUE_ZONES, edges=[(0, "ton fond")]),
            "dev": abs(pct) / 20, "href": "?vue=entrainement", "link": "Ta fatigue jour par jour"}


LOAD_TONES = {"over": "warn", "good": "muted", "keep": "muted", "taper": "muted", "low": "muted"}


def watch_load_row(load: dict, source: str) -> dict:
    """Without enough sessions: the watch's own load ratio (never red)."""
    r = load["ratio"]
    return {"key": "fatigue", "label": "Charge", "window": f"7 j ({source})", "value": f"×{num(r, 2)}",
            "ref": "1 = ton habitude" + (f" · au {load['when']}" if load["stale"] else ""), "word": load["word"],
            "tone": LOAD_TONES[load["key"]],
            "gauge": gauge(r, 0.4, 2.0, zones=[(0.4, 0.8, "z-soft"), (0.8, 1.3, "z-none"), (1.3, 2.0, "z-warn")],
                           edges=[(1.0, "1")]),
            "dev": abs(r - 1) * 3}


def legs_row(lg: dict, today: date) -> dict:
    hi = max(lg["p95"] or 0, lg["minutes"], 240) * 1.05
    big = lg["big"]
    sub = href = None
    if big:
        sub, href = f"ta plus grosse sortie : {when(big.day, today)}", f"/activities/{big.id}"
    band = (lg["p25"], lg["p75"]) if lg["p25"] is not None and lg["p75"] > lg["p25"] else None
    return {"key": "legs", "label": "Jambes", "window": "72 h",
            "value": hm(lg["minutes"]) + (f" · {dplus_txt(lg['dplus'])}" if lg["dplus"] >= 50 else ""),
            "ref": f"d'habitude {hm(band[0])}–{hm(band[1])}" if band else "sur 72 h", "word": lg["word"],
            "tone": "warn" if lg["key"] != "fresh" else "muted",
            "gauge": gauge(lg["minutes"], 0, hi, band=band,
                           edges=[(band[0], hm(band[0])), (band[1], hm(band[1]))] if band
                           else [(0, "0"), (hi, hm(hi))]),
            "dev": 2.5 if lg["key"] == "heavy" else 1.5 if lg["key"] == "loaded" else 0,
            "sub": sub, "sub_href": href}


def easy_hr_row(e: dict) -> dict:
    d = round(e["delta"])
    word, tone = (("haute", "warn") if d >= 5 else ("un peu haute", "warn") if d >= 3
                  else ("basse", "ok") if d <= -3 else ("normale", "muted"))
    s = "s" if e["n"] > 1 else ""
    row = {"key": "easy_hr", "label": "FC à allure facile", "window": "10 j", "value": f"{signed(d)} bpm",
           "ref": f"à même allure · {e['n']} sortie{s}", "word": word, "tone": tone,
           "gauge": gauge(d, -6, 8, band=(-2, 2), edges=[(-2, "−2"), (2, "+2")]), "dev": abs(d) / 2}
    if d >= 3:
        row["sub"] = "Plus haute qu'il y a deux semaines à la même allure : fatigue, chaleur ou début de rhume."
    elif d <= -3:
        row["sub"] = "Plus basse à la même allure : bonne forme."
    return row


def hrv_row(f: dict, chart: dict | None, dip_note: str | None) -> dict:
    z, (lo, hi), base = f["hrv_z"], f["hrv_band"], f["hrv_baseline"]
    word, tone = (("basse", "warn") if z < -1 else ("un peu basse", "warn") if z < -0.5
                  else ("haute", "ok") if z > 0.5 else ("normale", "muted"))
    if dip_note and z < -0.5:  # expected: after a long session, before a race
        tone = "muted"
    span = max(hi - lo, 0.25 * base)
    return {"key": "hrv", "label": "VFC", "window": "7 nuits", "value": f"{num(f['hrv_7d'])} ms",
            "ref": f"d'habitude {num(base)}", "word": word, "tone": tone,
            "gauge": gauge(f["hrv_7d"], lo - span, hi + span, band=(lo, hi), edges=[(base, num(base))]),
            "dev": abs(z) * 2, "chart": chart, "sub": dip_note}


def rhr_up_by(f: dict) -> int | None:
    """The 7 nights' resting HR over the usual, in whole bpm: the two printed numbers' difference,
    so the word and the decision read what the athlete reads."""
    if f.get("rhr_7d") is None or f.get("rhr_baseline") is None:
        return None
    return round(f["rhr_7d"]) - round(f["rhr_baseline"])


def rhr_row(f: dict, chart: dict | None, sub: str | None = None, note: str | None = None) -> dict:
    """The word from the same delta as the decision (+3 / +5 bpm over the 7 nights); `note`
    explains a high one that does not count (after a race, just before one)."""
    base, v, delta = f["rhr_baseline"], f["rhr_7d"], rhr_up_by(f)
    word, tone = (("haute", "warn") if delta >= 5 else ("un peu haute", "warn") if delta >= 3
                  else ("normale", "muted"))
    if note and delta >= 3:
        tone, sub = "muted", sub or note
    lo, hi = base - 3, base + 3
    return {"key": "rhr", "label": "FC au repos", "window": "7 nuits", "value": f"{num(v)} bpm",
            "ref": f"d'habitude {num(base)}", "word": word, "tone": tone,
            "gauge": gauge(v, lo - 6, hi + 6, band=(lo, hi), edges=[(base, num(base))]),
            "dev": abs(delta) / 1.5, "chart": chart, "sub": sub}


def sleep_row(nights: dict, scores: dict[date, float], today: date) -> dict | None:
    """The last 3 nights (2 at least); the watch's score is printed, never decides."""
    last3 = [r for r in nights["rows"][:3] if r["value"] is not None]
    if len(last3) < 2:
        return None
    avg = statistics.fmean(r["value"] for r in last3)
    word, tone = (("court", "warn") if avg < 360 else ("un peu court", "muted") if avg < 420 else ("ok", "muted"))
    ref = f"besoin {nights['need_h']} h"
    sc = [scores[r["day"]] for r in last3 if r["day"] in scores]
    month = [v for d, v in scores.items() if today - timedelta(days=28) < d <= today]
    if sc:
        ref += f" · score {num(statistics.fmean(sc))}"
        if len(month) >= 5:
            ref += f" (d'habitude {num(statistics.median(month))})"
    return {"key": "sleep", "label": "Sommeil", "window": "3 nuits", "value": fmt_minutes(avg), "ref": ref,
            "word": word, "tone": tone,
            "gauge": gauge(avg, 240, 600, band=(420, 540), edges=[(420, "7 h"), (540, "9 h")]),
            "dev": 2 if avg < 360 else 1 if avg < 420 else 0}


def stress_row(stress: dict[date, float], today: date) -> dict | None:
    hist = [v for d, v in stress.items() if today - timedelta(days=60) < d <= today - timedelta(days=3)]
    last = [stress[d] for d in (today - timedelta(days=k) for k in range(3)) if d in stress]
    if len(hist) < 20 or not last:
        return None
    q = statistics.quantiles(hist, n=4)
    v = statistics.fmean(last)
    high = v > q[2] + 5
    return {"key": "stress", "label": "Stress de jour", "window": "3 j", "value": num(v),
            "ref": f"d'habitude {num(statistics.median(hist))}", "word": "plus haut" if high else "normal",
            "tone": "warn" if high else "muted",
            "gauge": gauge(v, 0, 100, band=(q[0], q[2]), edges=[(q[0], num(q[0])), (q[2], num(q[2]))]),
            "dev": (v - q[2]) / 5 if high else 0}


COROS_BANDS = [(0, 20, "z-warn", "épuisé"), (20, 70, "z-soft", "fatigué"), (70, 90, "z-none", "normal"),
               (90, 101, "z-ok", "frais")]
GARMIN_BANDS = [(0, 25, "z-warn", "faible"), (25, 50, "z-soft", "basse"), (50, 75, "z-none", "modérée"),
                (75, 95, "z-ok", "haute"), (95, 101, "z-ok", "maximale")]


def watch_row(rec: dict | None, source: str | None, body_battery: float | None) -> dict | None:
    """COROS recovery % or Garmin Training Readiness, with the watch's own bands."""
    if not rec:
        return None
    v = rec["value"]
    bands = GARMIN_BANDS if source == "Garmin" else COROS_BANDS
    word = next(w for a, b, _, w in bands if a <= v < b)
    if source == "Garmin":
        label, value = "Training Readiness", num(v)
        sub = "note de ta montre : sommeil, VFC, récup et charge"
        if body_battery is not None:
            sub += f" · Body Battery {num(body_battery)} au réveil"
    else:
        label, value = "Récup COROS", f"{num(v)} %"
        sub = "calculée par COROS sur ta charge, sans ta nuit"
        if rec.get("full_h"):
            sub += f" · complète dans {num(rec['full_h'])} h"
    return {"key": "watch", "label": label, "window": "ta montre", "value": value, "ref": "", "word": word,
            "tone": "muted", "low": v < (50 if source == "Garmin" else 70),
            "high": v >= (75 if source == "Garmin" else 90),
            "gauge": gauge(v, 0, 100, zones=[(a, min(b, 100), cls) for a, b, cls, _ in bands],
                           edges=[(a, str(a)) for a, _, _, _ in bands[1:]]),
            "sub": sub, "dev": 0}


# ── the decision ────────────────────────────────────────────────────────────

def _resume_day(outing_day: date, minutes: float, dplus: float, today: date) -> str:
    gap = 3 if minutes > 300 or dplus >= 2500 else 2
    d = outing_day + timedelta(days=gap)
    if d <= today:
        return "demain"
    return "demain" if (d - today).days == 1 else WEEKDAYS_LONG[d.weekday()]


def decide(c: dict) -> dict:
    """{tone: ok|easy|rest|unknown, word, headline, text, resume, note, drivers,
    rule, hrv_note}. `rule` names the rung that decided; `hrv_note` explains a
    low HRV that does not count (after a long session, before a race)."""
    f, tr, legs, easy, feel = c["form"], c["tr"], c["legs"], c["easy"], c["feel"]
    nr, pr, today = c["next_race"], c["post_race"], c["today"]

    def out(tone, headline, text=None, resume=None, drivers=(), note=None, rule=None, word=None):
        word = word or {"ok": "feu vert", "easy": "facile", "rest": "repos", "unknown": "?"}[tone]
        return {"tone": tone, "word": word, "headline": headline, "text": text, "resume": resume,
                "drivers": list(drivers), "note": note, "rule": rule, "hrv_note": None, "rhr_note": None}

    hz, rd = f.get("hrv_z"), rhr_up_by(f)
    hrv_low, hrv_vlow = hz is not None and hz < -0.5, hz is not None and hz < -1
    rhr_up, rhr_vup = rd is not None and rd >= 3, rd is not None and rd >= 5
    race_week = bool(nr and nr["days"] <= 7)
    after_long = c["hard48"]
    # a low HRV is expected after a race or a long session, and before a race: it does not count
    # (after a race or on its eve, a high resting HR neither: those rungs decide on their own)
    race_soon = bool(nr and nr["days"] <= 2)
    after = "ta course" if pr and pr.get("race") else pr["name"] if pr else None
    hrv_note = rhr_note = None
    if hrv_low:
        if pr:
            hrv_note = f"Basse après {after} : normal, ça revient en quelques jours."
        elif race_soon or (race_week and not rhr_up):
            hrv_note = "Avant une course, nerfs et affûtage font souvent baisser la VFC : rien à changer."
        elif after_long and not rhr_up:
            hrv_note = f"Basse après ta sortie {of_day(after_long.day, today)} : normal, ça revient en 24–48 h."
    if rhr_up:
        if pr:
            rhr_note = f"Haute après {after} : normal, elle redescend en quelques jours."
        elif race_soon:
            rhr_note = "Avant une course, le stress fait souvent monter la FC au repos : rien à changer."
    hrv_flag = hrv_low and hrv_note is None

    def done(v):
        v["hrv_note"], v["rhr_note"] = hrv_note, rhr_note
        return v

    if c["ill"]:
        return out("rest", "Reste tranquille aujourd'hui",
                   "Ta FC au repos est nettement au-dessus de ton habitude deux nuits de suite : c'est souvent un début "
                   "de maladie (ou l'alcool, la chaleur, l'altitude). Repos ou footing très facile.",
                   "Reprends quand elle redescend vers ton habitude.", ["rhr"], rule="ill")
    if nr and nr["days"] <= 2:  # a race today or soon overrides any recovery wording
        if nr["days"] == 0:
            return done(out("ok", "Jour de course",
                            f"{nr['name']} aujourd'hui : pars plus lentement que tu ne le voudrais.", rule="race"))
        return done(out("ok", "Course demain : repos ou 20 min faciles" if nr["days"] == 1
                        else "Course après-demain : court et facile",
                        "20 à 30 min faciles avec 4 accélérations de 20 s, ou repos. Rien ne se gagne maintenant, tout "
                        "peut se perdre.", rule="race"))
    if pr:  # recovering is not a warning: orange, never red
        free = (pr["day"] + timedelta(days=pr["limit"] + 1)).strftime("%d/%m")
        # its time, when known and not already the Jambes row's number
        timed = pr.get("known") and not (legs and round(legs["minutes"]) == round(pr["minutes"]))
        what = f"{pr['name']} ({hm(pr['minutes'])})" if timed else pr["name"]
        if not pr["long"]:
            return done(out("easy", "Footing facile seulement", f"Après {what}, 2 à 3 jours faciles suffisent.",
                            f"Reprends l'intensité le {free}.", rule="race", word="récup"))
        if pr["days"] <= 3:
            return done(out("easy", "Récupère",
                            f"Après {what}, cœur et muscles mettent 1 à 2 semaines à revenir. Marche, vélo tranquille "
                            "ou footing très court." + ("" if hrv_note else " Une VFC basse ces jours-ci est normale."),
                            f"Pas d'intensité avant le {free}.", rule="race", word="récup"))
        return done(out("easy", "Footing facile seulement",
                        f"Après {what}, ton corps récupère encore, même si tu te sens bien.",
                        f"Pas d'intensité avant le {free}.", rule="race", word="récup"))

    if hrv_low and rhr_up:
        why = "Ta VFC est sous ta normale et ta FC au repos au-dessus, sur 7 nuits"
        why += (" : avant une course c'est souvent le stress, mais reste facile et dors bien." if race_week
                else " : ton corps encaisse autre chose que l'entraînement (sommeil, stress, virus ?)."
                if not tr or tr["pct"] <= 10 else " : la charge et la récupération ne suivent plus.")
        return out("rest", "Pas d'intensité aujourd'hui", why,
                   f"Reprends l'intensité quand ta VFC revient vers {num(f['hrv_baseline'])} ms.", ["hrv", "rhr"],
                   rule="red")
    short = c["short_night"]
    if (hrv_vlow and hrv_flag) or rhr_vup or (hrv_flag and short):
        key = "rhr" if rhr_vup else "hrv"
        what = ("Ta FC au repos est nettement au-dessus de ta normale" if key == "rhr"
                else "Ta VFC est nettement sous ta normale" + (" après une nuit courte" if short else ""))
        return done(out("easy", "Garde ta séance facile",
                        f"{what} : seul, ce signal ne suffit pas à tout arrêter, mais n'enchaîne pas de séance dure. "
                        "Dis-moi comment tu te sens ↓",
                        f"Reprends l'intensité quand ta VFC revient vers {num(f['hrv_baseline'])} ms." if key == "hrv"
                        else f"Reprends l'intensité quand elle revient vers {num(f['rhr_baseline'])} bpm.", [key],
                        rule="strong"))

    legs_hit = bool(legs and legs["key"] != "fresh")
    if legs_hit or (feel and feel.get("legs_heavy")):
        big = legs["big"] if legs_hit else None
        if big and (big.minutes >= 180 or big.dplus >= 1500):
            alone = round(legs["minutes"]) == round(big.minutes)  # the Jambes row already prints its numbers
            nums = "" if alone else f" ({hm(big.minutes)}, {dplus_txt(big.dplus)})"
            text = (f"Ta sortie {of_day(big.day, today)}{nums} pèse encore dans tes jambes, même si ton cœur a "
                    "récupéré.")
            resume = f"Séance dure possible à partir de {_resume_day(big.day, big.minutes, big.dplus, today)}."
        elif legs_hit:
            text, resume = "Tu as beaucoup marché ou couru ces trois derniers jours.", None
        else:
            text, resume = "Tu as les jambes lourdes : c'est toi qui sais.", "On regarde demain."
        return done(out("easy", "Endurance facile aujourd'hui", text, resume, ["legs"], rule="legs"))

    hist = (tr or {}).get("history") or {}
    high_days = sum(1 for d in (today - timedelta(days=k) for k in range(10)) if (hist.get(d) or -99) > 30)
    if high_days >= 7 or (easy and easy["high"] >= 2):
        why = ([f"Ta charge est très au-dessus de ton fond depuis {high_days} jours sur 10"] if high_days >= 7 else []) + \
              ([f"ton cœur bat plus vite à allure facile sur {easy['high']} sorties"] if easy and easy["high"] >= 2
               else [])
        text = " et ".join(why)
        return done(out("easy", "Semaine plus légère conseillée", text[:1].upper() + text[1:] + ".",
                        "Allège de 30 % cette semaine, garde une seule séance rythmée.",
                        (["fatigue"] if high_days >= 7 else []) + (["easy_hr"] if easy and easy["high"] >= 2 else []),
                        rule="accumulated"))

    # mild night signals (one or two), the excused HRV left out
    mild = (["hrv"] if hrv_flag else []) + (["rhr"] if rhr_up else []) + \
        (["sleep"] if "nuits courtes" in (f.get("reasons") or []) else [])
    if mild:
        names = {"hrv": "ta VFC", "rhr": "ta FC au repos", "sleep": "tes nuits courtes"}
        text = ("Tes nuits sont plus courtes que d'habitude : n'enchaîne pas deux séances dures." if mild == ["sleep"]
                else "Un signal sort de ta normale : seul, ce n'est pas grave, mais n'enchaîne pas deux séances dures."
                if len(mild) == 1 else
                f"Deux signaux sortent de ta normale ({names[mild[0]]} et {names[mild[1]]}) : pas de séance dure "
                "aujourd'hui.")
        return done(out("easy", "Garde ta séance facile", text, drivers=mild, rule="mild"))
    if short:
        return done(out("easy", "Séance dure le matin seulement", short, drivers=["sleep"], rule="short"))
    load = c["watch_load"]
    if (tr and tr["pct"] > 10 and c["jump"]) or (not tr and load and load["key"] == "over"):
        text = ("Ta charge des 7 derniers jours est nettement au-dessus de ton fond, après une grosse semaine. "
                if tr else "Tes 7 derniers jours pèsent bien plus que d'habitude. ")
        return done(out("easy", "Séance prévue, sans en rajouter",
                        text + "N'ajoute ni volume ni intensité : une semaine plus légère ensuite t'aidera à l'absorber.",
                        drivers=["fatigue"], rule="jump"))

    if feel and feel.get("value") == 3:
        return done(out("easy", "Garde ta séance facile",
                        "Tu te sens fatigué : c'est toi qui sais. Facile aujourd'hui, on regarde demain.", rule="feel"))
    status = f.get("status")
    nights_ok = status in ("ok", "fresh")
    no_night_note = (None if nights_ok or hrv_note or f.get("nights_recent", 0) >= 3 else
                     "Sans nuit mesurée, je ne vois que ta charge, tes jambes et ta FC en course — pas la maladie ni "
                     "le stress.")
    if nights_ok and (not tr or tr["pct"] <= 10):
        text = ("Ta VFC est au-dessus de ta normale : tu récupères bien." if status == "fresh"
                else "Ta VFC et ta FC au repos sont dans ta normale, tes jambes sont fraîches.")
        return done(out("ok", "Séance dure possible", text, drivers=["hrv", "rhr"], rule="hard_ok"))
    if tr or load:
        if tr:
            text = {"rested": "Tu en fais nettement moins que d'habitude : idéal avant une course, sinon ton fond baisse.",
                    "fresh": "Tu es plus frais que d'habitude : bon moment pour une séance de qualité.",
                    "balanced": "Ta charge est proche de ton habitude.",
                    "build": "Ta charge monte : tu construis.",
                    "loaded": "Ta charge des 7 derniers jours est nettement au-dessus de ton fond : n'en rajoute pas."}[
                tr["key"]]
        else:
            text = {"good": "Ta charge monte à un bon rythme.", "keep": "Ta charge reste proche de ton habitude.",
                    "taper": "Tu en fais moins que d'habitude : tu récupères.",
                    "low": "Tu en fais nettement moins que d'habitude : ta forme de fond baissera si ça dure.",
                    "over": ""}[load["key"]]
        if nights_ok:
            text = "Ta VFC et ta FC au repos sont dans ta normale. " + text
        return done(out("ok", "Entraînement prévu OK", text, note=no_night_note, drivers=["fatigue"], rule="plan_ok"))
    return done(out("unknown", "Pas encore d'avis",
                    "Il me faut 6 semaines d'activités Strava, ou ta montre, pour juger ta charge.", rule="none"))


# what each row says when it is in the athlete's normal
NORMAL_WORDS = {"fatigue": ("équilibré", "frais"), "legs": ("fraîches",), "easy_hr": ("normale", "basse"),
                "hrv": ("normale", "haute"), "rhr": ("normale",), "sleep": ("ok",), "stress": ("normal",)}


def disagreement(watch: dict | None, verdict: dict, rows: list[dict], source: str | None) -> str | None:
    """One line when the watch and the decision are far apart — no other
    advice than the decision's, never « ta montre se trompe »."""
    if not watch:
        return None
    if watch["low"] and verdict["rule"] in ("hard_ok", "plan_ok"):
        if not all(r["word"] in NORMAL_WORDS.get(r["key"], ()) and not (r["key"] == "hrv" and r.get("sub"))
                   for r in rows if r["key"] != "watch"):
            return None
        what = ("sa note mêle sommeil, VFC, récup et charge" if source == "Garmin"
                else "elle compte surtout ta charge récente")
        return f"Ta montre est plus prudente : {what}. Tes autres signaux sont dans ta normale."
    if watch["high"] and verdict["rule"] == "red":
        return "Ta montre te dit frais, mais ta VFC et ta FC au repos disent autre chose."
    if watch["high"] and verdict["rule"] == "ill":
        return "Ta montre te dit frais, mais ta FC au repos dit autre chose."
    return None


def order_rows(rows: list[dict], drivers: list[str]) -> tuple[list[dict], list[dict]]:
    """(visible, more): the drivers first, then by deviation; fatigue, legs and
    the watch (last) always visible, plus 2 others — drivers count among them,
    though a driver is never hidden."""
    watch = [r for r in rows if r["key"] == "watch"]
    rest = [r for r in rows if r["key"] != "watch"]
    for r in rest:
        r["driver"] = r["key"] in drivers
    rest.sort(key=lambda r: (not r["driver"], -r.get("dev", 0)))
    visible, more, others = [], [], 0
    for r in rest:
        if r["key"] in ("fatigue", "legs"):
            visible.append(r)
        elif r["driver"]:  # always shown, but it takes one of the 2 places
            visible.append(r)
            others += 1
        elif others < 2 and r.get("dev", 0) >= 0.5:
            visible.append(r)
            others += 1
        else:
            more.append(r)
    return visible + watch, more

