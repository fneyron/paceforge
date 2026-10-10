"""Validate browser-supplied plan data before touching a saved route."""
import json
import math

from app.schemas.simulator import CourseProfile
from app.services.checkpoints import normalize_checkpoint


def read_json(raw: str):
    if len(raw) > 8_000_000:
        raise ValueError("Données trop volumineuses")

    def invalid(value):
        raise ValueError("Valeur numérique invalide")

    data = json.loads(raw, parse_constant=invalid)

    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Valeur numérique invalide")
        if isinstance(value, dict):
            for v in value.values():
                finite(v)
        if isinstance(value, list):
            for v in value:
                finite(v)

    finite(data)
    return data


def course_data(raw: str):
    data = read_json(raw)
    course = CourseProfile.model_validate(data)
    if not 0 < course.total_distance_km <= 2000 or not course.segments:
        raise ValueError("Distance ou segments invalides")
    if min(course.total_elevation_gain, course.total_elevation_loss) < 0:
        raise ValueError("Dénivelé invalide")
    for segment in course.segments:
        if not 0 <= segment.start_km < segment.end_km <= course.total_distance_km + 0.1:
            raise ValueError("Segments invalides")
        if segment.distance_m <= 0:
            raise ValueError("Segment vide")
    for point in course.elevation_points:
        if not isinstance(point, dict) or not all(isinstance(point.get(k), (int, float))
                                                for k in ("distance_km", "elevation")):
            raise ValueError("Profil d’altitude invalide")
    for point in course.route_coords:
        if len(point) not in (3, 4) or not -90 <= point[0] <= 90 or not -180 <= point[1] <= 180:
            raise ValueError("Coordonnées invalides")
    data.update(course.model_dump())
    return data  # keep optional fields used by the other sports


def checkpoints_data(raw: str):
    data = read_json(raw)
    if not isinstance(data, list) or len(data) > 500:
        raise ValueError("Liste de passages invalide")
    out = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Passage invalide")
        for key in ("distance_km", "elevation", "stop_s", "target_s"):
            value = item.get(key)
            if (value not in (None, "")
                    and (isinstance(value, bool) or not math.isfinite(float(value)))):
                raise ValueError("Valeur de passage invalide")
        cp = normalize_checkpoint(item)
        if cp["distance_km"] < 0:
            raise ValueError("Distance de passage invalide")
        out.append(cp)
    return out
