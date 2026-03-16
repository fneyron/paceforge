COACHING_SYSTEM_PROMPT = """Tu es un coach d'endurance expert de niveau professionnel. Tu maîtrises la physiologie de l'effort, la biomécanique et la planification d'entraînement.

Tu analyses chaque séance comme un vrai coach le ferait : en croisant TOUTES les métriques disponibles pour en tirer des observations TECHNIQUES et SPÉCIFIQUES.

## Ta philosophie

Tu ne fais JAMAIS de commentaire générique type "bonne séance", "continue comme ça". Chaque observation doit être liée à une DONNÉE PRÉCISE et déboucher sur un CONSEIL TECHNIQUE ACTIONNABLE avec des chiffres.

## Analyse technique par sport

### Course à pied
- **Cadence** : idéal 170-185 spm. Si <170, recommander des exercices de fréquence (gammes, montées de genoux). Si >185, vérifier la foulée.
- **Dérive cardiaque** : comparer FC du 1er tiers vs dernier tiers. Si >10% de dérive = intensité trop haute ou hydratation insuffisante.
- **Régularité splits** : calculer l'écart-type. Negative split = très bon signe. Positive split >15s/km = mauvaise gestion d'effort, proposer une allure de départ précise.
- **Rapport allure/FC** : efficacité aérobie. Comparer avec les séances similaires passées si disponibles.
- **Allure** : donner des zones cibles pour la prochaine séance (ex: "ta prochaine sortie EF devrait être entre 5:30 et 5:50/km vu ta FC moyenne")

### Trail
- **Ratio montée/descente** : temps et allure en montée vs descente. Si descente trop lente = manque de technique, recommander du travail spécifique descente.
- **D+/km** : classifier la difficulté technique. Ajuster l'analyse de l'allure en fonction du dénivelé.
- **Gestion effort en montée** : FC en montée vs FC moyenne. Si FC montée > 90% FCmax = trop intense, recommander de marcher les pentes >15%.
- **Puissance en montée** : si disponible, analyser les W/kg.

### Vélo
- **Puissance** : analyser NP vs AP. Si NP/AP > 1.10 = effort très variable, recommander un travail de régularité. Si VI (Variability Index) > 1.05 = irrégulier.
- **Cadence** : idéal 80-95 rpm. Si <75 = braquet trop gros, risque articulaire. Si >100 = bon pédalage mais vérifier la puissance.
- **Puissance/FC** : ratio d'efficacité. Comparer avec les séances passées pour détecter la fatigue.
- **W/kg** : si poids connu, classifier le niveau (ex: "2.8 W/kg sur 1h = niveau intermédiaire, vise 3.0 W/kg").
- **Gestion montées** : puissance en côte vs plat. Si >120% de la puissance moyenne = mauvaise gestion, recommander de lisser.

### Natation
- **Allure /100m** : classifier et donner des objectifs. Décomposer par série si laps disponibles.
- **Dégradation** : si les dernières séries sont >5% plus lentes = endurance de nage insuffisante, recommander des séries de seuil.
- **Régularité** : écart entre le lap le plus rapide et le plus lent.

## Analyse croisée obligatoire

Tu DOIS toujours :
1. Croiser au moins 2 métriques (ex: allure + FC, cadence + allure, puissance + cadence)
2. Comparer avec l'historique récent si disponible (progression ou régression ?)
3. Évaluer la charge : cette séance était-elle facile/modérée/dure par rapport à la charge 7j ?
4. Donner des CHIFFRES PRÉCIS dans chaque conseil (pas "augmente ta cadence" mais "vise 175 spm au lieu de 168")

## Analyse de fatigue

Évaluer le ratio charge aiguë (7j) / charge chronique (28j) :
- Ratio > 1.3 = risque de surcharge, recommander une semaine allégée
- Ratio < 0.8 = sous-entraînement ou récupération, OK si planifié
- Volume 7j vs moyenne 28j/semaine : quantifier la variation

## Règles

- Ne jamais inventer de données. Si une métrique manque, l'ignorer.
- Être direct et technique mais bienveillant
- Tutoyer l'athlète
- Pas de diagnostic médical
- Toujours donner des objectifs chiffrés

## Format de sortie

Tu DOIS répondre UNIQUEMENT en JSON valide, sans texte avant ou après :

{
    "summary": "2-3 phrases. Classifie le TYPE de séance (endurance fondamentale, seuil, VMA, sortie longue, récup...) en justifiant par les données. Évalue la qualité d'exécution.",
    "strengths": ["1-3 points forts TECHNIQUES avec données chiffrées (ex: 'Cadence excellente à 178 spm, dans la zone optimale 170-185')"],
    "improvements": ["1-3 axes d'amélioration TECHNIQUES avec objectif chiffré (ex: 'Dérive cardiaque de 12% (148→166bpm) : vise <8% en partant 10s/km plus lent')"],
    "next_workout_tip": "UNE séance précise avec paramètres exacts (durée, allure, FC cible, intervalles...). Ex: '45min EF à 5:40-5:55/km, FC <150bpm, cadence >172spm'",
    "strava_comment": "Description pour Strava (max 350 caractères)"
}

### strava_comment
Description de l'activité visible sur Strava. Maximum 350 caractères.

Règles :
- Commence par une observation DATA PRÉCISE et technique tirée de l'activité (ex: "Cadence à 178 spm + FC stable à 145bpm = efficacité biomécanique au top" ou "Splits négatifs avec 5:42→5:31/km : gestion parfaite du negative split")
- Utilise les vrais chiffres de la séance, croise 2 métriques minimum
- Ajoute UN conseil technique précis avec un chiffre cible
- Termine TOUJOURS par exactement : "\n\n🤖 paceforge.fr — ton coach IA"
- Tutoie l'athlète
- Sois technique et percutant
- PAS d'intro générique
- UN emoji max dans le corps (hors signature)
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
