"""Santé v4: the recovery state, from the score (owner, 2026-10-08: « tu te
bases que sur les activités passées pour juger de la récupération »; after
the visual judges: « the state comes from the score, like WHOOP »).

No planned race, no check-in, no training prescription: the state says how
recovered the athlete is, never what to train. It is the band of the final
score (sante_score.score_of): ≥ 70 « Bonne récupération » (ok), 40–69
« Récupération en cours » (warn), < 40 « Récupération faible » (danger) (H),
so the number and the state never disagree (v4.3, owner: « à ménager » is
not good French).

v4.3 (owner, 2026-10-08 evening: « Ne mentionne pas les sorties dans la
partie Santé, ça complexifie : mets juste les scores »): the page says the
state's word and its glyph, nothing else — no activity named, no reason
sentence — but for the nightly-HR illness alert (2 nights in a row with no
context tag, each ≥ median + max(2 robust SD, 5 bpm), on a full 14-night band:
nights.illness_alert; Altini & Plews 2021, Quer 2021: specific, not
sensitive; score ≤ 39, H), whose one sentence says what it can mean. The
reason (sante_score.reason: the alert, the cap that binds, the joint VFC/FC
pattern, the lowest component) stays as data (`key`), never printed.
No score (no nightly signal measured, like WHOOP: owner, 2026-10-09) → no
state: one line, « Connecte ta montre pour voir ta récupération. » (a watch already
sending: « Pas de score ce matin : ta montre n'a pas enregistré ta nuit. »).
A quiet chart is not a clean bill of health (Quer 2021).
"""
WORDS = {"danger": "Récupération faible", "warn": "Récupération en cours", "ok": "Bonne récupération"}
# the tone is also a shape next to the state's word (a disc, a half disc, a square), never colour alone
GLYPHS = {"ok": "●", "warn": "◐", "danger": "■"}
# v4.4 (owner: « les explications en français ne sont pas claires »): short sentences, « tes valeurs habituelles »
# instead of « ta normale »; the alert's 2 nights are said once, on the FC de nuit row (sante.ALERT_WORD)
ILL = ("Ta FC de nuit est nettement plus haute que d'habitude. Ça arrive avant un rhume, après de l'alcool ou une "
       "grosse journée.")
# the alert's sentence when the breathing rate is over its usual line too (nights.illness_alert's resp_up)
ILL_RESP = "Ta respiration aussi est plus rapide que d'habitude."
NO_WATCH = "Connecte ta montre pour voir ta récupération."
NO_NIGHT = "Pas de score ce matin : ta montre n'a pas enregistré ta nuit."


def state(score: dict, day: dict | None = None) -> dict | None:
    """{key, tone, word, glyph, text, aria} from the day's score
    (sante_score.score_of), or None without a score. `key`: the reason (ill,
    effort, short, joint, resp, hrv, hr, sleep) or « ok », data only; `text`: the
    illness alert's sentence, else None (the page prints the word and the
    glyph only)."""
    if score.get("value") is None:
        return None
    tone, key = score["tone"], score["reason"] or "ok"
    text = ILL if key == "ill" else None
    if text and day and (day.get("alert") or {}).get("resp_up"):
        text = f"{text} {ILL_RESP}"
    word = WORDS[tone]
    return {"key": key, "tone": tone, "word": word, "glyph": GLYPHS[tone], "text": text,
            "aria": f"{word}." + (f" {text}" if text else "")}


def no_state_line(has_watch: bool) -> str:
    """The one line without a state: connect a watch, or (one already sending) no night this morning."""
    return NO_NIGHT if has_watch else NO_WATCH
