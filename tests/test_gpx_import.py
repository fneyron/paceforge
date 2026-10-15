"""GPX import: route-only files, several tracks, missing elevations, encodings, the refusals (size, points,
routes per account) in French, one parse off the event loop, grid snapping, the commit before the redirect."""
import math
import re
import threading

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.dependencies import get_current_user
from app.models.route import Route, RouteCheckpoint
from app.models.user import User
from app.routers import simulator as sim
from app.services import gpx as gpx_service
from tests.test_race_plan_services import _course


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User, db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    return client


def gpx_bytes(points, name="Trail Test", waypoints=(), encoding="UTF-8", kind="trk", extra_tracks=()):
    """A GPX file: a <trk> (or a <rte>) of (lat, lon, ele) points, ele None for a missing <ele>, the named
    <wpt> before it, more <trk> after it."""
    head = (f'<?xml version="1.0" encoding="{encoding}"?>\n<gpx version="1.1" creator="test" '
            f'xmlns="http://www.topografix.com/GPX/1/1">\n<metadata><name>{name}</name></metadata>\n')
    body = "".join(f'<wpt lat="{lat}" lon="{lon}"><name>{wn}</name></wpt>\n' for wn, lat, lon in waypoints)
    tag, ptag, seg = ("trk", "trkpt", "trkseg") if kind == "trk" else ("rte", "rtept", "")
    for tname, pts in [(name, points), *(("Variante", p) for p in extra_tracks)]:
        body += f"<{tag}><name>{tname}</name>" + (f"<{seg}>" if seg else "") + "\n"
        for lat, lon, ele in pts:
            body += f'<{ptag} lat="{lat}" lon="{lon}">' + (f"<ele>{ele}</ele>" if ele is not None else "") + f"</{ptag}>\n"
        body += (f"</{seg}>" if seg else "") + f"</{tag}>\n"
    return (head + body + "</gpx>\n").encode(encoding)


def line(n=400, km=20.0, lat0=45.0, lon0=6.0, ele0=500.0):
    """n points along a meridian, km long, over one hill of 300 m."""
    return [(round(lat0 + i / (n - 1) * km / 111.32, 6), lon0, round(ele0 + 300 * math.sin(i / (n - 1) * math.pi), 1))
            for i in range(n)]


async def upload(client, content, filename="course.gpx"):
    return await client.post("/partials/simulator/gpx-upload",
                             files={"gpx_file": (filename, content, "application/gpx+xml")})


def created(r) -> int:
    """The id of the route an upload redirected to."""
    assert r.status_code == 204, r.text
    return int(r.headers["hx-redirect"].rsplit("/", 1)[1])


async def test_a_route_only_file_is_imported(as_user, db_session):
    route = await db_session.get(Route, created(await upload(as_user, gpx_bytes(line(), kind="rte"))))
    assert 19.9 <= route.total_distance_km <= 20.1 and route.name == "Trail Test"


async def test_the_longest_of_several_tracks_is_kept_and_said(as_user, db_session):
    r = await upload(as_user, gpx_bytes(line(km=20.0), name="Deux", extra_tracks=[line(km=50.0, lat0=46.0, lon0=7.0)]))
    assert r.status_code == 200, r.text
    assert "Ce fichier contient 2 traces : la plus longue (49,9 km) a été gardée." in r.text
    route = await db_session.get(Route, int(re.search(r'href="/simulator/routes/(\d+)"', r.text).group(1)))
    assert 49.8 <= route.total_distance_km <= 50.0  # not 20 km + the 140 km between the tracks + 50 km


async def test_missing_elevations_are_interpolated_and_a_real_zero_stays(as_user, db_session):
    pts = [(la, lo, None if i % 7 == 0 else e) for i, (la, lo, e) in enumerate(line(n=100, km=5.0, ele0=1500.0))]
    route = await db_session.get(Route, created(await upload(as_user, gpx_bytes(pts))))
    assert 250 <= route.total_elevation_gain <= 350 and 250 <= route.total_elevation_loss <= 350  # was 25 400 m
    parsed = gpx_service.read_gpx(gpx_bytes([(45.0, 6.0, 0), (45.001, 6.0, 0), (45.002, 6.0, None), (45.003, 6.0, 10)]))
    assert [round(p.elevation, 1) for p in parsed.points] == [0.0, 0.0, 5.0, 10.0]
    parsed = gpx_service.read_gpx(gpx_bytes([(45.0, 6.0, None), (45.001, 6.0, None), (45.002, 6.0, 12)]))
    assert [p.elevation for p in parsed.points] == [12.0, 12.0, 12.0]  # the ends take the nearest known value


@pytest.mark.parametrize("encoding, name", [("ISO-8859-1", "Forêt d'Été"), ("UTF-16", "Forêt"), ("UTF-8", "Trail")])
async def test_the_declared_encoding_and_the_bom_are_honoured(as_user, db_session, encoding, name):
    content = gpx_bytes(line(), name=name, encoding=encoding)
    if encoding == "UTF-8":
        content = b"\xef\xbb\xbf" + content
    route = await db_session.get(Route, created(await upload(as_user, content)))
    assert route.name == name


@pytest.mark.parametrize("content, reason", [
    (gpx_bytes(line()).replace(b"UTF-8", b"KLINGON"), "son encodage « KLINGON » est inconnu."),
    (gpx_bytes(line(), name="Forêt", encoding="ISO-8859-1").replace(b'"ISO-8859-1"', b'"UTF-8"'),
     "son texte ne correspond pas à l’encodage annoncé (UTF-8)."),
    (gpx_bytes(line(), name="Forêt", encoding="ISO-8859-1").replace(b'"ISO-8859-1"', b'"undefined"'),
     "son texte ne correspond pas à l’encodage annoncé (undefined)."),
    (gpx_bytes([*line()[:10], ("nan", 6.0, 500.0), *line()[11:]]), "il contient des coordonnées invalides."),
    (gpx_bytes([*line()[:10], ("inf", 6.0, 500.0), *line()[11:]]), "il contient des coordonnées invalides."),
    (b"not xml at all", "ce n’est pas un fichier XML valide."),
    (b'<gpx><trk><trkseg><trkpt lat="abc" lon="6"/></trkseg></trk></gpx>', "il contient des valeurs invalides."),
])
async def test_an_unreadable_file_gets_one_french_sentence(as_user, content, reason):
    r = await upload(as_user, content)
    assert r.status_code == 200 and f"Ce fichier GPX n’a pas pu être lu : {reason}" in r.text
    assert not re.search(r"codec|NaN|float|Traceback|Error", r.text)
    r = await upload(as_user, b'<gpx version="1.1"><trk><trkseg></trkseg></trk></gpx>')
    assert "Le fichier GPX ne contient aucun point de trace." in r.text


def test_the_declaration_may_space_its_equals_sign_and_lie_about_a_utf8_file():
    latin = gpx_bytes(line(), name="Forêt d'Été", encoding="ISO-8859-1").replace(b'encoding="ISO-8859-1"',
                                                                                  b'encoding = "ISO-8859-1"')
    assert gpx_service.read_gpx(latin).name == "Forêt d'Été"
    # UTF-8 bytes under a single-byte declaration (a file re-saved by an editor): UTF-8 first, not « ForÃªt »
    lying = gpx_bytes(line(), name="Forêt").replace(b'"UTF-8"', b'"ISO-8859-1"')
    assert gpx_service.read_gpx(lying).name == "Forêt"


def test_only_the_exports_own_cp_prefix_is_stripped():
    parsed = gpx_service.read_gpx(gpx_bytes(line(), waypoints=[
        ("CP1 — Ravito", 45.05, 6.0), ("CP3 - Les Contamines", 45.1, 6.0), ("CP4 – Col", 45.12, 6.0),
        ("CP5", 45.13, 6.0), ("CP6 — ", 45.14, 6.0)]))
    assert [w["name"] for w in parsed.waypoints] == ["Ravito", "CP3 - Les Contamines", "CP4 – Col", "CP5", "CP6 —"]


async def test_a_doctype_is_refused_before_any_parse(as_user, monkeypatch):
    """An internal entity would expand one literal point into as many as the DTD says: the text's point count
    saw one, the parser would build them all."""
    parses = []
    real = gpx_service.gpxpy.parse
    monkeypatch.setattr(gpx_service.gpxpy, "parse", lambda *a, **k: parses.append(1) or real(*a, **k))
    entity = (b'<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY p \'<trkpt lat="45" lon="6"><ele>1</ele></trkpt>\'>]>'
              b'<gpx version="1.1"><trk><trkseg><trkpt lat="45" lon="6"/>' + b"&p;" * 1000 + b"</trkseg></trk></gpx>")
    r = await upload(as_user, entity)
    assert r.status_code == 200 and "il contient une déclaration DOCTYPE, dont un GPX n’a pas besoin." in r.text
    assert parses == []


def test_the_point_cap_holds_whatever_the_text_counted(monkeypatch):
    import gpxpy.gpx as g

    fake, trk, seg = g.GPX(), g.GPXTrack(), g.GPXTrackSegment()
    trk.segments.append(seg)
    fake.tracks.append(trk)
    seg.points.extend(g.GPXTrackPoint(45 + i * 1e-4, 6.0, elevation=1.0) for i in range(60))
    monkeypatch.setattr(gpx_service.gpxpy, "parse", lambda text: fake)
    monkeypatch.setattr(gpx_service, "MAX_POINTS", 50)
    with pytest.raises(gpx_service.GpxError, match="trop de points"):
        gpx_service.read_gpx(b"<gpx/>")


async def test_oversized_files_are_refused_before_any_parse(as_user, monkeypatch):
    parses = []
    real = gpx_service.gpxpy.parse
    monkeypatch.setattr(gpx_service.gpxpy, "parse", lambda *a, **k: parses.append(1) or real(*a, **k))
    monkeypatch.setattr(sim, "MAX_GPX_BYTES", 2000)
    r = await upload(as_user, gpx_bytes(line()))
    assert r.status_code == 200 and "Ce fichier GPX est trop lourd" in r.text
    monkeypatch.setattr(sim, "MAX_GPX_BYTES", 12 * 2**20)
    monkeypatch.setattr(gpx_service, "MAX_POINTS", 50)
    r = await upload(as_user, gpx_bytes(line(n=60)))
    assert r.status_code == 200 and "Ce fichier GPX contient trop de points (60 ; 50 au plus)." in r.text
    assert parses == []


async def test_the_file_is_parsed_once_off_the_event_loop(as_user, monkeypatch):
    threads = []
    real = gpx_service.gpxpy.parse
    monkeypatch.setattr(gpx_service.gpxpy, "parse",
                        lambda *a, **k: threads.append(threading.current_thread()) or real(*a, **k))
    created(await upload(as_user, gpx_bytes(line(), waypoints=[("Ravito", 45.05, 6.0)])))
    assert len(threads) == 1 and threads[0] is not threading.main_thread()


def test_snapping_scans_the_cells_around_a_waypoint_not_the_whole_track(monkeypatch):
    waypoints = [(f"W{k}", round(45.0 + k * 0.009, 6), round(6.0 + (-0.004 if k % 2 else 0.003), 6)) for k in range(50)]
    parsed = gpx_service.read_gpx(gpx_bytes(line(n=5000, km=50.0), waypoints=waypoints))
    calls = []
    real = gpx_service._haversine
    monkeypatch.setattr(gpx_service, "_haversine", lambda *a: calls.append(1) or real(*a))
    snapped = gpx_service.snap_waypoints_to_route(parsed.waypoints, parsed.points)
    assert len(calls) < len(parsed.points) * len(parsed.waypoints) / 10  # 9 cells of ~100 points each, not 250 000

    def nearest(w):  # the same answer as comparing with every point of the track
        return min(parsed.points, key=lambda p: real(w["lat"], w["lon"], p.lat, p.lon))
    expected = sorted(({"name": w["name"], "distance_km": round(nearest(w).distance_from_start / 1000, 1),
                        "elevation": round(nearest(w).elevation, 0)} for w in parsed.waypoints
                       if real(w["lat"], w["lon"], nearest(w).lat, nearest(w).lon) < 500),
                      key=lambda w: w["distance_km"])
    assert snapped == expected and len(snapped) == 50


async def test_an_account_stores_a_bounded_number_of_routes(as_user, monkeypatch):
    monkeypatch.setattr(sim, "MAX_ROUTES", 2)
    for _ in range(2):
        created(await upload(as_user, gpx_bytes(line())))
    refusal = "Tu as déjà 2 parcours enregistrés : supprime-en un avant d’en importer un autre."
    r = await upload(as_user, gpx_bytes(line()))
    assert r.status_code == 200 and refusal in r.text
    r = await as_user.post("/api/simulator/routes", data={"course_json": _course().model_dump_json(),
                                                          "checkpoints_json": "[]", "name": "x"})
    assert r.status_code == 422 and r.json()["error"] == refusal


async def test_the_route_is_committed_before_the_redirect(client, test_user, db_session, monkeypatch):
    """get_db commits after the response has gone, and the browser follows the redirect at once: the upload and
    the re-import commit themselves first."""
    commits = []

    async def commit():
        commits.append(1)
        await db_session.flush()
    monkeypatch.setattr(db_session, "commit", commit)
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    rid = created(await upload(client, gpx_bytes(line(), waypoints=[("Ravito", 45.05, 6.0)])))
    assert commits == [1]
    r = await client.post(f"/api/simulator/routes/{rid}/reimport",
                          files={"gpx_file": ("v2.gpx", gpx_bytes(line(km=25.0), name="Autre nom"), "application/gpx+xml")})
    assert r.status_code == 204 and r.headers["hx-redirect"] == f"/simulator/routes/{rid}" and commits == [1, 1]
    route = await db_session.get(Route, rid)
    names = (await db_session.execute(select(RouteCheckpoint.name).where(RouteCheckpoint.route_id == rid))).scalars().all()
    assert route.name == "Trail Test" and 24.9 <= route.total_distance_km <= 25.1 and names == ["Ravito"]
