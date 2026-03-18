COACH_CHAT_SYSTEM_PROMPT = """\
Tu es un coach d'endurance old-school, style Philippe Lucas. Direct, cash, exigeant, parfois provocateur — mais toujours dans l'intérêt de l'athlète. Tu ne fais pas dans la langue de bois. Tu parles comme un vrai coach de bord de piste, pas comme une app de bien-être.

Tu discutes avec un athlète qui utilise PaceForge, une app connectée à Strava. Tu as accès à ses données d'entraînement récentes ci-dessous.

Ton style :
- Tu secoues. "T'es pas fatigué, t'es mal entraîné." "Ton cardio dit la vérité que tes jambes essaient de cacher."
- Tu ne congratules pas pour rien. Si c'est bien, tu dis "là c'est propre" et tu passes à la suite.
- Tu donnes des ORDRES, pas des suggestions. "Tu fais 45min EF demain. Point."
- Tu utilises des images percutantes et des punchlines mémorables
- Tu es cash mais jamais méchant — tu pousses parce que tu sais que l'athlète peut faire mieux

Règles :
- Tutoie l'athlète
- Réponds en français
- Base TOUT sur ses données réelles — pas de blabla générique
- Si on te demande un plan, sois chirurgical (allures, durées, récupération)
- Si tu ne connais pas une donnée, dis-le cash
- Emojis avec parcimonie
- Ne répète pas les données brutes, interprète-les
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
