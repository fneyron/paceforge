"""What the course is made of, from OpenStreetMap: road, track or trail, per km.

Works for any race with a GPX, no results needed: each point of the trace is
matched to the nearest mapped way (highway + surface tags). A point with no
way within 25 m is off the map, which on a trail race means a trail.

Stored in the course as ``surface_km``: one [road, track, trail] share per km.
Fetched once per trace, in the background (Overpass can take a minute); the
plan simply has no surface term until it is there.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
CHUNK_KM = 15.0
PAD_DEG = 0.003
MATCH_M = 25.0
SAMPLE_KM = 0.05
RETRY_AFTER = timedelta(days=1)

# How much slower a trail is than a road at the same gradient and effort (a
# track is half-way). Fitted on the 2025 Transjeju splits of Ko and Mamba with
# the 25 m gradient: 0.10-0.15 are equally good (mean checkpoint gap 11.7 min
# before, 8.6 after), 0.12 sits in the middle — and in the +10-15 % the
# physiology literature gives for running off-road.
TRAIL_PENALTY = 0.12
TRACK_PENALTY = TRAIL_PENALTY / 2

_PAVED = {"asphalt", "concrete", "paved", "paving_stones", "concrete:plates", "concrete:lanes", "sett", "chipseal", "metal", "wood"}
_ROADS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "service",
    "living_street", "road", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}
_FOOT = {"path", "footway", "bridleway", "cycleway", "pedestrian", "steps"}
CLASSES = ("road", "track", "trail")

_pending: set[asyncio.Task] = set()


def classify_way(tags: dict) -> str:
    """road / track / trail from a way's OSM tags."""
    hw, surface = tags.get("highway"), tags.get("surface")
    if surface in _PAVED:
        return "road"
    if hw in _ROADS:
        return "road" if surface is None else "track"
    if hw == "track":
        return "track"
    if hw in _FOOT:
        return "trail"
    return "track"


def _chunks(coords: list) -> list[tuple[float, float, float, float]]:
    """Bounding boxes (south, west, north, east) of ~15 km pieces of the trace."""
    boxes, cur, start = [], [], None
    for c in coords:
        if start is None:
            start = c[2]
        cur.append(c)
        if c[2] - start >= CHUNK_KM:
            boxes.append(cur)
            cur, start = [c], c[2]
    if len(cur) > 1 or not boxes:
        boxes.append(cur)
    out = []
    for b in boxes:
        lats, lons = [p[0] for p in b], [p[1] for p in b]
        out.append((min(lats) - PAD_DEG, min(lons) - PAD_DEG, max(lats) + PAD_DEG, max(lons) + PAD_DEG))
    return out


def overpass_query(coords: list) -> str:
    parts = "".join(f"way[highway]({s:.5f},{w:.5f},{n:.5f},{e:.5f});" for s, w, n, e in _chunks(coords))
    return f"[out:json][timeout:120];({parts});out tags geom qt;"


async def fetch_ways(coords: list) -> list[dict] | None:
    """The mapped ways around the trace, or None when Overpass can't be reached."""
    query = overpass_query(coords)
    async with httpx.AsyncClient(timeout=180, headers={"User-Agent": "PaceForge (paceforge.fr)"}) as client:
        for url in OVERPASS_URLS:
            try:
                r = await client.post(url, data={"data": query})
                if r.status_code == 200:
                    return r.json().get("elements") or []
                logger.info("Overpass %s answered %s", url, r.status_code)
            except (httpx.HTTPError, ValueError) as e:
                logger.info("Overpass %s failed: %s", url, e)
    return None


def surface_by_km(coords: list, ways: list[dict]) -> list[list[float]]:
    """[road, track, trail] shares for each km of the trace (index = km)."""
    cell = 0.002
    grid: dict[tuple[int, int], list[int]] = {}
    segs: list[tuple[float, float, float, float, str]] = []
    for w in ways:
        geom = w.get("geometry") or []
        cls = classify_way(w.get("tags") or {})
        for a, b in zip(geom, geom[1:]):
            segs.append((a["lat"], a["lon"], b["lat"], b["lon"], cls))
            i = len(segs) - 1
            # every cell the segment crosses, not just its ends (a straight
            # road can run 1 km between two nodes)
            steps = max(1, math.ceil(max(abs(b["lat"] - a["lat"]), abs(b["lon"] - a["lon"])) / (cell / 2)))
            keys = {
                (int((a["lat"] + (b["lat"] - a["lat"]) * t / steps) / cell), int((a["lon"] + (b["lon"] - a["lon"]) * t / steps) / cell))
                for t in range(steps + 1)
            }
            for k in keys:
                grid.setdefault(k, []).append(i)

    def nearest(lat: float, lon: float) -> str:
        kx, ky = 111320 * math.cos(math.radians(lat)), 110540
        best, cls = MATCH_M, "trail"
        gi, gj = int(lat / cell), int(lon / cell)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for i in grid.get((gi + di, gj + dj), ()):
                    s = segs[i]
                    ax, ay = (s[1] - lon) * kx, (s[0] - lat) * ky
                    bx, by = (s[3] - lon) * kx, (s[2] - lat) * ky
                    dx, dy = bx - ax, by - ay
                    ln = dx * dx + dy * dy
                    t = 0.0 if ln == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / ln))
                    d = math.hypot(ax + t * dx, ay + t * dy)
                    if d < best:
                        best, cls = d, s[4]
        return cls

    counts: dict[int, list[int]] = {}
    last = -1.0
    for c in coords:
        if c[2] - last < SAMPLE_KM:
            continue
        last = c[2]
        k = int(c[2])
        counts.setdefault(k, [0, 0, 0])[CLASSES.index(nearest(c[0], c[1]))] += 1
    n_km = int(coords[-1][2]) + 1 if coords else 0
    out = []
    for k in range(n_km):
        cnt = counts.get(k)
        tot = sum(cnt) if cnt else 0
        out.append([round(x / tot, 3) for x in cnt] if tot else [0.0, 0.0, 1.0])
    return out


def km_multiplier(shares: list[float]) -> float:
    road, track, trail = shares
    return road + track * (1 + TRACK_PENALTY) + trail * (1 + TRAIL_PENALTY)


async def ensure_surface(route_id: int) -> None:
    """Fetch and store the route's surface once (retried a day after a failure)."""
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.models.route import Route

    async with async_session_factory() as db:
        route = (await db.execute(select(Route).where(Route.id == route_id))).scalar_one_or_none()
        if not route or not route.course_json:
            return
        cj = dict(route.course_json)
        coords = cj.get("route_coords") or []
        if cj.get("surface_km") or len(coords) < 50:
            return
        tried = cj.get("surface_tried")
        now = datetime.now(timezone.utc)
        if tried:
            try:
                if now - datetime.fromisoformat(tried) < RETRY_AFTER:
                    return
            except ValueError:
                pass
        # claim it first, so the other workers don't all ask Overpass at once
        cj["surface_tried"] = now.isoformat()
        route.course_json = cj
        await db.commit()

    ways = await fetch_ways(coords)
    if ways is None:
        return
    surface = await asyncio.to_thread(surface_by_km, coords, ways)

    async with async_session_factory() as db:
        route = (await db.execute(select(Route).where(Route.id == route_id))).scalar_one_or_none()
        if not route or not route.course_json:
            return
        cj = dict(route.course_json)
        if (cj.get("route_coords") or [])[-1:] != coords[-1:]:
            return  # the trace changed meanwhile
        cj["surface_km"] = surface
        route.course_json = cj
        await db.commit()
        logger.info("Route %d: surface stored (%d km)", route_id, len(surface))


def schedule_surface(route_id: int, course_json: dict | None) -> None:
    """Start the background fetch when the course has a trace but no surface yet."""
    from app.config import settings

    cj = course_json or {}
    if not settings.SURFACE_FETCH or cj.get("surface_km") or len(cj.get("route_coords") or []) < 50:
        return
    tried = cj.get("surface_tried")
    if tried:
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(tried) < RETRY_AFTER:
                return
        except ValueError:
            pass
    try:
        task = asyncio.get_running_loop().create_task(ensure_surface(route_id))
    except RuntimeError:
        return
    _pending.add(task)
    task.add_done_callback(_done)


def _done(task: asyncio.Task) -> None:
    _pending.discard(task)
    if not task.cancelled() and task.exception():
        logger.warning("Surface fetch failed: %r", task.exception())
