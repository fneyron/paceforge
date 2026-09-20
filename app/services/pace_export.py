"""Pace-strategy export for the watch: target passage times per waypoint.

A plain GPX route tells the watch where to go; it says nothing about *when*.
Garmin (Virtual Partner / course) and COROS (route with waypoints) both read
timestamps on track points, and every waypoint name is shown on the watch as
you approach it — so the target clock time is written INTO the name. Three
formats:

- GPX with ``<time>`` on every track point + timed waypoints (COROS app,
  Garmin Connect course import);
- TCX course with ``<Time>`` track points and CoursePoints (Garmin Connect,
  Virtual Partner follows the timed track);
- CSV (km, point, target clock, elapsed, section pace) for COROS Training Hub
  pace strategy / a spreadsheet.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape


def _fmt_clock(seconds: int) -> str:
    rem = int(seconds) % 86400
    return f"{rem // 3600:02d}:{(rem % 3600) // 60:02d}"


def _fmt_elapsed(seconds: int) -> str:
    s = int(seconds)
    return f"{s // 3600}h{(s % 3600) // 60:02d}"


def time_curve(course_segments: list[dict], sections: list[dict], use_target: bool) -> list[tuple[float, float]]:
    """(km, elapsed_s) breakpoints — one per course segment boundary, with the
    aid-station stop expressed as a jump at the checkpoint km.

    Inside a section, time is spread over the km segments proportionally to
    their terrain difficulty (base_time_s), so a climb inside a long section
    gets its share of the section's time rather than a flat average.
    """
    pts: list[tuple[float, float]] = [(0.0, 0.0)]
    if not sections:
        return pts
    elapsed_at_section_start = 0.0
    for s in sections:
        start_km, end_km = float(s["start_km"]), float(s["end_km"])
        sec_time = float(s["adjusted_time_s"] if (use_target and s.get("adjusted_time_s") is not None) else s["predicted_time_s"])
        clock_end = int(s["adjusted_clock_time_s"] if (use_target and s.get("adjusted_clock_time_s") is not None) else s["clock_time_s"])
        segs = [g for g in course_segments if g["end_km"] > start_km + 1e-6 and g["start_km"] < end_km - 1e-6]
        weights = []
        for g in segs:
            o0, o1 = max(g["start_km"], start_km), min(g["end_km"], end_km)
            length = g["end_km"] - g["start_km"]
            frac = (o1 - o0) / length if length > 0 else 0
            weights.append(((o0, o1), (g.get("base_time_s") or g.get("predicted_time_s") or length) * frac))
        total_w = sum(w for _, w in weights) or 1.0
        t = elapsed_at_section_start
        for (o0, o1), w in weights:
            t += sec_time * (w / total_w)
            pts.append((o1, t))
        # ensure the section end is exactly at its planned elapsed (rounding)
        planned_end = elapsed_at_section_start + sec_time
        if pts[-1][0] < end_km - 1e-6:
            pts.append((end_km, planned_end))
        else:
            pts[-1] = (end_km, planned_end)
        # aid stop → time jumps at the same km (clock times carry the stops)
        elapsed_at_section_start = planned_end
        stop_here = _stop_at(s, sections, use_target)
        if stop_here > 0:
            elapsed_at_section_start += stop_here
            pts.append((end_km, elapsed_at_section_start))
    return pts


def _stop_at(s: dict, sections: list[dict], use_target: bool) -> int:
    """Stop duration at the end of section ``s`` = next section's clock start
    minus this section's clock end (clock times carry the stops)."""
    i = sections.index(s)
    if i + 1 >= len(sections):
        return 0
    nxt = sections[i + 1]
    key_c = "adjusted_clock_time_s" if (use_target and s.get("adjusted_clock_time_s") is not None) else "clock_time_s"
    key_t = "adjusted_time_s" if (use_target and s.get("adjusted_time_s") is not None) else "predicted_time_s"
    # next.clock_end - next.time = clock at which the next section starts
    next_start_clock = int(nxt[key_c]) - int(round(float(nxt[key_t])))
    return max(0, next_start_clock - int(s[key_c]))


def elapsed_at(curve: list[tuple[float, float]], km: float) -> float:
    if km <= curve[0][0]:
        return curve[0][1]
    for (k0, t0), (k1, t1) in zip(curve, curve[1:], strict=False):
        if k1 >= km:
            span = k1 - k0
            if span <= 1e-9:
                return t0  # a stop: take the time BEFORE the jump for points at that km
            return t0 + (km - k0) / span * (t1 - t0)
    return curve[-1][1]


def _race_start(race_date: str | None, start_offset_s: int) -> datetime:
    try:
        d = datetime.strptime(race_date, "%Y-%m-%d") if race_date else datetime.now(timezone.utc)
    except ValueError:
        d = datetime.now(timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(seconds=int(start_offset_s))


def waypoint_rows(sections: list[dict], use_target: bool, start_offset_s: int) -> list[dict]:
    rows = []
    for i, s in enumerate(sections, start=1):
        clock = int(s["adjusted_clock_time_s"] if (use_target and s.get("adjusted_clock_time_s") is not None) else s["clock_time_s"])
        sec_time = float(s["adjusted_time_s"] if (use_target and s.get("adjusted_time_s") is not None) else s["predicted_time_s"])
        dist = float(s.get("distance_km") or 0)
        pace = sec_time / dist if dist > 0 else 0
        is_finish = i == len(sections)
        rows.append({
            "index": i, "name": "Arrivée" if is_finish else s["end_name"], "km": float(s["end_km"]),
            "elevation": s.get("end_elevation"), "clock_s": clock, "elapsed_s": clock - int(start_offset_s),
            "clock": _fmt_clock(clock), "elapsed": _fmt_elapsed(clock - int(start_offset_s)),
            "pace_s_per_km": int(round(pace)), "kind": s.get("kind"), "cutoff_clock": s.get("cutoff_clock"),
            "is_finish": is_finish,
        })
    return rows


def _coord_at_km(coords: list, km: float):
    lo, hi = 0, len(coords) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if coords[mid][2] <= km:
            lo = mid
        else:
            hi = mid
    return coords[lo] if abs(coords[lo][2] - km) <= abs(coords[hi][2] - km) else coords[hi]


def pacing_points(blocks: list[dict]) -> list[dict]:
    """One course point per pacing block, named with the instruction the watch
    should show when you reach it (short: watches truncate names).

    MONT = climb (HR ceiling · VAM), ESCAL = stairs (walk), DESC = descent
    (release HR), PLAT = flat (HR · pace). "libre" = race.
    """
    pts = []
    for b in blocks:
        hr = f"FC{b['hr_cap']}" if b.get("hr_cap") else ("libre" if b.get("hr_free") else "")
        if b["cls"] == "stairs":
            label = f"ESCAL marche {hr}".strip()
        elif b["cls"] == "climb":
            vam = f" {b['vam_m_per_h']}m/h" if b.get("vam_m_per_h") else ""
            label = f"MONT {hr}{vam}".strip()
        elif b["cls"] == "descent":
            label = f"DESC {hr} relache".strip()
        else:
            pace = b.get("pace_s_per_km") or 0
            label = f"PLAT {hr} {pace // 60}:{pace % 60:02d}".strip()
        pts.append({"km": float(b["start_km"]), "name": label, "desc": f"km {b['start_km']}–{b['end_km']} · {b['label']} · {b['instruction']}"})
    return pts


def build_pace_gpx(name: str, coords: list, course_segments: list[dict], sections: list[dict],
                   use_target: bool, race_date: str | None, start_offset_s: int,
                   extra_points: list[dict] | None = None) -> str:
    start = _race_start(race_date, start_offset_s)
    curve = time_curve(course_segments, sections, use_target)
    rows = waypoint_rows(sections, use_target, start_offset_s)
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write('<gpx version="1.1" creator="PaceForge" xmlns="http://www.topografix.com/GPX/1/1">\n')
    out.write(f"  <metadata><name>{escape(name)} — plan</name><time>{start.strftime('%Y-%m-%dT%H:%M:%SZ')}</time></metadata>\n")
    for pt in extra_points or []:
        c = _coord_at_km(coords, pt["km"])
        t = start + timedelta(seconds=elapsed_at(curve, pt["km"]))
        out.write(f'  <wpt lat="{c[0]:.6f}" lon="{c[1]:.6f}"><time>{t.strftime("%Y-%m-%dT%H:%M:%SZ")}</time><name>{escape(pt["name"])}</name><desc>{escape(pt.get("desc", ""))}</desc><sym>Flag</sym></wpt>\n')
    for r in rows:
        c = _coord_at_km(coords, r["km"])
        label = f"{'ARR' if r['is_finish'] else 'CP' + str(r['index'])} {r['clock']} · {r['name']}"
        t = start + timedelta(seconds=r["elapsed_s"])
        out.write(f'  <wpt lat="{c[0]:.6f}" lon="{c[1]:.6f}">')
        if r["elevation"] is not None:
            out.write(f"<ele>{float(r['elevation']):.1f}</ele>")
        out.write(f"<time>{t.strftime('%Y-%m-%dT%H:%M:%SZ')}</time><name>{escape(label)}</name>")
        out.write(f"<desc>{escape('km ' + str(r['km']) + ' · ' + r['elapsed'] + ' de course')}</desc></wpt>\n")
    out.write(f"  <trk><name>{escape(name)}</name><trkseg>\n")
    for c in coords:
        t = start + timedelta(seconds=elapsed_at(curve, float(c[2])))
        ele = f"<ele>{float(c[3]):.1f}</ele>" if len(c) > 3 and c[3] is not None else ""
        out.write(f'    <trkpt lat="{c[0]:.6f}" lon="{c[1]:.6f}">{ele}<time>{t.strftime("%Y-%m-%dT%H:%M:%SZ")}</time></trkpt>\n')
    out.write("  </trkseg></trk>\n</gpx>\n")
    return out.getvalue()


def build_pace_tcx(name: str, coords: list, course_segments: list[dict], sections: list[dict],
                   use_target: bool, race_date: str | None, start_offset_s: int,
                   extra_points: list[dict] | None = None) -> str:
    start = _race_start(race_date, start_offset_s)
    curve = time_curve(course_segments, sections, use_target)
    rows = waypoint_rows(sections, use_target, start_offset_s)
    total_s = int(curve[-1][1]) if curve else 0
    total_m = float(coords[-1][2]) * 1000 if coords else 0
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write('<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">\n')
    out.write("  <Courses><Course>\n")
    out.write(f"    <Name>{escape(name[:15])}</Name>\n")
    out.write(f"    <Lap><TotalTimeSeconds>{total_s}</TotalTimeSeconds><DistanceMeters>{total_m:.1f}</DistanceMeters>")
    out.write(f'<BeginPosition><LatitudeDegrees>{coords[0][0]:.6f}</LatitudeDegrees><LongitudeDegrees>{coords[0][1]:.6f}</LongitudeDegrees></BeginPosition>')
    out.write(f'<EndPosition><LatitudeDegrees>{coords[-1][0]:.6f}</LatitudeDegrees><LongitudeDegrees>{coords[-1][1]:.6f}</LongitudeDegrees></EndPosition>')
    out.write("<Intensity>Active</Intensity></Lap>\n")
    out.write("    <Track>\n")
    for c in coords:
        t = start + timedelta(seconds=elapsed_at(curve, float(c[2])))
        out.write("      <Trackpoint>")
        out.write(f"<Time>{t.strftime('%Y-%m-%dT%H:%M:%SZ')}</Time>")
        out.write(f"<Position><LatitudeDegrees>{c[0]:.6f}</LatitudeDegrees><LongitudeDegrees>{c[1]:.6f}</LongitudeDegrees></Position>")
        if len(c) > 3 and c[3] is not None:
            out.write(f"<AltitudeMeters>{float(c[3]):.1f}</AltitudeMeters>")
        out.write(f"<DistanceMeters>{float(c[2]) * 1000:.1f}</DistanceMeters></Trackpoint>\n")
    out.write("    </Track>\n")
    for pt in extra_points or []:
        c = _coord_at_km(coords, pt["km"])
        t = start + timedelta(seconds=elapsed_at(curve, pt["km"]))
        out.write("    <CoursePoint>")
        out.write(f"<Name>{escape(pt['name'][:15])}</Name><Time>{t.strftime('%Y-%m-%dT%H:%M:%SZ')}</Time>")
        out.write(f"<Position><LatitudeDegrees>{c[0]:.6f}</LatitudeDegrees><LongitudeDegrees>{c[1]:.6f}</LongitudeDegrees></Position>")
        out.write(f"<PointType>Generic</PointType><Notes>{escape(pt.get('desc', '')[:250])}</Notes></CoursePoint>\n")
    for r in rows:
        c = _coord_at_km(coords, r["km"])
        t = start + timedelta(seconds=r["elapsed_s"])
        label = f"{r['clock']} {r['name']}"[:10]
        out.write("    <CoursePoint>")
        out.write(f"<Name>{escape(label)}</Name><Time>{t.strftime('%Y-%m-%dT%H:%M:%SZ')}</Time>")
        out.write(f"<Position><LatitudeDegrees>{c[0]:.6f}</LatitudeDegrees><LongitudeDegrees>{c[1]:.6f}</LongitudeDegrees></Position>")
        kind = "Food" if r.get("kind") in ("full", "base") else ("Water" if r.get("kind") == "water" else "Generic")
        out.write(f"<PointType>{kind}</PointType>")
        out.write(f"<Notes>{escape('km ' + str(r['km']) + ' · ' + r['clock'] + ' · ' + r['name'])}</Notes></CoursePoint>\n")
    out.write("  </Course></Courses>\n</TrainingCenterDatabase>\n")
    return out.getvalue()


def build_pace_csv(name: str, sections: list[dict], use_target: bool, start_offset_s: int) -> str:
    rows = waypoint_rows(sections, use_target, start_offset_s)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["point", "km", "altitude_m", "heure_cible", "temps_course", "allure_section_min_km", "type", "barriere"])
    w.writerow(["Départ", 0, "", _fmt_clock(start_offset_s), "0h00", "", "", ""])
    for r in rows:
        pace = f"{r['pace_s_per_km'] // 60}:{r['pace_s_per_km'] % 60:02d}" if r["pace_s_per_km"] else ""
        w.writerow([r["name"], r["km"], int(r["elevation"]) if r["elevation"] is not None else "", r["clock"], r["elapsed"], pace, r.get("kind") or "", r.get("cutoff_clock") or ""])
    return out.getvalue()
