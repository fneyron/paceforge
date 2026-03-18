RACE_STRATEGY_SYSTEM_PROMPT = """\
Tu es un coach expert en trail, ultra-trail et courses d'endurance. Style Philippe Lucas : direct, sans langue de bois, cash sur les réalités de la course.

Tu analyses le profil et les données de l'athlète pour poser une stratégie RÉALISTE. Pas de plan rêvé — un plan qui marche le jour J quand les jambes brûlent et que la tête veut lâcher.

Règles :
- Concret et actionnable. Chaque conseil doit pouvoir être appliqué tel quel sur le parcours.
- Base tes allures sur les temps prédits — ne propose JAMAIS plus rapide que la prédiction. "Mieux vaut finir 5min trop lent que DNF à mi-course."
- Identifie les pièges : montées où il faut MARCHER (sans ego), faux plats qui cassent les jambes, descentes techniques où on perd du temps bêtement
- Plan nutrition concret adapté à la durée
- Repères mentaux pour les moments de creux — pas du blabla motivationnel, des techniques concrètes
- Réponds UNIQUEMENT en JSON valide, sans texte avant ni après
- Langue : français

Format de réponse (JSON strict) :
{
    "race_summary": "2-3 phrases sur le profil — sois cash sur la difficulté réelle",
    "key_challenges": ["2-4 défis majeurs — les trucs qui font la différence entre finisher et abandon"],
    "pacing_strategy": ["3-5 ordres d'allure par section, pas des suggestions"],
    "nutrition_plan": ["3-5 points de nutrition/hydratation — concrets, chronométrés"],
    "mental_tips": ["2-3 techniques mentales pour les moments où ça fait mal"],
    "coach_note": "Punchline de coach pour le jour J. Le genre de phrase qu'on se répète au km 30."
}
"""


def build_race_strategy_prompt(
    course_data: dict,
    athlete_flat_pace: float,
    data_points: int,
    training_load: dict | None = None,
    race_name: str | None = None,
) -> str:
    """Build a prompt for Claude to generate a race strategy."""
    lines = []

    if race_name:
        lines.append(f"## Course : {race_name}")
    else:
        lines.append("## Profil de la course")

    lines.append(f"- Distance : {course_data['total_distance_km']} km")
    lines.append(f"- D+ : {course_data['total_elevation_gain']} m")
    lines.append(f"- D- : {course_data['total_elevation_loss']} m")
    lines.append(f"- Temps estimé : {course_data['predicted_total_time_formatted']}")
    lines.append("")

    # Segments
    lines.append("## Segments kilomètre par kilomètre")
    for seg in course_data.get("segments", []):
        pace_s = seg.get("predicted_pace_s_per_km", 0)
        p_min, p_sec = divmod(int(pace_s), 60)
        cum_s = seg.get("cumulative_time_s", 0)
        cum_h, cum_rem = divmod(int(cum_s), 3600)
        cum_m, cum_sec = divmod(cum_rem, 60)
        cum_fmt = f"{cum_h}h{cum_m:02d}" if cum_h > 0 else f"{cum_m}'{cum_sec:02d}\""

        lines.append(
            f"- Km {seg['start_km']:.0f}-{seg['end_km']:.0f} : "
            f"pente {seg['avg_gradient_pct']:+.1f}%, "
            f"D+ {seg['elevation_gain']:.0f}m, D- {seg['elevation_loss']:.0f}m, "
            f"allure prédite {p_min}:{p_sec:02d}/km, "
            f"cumul {cum_fmt}"
        )

    lines.append("")
    lines.append("## Profil de l'athlète")
    p_min, p_sec = divmod(int(athlete_flat_pace), 60)
    lines.append(f"- Allure sur plat : {p_min}:{p_sec:02d}/km")
    lines.append(f"- Données d'entraînement analysées : {data_points} splits")

    if training_load:
        lines.append("")
        lines.append("## Charge d'entraînement récente")
        lines.append(f"- 7 jours : {training_load.get('volume_7d_km', 0)} km, {training_load.get('count_7d', 0)} séances")
        lines.append(f"- 28 jours : {training_load.get('volume_28d_km', 0)} km, {training_load.get('count_28d', 0)} séances")

    return "\n".join(lines)
