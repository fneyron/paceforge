"""Réglages and Activités: the hooks of their desktop layouts, and no dash for a
missing value in the activity rows (the cell stays, empty)."""

import re
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.user import User


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


def _activity(user: User, k: int, **kw) -> Activity:
    fields = dict(strava_activity_id=7000 + k, user_id=user.id, sport_type="TrailRun", name=f"Sortie {k}",
                  start_date=datetime.now(timezone.utc) - timedelta(days=k + 1), distance=12000.0,
                  moving_time=4200, elapsed_time=4300, total_elevation_gain=310.0, raw_data={})
    fields.update(kw)
    return Activity(**fields)


async def test_activity_rows_leave_missing_values_empty(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    # a trail run imported without speed nor heart rate, a flat run with everything
    db_session.add(_activity(test_user, 0))
    db_session.add(_activity(test_user, 1, sport_type="Run", name="Piste", total_elevation_gain=0.0,
                             average_speed=1000 / 300, average_heartrate=152.0))
    await db_session.flush()
    page = (await as_user.get("/activities")).text
    rows = re.findall(r'<a href="/activity/\d+".*?</a>', page, re.S)
    assert len(rows) == 2 and all("—" not in row for row in rows)
    trail, flat = (next(r for r in rows if name in r) for name in ("Sortie 0", "Piste"))
    # the cells are there (aligned columns), empty when the value is missing: no label without its value
    for cell in ("pf-act-dplus", "pf-act-pace", "pf-act-hr"):
        assert f'class="{cell} ' in trail and f'class="{cell} ' in flat
    assert "allure" not in trail and "bpm" not in trail and ">+310<" in trail
    assert "D+" not in flat and '5:00<span class="pf-act-pu">/km</span>' in flat and ">152<" in flat
    # one week, in the list: its rows sit beside its heading on a desktop (#activity-list > section)
    assert page.split('id="activity-list"')[1].count('<section id="week-') == 1


async def test_settings_sections_and_identity(as_user: AsyncClient):
    page = (await as_user.get("/settings")).text
    assert "max-w-3xl" not in page  # the column width lives in the CSS (none on a desktop)
    assert 'class="pf-section pf-set-id"' in page and 'class="pf-section pf-set-danger"' in page
    assert 'id="coros" class="pf-section' in page and 'id="garmin" class="pf-section' in page
    assert 'pf-set-num' in page  # the weight field: a number, not a 300 px field

