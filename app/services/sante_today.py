"""Santé v4: the recovery state, from the score (owner, 2026-10-08: « tu te
bases que sur les activités passées pour juger de la récupération »; after
the visual judges: « the state comes from the score, like WHOOP »).

No planned race, no check-in, no training prescription: the state says how
recovered the athlete is, never what to train. It is the band of the final
score (sante_score.score_of): ≥ 70 « Bien récupéré » (ok), 40–69
« Récupération en cours » (warn), < 40 « À ménager » (danger), so the number
and the state never disagree. Under « Bien récupéré » one sentence names the
main reason (sante_score.reason; evidence_final.md, SANTE_V4_SPEC.md §8;
(H) = a PaceForge heuristic, never shown as a finding):
- the nightly-HR illness alert (2 untagged nights in a row, each ≥ median +
  max(2 robust SD, 5 bpm), on a full 14-night band: nights.illness_alert;
  Altini & Plews 2021, Quer 2021: specific, not sensitive; score ≤ 39, H);
- a recovery window after a big effort (sante_training.effort_window: ≥ 10 h
  → 10 days, 6–10 h → 5 days, ≥ 3 h or ≥ 1 500 m D+ on foot → 2 days, H): the
  activity by its name and how long ago it started (« Grosse sortie il y a 6
  jours : Transjeju 100M. », linked to it), never its time: this week's
  Charge ring may print those very hours (each number printed once);
- a 24-h total under 6 h (Craven 2022): « Nuit courte. »;
- else the lowest component: « VFC basse sur 7 nuits. », « FC de nuit haute
  sur 7 nuits. », « Sommeil plus court que d'habitude cette semaine. » or
  « Nuit un peu courte. ».
No score (no nightly signal measured) → no state: one line, « Connecte ta
montre pour ta récupération. » (a watch already sending: « Pas de nuit
mesurée ce matin. »). A quiet chart is not a clean bill of health (Quer 2021).
"""
HR_UP_BPM = 3  # (H) the 7-night nightly HR this far over its median: « haute » in the Contributeurs
WORDS = {"danger": "À ménager", "warn": "Récupération en cours", "ok": "Bien récupéré"}
# the tone is also a shape next to the state's word (a disc, a half disc, a square), never colour alone
GLYPHS = {"ok": "●", "warn": "◐", "danger": "■"}
ILL = ("FC de nuit nettement au-dessus de ta normale 2 nuits de suite : ça arrive avant un rhume, après de "
       "l'alcool ou une grosse journée.")
TEXTS = {"hrv": "VFC basse sur 7 nuits.", "hr": "FC de nuit haute sur 7 nuits.", "short": "Nuit courte.",
         "debt": "Sommeil plus court que d'habitude cette semaine.", "sleep": "Nuit un peu courte."}
NO_WATCH = "Connecte ta montre pour ta récupération."
NO_NIGHT = "Pas de nuit mesurée ce matin."
NAME_MAX = 40  # characters of an activity's name in the sentence (a longer one ends with « … »)


def ago(k: int) -> str:
    """« hier », « il y a 5 jours »."""
    return "hier" if k == 1 else f"il y a {k} jours"


def ago_short(k: int) -> str:
    """The Contributeurs' shorter form: « hier », « il y a 5 j »."""
    return "hier" if k == 1 else f"il y a {k} j"


def activity_name(name: str | None) -> str:
    n = " ".join((name or "").split())
    return n if len(n) <= NAME_MAX else n[:NAME_MAX - 1].rstrip() + "…"


def effort_text(window: dict) -> str:
    """« Grosse sortie il y a 6 jours : Transjeju 100M. »: the activity by its
    name and how long ago it started (its day on Activités and its Charge
    bar's), never by its time."""
    name = activity_name(window["effort"].name)
    when = ago(window["ago"])
    return f"Grosse sortie {when} : {name}." if name else f"Grosse sortie {when}."


def short_text(st: dict, day: dict) -> str:
    """The sentence as a past day's readout says it (« mar. 6 oct. · … »): no
    « il y a » (it would count from that day, not today) and no final stop:
    « Grosse sortie : Transjeju 100M », « VFC basse sur 7 nuits »."""
    if st["key"] == "effort":
        name = activity_name(day["window"]["effort"].name)
        return f"Grosse sortie : {name}" if name else "Grosse sortie"
    return (st["text"] or "").rstrip(".")


def _sleep_text(score: dict) -> str:
    p = next((p for p in score["parts"] if p["key"] == "sleep"), None)
    return TEXTS["debt"] if p and p["debt"] else TEXTS["sleep"]


def state(score: dict, day: dict) -> dict | None:
    """{key, tone, word, glyph, text, href, aria} from the day's score
    (sante_score.score_of) and the day it read (sante._assess: window, …), or
    None without a score. `key`: the reason (ill, effort, short, hrv, hr,
    sleep) or « ok »."""
    if score.get("value") is None:
        return None
    tone, key = score["tone"], score["reason"] or "ok"
    text = href = None
    if key == "ill":
        text = ILL
    elif key == "effort":
        w = day["window"]
        text, href = effort_text(w), f"/activity/{w['effort'].session_id}"
    elif key == "sleep":
        text = _sleep_text(score)
    elif key != "ok":
        text = TEXTS[key]
    word = WORDS[tone]
    return {"key": key, "tone": tone, "word": word, "glyph": GLYPHS[tone], "text": text, "href": href,
            "aria": f"{word}." + (f" {text}" if text else "")}


def no_state_line(has_watch: bool) -> str:
    """The one line without a state: connect a watch, or (one already sending) no night this morning."""
    return NO_NIGHT if has_watch else NO_WATCH
