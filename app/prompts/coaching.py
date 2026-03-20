COACHING_SYSTEM_PROMPT = """Tu es un coach d'endurance old-school, exigeant et sans filtre. Style Philippe Lucas : direct, cash, punchlines. Tu adores tes athlètes mais tu ne leur fais AUCUN cadeau. Tu dis les choses comme elles sont, pas comme ils veulent les entendre.

Tu analyses chaque séance comme un coach qui a vu passer des centaines d'athlètes : en croisant TOUTES les métriques pour poser un diagnostic BRUTAL et HONNÊTE.

## Ta philosophie

Tu ne fais JAMAIS de commentaire tiède type "bonne séance", "continue comme ça", "belle sortie". Ça c'est bon pour les apps de running grand public. Toi tu es là pour faire progresser, pas pour caresser dans le sens du poil. Chaque observation est liée à une DONNÉE PRÉCISE et débouche sur un ORDRE CLAIR avec des chiffres. Tu tutoies, tu secoues, tu pousses. Quand c'est bien tu le dis sèchement ("là c'est propre"), quand c'est moyen tu ne cherches pas d'excuse.

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

## Séances structurées / fractionné

IMPORTANT : Si le type de séance est "fractionné", "séance structurée", "tempo", ou si des intervalles sont détectés dans les métriques, ou si les laps montrent une alternance travail/récup :
- La variabilité d'allure est NORMALE et VOULUE. Ne critique PAS le CV d'allure élevé ni le positive split.
- Analyse la QUALITÉ du fractionné : régularité des intervalles entre eux, dérive cardiaque entre les séries, récupération cardiaque entre les efforts.
- Compare les premières séries vs les dernières : y a-t-il une dégradation ?
- La cadence et la puissance SUR LES INTERVALLES sont les métriques clés, pas la moyenne globale.
- Pour le vélo : analyse la puissance sur les intervalles vs la récup, le ratio travail/repos.

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

- Ne JAMAIS inventer de données ou de pourcentages. Chaque chiffre cité DOIT venir des données fournies. Pas de "34% d'énergie gaspillée" ou "12% plus efficace" sans calcul réel dans les données. Si tu n'as pas le chiffre exact, dis juste ce qui est observé sans quantifier.
- Être direct, technique et sans complaisance. Pas de "bienveillance molle" — la vraie bienveillance c'est de dire la vérité.
- Tutoyer l'athlète
- Pas de diagnostic médical
- Toujours donner des objectifs chiffrés
- Utiliser des formulations percutantes et mémorables. Exemples de ton : "Ta dérive cardiaque dit ce que ta tête refuse d'admettre : t'es parti trop vite.", "168 spm de cadence, tu cours ou tu te promènes ?", "Là c'est chirurgical, rien à dire."

## Métriques pré-calculées

Quand des métriques avancées sont fournies (section "Métriques avancées"), utilise-les DIRECTEMENT dans ton analyse.
Ces calculs sont exacts — ne les recalcule pas. Concentre-toi sur l'INTERPRÉTATION et les CONSEILS TECHNIQUES.
Par exemple, si la dérive cardiaque est de 12%, ne dis pas juste "dérive élevée", mais explique ce que ça implique
et donne un conseil concret pour la corriger (allure de départ, hydratation, etc.).

## Format de sortie

Tu DOIS répondre UNIQUEMENT en JSON valide, sans texte avant ou après :

{
    "summary": "2-3 phrases. Classifie le TYPE de séance et pose le verdict sans détour. Ton punchline. Ex: 'Seuil lactique à 4:35/km, FC à 172bpm — t'as tenu le rythme comme un métronome. Mais ta cadence à 164 spm, c'est un frein à main serré.'",
    "strengths": ["1-3 points forts TECHNIQUES avec données chiffrées. Pas de compliment gratuit — que des faits. Ex: 'Negative split de 8s/km sur les 3 derniers bornes : ça c'est de la gestion.'"],
    "improvements": ["1-3 axes d'amélioration avec objectif chiffré et une formulation qui pique. Ex: 'Dérive cardiaque de 12% (148→166bpm) — tu t'es cramé comme un débutant. Prochaine fois tu pars à 5:50/km, pas 5:30.'"],
    "next_workout_tip": "UNE séance précise avec paramètres exacts. Formulé comme un ordre de coach, pas une suggestion. Ex: 'Demain 45min EF à 5:40-5:55/km, FC sous 150bpm, cadence au-dessus de 172spm. Non négociable.'",
    "strava_comment": "Description pour Strava (max 350 caractères)"
}

### strava_comment
Commentaire visible sur Strava. Maximum 350 caractères. C'est ta vitrine — chaque commentaire doit donner envie de revenir. C'est LE truc que les gens vont screenshot pour envoyer à leurs potes runners.

Règles :
- Commence TOUJOURS par "🤖 PaceForge — paceforge.fr\n" sur la première ligne (pour que le nom + URL soient visibles même sans développer la description sur Strava)
- UNIQUEMENT de l'analyse de la séance en cours. PAS de conseil pour la prochaine sortie, PAS de suggestion de séance future. L'app a déjà un planificateur pour ça.
- Ensuite une PUNCHLINE en français qui claque, basée sur les données réelles
- Croise 2-3 métriques avec les vrais chiffres pour poser un DIAGNOSTIC
- PAS de signature en fin de commentaire
- Tutoie l'athlète
- PAS d'intro générique, PAS de "belle séance", PAS de "bravo"
- UN emoji max

Exemples complets (avec le format exact "🤖 PaceForge — paceforge.fr\n..." — adapte avec les vrais chiffres) :

- "🤖 PaceForge — paceforge.fr\n5:12→4:58 en négatif, cardio plat à 148. Quand tu cours comme ça, y'a rien à dire 🎯"
- "🤖 PaceForge — paceforge.fr\nParti à 4:50, fini à 5:40. 50 secondes de dérive au km. T'as cramé toutes tes cartouches au premier virage 💀"
- "🤖 PaceForge — paceforge.fr\n13x500m à 3:22, pas un qui dépasse de 2 secondes. Machine."
- "🤖 PaceForge — paceforge.fr\n164 spm à 5:30 — avec cette cadence t'économises rien, tu compenses avec le cardio. +11% de dérive, logique."
- "🤖 PaceForge — paceforge.fr\nVI à 1.18 sur 3h. Accélération, freinage, accélération — tu roules en accordéon."
- "🤖 PaceForge — paceforge.fr\n850m de D+, 5% de dérive cardiaque. La montée c'est géré. La descente, -2min/km : les quads ont dit stop avant toi."
"""


def build_activity_prompt(
    activity_data: dict,
    training_load: dict,
    recent_activities: list[dict],
    computed_metrics: dict | None = None,
) -> str:
    """Build the user message with activity data for Claude analysis."""
    lines = ["## Données de l'activité\n"]

    # Core metrics
    sport = activity_data.get("sport_type", "Unknown")
    lines.append(f"- Sport : {sport}")
    lines.append(f"- Nom : {activity_data.get('name', 'N/A')}")

    # Workout type from Strava (0=default, 1=race, 2=long run, 3=workout/interval, 11=tempo, 12=interval for cycling)
    workout_type = activity_data.get("workout_type")
    if workout_type:
        wt_labels = {1: "Course/compétition", 2: "Sortie longue", 3: "Séance structurée/fractionné", 11: "Tempo", 12: "Fractionné vélo"}
        wt_label = wt_labels.get(workout_type, f"Type {workout_type}")
        lines.append(f"- Type de séance : {wt_label}")

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
        is_cycling_sport = sport in ("Ride", "VirtualRide", "EBikeRide", "GravelRide")
        if not is_cycling_sport:
            cadence *= 2  # Strava returns half steps for running
        unit = "rpm" if is_cycling_sport else "spm"
        lines.append(f"- Cadence : {cadence:.0f} {unit}")

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
    is_cycling = sport in ("Ride", "VirtualRide", "EBikeRide", "GravelRide")
    if splits and isinstance(splits, list) and len(splits) > 1:
        if is_cycling:
            # For cycling: show speed in km/h, not pace/km (and limit to every 10km)
            lines.append("\n### Vitesse par segment")
            for i, split in enumerate(splits, 1):
                if i % 10 != 0 and i != 1 and i != len(splits):
                    continue  # Show every 10km + first + last
                speed_str = ""
                if split.get("moving_time") and split.get("distance", 0) > 0:
                    speed_kmh = split["distance"] / split["moving_time"] * 3.6
                    speed_str = f"{speed_kmh:.1f} km/h"
                split_hr = ""
                if split.get("average_heartrate"):
                    split_hr = f" | FC {split['average_heartrate']:.0f}"
                lines.append(f"  km {i}: {speed_str}{split_hr}")
        else:
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

    # Advanced computed metrics
    if computed_metrics:
        lines.append("\n## Métriques avancées (pré-calculées depuis les streams)\n")
        _append_computed_metrics(lines, computed_metrics, sport)

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


def _append_computed_metrics(lines: list[str], metrics: dict, sport: str) -> None:
    """Format computed metrics into human-readable text for the prompt."""

    # Cardiac drift (all sports)
    cd = metrics.get("cardiac_drift")
    if cd:
        lines.append(
            f"- Dérive cardiaque : {cd['drift_pct']:.1f}% "
            f"({cd['first_half_avg']:.0f}→{cd['second_half_avg']:.0f} bpm) "
            f"— {cd['assessment']}"
        )

    # Effort classification
    ec = metrics.get("effort_classification")
    if ec:
        lines.append(
            f"- Classification effort : {ec['classification']} "
            f"(FC moy = {ec['avg_hr_pct']:.0f}% FCmax)"
        )

    # HR zones
    hz = metrics.get("hr_zones_distribution")
    if hz:
        zones_str = " | ".join(
            f"{z}: {hz[z]['pct']:.0f}%" for z in ["Z1", "Z2", "Z3", "Z4", "Z5"] if z in hz
        )
        lines.append(f"- Zones cardiaques : {zones_str}")

    # Running-specific
    if sport in ("Run", "TrailRun", "VirtualRun"):
        sl = metrics.get("stride_length")
        if sl:
            lines.append(
                f"- Longueur de foulée : {sl['avg_m']:.2f} m ({sl['assessment']})"
            )

        ca = metrics.get("cadence_analysis")
        if ca:
            lines.append(
                f"- Cadence (streams) : {ca['avg_spm']:.0f} spm, "
                f"{ca['pct_in_optimal_range']:.0f}% dans 170-185 ({ca['assessment']})"
            )

        # Skip pace variability and split analysis for structured workouts
        # (variability is intentional in intervals, split analysis is meaningless)
        is_structured = bool(metrics.get("structured_workout")) or bool(metrics.get("intervals_detected"))

        if not is_structured:
            pv = metrics.get("pace_variability")
            if pv:
                extra = ""
                if pv.get("fastest_split_pace") and pv.get("slowest_split_pace"):
                    extra = f", plus rapide {pv['fastest_split_pace']} / plus lent {pv['slowest_split_pace']}"
                lines.append(
                    f"- Variabilité d'allure : CV {pv['cv_pct']:.1f}% ({pv['assessment']}){extra}"
                )

            sa = metrics.get("split_analysis")
            if sa:
                lines.append(
                    f"- Splits : {sa['type']} split "
                    f"({sa['magnitude_s_per_km']:.0f}s/km de différence)"
                )

        ad = metrics.get("aerobic_decoupling")
        if ad:
            lines.append(
                f"- Découplage aérobie : {ad['decoupling_pct']:.1f}% ({ad['assessment']})"
            )

        st = metrics.get("stop_time_pct")
        if st:
            lines.append(f"- Temps arrêté : {st['pct']:.1f}% du temps total")

        rp = metrics.get("running_power")
        if rp:
            power_parts = [f"Puissance moy {rp['avg_watts']}W, max {rp['max_watts']}W"]
            if rp.get("normalized_power"):
                power_parts.append(f"NP {rp['normalized_power']}W")
            if rp.get("variability_index"):
                power_parts.append(f"VI {rp['variability_index']:.2f}")
            if rp.get("power_to_weight"):
                power_parts.append(f"{rp['power_to_weight']:.1f} W/kg")
            lines.append(f"- Running Power : {', '.join(power_parts)}")

        ppe = metrics.get("power_pace_efficiency")
        if ppe:
            lines.append(
                f"- Efficacité puissance/allure : {ppe['watts_per_ms']:.1f} W/(m/s) ({ppe['assessment']})"
            )

        rphr = metrics.get("running_power_hr_ratio")
        if rphr:
            lines.append(f"- Ratio Puissance/FC : {rphr['ratio']:.2f} {rphr['unit']}")

        sw = metrics.get("structured_workout")
        if sw:
            lines.append(f"\n### Analyse séance structurée")
            if sw.get("interval_type") == "time":
                lines.append(f"  - Format : {sw['repetitions']}x{sw.get('avg_interval_duration_fmt', '?')}")
                if sw.get("time_consistency_cv") is not None:
                    lines.append(f"  - Régularité durée : CV {sw['time_consistency_cv']:.1f}% ({sw.get('consistency', 'N/A')})")
            else:
                dist = sw.get('avg_interval_distance_m', 0)
                lines.append(f"  - Format : {sw['repetitions']}x{dist}m")
                if sw.get("avg_interval_pace"):
                    lines.append(f"  - Allure moyenne intervalles : {sw['avg_interval_pace']}")
                cv = sw.get('pace_consistency_cv', 0)
                if cv:
                    lines.append(f"  - Régularité allure : CV {cv:.1f}% ({sw.get('consistency', 'N/A')})")
            if sw.get("avg_interval_hr"):
                lines.append(f"  - FC moyenne intervalles : {sw['avg_interval_hr']} bpm")
            if sw.get("interval_hr_drift_pct") is not None:
                lines.append(f"  - Dérive FC inter-intervalles : {sw['interval_hr_drift_pct']:.1f}%")
            if sw.get("avg_interval_watts"):
                lines.append(f"  - Puissance moyenne intervalles : {sw['avg_interval_watts']}W")
            if sw.get("power_consistency_cv") is not None:
                lines.append(f"  - Régularité puissance : CV {sw['power_consistency_cv']:.1f}%")
            if sw.get("avg_interval_cadence_spm"):
                lines.append(f"  - Cadence moyenne intervalles : {sw['avg_interval_cadence_spm']} spm")
            if sw.get("avg_recovery_hr"):
                lines.append(f"  - FC moyenne récup : {sw['avg_recovery_hr']} bpm")
            if sw.get("work_rest_hr_delta"):
                lines.append(f"  - Delta FC travail/récup : {sw['work_rest_hr_delta']} bpm")

    # Cycling-specific
    if sport in ("Ride", "VirtualRide", "EBikeRide"):
        vi = metrics.get("variability_index")
        if vi:
            lines.append(
                f"- Variability Index (NP/AP) : {vi['vi']:.2f} ({vi['assessment']})"
            )

        if_val = metrics.get("intensity_factor")
        if if_val:
            lines.append(f"- Intensity Factor (NP/FTP) : {if_val['if_value']:.2f}")

        tss = metrics.get("tss_estimate")
        if tss:
            lines.append(f"- TSS estimé : {tss['tss']:.0f}")

        np_comp = metrics.get("normalized_power_computed")
        if np_comp:
            lines.append(f"- Puissance normalisée (streams) : {np_comp['np_watts']:.0f} W")

        ca = metrics.get("cadence_analysis")
        if ca:
            avg_cad = ca.get("avg_rpm") or ca.get("avg_spm", 0)
            lines.append(
                f"- Cadence (streams) : {avg_cad:.0f} rpm, "
                f"{ca['pct_in_optimal_range']:.0f}% dans 80-95 ({ca['assessment']})"
            )

        phr = metrics.get("power_hr_ratio")
        if phr:
            lines.append(f"- Ratio Puissance/FC : {phr['ratio']:.2f} W/bpm")

        pz = metrics.get("power_zones_distribution")
        if pz:
            pz_str = " | ".join(
                f"{z}: {pz[z]['pct']:.0f}%" for z in sorted(pz.keys())
            )
            lines.append(f"- Zones de puissance : {pz_str}")

    # Swimming-specific
    if sport == "Swim":
        pd = metrics.get("pace_degradation")
        if pd:
            lines.append(
                f"- Dégradation d'allure : {pd['degradation_pct']:.1f}% "
                f"({pd.get('first_laps_pace', '?')} → {pd.get('last_laps_pace', '?')})"
            )

        cs = metrics.get("consistency_score")
        if cs:
            lines.append(
                f"- Régularité : CV {cs['cv_pct']:.1f}% ({cs['assessment']})"
            )

    # Intervals detected
    intervals = metrics.get("intervals_detected")
    if intervals:
        work_intervals = [i for i in intervals if i["type"] == "work"]
        if work_intervals:
            lines.append(f"\n### Intervalles détectés ({len(work_intervals)} efforts)")
            for i, intv in enumerate(work_intervals[:10], 1):
                dur = intv["duration_s"]
                hr_str = f" | FC {intv['avg_hr']:.0f}" if intv.get("avg_hr") else ""
                lines.append(f"  Effort {i}: {dur}s{hr_str}")
