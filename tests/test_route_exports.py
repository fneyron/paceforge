"""Exports: a file name in any script, a CSV Excel opens as text, the GPX trace re-imported as it was."""
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.dependencies import get_current_user
from app.models.route import RouteCheckpoint
from app.models.user import User
from app.services.pace_export import _cell
from tests.test_gpx_import import created, gpx_bytes, line, upload
from tests.test_race_plan_services import CPS, _course


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User, db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    return client


async def _route(client, name="Jeju test", cps=CPS) -> int:
    r = await client.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(cps), "name": name,
        "target_time_s": 5 * 3600, "race_date": "2026-10-02", "start_hour": 21, "start_minute": 0, "sport_type": "trail"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.mark.parametrize("name, ascii_name, encoded", [
    ("Trail Červený", "Trail Cerveny", "Trail%20%C4%8Cerven%C3%BD"),
    ("Ştefan Trail", "Stefan Trail", "%C5%9Etefan%20Trail"),
    ("제주 100K", "100K", "%EC%A0%9C%EC%A3%BC%20100K"),
    ("Œuvre", "uvre", "%C5%92uvre"),
])
async def test_export_file_names_in_any_script(as_user, name, ascii_name, encoded):
    """Headers go out latin-1: an ASCII name for every browser, the full one RFC 5987-encoded beside it."""
    rid = await _route(as_user, name=name)
    for url, suffix in ((f"/api/simulator/routes/{rid}/gpx", ".gpx"),
                        (f"/api/simulator/routes/{rid}/pace-export?format=csv", "-plan.csv"),
                        (f"/api/simulator/routes/{rid}/pace-export?format=tcx", "-plan.tcx"),
                        (f"/api/simulator/routes/{rid}/pace-export?format=gpx", "-plan.gpx")):
        r = await as_user.get(url)
        assert r.status_code == 200, url
        assert r.headers["content-disposition"] == f"attachment; filename=\"{ascii_name}{suffix}\"; filename*=UTF-8''{encoded}{suffix}"


async def test_the_csv_opens_in_excel_as_text(as_user):
    cps = [dict(CPS[0], name='=HYPERLINK("http://evil";"x")'), dict(CPS[1], name="+33 Col; du Bonhomme"),
           dict(CPS[2], name="-Village")]
    r = await as_user.get(f"/api/simulator/routes/{await _route(as_user, cps=cps)}/pace-export?format=csv")
    assert r.status_code == 200 and r.content.startswith(b"\xef\xbb\xbf")  # the BOM: Excel reads UTF-8 with it
    rows = r.content.decode("utf-8-sig").split("\r\n")  # CRLF, « ; » separated as before
    assert rows[0].startswith("point;km;altitude_m;") and rows[1].startswith("Départ;0;;21:00;")
    assert rows[2].startswith('"\'=HYPERLINK(""http://evil"";""x"")";6.0;')  # text, not a formula Excel runs
    assert rows[3].startswith("\"'+33 Col; du Bonhomme\";10.0;") and rows[4].startswith("'-Village;20.0;")
    assert _cell("@home") == "'@home" and _cell("Col") == "Col" and _cell("") == "" and _cell(6.0) == 6.0


async def test_the_trace_export_reimports_without_stacking_cp_prefixes(as_user, db_session):
    rid = created(await upload(as_user, gpx_bytes(line(), waypoints=[("Ravito", 45.05, 6.0), ("Col", 45.1, 6.0)])))
    first = (await db_session.execute(select(RouteCheckpoint.distance_km).where(RouteCheckpoint.route_id == rid)
                                      .order_by(RouteCheckpoint.distance_km))).scalars().all()
    for _ in range(2):
        exported = await as_user.get(f"/api/simulator/routes/{rid}/gpx")
        assert exported.status_code == 200 and "<name>CP1 — Ravito</name>" in exported.text
        rid = created(await upload(as_user, exported.content, filename="export.gpx"))
    cps = (await db_session.execute(select(RouteCheckpoint).where(RouteCheckpoint.route_id == rid)
                                    .order_by(RouteCheckpoint.distance_km))).scalars().all()
    assert [c.name for c in cps] == ["Ravito", "Col"]  # not « CP1 — CP1 — Ravito »
    assert all(abs(c.distance_km - km) <= 0.1 for c, km in zip(cps, first, strict=True))
