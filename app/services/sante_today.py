"""Santé v4: the day's state, in words (owner, 2026-10-08: « tu te bases que sur les activités passées pour juger
de la récupération »; 2026-10-14: scientific basis — the state is the rule-based reading of sante_score.score_of,
no longer the band of a weighted score).

No planned race, no check-in, no training prescription: the state says how the night signals read, never what to
train. Its tones: « Bonne récupération » (ok, no reason), « Récupération en cours » (warn: an orange reason),
« Récupération faible » (danger: the night-HR illness alert, or the first days after an ultra) (H). Each reason is
listed as data (`reasons`: the wording on the page is the owner's to validate); the page prints the state's word
and its glyph, and the illness alert's one sentence (2 nights in a row with no context tag, each ≥ median + max(2
robust SD, 5 bpm), on a full 14-night baseline: nights.illness_alert; Altini & Plews 2021, Quer 2021: specific,
not sensitive), what it can mean.
No state (the night or the baselines missing) → one line saying why (C8: a reason, never a number).
A quiet chart is not a clean bill of health (Quer 2021).
"""
WORDS = {"danger": "Récupération faible", "warn": "Récupération en cours", "ok": "Bonne récupération"}
# the tone is also a shape next to the state's word (a disc, a half disc, a square), never colour alone
GLYPHS = {"ok": "●", "warn": "◐", "danger": "■"}
# v4.4 (owner: « les explications en français ne sont pas claires »): short sentences, « tes valeurs habituelles »
# instead of « ta normale »; the alert's 2 nights are said once, on the FC de nuit card (sante.ALERT_WORD)
ILL = ("Ta FC de nuit est nettement plus haute que d'habitude. Ça arrive avant un rhume, après de l'alcool ou une "
       "grosse journée.")
# the alert's sentence when the breathing rate is up too (nights.illness_alert's resp_up)
ILL_RESP = "Ta respiration aussi est plus rapide que d'habitude."
NO_WATCH = "Connecte ta montre pour voir ta récupération."
# Missing data cannot tell us whether the watch recorded a night or has yet to send it.
NO_NIGHT = "Pas de score ce matin : il manque des données de nuit."
# the baselines or the week too thin (owner, 2026-10-14: scientific basis — a reason, never a number)
NOT_YET = "Pas de score ce matin : {label}."


def state(score: dict, day: dict | None = None) -> dict | None:
    """{key, tone, word, glyph, text, aria, reasons} from the day's state (sante_score.score_of), or None without
    one. `key`: the first reason (ill, effort, hrv, hr, resp, short) or « ok », data only; `reasons`: every reason,
    as data; `text`: the illness alert's sentence, else None (the page prints the word and the glyph only)."""
    if score.get("value") is None:
        return None
    tone, key = score["tone"], score["reason"] or "ok"
    reasons = list(score.get("reasons") or [])
    text = ILL if key == "ill" else None
    if text and day and (day.get("alert") or {}).get("resp_up"):
        text = f"{text} {ILL_RESP}"
    if reasons and reasons[0]["key"] == "ill":
        reasons[0] = {**reasons[0], "text": text}
    word = WORDS[tone]
    return {"key": key, "tone": tone, "word": word, "glyph": GLYPHS[tone], "text": text, "reasons": reasons,
            "aria": f"{word}." + (f" {text}" if text else "")}


def no_state_line(has_watch: bool, missing: dict | None = None) -> str:
    """The one line without a state: connect a watch; else why there is none (the baselines building, a week too
    thin, last night measured by another watch or calculation), or no night this morning."""
    if not has_watch:
        return NO_WATCH
    if missing and missing.get("key") in ("building", "few", "elsewhere"):
        return NOT_YET.format(label=missing["label"])
    return NO_NIGHT
