"""One place to explain the existing Santé observations, without changing their calculations.

Interpretations describe a personal trend, never diagnose its cause or clear an athlete to train.
The last point, the seven-day trend and a provisional reference remain distinct.
"""

from app.services import sante_load


HRV_CONTEXT = (
    "La VFC décrit la variation du temps entre les battements. Une baisse durable peut accompagner fatigue, "
    "stress, alcool ou maladie. Croise-la avec le sommeil, la FC de nuit et ton ressenti ; elle n’indique pas la cause."
)
HR_CONTEXT = (
    "Une FC de nuit plus haute peut accompagner fatigue, chaleur, alcool ou début de maladie. "
    "À croiser avec ton ressenti."
)


def nightly(metric, card, trend, *, same_series=True, latest_below=False):
    """Explain the already-computed status; latest_below compares only to the drawn personal reference."""
    reference, status, measured = card["reference"], card["status"], card["measurement"]
    detail = HRV_CONTEXT if metric == "hrv" else HR_CONTEXT
    if not measured["is_current"]:
        return {"key": "missing", "text": "Pas de mesure cette nuit ; le graphique garde les relevés datés.",
                "detail": detail}
    if not same_series:
        return {"key": "different_series", "text": "Ce relevé n’est pas comparable à la tendance affichée.",
                "detail": "Chaque source et chaque méthode conservent leur propre référence. " + detail}
    if metric == "hrv" and "SDNN" in (trend or {}).get("source", ""):
        return {"key": "sdnn", "text": "Cette VFC reste descriptive et n’entre pas dans l’indice de récupération.",
                "detail": "La SDNN et la RMSSD mesurent la variabilité différemment et ne se comparent pas directement."}
    if not reference["ready"]:
        return {"key": "building", "text": "Quelques nuits supplémentaires permettront de situer cette valeur.",
                "detail": "La référence utilise les nuits antérieures à la semaine comparée. "
                "Sans référence personnelle suffisante, un nombre seul ne permet pas de dire « bas » ou « haut ». "
                + detail}
    if status["key"] == "few":
        return {"key": "few", "text": "Trop peu de nuits récentes pour dégager une tendance.", "detail": detail}
    if status["key"] == ("below" if metric == "hrv" else "above"):
        text = ("Cette baisse peut accompagner fatigue ou stress." if metric == "hrv" else
                "Cette hausse peut accompagner une fatigue ou un stress physique.")
        if reference["provisional"]:
            text += " Ton repère est encore provisoire."
        return {"key": "low_hrv" if metric == "hrv" else "high_hr", "text": text, "detail": detail}
    if reference["provisional"]:
        return {"key": "provisional", "text": "La tendance se situe par rapport à un repère encore provisoire.",
                "detail": detail}
    if metric == "hrv" and status["key"] == "in" and latest_below:
        return {"key": "latest_low", "text": "Dernière mesure sous le repère, tendance sur 7 jours habituelle.",
                "detail": "Le point est sous le repère affiché. Une nuit isolée ne suffit pas à conclure à une fatigue. "
                + detail}
    return {"key": "usual" if status["key"] == "in" else "other", "text": "", "detail": detail}


def _item(key, title, text, details=(), source=None, date=None, chart_key=None):
    return {"key": key, "title": title, "text": text, "details": [d for d in details if d],
            "source": source, "date": date, "chart_key": chart_key}


def build(page):
    """Consolidate calculation, coverage and provenance text in a single disclosure model."""
    synthesis = page.get("synthesis") or {}
    items = []
    quality = page.get("quality") or {}
    score_details = [f"{row['name']} : {row['state']}." for row in quality.get("rows", [])]
    score_details.extend(r.get("text") for r in synthesis.get("reasons", []) + synthesis.get("more_reasons", [])
                         if r.get("key") == "ill")
    score_details.extend(c.get("detail") for c in synthesis.get("context", []))
    items.append(_item("recovery", "Récupération", "L’indice décrit les signaux nocturnes disponibles.",
                       score_details, date=(synthesis.get("freshness") or {}).get("label")))
    methods = {m["key"]: m for m in synthesis.get("methods", [])}
    for key, metric, title in (("vfc", "hrv", "VFC"), ("fc", "hr", "FC de nuit")):
        card = page.get(key)
        if not card:
            continue
        interpretation = card["interpretation"]
        ref, trend, measured = card["reference"], card["trend"], card["measurement"]
        method = methods.get(metric, {})
        ref_text = (ref.get("detail") or f"Référence personnelle : {ref['n']} nuits mesurées.") if ref["ready"] else (
            f"Référence en construction : {ref['n']}/{ref['required']} nuits mesurées.")
        method_text = method.get("detail")
        if metric == "hrv" and "SDNN" not in method.get("source", ""):
            method_text = "Tendance : RMSSD nocturne, moyenne géométrique sur 7 jours (au moins 3 nuits)."
            if method.get("source") == "COROS":
                method_text += " Au moins 12 points par nuit ; distincte de l’HRV Avg."
        details = [interpretation["detail"], ref_text,
                   f"{trend['label']} : {trend['n']} nuits mesurées.", method_text]
        if ref.get("source") and ref["source"] != measured["source"]:
            details.append(f"Source de la référence : {ref['source']}.")
            if metric == "hrv" and "SDNN" in measured["source"]:
                details.append("Le dernier relevé est une SDNN, distincte de la tendance RMSSD ; il reste hors indice.")
        items.append(_item(key, title, interpretation["text"], details, measured["source"], measured["date_label"],
                           card["key"]))
    sleep = page.get("sleep") or {}
    hero = sleep.get("hero") or {}
    if hero or sleep.get("chart"):
        items.append(_item("sleep", "Sommeil", "Le cadran compare le sommeil mesuré au repère de 8 heures.",
                           [(synthesis.get("sleep_gap") or {}).get("label"),
                            (synthesis.get("sleep_gap") or {}).get("detail"),
                            "Les phases décrivent l’estimation de la montre ; elles ne remplacent pas une mesure en laboratoire."],
                           hero.get("source"), hero.get("label")))
    if page.get("training"):
        reference = page.get("hr_reference") or {}
        if reference.get("reference") is not None:
            label = "FC maximale renseignée" if reference.get("source") == "confirmed" else "Repère observé provisoire"
            reference_text = (f"{label} : {reference['reference']} bpm. La charge, les zones et les plafonds automatiques "
                              "de course utilisent cette même référence.")
        else:
            reference_text = "Sans référence cardio suffisante, la charge est estimée à partir des durées."
        items.append(_item("training", "Entraînement", "La charge des 7 derniers jours est comparée à ta semaine habituelle.",
                           ["100 % est un repère, pas un objectif. La charge tient compte de la durée et de l’intensité des activités importées.",
                            reference_text,
                            f"Le pourcentage apparaît après {sante_load.USUAL_LABEL_WEEKS} semaines actives "
                            f"parmi les {sante_load.USUAL_WEEKS} semaines précédentes."]))
    # the night HR's trend is the FC de nuit card's (above)
    for card in ([page["respiration"]] if page.get("respiration") else []) + [
            t for t in page.get("trends", []) if t["key"] != "hr_night"]:
        respiratory = card["key"] == "resp_night"
        details = [card.get("trend")]
        if respiratory:
            details.append("Une hausse persistante est à croiser avec ta FC et ton ressenti ; elle n’indique pas la cause. "
                           "Le repère du graphique est une médiane récente, distincte du seuil utilisé pour le signal nocturne.")
        text = "Nombre moyen de respirations par minute pendant la nuit." if respiratory else card["meaning"]
        items.append(_item(card["key"], card["title"], text, details, card.get("source"),
                           card.get("latest"), (card.get("chart") or {}).get("key")))
    return {"title": "Comprendre mes indicateurs", "intro": synthesis.get("detail", ""), "items": items}
