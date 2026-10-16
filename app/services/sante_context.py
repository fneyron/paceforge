"""Compact, dated context for the nightly signals; never a training clearance or a healing deadline."""
from app.services import sante_training as st, viz

CONTEXT_DAYS = 28  # Display lookback only, not a physiological recovery window.


def recent_effort(efforts, sessions, today):
    """One long recorded effort or provider-tagged race, using only imported activity data."""
    candidates = []
    for e in efforts:
        day = e.end.date()  # display calendar days since the real finish, not the sleep cycle's anchor
        ago = (today - day).days
        if e.kind == "ultra" and e.end.date() <= today and 0 <= ago <= CONTEXT_DAYS:
            candidates.append((day, e.session_id, e.name or "un effort long", "effort"))
    for s in sessions:
        finished = st.local_end(s)
        day = finished.date()
        ago = (today - day).days
        if s.is_race and finished.date() <= today and 0 <= ago <= CONTEXT_DAYS:
            candidates.append((day, s.id, s.name or "une course enregistrée", "race"))
    if not candidates:
        return []
    day, sid, name, kind = max(candidates, key=lambda x: (x[0], x[3] == "effort", x[1]))
    return [{"key": "after_effort", "label": f"Après {name} · J+{(today - day).days}",
             "detail": "Une tendance nocturne habituelle ne mesure pas la récupération des muscles et des tendons après cet effort.",
             "tone": "neutral", "source": "Activité enregistrée", "activity_id": sid,
             "date": day.isoformat(), "kind": kind}]


def synthesis(day, nights, calendar_day, efforts, sessions, gap):
    score, state = day["score"], day["state"]
    assessed = day["day"]
    current = assessed == calendar_day and day["night_measured"]
    latest = max((d for d, n in nights.items() if d <= calendar_day
                  and any(v is not None for v in (n.asleep, n.hr, n.hrv))), default=None)
    n = nights.get(assessed)
    sources = sorted({viz.source_label(s) for s in (n.source, n.hr_source, n.hrv_source) if s}) if n else []
    if current:
        stamp = f"Nuit du {viz.d_short(assessed)}"
    elif assessed != calendar_day:
        stamp = f"Bilan du {viz.d_short(assessed)} · nuit du {viz.d_short(calendar_day)} en attente"
    else:
        stamp = f"Nuit du {viz.d_short(calendar_day)} non reçue"
        if latest:
            stamp += f" · dernière nuit le {viz.d_short(latest)}"
    reasons = list((state or score).get("reasons") or [])
    if score["value"] is None and score.get("missing"):
        reasons = [{"key": score["missing"]["key"], "label": score["missing"]["label"],
                    "text": None, "tone": "none"}]
    methods = []
    for metric, label in (("hrv", "VFC"), ("hr", "FC de nuit")):
        trend = day["trends"].get(metric)
        if not trend:
            continue
        source = trend["source"]
        if metric == "hr":
            detail = "Moyenne pendant la nuit, distincte de la FC de repos mesurée sur la journée."
        elif "(SDNN sommeil)" in source:
            detail = "SDNN mesurée pendant le sommeil ; affichée séparément et exclue de l’indice RMSSD."
        else:
            detail = "RMSSD nocturne ; la tendance est une moyenne géométrique sur 7 jours, avec au moins 3 nuits."
            if source == "COROS":
                detail += " Chaque nuit utilise au moins 12 points de sa fenêtre principale, pas l’HRV Avg ni la plage COROS."
        methods.append({"key": metric, "label": label, "source": viz.source_label(source), "detail": detail})
    measured = gap["n"] + gap.get("raced", 0)
    uncertain = measured < gap["days"]
    gap_detail = (f"Comparaison au repère de 8 h, pas une mesure de ton besoin personnel. "
                  f"{measured}/{gap['days']} nuits documentées.")
    if uncertain:
        gap_detail += " Les nuits manquantes augmentent l’incertitude ; elles ne comptent pas comme zéro sommeil."
    return {
        "signal": {"label": "Signaux nocturnes", "tone": score["tone"] or "none", "value": score["value"],
                   "unit": "/100", "summary": state["word"] if state else "Mesures insuffisantes"},
        "reasons": reasons[:2], "more_reasons": reasons[2:],
        "freshness": {"day": assessed.isoformat(), "label": stamp, "current": current, "missing": not current,
                      "source": " · ".join(sources), "last_measured": latest.isoformat() if latest else None},
        "context": recent_effort(efforts, sessions, calendar_day), "methods": methods,
        "sleep_gap": {"minutes": gap["minutes"], "n": gap["n"], "days": gap["days"], "estimated": True,
                      "uncertain": uncertain, "label": gap["text"], "detail": gap_detail},
        "detail": "Indice descriptif des signaux reçus, pas un pourcentage de récupération ni une consigne d’entraînement.",
    }
