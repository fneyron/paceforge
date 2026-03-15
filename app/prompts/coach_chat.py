COACH_CHAT_SYSTEM_PROMPT = """\
Tu es un coach sportif expert en endurance (course à pied, trail, vélo, natation, triathlon).
Tu discutes avec un athlète qui utilise PaceForge, une app connectée à Strava.

Tu as accès à ses données d'entraînement récentes ci-dessous. Utilise-les pour personnaliser tes réponses.

Règles :
- Sois concis, direct et actionnable (pas de blabla)
- Tutoie l'athlète
- Réponds en français
- Base tes conseils sur ses données réelles (charge, allure, fréquence cardiaque, etc.)
- Si on te demande un plan, sois précis (allures, durées, récupération)
- Si tu ne connais pas une donnée, dis-le plutôt que d'inventer
- Tu peux utiliser des emojis avec parcimonie
- Ne répète pas les données brutes, analyse-les
- Maximum 300 mots par réponse sauf si l'athlète demande un plan détaillé
"""


def build_coach_context(
    user_data: dict,
    training_load: dict,
    recent_activities: list[dict],
    race_goal: dict | None = None,
    zones: dict | None = None,
) -> str:
    """Build the context block injected into the system prompt."""
    lines = ["## Données de l'athlète"]

    if user_data.get("firstname"):
        lines.append(f"- Prénom : {user_data['firstname']}")
    if user_data.get("weight_kg"):
        lines.append(f"- Poids : {user_data['weight_kg']} kg")

    lines.append("")
    lines.append("## Charge d'entraînement")
    lines.append(f"- 7 jours : {training_load.get('volume_7d_km', 0)} km, "
                 f"{training_load.get('volume_7d_hours', 0)}h, "
                 f"{training_load.get('count_7d', 0)} séances")
    lines.append(f"- 28 jours : {training_load.get('volume_28d_km', 0)} km, "
                 f"{training_load.get('volume_28d_hours', 0)}h, "
                 f"{training_load.get('count_28d', 0)} séances")

    if training_load.get("sport_breakdown_7d"):
        breakdown = ", ".join(
            f"{s}: {km}km" for s, km in training_load["sport_breakdown_7d"].items()
        )
        lines.append(f"- Répartition 7j : {breakdown}")

    if race_goal:
        lines.append("")
        lines.append("## Objectif course")
        lines.append(f"- {race_goal['name']} — {race_goal['date']}")
        lines.append(f"- Distance : {race_goal['distance_km']} km")
        lines.append(f"- J-{race_goal['days_remaining']}")

    if zones:
        lines.append("")
        lines.append("## Zones d'entraînement estimées")
        if zones.get("hr_zones"):
            lines.append("FC : " + " / ".join(
                f"Z{i+1}: {z['min']}-{z['max']} bpm" for i, z in enumerate(zones["hr_zones"])
            ))
        if zones.get("pace_zones"):
            lines.append("Allure : " + " / ".join(
                f"Z{i+1}: {z['label']}" for i, z in enumerate(zones["pace_zones"])
            ))

    if recent_activities:
        lines.append("")
        lines.append("## 10 dernières activités")
        for a in recent_activities[:10]:
            dist = a.get("distance_km", 0)
            dur = a.get("duration_formatted", "")
            sport = a.get("sport_type", "")
            name = a.get("name", "")
            hr = a.get("average_heartrate")
            pace = a.get("pace_formatted", "")
            elev = a.get("total_elevation_gain", 0)

            line = f"- {name} ({sport}) — {dist}km, {dur}"
            if pace:
                line += f", {pace}"
            if hr:
                line += f", FC {hr:.0f}bpm"
            if elev and elev > 50:
                line += f", D+ {elev:.0f}m"
            lines.append(line)

    return "\n".join(lines)
