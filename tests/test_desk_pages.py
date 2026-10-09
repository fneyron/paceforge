"""Réglages and Activités: the hooks of their desktop layouts. A missing value in an
activity row keeps main's « — » over its label below 1024 px; on a desktop that dash
(.pf-act-none) is not drawn and a column no row fills is not drawn either."""

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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


async def test_activity_rows_missing_values(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    # a trail run imported without speed nor heart rate, a flat run with everything
    db_session.add(_activity(test_user, 0))
    db_session.add(_activity(test_user, 1, sport_type="Run", name="Piste", total_elevation_gain=0.0,
                             average_speed=1000 / 300, average_heartrate=152.0))
    await db_session.flush()
    page = (await as_user.get("/activities")).text
    rows = re.findall(r'<a href="/activity/\d+".*?</a>', page, re.S)
    assert len(rows) == 2
    trail, flat = (next(r for r in rows if name in r) for name in ("Sortie 0", "Piste"))
    for cell in ("pf-act-dplus", "pf-act-pace", "pf-act-hr"):
        assert f'class="{cell} ' in trail and f'class="{cell} ' in flat
    # below 1024 px, main's « — » over its label (the pace's in ink, the flat D+'s in grey); the bpm column is desktop only
    assert '<b class="pf-act-none block text-[16px] font-semibold text-ink">—</b><span class="pf-label">allure</span>' in trail
    assert '<b class="pf-act-none block text-[16px] font-semibold text-gray-300">—</b><span class="pf-label">D+</span>' in flat
    assert ">+310<" in trail and "bpm" not in trail
    assert '5:00<span class="pf-act-pu">/km</span>' in flat and ">152<" in flat and "pf-act-none" not in flat.split("pf-act-pace")[1]
    # one week, in the list: its rows sit beside its heading on a desktop (#activity-list > section)
    assert page.split('id="activity-list"')[1].count('<section id="week-') == 1


async def test_activity_rows_ultra_values_and_recent_view(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    # GPS distances are never round, an ultra's D+ has 4 or 5 digits: the desktop values hold them on one line
    db_session.add(_activity(test_user, 0, distance=12345.6, total_elevation_gain=1234.0, average_speed=2.3))
    db_session.add(_activity(test_user, 2, name="Transjeju", distance=147060.0, moving_time=75480, total_elevation_gain=12000.0))
    await db_session.flush()
    page = (await as_user.get("/activities")).text
    assert '>12.35<small class="pf-act-u"> km</small>' in page and '>+1234<small class="pf-act-u"> m</small>' in page
    assert '>147.06<small class="pf-act-u"> km</small>' in page and '>+12000<small class="pf-act-u"> m</small>' in page
    css = (Path(__file__).resolve().parents[1] / "app/static/css/interface.css").read_text(encoding="utf-8")
    desk = css.split("/* @desk-pages */", 1)[1]
    assert ".pf-act-stats b { white-space: nowrap; }" in desk
    widths = {sel: int(w) for sel, w in re.findall(r"\.pf-act-stats > ([^{]+?) \{ width: (\d+)px; \}", desk)}
    assert widths[":nth-child(1)"] >= 76 and widths[".pf-act-dplus"] >= 76  # « 147.06 km », « +12000 m »: 75 px
    # Santé's « depuis » view lists the same rows in #recent: the empty-column rule covers it too
    since = (date.today() - timedelta(days=7)).isoformat()
    recent = (await as_user.get(f"/activities?depuis={since}")).text
    assert '<section id="recent"' in recent and recent.split('<section id="recent"')[1].count('class="pf-activity-row') == 2
    for col in ("dplus", "pace"):
        assert f":is(#activity-list, #recent):not(:has(.pf-act-{col} > b:not(.pf-act-none))) .pf-act-{col}" in desk


async def test_settings_sections_and_identity(as_user: AsyncClient):
    page = (await as_user.get("/settings")).text
    assert "max-w-3xl" not in page  # the column width lives in the CSS (none on a desktop)
    assert 'class="pf-section pf-set-id"' in page and 'class="pf-section pf-set-danger"' in page
    assert 'id="coros" class="pf-section' in page and 'id="garmin" class="pf-section' in page
    assert 'pf-set-num' in page  # the weight field: a number, not a 300 px field

