"""The 145,6 km GPX is the 2025 course (same as 2026 up to the Camping site): checkpoints keep the roadbook km."""
import importlib.util
import math
import pathlib

MIG = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "n8c9d0e1f2a3_transjeju_checkpoints_unscaled.py"

# 2026 roadbook up to the Camping site, then the 2025 finish: Healing Forest 2 (+324/−394) and 8,5 km to the line
GRID = [(0.0, 80, 0, 0), (7.1, 419, 341, 3), (20.2, 1211, 1176, 45), (28.6, 953, 1656, 783), (40.8, 572, 1814, 1323),
        (58.5, 762, 3171, 2489), (74.3, 351, 3216, 2946), (86.9, 273, 3494, 3300), (106.4, 239, 3797, 3636),
        (115.8, 413, 4066, 3733), (127.6, 489, 4449, 4040), (137.1, 419, 4773, 4434), (145.6, 78, 4983, 4983)]


def _load():
    spec = importlib.util.spec_from_file_location("mig_tj_unscaled", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gps(km):  # a watch/GPX measure: ±0,4 km of drift along the way, same end
    return km + 0.4 * math.sin(2 * math.pi * km / 145.6)


def _trace():
    pts = []
    for i in range(len(GRID) - 1):
        (k0, a0, g0, l0), (k1, _, g1, l1) = GRID[i], GRID[i + 1]
        up, down = g1 - g0, l1 - l0
        n = max(2, int((k1 - k0) * 10))
        split = up / (up + down) if up + down else 0.5
        for j in range(n):
            f = j / n
            e = a0 + up * (f / split) if f < split else a0 + up - down * ((f - split) / (1 - split))
            pts.append((_gps(k0 + (k1 - k0) * f), e))
    pts.append((_gps(145.6), 78.0))
    return pts


def test_the_2025_trace_keeps_the_roadbook_km_up_to_the_camping_site():
    mig = _load()
    pts = _trace()
    out = mig.align_on_profile(pts, pts[-1][0])
    assert [c["name"] for c in out][5] == "Jeongseok Aviation Pavilion" and len(out) == 11
    for (name, km, *_), cp in zip(mig.OFFICIAL, out):
        assert abs(cp["distance_km"] - _gps(km)) <= 0.4, (name, cp["distance_km"], round(_gps(km), 1))
    camping = out[9]["distance_km"]
    assert camping > 126.5  # the previous scaling put it at 125,0
