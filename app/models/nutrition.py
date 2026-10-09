from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NutritionProduct(Base):
    """A reusable nutrition item in the athlete's pantry (gel, drink mix, bar…).

    Values are per single unit (one gel, one bar, one bottle of mix). The
    per-race plan (see Route.nutrition_json) references these by id.
    """

    __tablename__ = "nutrition_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # gel | drink | bar | solid | salt
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="gel")
    carbs_g: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    sodium_mg: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    caffeine_mg: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_ml: Mapped[float | None] = mapped_column(Float, nullable=True)  # a drink's flask, a salt tablet's water
    # prises in one unit: a resealable PF 90 pouch is taken in 3 goes of 30 g
    servings: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
