WEEKLY_DIGEST_SYSTEM_PROMPT = """Tu es un coach d'endurance exigeant, style Philippe Lucas. Tu rédiges le bilan hebdomadaire comme un vrai coach le ferait à son athlète : sans filtre, sans complaisance, avec des punchlines.

Tu analyses la semaine et tu poses le verdict. Si la semaine était molle, tu le dis. Si c'était solide, tu le reconnais sèchement et tu enchaînes sur ce qu'il faut faire ensuite. Pas de "bravo pour cette belle semaine" — ça c'est pour les apps de méditation.

## Ton et style
- Direct et cash. Tu secoues quand il faut, tu valides quand c'est mérité.
- Concret et spécifique avec des données — jamais de généralités creuses
- Punchlines mémorables. Ex: "3 séances en 7 jours alors que t'en faisais 5 le mois dernier — c'est pas de la récup, c'est de la démission." ou "Semaine propre. Charge maîtrisée, splits réguliers. Là tu bosses comme un pro."
- Adapté au niveau : tu ne parles pas à un débutant comme à un confirmé

## Règles
- Ne jamais inventer de données
- Comparer avec la semaine précédente et la moyenne 4 semaines
- Identifier les tendances (progression, stagnation, régression)
- Adapter les recommandations à la charge récente
- Si l'athlète a un objectif course, intégrer des conseils de préparation

## Format de sortie obligatoire

Tu DOIS répondre UNIQUEMENT en JSON valide :

{
    "summary": "3-4 phrases résumant la semaine : volume, intensité, qualité globale, tendance.",
    "highlights": ["2 à 4 points positifs ou réalisations notables de la semaine"],
    "recommendations": ["2 à 3 suggestions concrètes pour la semaine à venir"],
    "volume_assessment": "Court résumé de la tendance volume (ex: 'Volume en hausse de 15% vs semaine précédente, stable vs moyenne 4 semaines.')"
}

## Description des champs

### summary
3-4 phrases couvrant : le volume total, le type d'effort dominant, la qualité générale, et la tendance par rapport aux semaines précédentes.

### highlights
2-4 réalisations concrètes : meilleure allure, sortie longue réussie, régularité, bon ratio effort/récupération, etc.

### recommendations
2-3 suggestions spécifiques pour la semaine suivante : type de séance, volume cible, récupération, points techniques.

### volume_assessment
Une phrase comparant le volume de la semaine avec la semaine précédente et la moyenne 4 semaines.
"""


def build_weekly_digest_prompt(
    week_activities: list[dict],
    this_week_load: dict,
    prev_week_load: dict,
    avg_4w_load: dict,
    race_goal: dict | None = None,
) -> str:
    """Build the user message for weekly digest generation."""
    lines = ["## Bilan de la semaine\n"]

    # This week's load
    lines.append("### Volume cette semaine")
    lines.append(
        f"- Distance : {this_week_load.get('distance_km', 0):.1f} km"
    )
    lines.append(
        f"- Durée : {this_week_load.get('duration_hours', 0):.1f}h"
    )
    lines.append(
        f"- Séances : {this_week_load.get('count', 0)}"
    )

    # Comparison
    lines.append("\n### Comparaison")
    lines.append(
        f"- Semaine précédente : {prev_week_load.get('distance_km', 0):.1f} km, "
        f"{prev_week_load.get('duration_hours', 0):.1f}h, "
        f"{prev_week_load.get('count', 0)} séances"
    )
    lines.append(
        f"- Moyenne 4 semaines : {avg_4w_load.get('distance_km', 0):.1f} km/sem, "
        f"{avg_4w_load.get('duration_hours', 0):.1f}h/sem, "
        f"{avg_4w_load.get('count', 0):.0f} séances/sem"
    )

    # Activities detail
    if week_activities:
        lines.append("\n### Activités de la semaine\n")
        for act in week_activities:
            dist_km = act.get("distance", 0) / 1000
            sport = act.get("sport_type", "?")
            name = act.get("name", "?")
            date_str = act.get("start_date", "?")
            if hasattr(date_str, "strftime"):
                date_str = date_str.strftime("%A %d/%m")
            elif isinstance(date_str, str) and len(date_str) > 10:
                date_str = date_str[:10]

            moving_time = act.get("moving_time", 0)
            h, remainder = divmod(moving_time, 3600)
            m, s = divmod(remainder, 60)
            time_str = f"{h}h{m:02d}" if h else f"{m}'"

            pace_str = ""
            avg_speed = act.get("average_speed")
            if avg_speed and avg_speed > 0:
                if sport in ("Ride", "VirtualRide"):
                    pace_str = f" · {avg_speed * 3.6:.1f} km/h"
                elif sport not in ("Swim",) and dist_km > 0:
                    pace_km = 1000 / avg_speed
                    pm, ps = divmod(int(pace_km), 60)
                    pace_str = f" · {pm}:{ps:02d}/km"

            hr_str = ""
            if act.get("average_heartrate"):
                hr_str = f" · FC {act['average_heartrate']:.0f}"

            elev_str = ""
            if act.get("total_elevation_gain", 0) > 10:
                elev_str = f" · D+{act['total_elevation_gain']:.0f}m"

            lines.append(
                f"- {date_str} | {sport} | {name} | {dist_km:.1f} km | {time_str}{pace_str}{hr_str}{elev_str}"
            )

    # Race goal
    if race_goal:
        lines.append("\n### Objectif course")
        lines.append(f"- Course : {race_goal.get('name', '?')}")
        lines.append(f"- Date : {race_goal.get('date', '?')}")
        lines.append(f"- Distance : {race_goal.get('distance_km', '?')} km")
        days = race_goal.get("days_remaining")
        if days is not None:
            lines.append(f"- J-{days}")

    lines.append("\n## Instructions")
    lines.append("Génère le bilan hebdomadaire. Réponds uniquement en JSON valide.")

    return "\n".join(lines)
