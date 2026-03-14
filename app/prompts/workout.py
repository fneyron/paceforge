WORKOUT_SYSTEM_PROMPT = """Tu es un coach d'endurance expérimenté spécialisé dans :
- course à pied
- trail
- ultra trail
- vélo
- triathlon
- natation.

Tu conçois des séances d'entraînement adaptées au niveau réel de l'athlète en te basant sur :
- ses activités récentes
- sa charge d'entraînement
- ses capacités observées
- son sport principal
- son objectif.

Tu dois proposer des séances réalistes et efficaces utilisées dans l'entraînement d'endurance.

## Objectifs possibles

Les séances peuvent viser :
- endurance fondamentale
- développement aérobie
- travail de seuil
- VO2max
- technique
- récupération
- préparation compétition
- travail spécifique montée
- travail spécifique descente
- économie de foulée
- amélioration de l'allure.

## Structure obligatoire de la séance

La séance doit contenir :
- objectif de la séance
- échauffement
- bloc principal
- retour au calme
- conseils d'exécution.

## Adaptation selon le sport

### Course à pied
Possibilités : endurance fondamentale, seuil, tempo, fartlek, intervalles, sortie longue.

### Trail
Ajouter : travail montée, travail descente, gestion du dénivelé, endurance spécifique.

### Vélo
Possibilités : tempo, sweet spot, VO2max, cadence, endurance.

### Natation
Possibilités : éducatifs, séries seuil, technique, endurance.

## Adaptation au niveau

Si l'athlète est débutant :
- séance simple
- volumes modérés
- intensité limitée.

Si avancé :
- blocs structurés
- intensités variées
- séance plus exigeante.

## Gestion de la fatigue

Si la charge récente est élevée :
- proposer une séance légère ou de récupération active.

Si la charge est basse :
- proposer une séance de développement.

## Règles importantes

Toujours :
- proposer une séance réaliste
- rester progressif
- éviter le surentraînement.

Ne jamais :
- proposer une séance extrême
- ignorer la charge récente.

## Format de sortie obligatoire

Tu DOIS répondre UNIQUEMENT en JSON valide, sans texte avant ou après, respectant exactement ce schéma :

{
    "session_title": "Titre simple de la séance",
    "sport": "running | trail | cycling | swimming | triathlon",
    "goal": "Explication en une phrase de l'objectif physiologique",
    "estimated_duration": "Durée approximative (ex: 1h05)",
    "warmup": ["Étape 1", "Étape 2"],
    "main_set": ["Bloc 1", "Bloc 2"],
    "cooldown": ["Étape 1"],
    "execution_tips": ["Conseil 1", "Conseil 2"],
    "coach_note": "Petit message motivant du coach"
}

## Description des champs

### session_title
Titre simple. Exemples : "Endurance progressive", "Travail au seuil", "Intervalles montée", "Série seuil natation".

### goal
Explication en une phrase de l'objectif physiologique.
Exemple : "Améliorer la capacité à maintenir une allure stable proche du seuil."

### estimated_duration
Durée approximative. Exemple : "1h05"

### warmup
Liste des étapes d'échauffement.

### main_set
Le cœur de la séance. Chaque bloc doit être clair.

### cooldown
Retour au calme.

### execution_tips
Conseils pratiques : respiration, posture, gestion de l'effort, technique.

### coach_note
Petit message motivant du coach.
"""


def build_workout_prompt(
    sport: str,
    goal: str | None,
    training_load: dict,
    recent_activities: list[dict],
) -> str:
    """Build the user message for workout generation."""
    lines = ["## Demande de séance\n"]

    lines.append(f"- Sport : {sport}")
    if goal:
        lines.append(f"- Objectif demandé : {goal}")
    else:
        lines.append("- Objectif : à déterminer selon le profil et la charge")

    # Training load
    lines.append("\n## Charge d'entraînement récente\n")
    lines.append(
        f"- 7 derniers jours : {training_load.get('volume_7d_km', 0):.1f} km, "
        f"{training_load.get('volume_7d_hours', 0):.1f}h, "
        f"{training_load.get('count_7d', 0)} séances"
    )
    lines.append(
        f"- 28 derniers jours : {training_load.get('volume_28d_km', 0):.1f} km, "
        f"{training_load.get('volume_28d_hours', 0):.1f}h, "
        f"{training_load.get('count_28d', 0)} séances"
    )

    sport_breakdown = training_load.get("sport_breakdown_7d", {})
    if sport_breakdown:
        breakdown_str = ", ".join(
            f"{s}: {d:.1f}km" for s, d in sport_breakdown.items()
        )
        lines.append(f"- Répartition 7j : {breakdown_str}")

    # Recent activities
    if recent_activities:
        lines.append("\n## Historique récent (dernières séances)\n")
        for act in recent_activities[:10]:
            act_dist = act.get("distance", 0) / 1000
            act_sport = act.get("sport_type", "?")
            act_date = act.get("start_date", "?")
            if isinstance(act_date, str) and len(act_date) > 10:
                act_date = act_date[:10]
            elif hasattr(act_date, "strftime"):
                act_date = act_date.strftime("%Y-%m-%d")

            act_time = act.get("moving_time", 0)
            a_min, a_sec = divmod(act_time % 3600, 60)
            a_hr = act_time // 3600
            time_str = f"{a_hr}h{a_min:02d}" if a_hr else f"{a_min}'"

            # Pace info
            pace_str = ""
            avg_speed = act.get("average_speed")
            if avg_speed and avg_speed > 0:
                act_sport_type = act.get("sport_type", "")
                if act_sport_type in ("Ride", "VirtualRide", "EBikeRide"):
                    pace_str = f" | {avg_speed * 3.6:.1f}km/h"
                elif act_sport_type == "Swim":
                    p100 = 100 / avg_speed
                    pm, ps = divmod(int(p100), 60)
                    pace_str = f" | {pm}:{ps:02d}/100m"
                else:
                    pkm = 1000 / avg_speed
                    pm, ps = divmod(int(pkm), 60)
                    pace_str = f" | {pm}:{ps:02d}/km"

            hr_str = ""
            if act.get("average_heartrate"):
                hr_str = f" | FC {act['average_heartrate']:.0f}"

            lines.append(
                f"  - {act_date} | {act_sport} | {act_dist:.1f}km | {time_str}{pace_str}{hr_str}"
            )

    lines.append("\n## Instructions")
    lines.append(
        "Génère une séance adaptée au niveau et à la charge de cet athlète. "
        "Réponds uniquement en JSON valide."
    )

    return "\n".join(lines)
