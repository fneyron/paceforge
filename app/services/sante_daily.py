"""Additional context, with explicit dates and sources; never recovery inputs."""
import math
import statistics
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.models.mobile import MobileDaily
from app.models.user import User
from app.services import viz
from app.services.night_measurements import series_source

SIGNALS = {
    "steps": ("Pas", "pas", 0, 2000, "Le mouvement de ta journée, à comparer à tes habitudes."),
    "hr_day": ("FC sur 24 h", "bpm", 0, 20,
               "Moyenne des relevés reçus, activité et sommeil compris. Ce n’est pas ta FC au repos."),
    "resp_night": ("Respiration nocturne", "resp./min", 1, 4,
                   "À lire avec tes autres valeurs nocturnes et ton ressenti."),
    "resp_day": ("Respiration sur 24 h", "resp./min", 1, 4,
                 "Moyenne des relevés du téléphone, éveil et sommeil compris. À distinguer de la respiration nocturne."),
    "body_fat": ("Masse grasse", "%", 1, 4,
                 "Estimation de la balance, sensible notamment à l’hydratation. Compare des pesées dans les mêmes conditions."),
    "stress": ("Stress Garmin", "/100", 0, 30,
               "Estimation Garmin pendant les périodes mesurées. Elle ne mesure pas uniquement le stress mental."),
    "weight": ("Poids", "kg", 1, 3, "Observe la tendance sur plusieurs semaines, dans des conditions comparables."),
}
LIMITS = {"steps": (0, 200000), "hr_day": (25, 230), "resp_night": (4, 40),
          "stress": (0, 100), "weight": (25, 300), "body_fat": (1, 75), "resp_day": (4, 60)}


def _valid(row: HealthMetric) -> bool:
    lo, hi = LIMITS[row.metric]
    return math.isfinite(row.value) and lo <= row.value <= hi


def _source(row: HealthMetric) -> str:
    source = row.source or "Inconnue"
    if row.metric == "resp_night":
        return series_source(source, row.details or {}, "resp")
    origins = (row.details or {}).get("origins")
    return source + (" · " + ", ".join(origins) if origins else "")


def daily_card(metric: str, rows: list[HealthMetric], today: date) -> dict | None:
    valid = sorted((r for r in rows if r.date <= today and _valid(r)), key=lambda r: r.date)
    if not valid:
        return None
    last = valid[-1]
    name, unit, digits, min_span, meaning = SIGNALS[metric]
    if metric == "stress" and last.source != "Garmin":
        name = "Stress"
        meaning = "Estimation de la source indiquée, à lire avec ton ressenti."
    stale = (today - last.date).days > (14 if metric in ("weight", "body_fat") else 2)
    out = {"key": metric, "title": name, "meaning": meaning, "source": _source(last),
           "latest": last.date.strftime("%d/%m/%Y"), "stale": stale,
           "value": viz.num(last.value, digits, unit), "chart": None, "trend": None}
    days = [today - timedelta(days=29-i) for i in range(30)]
    by_day = {r.date: r for r in valid if r.date in days}
    if not by_day:
        return out
    values = [by_day[d].value if d in by_day else None for d in days]
    sources = [_source(by_day[d]) if d in by_day else None for d in days]
    notes = [("journée en cours" if d == today and metric in ("steps", "hr_day", "stress", "resp_day") else "")
             for d in days]
    out["chart"] = viz.night_card(
        "daily-" + metric, days, values, band=[None]*30, prov=[False]*30, mean=[None]*30,
        unit=unit, unit_long=unit, name=name, digits=digits, min_span=min_span, sources=sources,
        notes=notes, daytime=metric != "resp_night", zero_base=metric == "steps",
    )
    # Compare complete days only, using the latest compatible source.
    same = [r for r in valid if _source(r) == _source(last)]
    current = [r.value for r in same if today-timedelta(days=7) <= r.date < today]
    previous = [r.value for r in same if today-timedelta(days=14) <= r.date < today-timedelta(days=7)]
    if not stale and len(current) >= 4 and len(previous) >= 4:
        delta = statistics.mean(current) - statistics.mean(previous)
        out["trend"] = (f"Moyenne des 7 derniers jours complets : {viz.signed(delta, digits, unit)} "
                        f"par rapport aux 7 précédents ({len(current)} et {len(previous)} jours mesurés).")
    return out


async def daily_context(db: AsyncSession, user_id: int, today: date) -> list[dict]:
    rows = (await db.execute(select(HealthMetric).where(
        HealthMetric.user_id == user_id, HealthMetric.metric.in_(SIGNALS), HealthMetric.date <= today,
    ).order_by(HealthMetric.date))).scalars().all()
    # Select exactly one value per day. Prefer a direct watch to its phone copy.
    # Between phone platforms, choose the most recent measured weigh-in, otherwise
    # the most recently refreshed daily aggregate; never sum the two totals.
    by_day = {(r.date, r.metric): r for r in rows}
    phone_rows = (await db.scalars(select(MobileDaily).where(
        MobileDaily.user_id == user_id, MobileDaily.date <= today,
    ).order_by(MobileDaily.measured_at.asc().nullsfirst(), MobileDaily.updated_at, MobileDaily.id))).all()
    chosen = {}
    for r in phone_rows:
        chosen[(r.date, r.metric)] = r
    for key, r in chosen.items():
        if key not in by_day:
            source = "Apple Santé" if r.platform == "ios" else "Health Connect"
            by_day[key] = HealthMetric(date=r.date, metric=r.metric, value=r.value, source=source,
                                       details={"origins": sorted(r.sources)})
    rows = list(by_day.values())
    cards = [c for metric in SIGNALS if (c := daily_card(metric, [r for r in rows if r.metric == metric], today))]
    if not any(c["key"] == "weight" for c in cards):
        user = await db.get(User, user_id)
        if user and user.weight_kg is not None:
            cards.append({"key": "weight", "title": "Poids du profil", "value": viz.num(user.weight_kg, 1, "kg"),
                          "source": "Saisie manuelle", "latest": None, "stale": False,
                          "chart": None, "trend": None,
                          "meaning": "Poids renseigné dans Réglages, sans date de pesée ni historique."})
    return cards
