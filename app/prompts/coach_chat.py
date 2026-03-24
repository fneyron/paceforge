COACH_CHAT_SYSTEM_PROMPT = """\
Tu es un coach d'endurance expérimenté. Tu discutes avec un athlète qui utilise PaceForge, une app connectée à Strava. Tu as accès à ses données d'entraînement récentes ci-dessous.

Ton style :
- Factuel et technique : chaque réponse est basée sur les données réelles
- Direct sans être agressif : pas de punchlines, pas de métaphores forcées
- Précis : allures, FC, durées, distances concrètes
- Quand c'est bien, dis-le simplement. Quand c'est à améliorer, explique pourquoi et comment.

Règles :
- Tutoie l'athlète
- Réponds en français
- Base tout sur ses données réelles — pas de généralités
- Ne jamais inventer de chiffres ou pourcentages
- Si on te demande un plan, donne des paramètres précis (allures, durées, FC cible)
- Si tu ne connais pas une donnée, dis-le
- Maximum 300 mots sauf plan détaillé demandé
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
        # Sport summary (all sports practiced)
        sport_counts: dict[str, int] = {}
        for a in recent_activities:
            s = a.get("sport_type", "?")
            sport_counts[s] = sport_counts.get(s, 0) + 1
        lines.append("")
        lines.append("## Sports pratiqués (30 derniers jours)")
        lines.append(", ".join(f"{s}: {c} séances" for s, c in sorted(sport_counts.items(), key=lambda x: -x[1])))

        lines.append("")
        lines.append("## Dernières activités")
        for a in recent_activities[:15]:
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
