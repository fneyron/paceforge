"""GPX file parsing and course profile building."""

import logging
import math

import gpxpy

from app.schemas.simulator import CourseProfile, CourseSegment, GpxPoint

logger = logging.getLogger(__name__)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two GPS points."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_gpx(file_content: bytes) -> tuple[list[GpxPoint], list[dict]]:
    """Parse a GPX file. Returns (track_points, waypoints).

    Waypoints are [{name, lat, lon, elevation}, ...] from <wpt> elements.
    """
    gpx = gpxpy.parse(file_content.decode("utf-8"))

    points: list[GpxPoint] = []
    cumulative_distance = 0.0

    for track in gpx.tracks:
        for segment in track.segments:
            for i, point in enumerate(segment.points):
                if i > 0 or points:
                    prev = points[-1] if points else None
                    if prev:
                        d = _haversine(prev.lat, prev.lon, point.latitude, point.longitude)
                        cumulative_distance += d

                points.append(
                    GpxPoint(
                        lat=point.latitude,
                        lon=point.longitude,
                        elevation=point.elevation or 0.0,
                        distance_from_start=cumulative_distance,
                    )
                )

    if not points:
        raise ValueError("Le fichier GPX ne contient aucun point de trace.")

    # Parse waypoints (<wpt> elements). Only keep genuinely named waypoints —
    # official race GPX files carry dozens of unnamed/auto-numbered points that
    # would otherwise flood the checkpoint list.
    waypoints = []
    for wpt in gpx.waypoints:
        name = (wpt.name or "").strip()
        if not name:
            continue
        waypoints.append({
            "name": name,
            "lat": wpt.latitude,
            "lon": wpt.longitude,
            "elevation": wpt.elevation or 0.0,
        })

    return points, waypoints


def snap_waypoints_to_route(
    waypoints: list[dict], points: list[GpxPoint]
) -> list[dict]:
    """Snap waypoints to the nearest point on the route and compute distance_km."""
    result = []
    for wpt in waypoints:
        best_dist = float("inf")
        best_km = 0.0
        best_elev = 0.0
        for pt in points:
            d = _haversine(wpt["lat"], wpt["lon"], pt.lat, pt.lon)
            if d < best_dist:
                best_dist = d
                best_km = pt.distance_from_start / 1000
                best_elev = pt.elevation
        # Only include if reasonably close to the route (< 500m)
        if best_dist < 500:
            result.append({
                "name": wpt["name"],
                "distance_km": round(best_km, 1),
                "elevation": round(best_elev, 0),
            })
    result.sort(key=lambda w: w["distance_km"])
    return result


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
        raise ValueError("Aucun point GPS.")

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
