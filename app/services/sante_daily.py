"""Additional context, with explicit dates and sources; never recovery inputs."""
import math
import statistics
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.models.mobile import MobileDaily
from app.models.user import User
from app.services import viz
from app.services.health_sources import preference, select_metrics
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
    "stress": ("Stress", "/100", 0, 30,
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


def _measured_at(row: HealthMetric) -> datetime | None:
    """Only an explicit instant can beat a timestamped phone weigh-in.

    updated_at is an import date, not a measurement date. Old daily exports
    without a timezone/instant remain fallbacks, regardless of brand priority.
    """
    try:
        stamp = datetime.fromisoformat((row.details or {}).get("measured_at", ""))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (ValueError, TypeError):
        return None


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
    # A visible recent reference, without treating a race day as a usual day.
    same = [r for r in valid if _source(r) == _source(last)]
    current = [r.value for r in same if today-timedelta(days=7) <= r.date < today]
    reference = None
    if not stale and len(current) >= 4:
        median = statistics.median(current)
        reference = (median, f"médiane 7 j · {viz.num(median, digits, unit)}")
        out["trend"] = (f"Repère calculé sur les 7 derniers jours écoulés ({len(current)} jours avec des relevés). "
                        f"Source : {_source(last)}. Le temps de port peut varier.")
    out["chart"] = viz.night_card(
        "daily-" + metric, days, values, band=[None]*30, prov=[False]*30, mean=[None]*30,
        unit=unit, unit_long=unit, name=name, digits=digits, min_span=min_span, sources=sources,
        notes=notes, daytime=metric != "resp_night", zero_base=metric == "steps", reference=reference, compact=True,
    )
    return out


WINDOW_DAYS = 30  # the cards' charts; each metric's latest row before them still says how old it is
DETAIL_KEYS = ("origins", "measured_at", "method", "scope")  # what a card reads of a row's details (never the
# raw readings a Garmin day row carries: ≈ 22 KiB each, once read on every load)


def _light(date_, metric, value, source, details) -> HealthMetric:
    return HealthMetric(date=date_, metric=metric, value=value, source=source,
                        details={k: v for k, v in zip(DETAIL_KEYS, details, strict=True) if v is not None})


async def _metric_rows(db: AsyncSession, user_id: int, today: date) -> list[HealthMetric]:
    """The daily rows of the 30 days up to `today`, plus, for a metric without one, its latest day before them
    (every source of that day), each with the details keys a card reads (DETAIL_KEYS) only; COROS's stress is an
    archive the page never shows (its connection no longer sends it)."""
    lo = today - timedelta(days=WINDOW_DAYS - 1)
    cols = (HealthMetric.date, HealthMetric.metric, HealthMetric.value, HealthMetric.source,
            HealthMetric.details["origins"], *(HealthMetric.details[k].as_string() for k in DETAIL_KEYS[1:]))
    shown = and_(HealthMetric.user_id == user_id, HealthMetric.metric.in_(SIGNALS),
                 or_(HealthMetric.metric != "stress", HealthMetric.source != "COROS"))
    rows = (await db.execute(select(*cols).where(shown, HealthMetric.date >= lo, HealthMetric.date <= today))).all()
    have = {r[1] for r in rows}
    older = (await db.execute(select(HealthMetric.metric, func.max(HealthMetric.date)).where(
        shown, HealthMetric.date < lo, HealthMetric.metric.not_in(have) if have else true())
        .group_by(HealthMetric.metric))).all()
    if older:
        rows += (await db.execute(select(*cols).where(shown, or_(*(and_(HealthMetric.metric == m, HealthMetric.date == d)
                                                                  for m, d in older))))).all()
    return [_light(d, m, v, src, det) for d, m, v, src, *det in rows]


async def _phone_rows(db: AsyncSession, user_id: int, today: date) -> list[MobileDaily]:
    """The phone's rows of the 30 days, plus each metric's latest day before them when it has none in them."""
    lo = today - timedelta(days=WINDOW_DAYS - 1)
    order = (MobileDaily.measured_at.asc().nullsfirst(), MobileDaily.updated_at, MobileDaily.id)
    rows = list((await db.scalars(select(MobileDaily).where(
        MobileDaily.user_id == user_id, MobileDaily.date >= lo, MobileDaily.date <= today).order_by(*order))).all())
    have = {r.metric for r in rows}
    older = (await db.execute(select(MobileDaily.metric, func.max(MobileDaily.date)).where(
        MobileDaily.user_id == user_id, MobileDaily.date < lo, MobileDaily.metric.not_in(have) if have else true())
        .group_by(MobileDaily.metric))).all()
    if older:
        rows = list((await db.scalars(select(MobileDaily).where(
            MobileDaily.user_id == user_id, or_(*(and_(MobileDaily.metric == m, MobileDaily.date == d)
                                                  for m, d in older))).order_by(*order))).all()) + rows
    return rows


async def daily_context(db: AsyncSession, user_id: int, today: date, now: datetime | None = None) -> list[dict]:
    """The « Au quotidien » cards: the 30 days up to `today` (each metric's latest older value still dated), one
    value per day and metric; the weight is the one weight rule's (athlete_weight: the most recent dated value of
    the weigh-ins and the value typed in Réglages, stale after 30 days), the chart its weigh-ins of the 30 days."""
    rows = await _metric_rows(db, user_id, today)
    # Select exactly one value per day. Prefer a direct watch to its phone copy
    # for aggregates; a weigh-in instead follows its actual measurement time.
    # Between phone platforms, choose the most recent measured weigh-in, otherwise
    # the most recently refreshed daily aggregate; never sum the two totals.
    by_day = select_metrics([r for r in rows if _valid(r)], await preference(db, user_id))
    chosen = {}
    for r in await _phone_rows(db, user_id, today):
        chosen[(r.date, r.metric)] = r
    for key, r in chosen.items():
        previous = by_day.get(key)
        newer_weigh_in = False
        if r.metric in ("weight", "body_fat") and r.measured_at:
            instant = r.measured_at.replace(tzinfo=timezone.utc) if r.measured_at.tzinfo is None else r.measured_at
            # Brand selection is inappropriate for discrete measurements: check
            # every dated candidate, including one hidden by the preferred watch.
            candidates = [v for v in rows if (v.date, v.metric) == key and _valid(v) and _measured_at(v)]
            dated = max(candidates, key=_measured_at, default=None)
            if dated and _measured_at(dated) > instant:
                by_day[key] = dated
            else:
                newer_weigh_in = True
        if previous is None or newer_weigh_in:
            source = "Apple Santé" if r.platform == "ios" else "Health Connect"
            by_day[key] = HealthMetric(date=r.date, metric=r.metric, value=r.value, source=source,
                                       details={"origins": sorted(r.sources),
                                                "measured_at": r.measured_at.isoformat() if r.measured_at else None})
    rows = list(by_day.values())
    cards = [c for metric in SIGNALS if metric != "weight"
             and (c := daily_card(metric, [r for r in rows if r.metric == metric], today))]
    if (weight := await _weight_card(db, user_id, [r for r in rows if r.metric == "weight"], today, now)):
        cards.insert(list(SIGNALS).index("weight"), weight)
    return cards


async def _weight_card(db: AsyncSession, user_id: int, rows: list, today: date, now: datetime | None) -> dict | None:
    """The single weight Santé prints (owner, 2026-10-14: scientific basis — it showed the latest phone weigh-in
    even when stale, and hid a more recent value typed in Réglages): athlete_weight's value, source and date, « Donnée
    ancienne » after 30 days; the 30 days' weigh-ins drawn when that weight is the latest of them."""
    from app.services import timezones
    from app.services.athlete_weight import athlete_weight

    user = await db.get(User, user_id)
    card = daily_card("weight", rows, today)
    reading = await athlete_weight(db, user, now) if user else None
    if reading is None:
        return card
    typed = reading.source == "Saisie manuelle"
    latest = timezones.local_date(reading.at, timezones.athlete_zone(user)).strftime("%d/%m/%Y") if reading.at \
        else None
    out = {"key": "weight", "title": "Poids du profil" if typed else "Poids", "value": viz.num(reading.kg, 1, "kg"),
           "source": reading.source, "latest": latest, "stale": reading.stale, "chart": None, "trend": None,
           "meaning": "Poids renseigné dans Réglages, sans date de pesée ni historique." if typed
           else SIGNALS["weight"][4]}
    if card and not typed and card["chart"]:
        out.update(chart=card["chart"], trend=card["trend"], source=card["source"])
    return out
