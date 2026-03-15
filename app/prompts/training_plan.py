TRAINING_PLAN_SYSTEM_PROMPT = """\
Tu es un coach d'endurance expert. Tu conçois des plans d'entraînement structurés sur plusieurs semaines.

Tu dois produire un plan réaliste basé sur :
- le niveau actuel de l'athlète (volume, allure, FC)
- son objectif (course cible, progression générale)
- le temps disponible jusqu'à l'objectif
- les principes de périodisation (progressivité, récupération, spécificité)

## Principes de planification

- Augmenter le volume max 10% / semaine
- Prévoir 1 semaine allégée toutes les 3-4 semaines
- Intégrer des séances variées : endurance, seuil, VMA, sortie longue, récupération
- Adapter au sport principal (course, trail, vélo, natation)
- Si objectif course : inclure une phase d'affûtage (taper) les 2 dernières semaines

## Format de sortie obligatoire

Tu DOIS répondre UNIQUEMENT en JSON valide :

{
    "plan_title": "Titre du plan",
    "duration_weeks": 8,
    "goal_summary": "Résumé de l'objectif en 1-2 phrases",
    "philosophy": "Approche générale du plan en 1-2 phrases",
    "weeks": [
        {
            "week_number": 1,
            "theme": "Construction de base",
            "volume_target_km": 35,
            "intensity": "faible",
            "sessions": [
                {
                    "day": "Lundi",
                    "title": "Endurance fondamentale",
                    "description": "45min footing facile en Z2",
                    "type": "endurance"
                }
            ],
            "coach_note": "Semaine de mise en route"
        }
    ],
    "key_sessions_explanation": "Explication des types de séances clés du plan",
    "coach_advice": "Conseils généraux pour suivre ce plan"
}

## Types de séances possibles
- endurance : footing facile, sortie longue
- seuil : tempo, seuil lactique
- vma : intervalles courts, 30/30
- technique : éducatifs, côtes, foulée
- force : renforcement, PPG
- repos : jour off ou récupération active
- competition : course ou simulation
- specifique : travail spécifique à l'objectif
"""


def build_training_plan_prompt(
    sport: str,
    duration_weeks: int,
    goal: str | None,
    training_load: dict,
    recent_activities: list[dict],
    race_goal: dict | None = None,
    zones: dict | None = None,
) -> str:
    """Build the user message for training plan generation."""
    lines = ["## Demande de plan d'entraînement\n"]

    lines.append(f"- Sport principal : {sport}")
    lines.append(f"- Durée du plan : {duration_weeks} semaines")
    if goal:
        lines.append(f"- Objectif : {goal}")

    if race_goal:
        lines.append(f"\n## Course objectif")
        lines.append(f"- {race_goal['name']} — {race_goal['date']}")
        lines.append(f"- Distance : {race_goal['distance_km']} km")
        lines.append(f"- J-{race_goal['days_remaining']}")

    lines.append("\n## Niveau actuel\n")
    lines.append(
        f"- Volume 7j : {training_load.get('volume_7d_km', 0):.1f} km, "
        f"{training_load.get('volume_7d_hours', 0):.1f}h, "
        f"{training_load.get('count_7d', 0)} séances"
    )
    lines.append(
        f"- Volume 28j : {training_load.get('volume_28d_km', 0):.1f} km, "
        f"{training_load.get('volume_28d_hours', 0):.1f}h, "
        f"{training_load.get('count_28d', 0)} séances"
    )

    if zones:
        if zones.get("hr_zones"):
            lines.append("- Zones FC : " + " / ".join(
                f"Z{z['zone']}: {z['min']}-{z['max']}bpm" for z in zones["hr_zones"]
            ))
        if zones.get("pace_zones"):
            lines.append("- Zones allure : " + " / ".join(
                f"Z{z['zone']}: {z['label']}" for z in zones["pace_zones"]
            ))

    if recent_activities:
        lines.append("\n## Activités récentes\n")
        for act in recent_activities[:10]:
            dist = act.get("distance_km", 0)
            sport_type = act.get("sport_type", "")
            name = act.get("name", "")
            pace = act.get("pace_formatted", "")
            hr = act.get("average_heartrate")
            line = f"- {name} ({sport_type}) — {dist}km"
            if pace:
                line += f", {pace}"
            if hr:
                line += f", FC {hr:.0f}"
            lines.append(line)

    lines.append(f"\nGénère un plan de {duration_weeks} semaines. Réponds uniquement en JSON valide.")
    return "\n".join(lines)
