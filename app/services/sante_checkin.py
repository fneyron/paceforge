"""Optional, dated self-report. It is context, never an input to the night index.

The versioned object shares the legacy feel row without translating its answers
into the old feel value, illness tags or alcohol flags.
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.services import viz

KEY = "daily_checkin"
# The page asks the first one only, in one tap (owner, 2026-10-17: « le ressenti en un tap »); the others stay
# readable for the days they were answered.
FEEL = (("good", "Bien"), ("ok", "Moyen"), ("bad", "Cassé"))
FIELDS = (
    ("feel", "Ressenti", FEEL),
    ("fatigue", "Fatigue", (("usual", "Habituelle"), ("high", "Plus forte"))),
    ("pain", "Douleur", (("none", "Non"), ("present", "Oui"))),
    ("effort", "Dernier effort", (("usual", "Bien toléré"), ("harder", "Plus difficile"),
                                   ("not_done", "Pas d’effort récent"))),
)


def _query(user_id: int, day: date):
    return select(HealthMetric).where(HealthMetric.user_id == user_id, HealthMetric.date == day,
                                      HealthMetric.metric == "feel", HealthMetric.source == "PaceForge")


def presentation(day: date, details: dict | None = None) -> dict:
    raw = details.get(KEY) if isinstance(details, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    answers = {key: raw.get(key) for key, _, options in FIELDS
               if raw.get(key) in dict(options)} if raw.get("v") == 1 else {}
    signals = []
    if answers.get("pain") == "present":
        signals.append("Douleur signalée")
    if answers.get("fatigue") == "high":
        signals.append("Fatigue plus forte")
    if answers.get("effort") == "harder":
        signals.append("Effort moins bien toléré")
    guidance = None
    if signals:
        guidance = "Tiens compte de ce ressenti pour la reprise, même si tes signaux nocturnes sont habituels."
    return {"day": day.isoformat(), "date_label": viz.d_short(day), "answers": answers,
            "feel": answers.get("feel"), "feel_options": FEEL,
            "fields": [{"key": key, "label": label, "options": options} for key, label, options in FIELDS],
            "saved": bool(answers), "attention": bool(signals),
            "summary": signals[0] if signals else "Renseigné aujourd’hui" if answers else "Facultatif",
            "signals": signals, "guidance": guidance}


async def read(db: AsyncSession, user_id: int, day: date) -> dict:
    row = (await db.execute(_query(user_id, day))).scalar_one_or_none()
    return presentation(day, row.details if row else None)


async def save(db: AsyncSession, user_id: int, day: date, answers: dict | None) -> dict:
    if answers is not None:
        allowed = {key: dict(options) for key, _, options in FIELDS}
        if not answers or any(key not in allowed or value not in allowed[key] for key, value in answers.items()):
            raise ValueError("Invalid self-report")
        from app.services import health_consent

        if not await health_consent.allowed(db, user_id):  # health data: written only with the consent
            raise PermissionError("No consent to health data")
    row =(await db.execute(_query(user_id, day).with_for_update())).scalar_one_or_none()
    if row is None and answers:
        try:
            async with db.begin_nested():
                row = HealthMetric(user_id=user_id, date=day, metric="feel", source="PaceForge", value=2,
                                   n_samples=1, details={"answered": False})
                db.add(row)
                await db.flush()
        except IntegrityError:
            # Another tab created the same day's row while this request waited.
            row = (await db.execute(_query(user_id, day).with_for_update())).scalar_one()
    if row is not None:
        details = dict(row.details or {})
        if answers:
            details[KEY] = {"v": 1, **answers}
        else:
            details.pop(KEY, None)
        row.details = details
        await db.flush()
    return presentation(day, row.details if row else None)


async def feels(db: AsyncSession, user_id: int, first: date, last: date) -> dict[date, str]:
    """The one-tap answers of the days `first` to `last` (Santé's track of dots), the unanswered days left out."""
    rows = (await db.execute(select(HealthMetric.date, HealthMetric.details).where(
        HealthMetric.user_id == user_id, HealthMetric.metric == "feel", HealthMetric.source == "PaceForge",
        HealthMetric.date >= first, HealthMetric.date <= last))).all()
    return {day: feel for day, details in rows if (feel := presentation(day, details)["feel"])}
