"""Santé v4: the recovery state, from past activities and the nights only.

Owner, 2026-10-08: « tu te bases que sur les activités passées pour juger de
la récupération ». No planned race, no check-in, no training prescription: the
state says how recovered the athlete is, never what to train. First match
wins (evidence_final.md, SANTE_V4_SPEC.md; (H) = a PaceForge heuristic, never
shown as a finding):
1. the nightly-HR illness alert (2 untagged nights in a row, each ≥ median +
   max(2 robust SD, 5 bpm), on a full 14-night band: nights.illness_alert;
   Altini & Plews 2021, Quer 2021: specific, not sensitive) → « À ménager »;
2. a recovery window after a big effort (sante_training.effort_window: ≥ 10 h
   → 10 days, 6–10 h → 5 days, ≥ 3 h or ≥ 1 500 m D+ on foot → 2 days, H) →
   « Récupération en cours », the sentence names the activity;
3. the 7-night HRV under its band AND the 7-night nightly HR ≥ its median +
   3 bpm (H; Plews 2013, 2014) → « Récupération en cours »;
4. a 24-h total (the main night + the day's naps) under 6 h (Craven 2022) →
   « Récupération en cours »;
5. otherwise « Bien récupéré » with at least one nightly signal measured (no
   sentence), else no state: one line, « Connecte ta montre pour ta
   récupération. » (a watch already sending: « Pas de nuit mesurée ce
   matin. »).
A single signal outside its normal shows in the Contributeurs and never moves
the state (the alert is the exception). A quiet chart is not a clean bill of
health (Quer 2021).
"""
from app.services.nights import SHORT_DAY_MIN
from app.services.viz import hm, hm_long

HR_UP_BPM = 3  # (H) the 7-night nightly HR this far over its median, with the HRV under its band: rung 3
TONES = {"ill": "danger", "effort": "warn", "hrv": "warn", "short": "warn", "ok": "ok"}
WORDS = {"danger": "À ménager", "warn": "Récupération en cours", "ok": "Bien récupéré"}
SHORT_WORDS = {"danger": "à ménager", "warn": "en cours", "ok": "bien récupéré"}  # under the ring
# the tone is also a shape next to the state's word (a disc, a half disc, a square: sante_page.html), never colour alone
ILL = ("FC de nuit nettement au-dessus de ta normale 2 nuits de suite : ça arrive avant un rhume, après de "
       "l'alcool ou une grosse journée.")
LOW_HRV = "VFC basse et FC de nuit haute sur 7 nuits."
SHORT = "Nuit courte."
NO_WATCH = "Connecte ta montre pour ta récupération."
NO_NIGHT = "Pas de nuit mesurée ce matin."


def ago(k: int) -> str:
    """« hier », « il y a 5 jours »."""
    return "hier" if k == 1 else f"il y a {k} jours"


def ago_short(k: int) -> str:
    """The Contributeurs' shorter form: « hier », « il y a 5 j »."""
    return "hier" if k == 1 else f"il y a {k} j"


def effort_text(window: dict) -> str:
    """« Grosse sortie de 16h53 il y a 5 jours. » (its own time, stops included)."""
    return f"Grosse sortie de {hm(window['effort'].minutes)} {ago(window['days'])}."


def effort_spoken(window: dict) -> str:
    return f"Grosse sortie de {hm_long(window['effort'].minutes)} {ago(window['days'])}."


def state(c: dict) -> dict | None:
    """{key, tone, word, short, text, href, aria} or None (nothing to go
    on). `c`: alert (nights.illness_alert or None), window (effort_window or
    None), hrv (the 7-night status: « above » / « below » / « in » / None),
    hr_up (the 7-night nightly HR ≥ its median + 3 bpm), tst24 (today's 24-h
    total in minutes, or None), measured (a nightly signal is in the score)."""
    def out(key, text=None, href=None, spoken=None):
        tone = TONES[key]
        return {"key": key, "tone": tone, "word": WORDS[tone], "short": SHORT_WORDS[tone], "text": text,
                "href": href, "aria": f"{WORDS[tone]}." + (f" {spoken or text}" if text else "")}

    if c.get("alert"):
        return out("ill", ILL)
    w = c.get("window")
    if w:
        return out("effort", effort_text(w), f"/activity/{w['effort'].session_id}", effort_spoken(w))
    if c.get("hrv") == "below" and c.get("hr_up"):
        return out("hrv", LOW_HRV)
    tst = c.get("tst24")
    if tst is not None and tst < SHORT_DAY_MIN:
        return out("short", SHORT)
    if c.get("measured"):
        return out("ok")
    return None


def no_state_line(has_watch: bool) -> str:
    """The one line without a state: connect a watch, or (one already sending) no night this morning."""
    return NO_NIGHT if has_watch else NO_WATCH
