"""GPX file parsing and course profile building."""

import codecs
import logging
import math
import re
from dataclasses import dataclass

import gpxpy
from gpxpy.gpx import GPXException, GPXXMLSyntaxException

from app.schemas.simulator import CourseProfile, CourseSegment, GpxPoint

logger = logging.getLogger(__name__)

# a file past this many <trkpt> / <rtept> is refused before the XML is parsed (the parse is what costs)
MAX_POINTS = 150_000
# a waypoint further than this from the track is not one of its checkpoints
SNAP_MAX_M = 500.0
_CELL = 0.01  # degrees: 1.1 km of latitude, so a cell and its neighbours hold every point within SNAP_MAX_M
_CANNOT_READ = "Ce fichier GPX n’a pas pu être lu : "
_ENCODING = re.compile(rb"^\s*<\?xml[^>]*?encoding\s*=\s*[\"']([A-Za-z0-9._-]+)[\"']")
_CP_PREFIX = re.compile(r"^CP\d+ — ")  # exactly what the GPX export writes before a checkpoint's name


class GpxError(ValueError):
    """A GPX import refused, with the French sentence to show."""


@dataclass
class ParsedGpx:
    points: list[GpxPoint]
    waypoints: list[dict]  # [{name, lat, lon, elevation}, ...] from the named <wpt> elements
    name: str | None  # <metadata><name>, else the kept track's (route's)
    notice: str | None = None  # the file had several tracks: only the longest is kept, said so


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two GPS points."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _thousands(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _too_many(n: int) -> GpxError:
    return GpxError(f"Ce fichier GPX contient trop de points ({_thousands(n)} ; {_thousands(MAX_POINTS)} au plus).")


def _decode(content: bytes, declared: str) -> str:
    """A single-byte declaration (ISO-8859-1, windows-1252…) over UTF-8 bytes — a file re-saved by an editor —
    would decode silently to mojibake: UTF-8 is tried first, the declared codec is the fallback."""
    if not codecs.lookup(declared).name.startswith(("utf-8", "utf-16", "utf-32")):  # LookupError: unknown
        try:
            return content.decode("utf-8")
        except UnicodeError:
            pass
    return content.decode(declared)


def decode_gpx(content: bytes) -> str:
    """The file's text, read by its BOM or its XML declaration (UTF-8 without either)."""
    for bom, enc in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                     (codecs.BOM_UTF16_BE, "utf-16-be")):
        if content.startswith(bom):
            content, declared = content[len(bom):], enc
            break
    else:
        if content.startswith(b"<\x00?\x00"):
            declared = "utf-16-le"
        elif content.startswith(b"\x00<\x00?"):
            declared = "utf-16-be"
        else:
            m = _ENCODING.match(content)
            declared = m.group(1).decode("ascii") if m else "utf-8"
    try:
        return _decode(content, declared)
    except LookupError:
        raise GpxError(f"{_CANNOT_READ}son encodage « {declared} » est inconnu.") from None
    except UnicodeError:  # a decode error, or a codec such as « undefined » that refuses every byte
        raise GpxError(f"{_CANNOT_READ}son texte ne correspond pas à l’encodage annoncé ({declared}).") from None


def _fill_elevations(ele: list[float | None], dist: list[float]) -> list[float]:
    """Missing <ele> values interpolated along the distance between their known neighbours (the nearest known
    one before the first and after the last); none known at all → 0. A real 0 m is a value, kept."""
    known = [i for i, e in enumerate(ele) if e is not None]
    if not known:
        return [0.0] * len(ele)
    out = list(ele)
    for i in range(known[0]):
        out[i] = ele[known[0]]
    for i in range(known[-1] + 1, len(ele)):
        out[i] = ele[known[-1]]
    for a, b in zip(known, known[1:], strict=False):
        span = dist[b] - dist[a]
        for i in range(a + 1, b):
            f = (dist[i] - dist[a]) / span if span > 0 else 0.0
            out[i] = ele[a] + f * (ele[b] - ele[a])
    return out  # type: ignore[return-value]


def _track(name: str | None, raw: list) -> tuple[str | None, list[GpxPoint]] | None:
    """One track's (or route's) points with their cumulative distance and every elevation filled in."""
    if not raw:
        return None
    lat: list[float] = []
    lon: list[float] = []
    ele: list[float | None] = []
    dist: list[float] = []
    for p in raw:
        la, lo = p.latitude, p.longitude
        if la is None or lo is None or not (-90 <= la <= 90 and -180 <= lo <= 180):  # NaN and ±inf fail too
            raise GpxError(f"{_CANNOT_READ}il contient des coordonnées invalides.")
        dist.append(dist[-1] + _haversine(lat[-1], lon[-1], la, lo) if lat else 0.0)
        lat.append(la)
        lon.append(lo)
        ele.append(float(p.elevation) if p.elevation is not None and math.isfinite(p.elevation) else None)
    filled = _fill_elevations(ele, dist)
    return name, [GpxPoint(lat=a, lon=b, elevation=c, distance_from_start=d)
                  for a, b, c, d in zip(lat, lon, filled, dist, strict=True)]


def read_gpx(content: bytes) -> ParsedGpx:
    """The usable track of a GPX file (<trk>, else <rte>), its named waypoints and its name. Several tracks:
    the longest is kept (each measured from its own start), the others left out and said so."""
    text = decode_gpx(content)
    if "<!DOCTYPE" in text:  # a DTD can define an entity that expands into points by the million: no GPX needs one
        raise GpxError(f"{_CANNOT_READ}il contient une déclaration DOCTYPE, dont un GPX n’a pas besoin.")
    n = text.count("<trkpt") + text.count("<rtept")
    if n > MAX_POINTS:
        raise _too_many(n)
    try:
        gpx = gpxpy.parse(text)
    except GPXXMLSyntaxException:
        raise GpxError(f"{_CANNOT_READ}ce n’est pas un fichier XML valide.") from None
    except (GPXException, ValueError, OverflowError):
        raise GpxError(f"{_CANNOT_READ}il contient des valeurs invalides.") from None

    tracks = [t for t in (_track(trk.name, [p for s in trk.segments for p in s.points]) for trk in gpx.tracks) if t]
    if not tracks:
        tracks = [t for t in (_track(rte.name, rte.points) for rte in gpx.routes) if t]
    if not tracks:
        raise GpxError("Le fichier GPX ne contient aucun point de trace.")
    if sum(len(t[1]) for t in tracks) > MAX_POINTS:  # whatever the text's count said
        raise _too_many(sum(len(t[1]) for t in tracks))
    name, points = max(tracks, key=lambda t: t[1][-1].distance_from_start)
    notice = None
    if len(tracks) > 1:
        km = f"{points[-1].distance_from_start / 1000:.1f}".replace(".", ",")
        notice = f"Ce fichier contient {len(tracks)} traces : la plus longue ({km} km) a été gardée."

    # Only the genuinely named waypoints: official race GPX files carry dozens of unnamed / auto-numbered
    # points that would otherwise flood the checkpoint list. A re-import of PaceForge's own export loses the
    # « CP1 — » its names were given, so they never stack.
    waypoints = []
    for wpt in gpx.waypoints:
        original = (wpt.name or "").strip()
        wname = _CP_PREFIX.sub("", original) or original
        if not wname or wpt.latitude is None or wpt.longitude is None:
            continue
        waypoints.append({"name": wname, "lat": wpt.latitude, "lon": wpt.longitude, "elevation": wpt.elevation or 0.0})

    title = (gpx.name or name or "").strip()[:120]
    return ParsedGpx(points, waypoints, title or None, notice)


def parse_gpx(file_content: bytes) -> tuple[list[GpxPoint], list[dict]]:
    """(track points, waypoints) of a GPX file: read_gpx's, for the migration that imports a trace."""
    parsed = read_gpx(file_content)
    return parsed.points, parsed.waypoints


def snap_waypoints_to_route(
    waypoints: list[dict], points: list[GpxPoint]
) -> list[dict]:
    """Snap waypoints to the nearest point on the route and compute distance_km. The points are bucketed on a
    coarse grid first: a waypoint is compared with the cells around it, not with the whole track."""
    cells: dict[tuple[int, int], list[GpxPoint]] = {}
    for pt in points:
        cells.setdefault((int(pt.lat // _CELL), int(pt.lon // _CELL)), []).append(pt)
    result = []
    for wpt in waypoints:
        lat, lon = wpt["lat"], wpt["lon"]
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue
        ci, cj = int(lat // _CELL), int(lon // _CELL)
        # longitude cells narrow towards the poles: as many of them as SNAP_MAX_M spans
        reach = 1 + int(SNAP_MAX_M / (_CELL * 111_320 * max(math.cos(math.radians(lat)), 0.01)))
        best_dist, best = float("inf"), None
        for di in (-1, 0, 1):
            for dj in range(-reach, reach + 1):
                for pt in cells.get((ci + di, cj + dj), ()):
                    d = _haversine(lat, lon, pt.lat, pt.lon)
                    if d < best_dist:
                        best_dist, best = d, pt
        if best is not None and best_dist < SNAP_MAX_M:
            result.append({
                "name": wpt["name"],
                "distance_km": round(best.distance_from_start / 1000, 1),
                "elevation": round(best.elevation, 0),
            })
    result.sort(key=lambda w: w["distance_km"])
    return result


def import_course(content: bytes, name: str | None = None, fallback: str = "Course",
                  ) -> tuple[CourseProfile, list[dict], ParsedGpx]:
    """An import's CPU work in one call, to run off the event loop: the file read, its profile built, its
    waypoints snapped. ``name``: the course's (a re-import keeps the route's), else the file's own, else
    ``fallback``."""
    parsed = read_gpx(content)
    course = build_course_profile(parsed.points, name=name or parsed.name or fallback)
    return course, snap_waypoints_to_route(parsed.waypoints, parsed.points), parsed


def _gain_loss_hysteresis(elevations: list[float], threshold: float = 3.0) -> tuple[float, float]:
    """Total elevation gain/loss with a hysteresis threshold (meters).

    A climb is only counted once it rises ``threshold`` above the running
    reference; this filters GPS noise without the heavy under-counting of moving
    averages. Tuned to match Strava/COROS totals closely (e.g. 90km Mont-Blanc:
    ~6.27 km vs Strava 6.26 km).
    """
    if len(elevations) < 2:
        return 0.0, 0.0
    gain = loss = 0.0
    ref_up = ref_down = elevations[0]
    for v in elevations[1:]:
        if v - ref_up > threshold:
            gain += v - ref_up
            ref_up = v
        elif v < ref_up:
            ref_up = v
        if ref_down - v > threshold:
            loss += ref_down - v
            ref_down = v
        elif v > ref_down:
            ref_down = v
    return gain, loss


# Effort D+ (race calibration): one algorithm for the course and for the
# athlete's past races, robust to how noisy the elevation is. Resampled on a
# 20 m distance grid, 100 m moving average, 2 m hysteresis. On 113 UTMB Live
# tracks: median 0.975 of the official D+, 88 % within ±10 % (the per-km raw
# hysteresis above: median 1.026 but 57 %, noisy tracks reading +20-50 %).
# On a watch stream it barely depends on the sampling (Chiang Mai 100k: 4612 m
# at 18 m spacing, 4579 at 37 m, 4503 at 74 m).
DPLUS_GRID_M = 20.0
DPLUS_WINDOW = 5
DPLUS_THRESHOLD_M = 2.0


def profile_elevation_gain(dist_m: list, alt_m: list) -> tuple[float, float]:
    """(gain, loss) in metres of an elevation profile given as cumulative
    distance (m) and altitude (m) lists. Points with a missing value or a
    distance going backwards are skipped; fewer than 2 points → (0, 0)."""
    pts: list[tuple[float, float]] = []
    for d, a in zip(dist_m or [], alt_m or [], strict=False):
        if d is None or a is None:
            continue
        d, a = float(d), float(a)
        if pts and d < pts[-1][0]:
            continue
        pts.append((d, a))
    if len(pts) < 2 or pts[-1][0] <= pts[0][0]:
        return 0.0, 0.0
    # linear resample on the distance grid
    grid: list[float] = []
    j, t, end, n = 0, pts[0][0], pts[-1][0], len(pts)
    while t <= end:
        while j < n - 2 and pts[j + 1][0] < t:
            j += 1
        (d0, a0), (d1, a1) = pts[j], pts[j + 1]
        f = (t - d0) / (d1 - d0) if d1 > d0 else 0.0
        grid.append(a0 + min(max(f, 0.0), 1.0) * (a1 - a0))
        t += DPLUS_GRID_M
    if grid and end - (t - DPLUS_GRID_M) > 1e-6:
        grid.append(pts[-1][1])
    # centred moving average (truncated at the ends)
    half, m = DPLUS_WINDOW // 2, len(grid)
    csum = [0.0]
    for v in grid:
        csum.append(csum[-1] + v)
    smooth = [
        (csum[min(m, i + half + 1)] - csum[max(0, i - half)]) / (min(m, i + half + 1) - max(0, i - half))
        for i in range(m)
    ]
    return _gain_loss_hysteresis(smooth, DPLUS_THRESHOLD_M)


def _smooth_elevations(points: list[GpxPoint], window: int = 5) -> list[float]:
    """Apply moving average to smooth GPS elevation noise."""
    elevations = [p.elevation for p in points]
    n = len(elevations)
    if n < window:
        return elevations

    smoothed = []
    half = window // 2
    for i in range(n):
        start = max(0, i - half)
        end = min(n, i + half + 1)
        smoothed.append(sum(elevations[start:end]) / (end - start))
    return smoothed


def build_course_profile(
    points: list[GpxPoint],
    segment_distance_m: float = 1000,
    name: str = "Course",
) -> CourseProfile:
    """Split a GPX track into segments and compute elevation stats."""
    if not points:
        raise GpxError("Aucun point GPS.")

    smoothed = _smooth_elevations(points)
    total_distance = points[-1].distance_from_start

    # Build segments
    segments: list[CourseSegment] = []
    seg_start_idx = 0
    seg_start_dist = 0.0
    seg_num = 0
    num_segments = max(1, int(total_distance / segment_distance_m))

    for seg_num in range(num_segments):
        seg_end_dist = min((seg_num + 1) * segment_distance_m, total_distance)
        if seg_num == num_segments - 1:
            seg_end_dist = total_distance

        # Find points in this segment
        seg_points_idx = []
        for j in range(seg_start_idx, len(points)):
            if points[j].distance_from_start >= seg_start_dist:
                seg_points_idx.append(j)
            if points[j].distance_from_start >= seg_end_dist:
                break

        if not seg_points_idx:
            continue

        # Gain/loss from RAW elevations with a hysteresis threshold (matches
        # Strava/COROS far better than averaging the noise away). min/max and
        # gradient still use the smoothed series to avoid spiky display values.
        elevs = [smoothed[j] for j in seg_points_idx]
        raw_elevs = [points[j].elevation for j in seg_points_idx]
        gain, loss = _gain_loss_hysteresis(raw_elevs)

        distance_m = seg_end_dist - seg_start_dist
        if distance_m < 1:
            continue

        start_elev = smoothed[seg_points_idx[0]]
        end_elev = smoothed[seg_points_idx[-1]]
        avg_gradient = (end_elev - start_elev) / distance_m * 100

        segments.append(
            CourseSegment(
                index=len(segments),
                start_km=round(seg_start_dist / 1000, 2),
                end_km=round(seg_end_dist / 1000, 2),
                distance_m=round(distance_m, 1),
                elevation_gain=round(gain, 1),
                elevation_loss=round(loss, 1),
                avg_gradient_pct=round(avg_gradient, 1),
                min_elevation=round(min(elevs), 1),
                max_elevation=round(max(elevs), 1),
            )
        )

        seg_start_dist = seg_end_dist
        seg_start_idx = seg_points_idx[-1] if seg_points_idx else seg_start_idx

    # Sample elevation profile for chart
    elevation_points = _sample_elevation_profile(points, smoothed, max_points=400)

    # Full-resolution polyline for map + accurate GPX re-export.
    route_coords = _sample_route_coords(points)

    # Km markers for map (position at each km)
    km_markers = _build_km_markers(points, smoothed, segment_distance_m)

    total_gain = sum(s.elevation_gain for s in segments)
    total_loss = sum(s.elevation_loss for s in segments)
    dplus_effort, _ = profile_elevation_gain([p.distance_from_start for p in points], [p.elevation for p in points])

    return CourseProfile(
        name=name,
        total_distance_km=round(total_distance / 1000, 2),
        total_elevation_gain=round(total_gain, 0),
        total_elevation_loss=round(total_loss, 0),
        segments=segments,
        elevation_points=elevation_points,
        route_coords=route_coords,
        km_markers=km_markers,
        dplus_effort=round(dplus_effort, 0),
    )


def _sample_elevation_profile(
    points: list[GpxPoint],
    smoothed: list[float],
    max_points: int = 400,
) -> list[dict]:
    """Downsample elevation data for chart rendering."""
    n = len(points)
    if n <= max_points:
        step = 1
    else:
        step = n // max_points

    result = []
    for i in range(0, n, step):
        result.append({
            "distance_km": round(points[i].distance_from_start / 1000, 3),
            "elevation": round(smoothed[i], 1),
        })

    # Always include last point
    if result and result[-1]["distance_km"] != round(points[-1].distance_from_start / 1000, 3):
        result.append({
            "distance_km": round(points[-1].distance_from_start / 1000, 3),
            "elevation": round(smoothed[-1], 1),
        })

    return result


def _sample_route_coords(
    points: list[GpxPoint],
    max_points: int = 20000,
) -> list[list[float]]:
    """GPS coordinates for map rendering AND for GPX re-export.

    Returns [[lat, lon, distance_km, elevation], ...]. Kept at (near) full
    resolution: this polyline is what the GPX export rebuilds the track from, so
    decimating it here shortens the exported distance (cutting switchbacks). The
    map decimates client-side for display; only pathological tracks (> max_points)
    are thinned, and even then with a small step to preserve length.
    """
    n = len(points)
    step = max(1, n // max_points)

    coords = []
    for i in range(0, n, step):
        coords.append([
            round(points[i].lat, 6),
            round(points[i].lon, 6),
            round(points[i].distance_from_start / 1000, 3),
            round(points[i].elevation, 1),
        ])

    # Always include last point
    last = [
        round(points[-1].lat, 6),
        round(points[-1].lon, 6),
        round(points[-1].distance_from_start / 1000, 3),
        round(points[-1].elevation, 1),
    ]
    if coords and coords[-1] != last:
        coords.append(last)

    return coords


def _build_km_markers(
    points: list[GpxPoint],
    smoothed: list[float],
    segment_distance_m: float,
) -> list[dict]:
    """Build km marker positions for map. Returns [{km, lat, lon, elevation}, ...]."""
    markers = []
    next_km = segment_distance_m

    for i, pt in enumerate(points):
        if pt.distance_from_start >= next_km:
            km_num = int(next_km / 1000)
            markers.append({
                "km": km_num,
                "lat": round(pt.lat, 6),
                "lon": round(pt.lon, 6),
                "elevation": round(smoothed[i], 0),
            })
            next_km += segment_distance_m

    return markers
