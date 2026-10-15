"""Short, non-prescriptive interpretation of the nightly signals.

Usual, to watch, or unusual: tones follow sante_score's measured reasons, never a race window or a check-in.
The synthesis shows at most two reasons and keeps the rest in details. The night-HR acute alert retains its
specific wording and existing full-reference rules. No eligible night or reference means no index.
A quiet chart is not a clean bill of health; it cannot establish muscle or tendon recovery.
"""
WORDS = {"danger": "Signaux inhabituels", "warn": "Signaux à surveiller", "ok": "Signaux habituels"}
# the tone is also a shape next to the state's word (a disc, a half disc, a square), never colour alone
GLYPHS = {"ok": "●", "warn": "◐", "danger": "■"}
# v4.4 (owner: « les explications en français ne sont pas claires »): short sentences, « tes valeurs habituelles »
# instead of « ta normale »; the alert's 2 nights are said once, on the FC de nuit card (sante.ALERT_WORD)
ILL = ("Ta FC de nuit est nettement plus haute que d'habitude. Ça arrive avant un rhume, après de l'alcool ou une "
       "grosse journée.")
# the alert's sentence when the breathing rate is up too (nights.illness_alert's resp_up)
ILL_RESP = "Ta respiration aussi est plus rapide que d'habitude."
NO_WATCH = "Connecte ta montre pour voir tes signaux nocturnes."
# Missing data cannot tell us whether the watch recorded a night or has yet to send it.
NO_NIGHT = "Pas de score ce matin : il manque des données de nuit."
# the baselines or the week too thin (owner, 2026-10-14: scientific basis — a reason, never a number)
NOT_YET = "Pas de score ce matin : {label}."


def state(score: dict, day: dict | None = None) -> dict | None:
    """{key, tone, word, glyph, text, aria, reasons} from the day's state (sante_score.score_of), or None without
    one. `key`: the first reason (ill, hrv, hr, resp, short) or « ok », data only; `reasons`: every reason,
    as data; `text`: the illness alert's sentence, else None."""
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
