"""A long course for the Nutrition tests and the screenshots: 145 km, ten
points where the bag is refilled (two bases vie with a drop bag, a crew point)
and one water point, a 06:00 start."""

from app.schemas.simulator import CourseProfile, CourseSegment
from app.services import checkpoints as cpsvc
from app.services.race_simulator import compute_passage_times

# grade per km block: (km count, grade %)
_PROFILE = [(8, 3), (6, 9), (9, 6), (5, -8), (5, 2), (6, -6), (6, 4), (7, 10), (5, -12), (7, -4), (6, 1), (8, 5),
            (6, -5), (7, 3), (6, -3), (8, 2), (6, 6), (7, -6), (6, 1), (5, 4), (6, -7), (10, -1)]

LONG_CPS = [
    {"name": "Healing Forest", "distance_km": 12.0, "kind": "full"},
    {"name": "Yeongsil", "distance_km": 23.0, "kind": "full"},
    {"name": "Eorimok", "distance_km": 33.0, "kind": "full"},
    {"name": "Gwanumsa", "distance_km": 45.0, "kind": "full"},
    {"name": "Jeongseok", "distance_km": 52.0, "kind": "water"},
    {"name": "Seongpanak", "distance_km": 64.0, "kind": "full"},
    {"name": "Saryeoni", "distance_km": 75.0, "kind": "full"},
    {"name": "Gasiri", "distance_km": 88.0, "kind": "base", "drop_bag": True},
    {"name": "Meochewat", "distance_km": 104.0, "kind": "full", "crew": True},
    {"name": "Camping", "distance_km": 118.0, "kind": "base", "drop_bag": True},
    {"name": "Sumeunmul", "distance_km": 130.0, "kind": "full"},
]
LONG_CPS = [{"elevation": None, "crew": False, "drop_bag": False, "cutoff_clock": None, **cp} for cp in LONG_CPS]


def long_course() -> CourseProfile:
    grades = [g for n, g in _PROFILE for _ in range(n)]
    segs, elev, pts, coords = [], 300.0, [], []
    for i, g in enumerate(grades):
        start_e = elev
        elev = max(50.0, elev + g * 10)
        segs.append(CourseSegment(
            index=i, start_km=float(i), end_km=float(i + 1), distance_m=1000.0,
            elevation_gain=max(0.0, elev - start_e), elevation_loss=max(0.0, start_e - elev),
            avg_gradient_pct=float(g), min_elevation=min(start_e, elev), max_elevation=max(start_e, elev),
        ))
        for k in range(2):
            km = i + k / 2
            e = start_e + (elev - start_e) * k / 2
            pts.append({"distance_km": km, "elevation": e})
            coords.append([33.3 + km * 0.002, 126.5 + km * 0.001, km, e])
    total = float(len(grades))
    pts.append({"distance_km": total, "elevation": elev})
    coords.append([33.3 + total * 0.002, 126.5 + total * 0.001, total, elev])
    course = CourseProfile(
        name="Transjeju test", total_distance_km=total,
        total_elevation_gain=sum(s.elevation_gain for s in segs), total_elevation_loss=sum(s.elevation_loss for s in segs),
        segments=segs, elevation_points=pts, route_coords=coords,
    )
    cum = 0.0
    for s in course.segments:
        factor = 1 + max(0.0, s.avg_gradient_pct) * 0.12 - max(0.0, -s.avg_gradient_pct) * 0.03
        s.base_time_s = 380 * factor
        s.predicted_time_s = s.base_time_s
        s.predicted_pace_s_per_km = s.predicted_time_s
        cum += s.predicted_time_s
        s.cumulative_time_s = cum
    course.predicted_total_time_s = int(cum)
    return course


def long_sections(target_s: int | None = int(16.5 * 3600), start_hour: int = 6, cps: list[dict] | None = None):
    cps = LONG_CPS if cps is None else cps
    course = long_course()
    aid = {cp["distance_km"] for cp in cps if cp.get("kind", "none") != "none"}
    stops = {cp["distance_km"]: {"water": 120, "full": 300, "base": 900}[cp["kind"]] for cp in cps if cp.get("kind", "none") != "none"}
    secs = compute_passage_times(course, cps, target_s, 1.0, start_hour, 0, None, aid_kms=aid, aid_stops=stops)
    return course, cpsvc.annotate_cutoffs(secs, cps, start_hour * 3600)
