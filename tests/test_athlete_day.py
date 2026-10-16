"""« Mes courses » and the plan page live on the athlete's day (sante_training.athlete_today, the day Santé
reads), not on the server's UTC day: at 20:54 UTC an athlete in Seoul is on the next morning."""

import html as html_lib
import json
import re
from datetime import date, datetime, timezone

import pytest
from httpx import AsyncClient

from app.dependencies import get_current_user
from app.models.route import Route
from app.models.user import User
from app.routers.simulator import _route_card
from app.services import sante_training as st
from tests.test_race_plan_services import CPS, _course

_REAL_TODAY = st.athlete_today  # the helper itself, whatever a test pinned before


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User, db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


def _pin_now(monkeypatch, now: datetime):
    """The athlete's day read at a fixed instant, through the real helper (the user's zone decides)."""

    async def at(db, user_id, now_=None, latest=None, zone=None):
        return await _REAL_TODAY(db, user_id, now, latest, zone=zone)

    monkeypatch.setattr(st, "athlete_today", at)


async def _route(client: AsyncClient, name: str, race_date: str, start_hour: int = 5) -> int:
    r = await client.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(CPS), "name": name,
        "race_date": race_date, "start_hour": start_hour, "start_minute": 0, "sport_type": "trail",
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _next_card(client: AsyncClient, rid: int) -> str:
    """The text of the « Prochaine course » card on « Mes courses » (its date pill says when)."""
    html = (await client.get("/simulator")).text
    block = html.split(f'id="route-next-{rid}"')[1].split("</a>")[0]
    return html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", block)))


@pytest.mark.asyncio
async def test_race_day_in_seoul_is_today_not_tomorrow(as_user: AsyncClient, test_user: User, monkeypatch):
    test_user.timezone = "Asia/Seoul"
    rid = await _route(as_user, "Jour J Séoul", "2026-10-15")  # 05:00 KST on the 15th: 20:00 UTC on the 14th
    # 20:54 UTC on the 14th is 05:54 on the 15th in Seoul: the race started an hour ago
    _pin_now(monkeypatch, datetime(2026, 10, 14, 20, 54, tzinfo=timezone.utc))
    card = await _next_card(as_user, rid)
    assert "jeu. 15 oct. · aujourd'hui" in card and "demain" not in card
    html = (await as_user.get(f"/simulator/routes/{rid}")).text
    assert 'id="rtab-realise"' in html  # the débrief is in the menu on race day
    assert '<span id="meta-date">jeu. 15 oct.</span>' in html
    # the evening before in Seoul (10:00 UTC on the 14th, 19:00 there): tomorrow, no débrief yet
    _pin_now(monkeypatch, datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc))
    assert "jeu. 15 oct. · demain" in await _next_card(as_user, rid)
    assert 'id="rtab-realise"' not in (await as_user.get(f"/simulator/routes/{rid}")).text


@pytest.mark.asyncio
async def test_the_year_follows_the_athletes_day(as_user: AsyncClient, test_user: User, monkeypatch):
    test_user.timezone = "Asia/Seoul"
    rid = await _route(as_user, "Nouvel an", "2027-01-01")
    _pin_now(monkeypatch, datetime(2026, 12, 31, 20, 54, tzinfo=timezone.utc))  # already 2027 in Seoul
    html = (await as_user.get(f"/simulator/routes/{rid}")).text
    assert '<span id="meta-date">ven. 1 janv.</span>' in html  # this year's: no year
    assert "ven. 1 janv. · aujourd'hui" in await _next_card(as_user, rid)
    _pin_now(monkeypatch, datetime(2026, 12, 31, 10, 0, tzinfo=timezone.utc))  # still 2026 there
    assert '<span id="meta-date">ven. 1 janv. 2027</span>' in (await as_user.get(f"/simulator/routes/{rid}")).text
    assert "ven. 1 janv. 2027 · demain" in await _next_card(as_user, rid)


def test_route_card_reads_the_day_it_is_given():
    rt = Route(user_id=1, name="r", total_distance_km=30, race_date="2027-01-01")
    rt.days_to = 0
    assert _route_card(rt, today=date(2027, 1, 1))["date"] == "ven. 1 janv."
    assert _route_card(rt, today=date(2027, 1, 1))["when"] == "aujourd'hui"
    assert _route_card(rt, today=date(2026, 12, 31))["date"] == "ven. 1 janv. 2027"
    rt.days_to = -1
    assert _route_card(rt, today=date(2027, 1, 2))["date"] == "ven. 1 janv. 2027"  # past races carry their year
