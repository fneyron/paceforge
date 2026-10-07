"""Santé › Aujourd'hui: one action for today, at most 2 linked drivers, at most
3 tiles, the one-tap check-in. No score, no paragraph: at most one sentence,
for « quand reprendre » or the nap tip.

The ladder (first match wins; evidence_final.md, SANTE_DECISIONS.md; (H) = a
PaceForge heuristic, never shown as a finding):
R1  a race in 0–2 days.
R2  illness: the « malade » chip today, or the nightly-HR alert (two nights in
    a row, each ≥ normal + max(2 SD, 5 bpm) (H); Altini & Plews 2021, Quer
    2021: specific, not sensitive). It also fires in race week. → rest.
R3  « Reprise » (Schwellnus 2022; Snyders 2022; Radin 2021; gates (H)), the
    state R2 opens (nights.reprise); still open after 14 days → see a doctor.
R4  after a race (or an exceptional outing): « Récupère » J+1 → J+3, then
    « Footing facile seulement »; intensity back at J+7, J+10 after ≥ 10 h,
    J+3 after a race under 3 h (H).
R5  short night: a main episode (any length) and a 24-h total < 6 h (Craven
    2022; a nap that ended in the 24 h before the main wake counts, H),
    outside J-7 → J-1. Early wake (≥ 60 min before the median wake, H, or no
    median) → « Séance dure ce matin, sinon facile » + the nap tip (Lastella
    2021; Mesas 2023; Mograss 2022). Otherwise the ladder goes on and the
    sentence slot says « Nuit courte : place ta séance dure plutôt le matin. »
R6  legs (H): an outing ≥ 3 h or ≥ 1 500 m D+ in the last 48 h, or the
    check-in « jambes ». A mover by the athlete's sign-off, never worded
    « fatigue ».
R7  7-night HRV under the band AND (7-night nightly HR above it OR « moins
    bien ») (H; Plews 2013, 2014; Düking 2021), never in J-7 → J-1.
R8  easy-pace HR « à surveiller » (the 2 latest easy runs, both in 14 days,
    each ≥ 3 bpm over their normal: sante_training.easy_watch, the rule
    Activités › FC en footing uses; Nuuttila 2022; H) AND « moins bien ».
R9  « moins bien » alone (H): the athlete's own call (Saw 2016).
R10 race week J-7 → J-3: « Semaine de course : séance prévue, sans en rajouter ».
R11 « Séance prévue : rien ne s'y oppose » (nights in the normal, or ≥ 6
    sessions in 42 days (H)). A quiet chart is not a clean bill of health.
R12 « Pas encore d'avis ».
A single out-of-band signal shows on its tile and never moves the action (the
R2 alert is the exception). Tagged nights and race week never trigger anything
but the R2 alert.
"""
from datetime import date, timedelta

from app.services.viz import GLYPH, hm, signed
from app.services.viz import tile as viz_tile

WEEKDAYS_LONG = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
WEEKDAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")


def of_day(d: date, today: date) -> str:
    """« d'hier », « de samedi », « du 28/09 »."""
    k = (today - d).days
    if k == 0:
        return "d'aujourd'hui"
    if k == 1:
        return "d'hier"
    return f"de {WEEKDAYS_LONG[d.weekday()]}" if k < 7 else f"du {d.strftime('%d/%m')}"


# ── the decision ────────────────────────────────────────────────────────────

TONE_GLYPH = {"ok": "●", "easy": "◐", "rest": "■", "unknown": "?"}
TONE_WORD = {"ok": "séance prévue", "easy": "facile", "rest": "repos", "unknown": "pas d'avis"}
SOMMEIL = "/sante?vue=sommeil"
LEGS_MIN, LEGS_DPLUS = 180, 1500  # (H) the outing that still weighs in the legs (48 h)
MIN_SESSIONS_42 = 6  # (H) sessions in 42 days for « séance prévue »
SHORT_LATE = "Nuit courte : place ta séance dure plutôt le matin."


def chip(glyph: str, word: str, href: str | None, aria: str | None = None) -> dict:
    return {"glyph": glyph, "word": word, "href": href, "aria": aria or word}


def race_chip(race: dict) -> dict:
    """« ⚑ Transjeju · J+5 » (days since: > 0; until: < 0) → the race page's preparation."""
    k = race["days"]
    when_ = "jour J" if k == 0 else f"J{'+' if k > 0 else '−'}{abs(k)}"
    name = race["name"] if len(race["name"]) <= 18 else race["name"][:16].rstrip() + "…"
    said = "jour de course" if k == 0 else f"{abs(k)} jour{'s' if abs(k) > 1 else ''} {'après' if k > 0 else 'avant'}"
    return chip(GLYPH["race"], f"{name} · {when_}", race.get("href"), f"{race['name']}, {said}")


def outing_chip(s, today: date) -> dict:
    """« ◆ sortie de sam. » → /activity/{id}."""
    k = (today - s.day).days
    word = ("sortie d'aujourd'hui" if k == 0 else "sortie d'hier" if k == 1
            else f"sortie de {WEEKDAYS[s.day.weekday()]}")
    return chip(GLYPH["long"], word, f"/activity/{s.id}", f"{word}, {hm(s.minutes)}")


def _resume_day(outing_day: date, minutes: float, dplus: float, today: date) -> str:
    """When a hard session fits again after a big outing (H): 2 days, 3 after 5 h or 2 500 m D+."""
    gap = 3 if minutes > 300 or dplus >= 2500 else 2
    d = outing_day + timedelta(days=gap)
    if (d - today).days <= 1:
        return "demain"
    return WEEKDAYS_LONG[d.weekday()]


def decide(c: dict) -> dict:
    """{tone, glyph, word, headline, text, chips, rule, drivers}. `c`: today;
    next_race {days, name, href}; post (sante._post_race, with href); feel
    (feel_of of today, or None); alert (nights.illness_alert); reprise
    (nights.reprise); short {early, tip} when R5 applies (a main episode, a
    24-h total < 6 h, not in race week); legs {big: the outing of the last
    48 h ≥ 3 h or ≥ 1 500 m D+, or None}; hrv / hr: the 7-night status
    (« above » / « below » / « in » / None); easy {flag}; sessions42;
    has_watch; has_sessions. `drivers` name the tiles behind the action (hr,
    hrv, sleep, easy); `chips` are the ≤ 2 linked drivers under the headline."""
    today, nr, pr = c["today"], c.get("next_race"), c.get("post")
    feel = c.get("feel") if (c.get("feel") or {}).get("answered", True) else None
    why = set((feel or {}).get("why") or [])
    worse = bool(feel and feel["value"] == 3)
    race_week = bool(nr and 1 <= nr["days"] <= 7)

    def out(tone, headline, text=None, chips=(), rule=None, drivers=()):
        return {"tone": tone, "glyph": TONE_GLYPH[tone], "word": TONE_WORD[tone], "headline": headline,
                "text": text, "chips": [x for x in chips if x][:2], "rule": rule, "drivers": list(drivers)}

    alert_chip = chip("↑", "FC de nuit · 2 nuits", f"{SOMMEIL}#coeur",
                      "FC de nuit au-dessus de ta normale 2 nuits de suite")
    # R1 race in 0–2 days
    if nr and nr["days"] <= 2:
        head = ("Jour de course" if nr["days"] == 0 else "Course demain : repos ou 20 min faciles"
                if nr["days"] == 1 else "Course après-demain : court et facile")
        text = ("Pars plus lentement que tu ne le voudrais." if nr["days"] == 0
                else "20 à 30 min faciles avec 4 accélérations de 20 s, ou repos.")
        return out("ok", head, text, [race_chip({**nr, "days": -nr["days"]}), alert_chip if c.get("alert") else None], "race")
    # R2 illness: the chip, or the HR alert (race week included)
    if worse and "sick" in why:
        return out("rest", "Pas d'intensité aujourd'hui", "Repos tant que tu as de la fièvre ou des courbatures partout.",
                   [alert_chip if c.get("alert") else None], "ill", ["hr"] if c.get("alert") else [])
    if c.get("alert"):
        return out("rest", "Pas d'intensité aujourd'hui",
                   "FC de nuit nettement au-dessus de ta normale 2 nuits de suite : ça arrive avant un rhume, après de "
                   "l'alcool ou une grosse journée.", [alert_chip], "ill", ["hr"])
    # R3 « Reprise »
    rp = c.get("reprise")
    if rp:
        g = rp["gates"]
        chips = [chip("↑", "FC de nuit", f"{SOMMEIL}#coeur", "FC de nuit pas encore redescendue")
                 if not g["night_hr"] else None,
                 chip("↑", "FC en footing · 14 j", "/activities#fc-facile", "FC à allure facile pas encore revenue")
                 if not g["easy_hr"] else None]
        text = ("Toujours pas reparti après 2 semaines : vois un médecin." if rp["see_doctor"]
                else "Footings faciles ; l'intensité quand ta FC en footing est revenue.")
        return out("easy", "Reprise en douceur", text, chips, "reprise",
                   (["hr"] if not g["night_hr"] else []) + ["easy"])
    # R4 after a race or an exceptional outing
    if pr:
        ch = (race_chip({"days": pr["days"], "name": pr["race_name"], "href": pr.get("href")}) if pr.get("race_name")
              else outing_chip(pr["session"], today) if pr.get("session") else None)
        head = "Récupère" if pr["long"] and pr["days"] <= 3 else "Footing facile seulement"
        return out("easy", head, f"Pas d'intensité avant le {pr['free'].strftime('%d/%m')}.", [ch], "race")
    # R5 short night (24-h total < 6 h) after an early wake
    short = c.get("short")
    if short and short["early"]:
        return out("easy", "Séance dure ce matin, sinon facile",
                   f"Une sieste de 20 à 90 min avant {short['tip']} aide ; laisse 30 min avant de courir.",
                   [chip("↓", "Sommeil · 24 h", f"{SOMMEIL}#nuits", "Nuit courte : moins de 6 heures sur 24 heures")],
                   "short", ["sleep"])
    late = bool(short)  # a short night without an early wake: the ladder goes on, the sentence slot says it
    sleep_chip = chip("↓", "Sommeil · 24 h", f"{SOMMEIL}#nuits", "Nuit courte : moins de 6 heures sur 24 heures") \
        if late else None
    also = ["sleep"] if late else []
    # R6 legs (H)
    big = (c.get("legs") or {}).get("big")
    if big is not None:
        resume = _resume_day(big.day, big.minutes, big.dplus, today)
        return out("easy", "Endurance facile aujourd'hui", f"Séance dure possible {resume}.",
                   [outing_chip(big, today), sleep_chip], "legs", also)
    if worse and "legs" in why:
        return out("easy", "Endurance facile aujourd'hui", "Jambes lourdes : c'est toi qui sais, on regarde demain.",
                   [sleep_chip], "legs", also)
    # R7 HRV under the band AND (nightly HR above OR « moins bien ») (H), never in race week
    if not race_week and c.get("hrv") == "below" and (c.get("hr") == "above" or worse):
        both = c.get("hr") == "above"
        chips = [chip("↓", "VFC · 7 nuits", f"{SOMMEIL}#coeur", "VFC sous ta normale sur 7 nuits"),
                 chip("↑", "FC de nuit · 7 nuits", f"{SOMMEIL}#coeur", "FC de nuit au-dessus de ta normale sur 7 nuits")
                 if both else sleep_chip]
        text = ("Reprends l'intensité quand elles reviennent dans ta normale." if both
                else "Tu te sens moins bien aussi : reprends quand ta VFC revient.")
        return out("easy", "Garde ta séance facile", text, chips, "hrv", (["hrv", "hr"] if both else ["hrv"]) + also)
    # R8 easy-pace HR flag AND « moins bien » (H)
    if worse and (c.get("easy") or {}).get("flag"):
        return out("easy", "Garde ta séance facile", "Cœur plus haut en footing et tu te sens moins bien.",
                   [chip("↑", "FC en footing · 14 j", "/activities#fc-facile", "FC à allure facile plus haute"),
                    sleep_chip], "easy_hr", ["easy"] + also)
    # R9 « moins bien » alone (H): the athlete's own call (Saw 2016)
    if worse:
        return out("easy", "Garde ta séance facile", "Tu te sens moins bien : c'est toi qui sais, on regarde demain.",
                   [sleep_chip], "feel", also)
    # R10 race week J-7 → J-3
    if nr and 3 <= nr["days"] <= 7:
        return out("ok", "Semaine de course : séance prévue, sans en rajouter", SHORT_LATE if late else None,
                   [race_chip({**nr, "days": -nr["days"]}), sleep_chip], "race_week", also)
    # R11 nothing against the plan
    if (c.get("hrv") == "in" and c.get("hr") == "in") or c.get("sessions42", 0) >= MIN_SESSIONS_42:
        return out("ok", "Séance prévue : rien ne s'y oppose", SHORT_LATE if late else None, [sleep_chip], "plan",
                   also)
    # R12 nothing to go on
    text = (SHORT_LATE if late else "Il me faut 6 séances sur 6 semaines."
            if c.get("has_sessions") or c.get("has_watch") else "Connecte Strava ou ta montre dans Réglages.")
    return out("unknown", "Pas encore d'avis", text, [sleep_chip], "none", also)


# ── tiles ───────────────────────────────────────────────────────────────────

STATUS = {  # (metric, status) → (glyph, word, tone): direction by glyph AND word, never colour alone
    ("hr", "above"): ("▲", "au-dessus", "warn"), ("hr", "below"): ("▼", "en dessous", "muted"),
    ("hrv", "below"): ("▼", "en dessous", "warn"), ("hrv", "above"): ("▲", "au-dessus", "muted"),
    ("sleep", "below"): ("▼", "plus court", "warn"), ("sleep", "above"): ("▲", "plus long", "muted"),
    ("sleep", "short"): ("▼", "moins de 6 h", "warn"),
}
IN_BAND = ("●", "dans ta normale", "muted")
NIGHTLY = ("hr", "hrv", "sleep")


def make_tiles(stats: dict, drivers: list[str], reprise: bool) -> list[dict]:
    """At most 3 tiles. `stats[key]` (hr, hrv, sleep, easy): {label, text (the
    printed value) | None, unit, spoken, status, means (14 values), band,
    href, gap; easy: word, flag}. Order: the decision's drivers, then the
    out-of-band tiles, then FC de nuit, VFC, Sommeil; FC en footing (only when
    flagged or during « Reprise ») takes the last slot."""
    cand = [k for k in NIGHTLY if stats.get(k) and stats[k]["text"] is not None]
    order = sorted(cand, key=lambda k: (k not in drivers, stats[k].get("status") not in ("above", "below", "short"),
                                        NIGHTLY.index(k)))
    easy = stats.get("easy")
    if easy and (easy.get("flag") or reprise):
        keys = (["easy"] + order[:2]) if "easy" in drivers else (order[:2] + ["easy"])
    else:
        keys = order[:3]
    out = []
    for k in keys:
        s = stats[k]
        if k == "easy":
            glyph, word, tone = s.get("word") or (None, None, "muted")
        else:
            st = s.get("status")
            glyph, word, tone = STATUS.get((k, st)) or (IN_BAND if st == "in" else (None, None, "muted"))
        is_driver = k in drivers
        aria = (f"{s['label']} : {s['spoken']}" + (f", {word}" if word else "")
                + (". Facteur de la décision" if is_driver else ""))
        out.append({"key": k, **viz_tile(s["label"], s["text"], s["unit"], s["means"], s.get("band"), word=word,
                                         glyph=glyph, tone=tone, href=s["href"], aria=aria, driver=is_driver,
                                         gap=s.get("gap"))})
    return out


def sparse_line(missing: list[str], seen: dict) -> str | None:
    """One muted line for the nightly tiles that could not show (fewer than 3
    usable nights in the last 7): why, once. `seen`: {measured: nights with HR
    or HRV in the last 7, race: how many sit around a race, nap: COROS
    nap-day HR left out, tag: the commonest other context word}."""
    if not missing:
        return None
    m = seen["measured"]
    if m < 3:
        if m == 0:
            return "Pas de nuit mesurée ces 7 jours : je juge sur tes séances et ton ressenti."
        s = "s" if m > 1 else ""
        return f"{m} nuit{s} mesurée{s} sur 7 : je juge sur tes séances et ton ressenti."
    both = len(missing) == 2
    who = "FC et VFC de nuit" if both else "FC de nuit" if missing == ["hr"] else "VFC"
    judged = "pas jugées" if both else "pas jugée"
    if seen.get("race"):
        return f"Nuits autour de ta course : {who} {judged}."
    if missing == ["hr"] and seen.get("nap"):
        return "FC de nuit des jours avec sieste : pas encore comptée (COROS)."
    if seen.get("tag"):
        return f"Nuits {GLYPH['tag']} {seen['tag']} : {who} {judged}."
    return f"{who} : pas assez de nuits ces 7 jours."


# ── FC en footing (sante_training.easy_watch, shared with Activités › A3) ────

EASY_DAYS = 14  # the sparkline


def easy_stats(model: dict | None, today: date, reprise: bool) -> dict | None:
    """The FC en footing tile (shown when « à surveiller », or during
    « Reprise »): the mean bpm of the 2 latest easy runs over their normal —
    the number Activités › FC en footing says in words, printed here only.
    The sparkline is that value as it stood on each of the last 14 days."""
    from app.services import sante_training as st

    base = {"label": "FC en footing · 14 j", "unit": "bpm", "href": "/activities#fc-facile"}
    e = st.easy_watch(model, today)
    if not e:
        return {**base, "text": None, "spoken": "pas de footing mesuré", "means": [None] * EASY_DAYS, "band": None,
                "gap": "pas de footing mesuré", "word": (None, None, "muted"), "flag": False} if reprise else None
    means = [(st.easy_watch(model, today - timedelta(days=k)) or {}).get("value") for k in range(EASY_DAYS - 1, -1, -1)]
    v = round(e["value"])
    word = (("▲", "à surveiller", "warn") if e["flag"] else ("▲", "au-dessus", "muted") if v >= st.EASY_BPM
            else ("●", "comme d'habitude", "muted"))
    return {**base, "text": signed(v), "spoken": f"{signed(v)} battements par minute à la même allure",
            "means": means, "band": (-st.EASY_BPM, st.EASY_BPM), "word": word, "flag": e["flag"],
            "status": "above" if e["flag"] else None}
