COACHING_SYSTEM_PROMPT = """Tu es un coach d'endurance expert spécialisé dans les sports d'endurance :
course à pied, trail, vélo et natation.

Tu analyses les séances sportives et proposes des conseils concrets pour améliorer les performances et optimiser l'entraînement.

Tu es à la fois coach, analyste de données, spécialiste en physiologie de l'endurance et conseiller stratégique pour courses longues.

Ton objectif est d'aider l'athlète à comprendre ses séances, identifier ses axes d'amélioration, optimiser son entraînement, mieux gérer son effort et progresser durablement.

## Ton et style

Le ton doit être clair, bienveillant, précis, motivant et jamais culpabilisant.
Tu es un coach expérimenté, pas un scientifique.
Tu dois éviter le jargon inutile, rester concret et proposer des actions simples.

## Règles spécifiques par sport

### Course à pied
Analyser : allure moyenne, dérive cardiaque, cadence, régularité des splits, gestion du départ, fatigue sur la fin.

### Trail
Analyser : gestion du dénivelé, allure relative en montée, fatigue en descente, régularité globale.

### Vélo
Analyser : puissance moyenne si disponible, régularité de l'effort, cadence, gestion des montées.

### Natation
Analyser : allure /100m, régularité des séries, dégradation de vitesse.

## Analyse de fatigue

Si la charge récente est élevée, mentionner : besoin potentiel de récupération, risque de surcharge, importance du repos. Ne jamais être alarmiste.

## Règles importantes

Tu dois :
- éviter les critiques dures
- éviter les diagnostics médicaux
- éviter les conclusions non supportées par les données
- rester positif et utile
- ne jamais inventer de métriques
- si certaines données manquent, utiliser uniquement celles disponibles et rester prudent

## Format de sortie

Tu DOIS répondre UNIQUEMENT en JSON valide, sans texte avant ou après, respectant exactement ce schéma :

{
    "summary": "2 à 3 phrases max. Explique l'intensité, la qualité de l'exécution, l'objectif probable.",
    "strengths": ["1 à 3 points positifs concrets basés sur les données"],
    "improvements": ["1 à 3 axes d'amélioration concrets et actionnables"],
    "next_workout_tip": "Un seul conseil spécifique pour la prochaine séance",
    "strava_comment": "Commentaire Strava court (max 300 caractères) structuré ainsi :\\nAnalyse automatique 🧠\\n\\nObservation principale.\\nConseil concret.\\n\\n👉 Conseil actionnable.\\n\\nAnalyse générée par PaceForge"
}

## Règles pour chaque champ

### summary
2 à 3 phrases maximum expliquant l'intensité, la qualité de l'exécution et l'objectif probable.

### strengths
Liste de 1 à 3 éléments positifs basés sur les données (régularité allure, cadence stable, gestion dénivelé, FC contrôlée, progression).

### improvements
1 à 3 axes maximum, chacun concret, compréhensible et actionnable.

### next_workout_tip
Un seul conseil : séance spécifique, ajustement d'allure, travail technique ou récupération.

### strava_comment
Court, engageant, utile. Maximum 300 caractères. Structure :
- Analyse automatique 🧠
- Observation principale
- 👉 Conseil concret
- Analyse générée par PaceForge
"""


def build_activity_prompt(
    activity_data: dict,
    training_load: dict,
    recent_activities: list[dict],
) -> str:
    """Build the user message with activity data for Claude analysis."""
    lines = ["## Données de l'activité\n"]

    # Core metrics
    sport = activity_data.get("sport_type", "Unknown")
    lines.append(f"- Sport : {sport}")
    lines.append(f"- Nom : {activity_data.get('name', 'N/A')}")

    distance_km = activity_data.get("distance", 0) / 1000
    lines.append(f"- Distance : {distance_km:.2f} km")

    moving_time = activity_data.get("moving_time", 0)
    hours, remainder = divmod(moving_time, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        lines.append(f"- Durée : {hours}h{minutes:02d}'{seconds:02d}\"")
    else:
        lines.append(f"- Durée : {minutes}'{seconds:02d}\"")

    # Pace/Speed
    avg_speed = activity_data.get("average_speed")
    if avg_speed and avg_speed > 0:
        if sport in ("Ride", "VirtualRide", "EBikeRide"):
            lines.append(f"- Vitesse moyenne : {avg_speed * 3.6:.1f} km/h")
        elif sport in ("Swim",):
            pace_100m = 100 / avg_speed
            p_min, p_sec = divmod(int(pace_100m), 60)
            lines.append(f"- Allure : {p_min}:{p_sec:02d}/100m")
        else:
            pace_km = 1000 / avg_speed
            p_min, p_sec = divmod(int(pace_km), 60)
            lines.append(f"- Allure moyenne : {p_min}:{p_sec:02d}/km")

    # Heart rate
    if activity_data.get("average_heartrate"):
        lines.append(f"- FC moyenne : {activity_data['average_heartrate']:.0f} bpm")
    if activity_data.get("max_heartrate"):
        lines.append(f"- FC max : {activity_data['max_heartrate']:.0f} bpm")

    # Cadence
    if activity_data.get("average_cadence"):
        cadence = activity_data["average_cadence"]
        if sport not in ("Ride", "VirtualRide", "EBikeRide"):
            cadence *= 2  # Strava returns half steps for running
        lines.append(f"- Cadence : {cadence:.0f} spm")

    # Power
    if activity_data.get("average_watts"):
        lines.append(f"- Puissance moyenne : {activity_data['average_watts']:.0f} W")
    if activity_data.get("weighted_average_watts"):
        lines.append(
            f"- Puissance normalisée : {activity_data['weighted_average_watts']:.0f} W"
        )

    # Elevation
    if activity_data.get("total_elevation_gain", 0) > 0:
        lines.append(
            f"- Dénivelé positif : {activity_data['total_elevation_gain']:.0f} m"
        )

    # Splits
    splits = activity_data.get("splits_metric")
    if splits and isinstance(splits, list) and len(splits) > 1:
        lines.append("\n### Splits par kilomètre")
        for i, split in enumerate(splits, 1):
            split_pace = ""
            if split.get("moving_time") and split.get("distance", 0) > 0:
                pace = split["moving_time"] / (split["distance"] / 1000)
                s_min, s_sec = divmod(int(pace), 60)
                split_pace = f"{s_min}:{s_sec:02d}/km"
            split_hr = ""
            if split.get("average_heartrate"):
                split_hr = f" | FC {split['average_heartrate']:.0f}"
            lines.append(f"  km {i}: {split_pace}{split_hr}")

    # Laps
    laps = activity_data.get("laps")
    if laps and isinstance(laps, list) and len(laps) > 1:
        lines.append("\n### Laps")
        for i, lap in enumerate(laps, 1):
            lap_dist = lap.get("distance", 0) / 1000
            lap_time = lap.get("moving_time", 0)
            l_min, l_sec = divmod(lap_time, 60)
            lap_hr = f" | FC {lap['average_heartrate']:.0f}" if lap.get("average_heartrate") else ""
            lines.append(f"  Lap {i}: {lap_dist:.2f}km en {l_min}'{l_sec:02d}\"{lap_hr}")

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

    # Recent activities context
    if recent_activities:
        lines.append("\n## Historique récent (dernières séances)\n")
        for act in recent_activities[:10]:
            act_dist = act.get("distance", 0) / 1000
            act_sport = act.get("sport_type", "?")
            act_date = act.get("start_date", "?")
            if isinstance(act_date, str) and len(act_date) > 10:
                act_date = act_date[:10]
            act_time = act.get("moving_time", 0)
            a_min, a_sec = divmod(act_time % 3600, 60)
            a_hr = act_time // 3600
            time_str = f"{a_hr}h{a_min:02d}" if a_hr else f"{a_min}'"
            lines.append(f"  - {act_date} | {act_sport} | {act_dist:.1f}km | {time_str}")

    return "\n".join(lines)
