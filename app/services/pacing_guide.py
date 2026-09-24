"""Terrain-typed pacing instructions.

From the per-km gradient of the course, the race is cut into blocks (climb,
stairs, descent, flat) and each block gets ONE instruction the athlete can act
on with the dials on the watch: a heart-rate ceiling on climbs with the VAM the
plan needs, "walk, hands on thighs" when it is steeper than a staircase, a
release heart rate on descents, and an effort pace on the flat. Steep stretches
are flagged explicitly — nobody spots a 25 % km on a 148 km profile by eye.

Everything is derived from the course, the plan and a few numbers the athlete
sets (heart-rate ceilings, walk threshold). No model, no LLM.
"""

from __future__ import annotations

from app.schemas.simulator import CourseProfile

CLIMB_GRADE = 5.0        # % — above this a km is a climb
DESCENT_GRADE = -5.0     # % — below this a km is a descent
DEFAULT_WALK_GRADE = 18  # % — "stairs": walk, hands on thighs
MIN_BLOCK_KM = 2.0       # shorter blocks are merged into a neighbour (stairs are kept)

# Progressive ceilings as the race goes on: hold back early, let go late.
# (fraction of distance, offset in bpm from the climb ceiling, label)
_PHASES = [
    (0.30, -5, "départ : jamais plus, même dans l'euphorie"),
    (0.70, 0, "régularité — les places se perdent ici, elles ne s'y prennent pas"),
    (0.85, +4, "on peut commencer à remonter"),
    (1.01, None, "libre — course"),
]


def default_hr_caps(max_hr: int | None) -> dict:
    """Defaults from the athlete's max HR (≈ LT1 − 5 on climbs). The athlete
    overrides them — these are starting points, not prescriptions."""
    if not max_hr or max_hr < 120:
        return {"hr_cap_climb": None, "hr_cap_flat": None, "hr_release_descent": None}
    climb = int(round(max_hr * 0.75))
    return {"hr_cap_climb": climb, "hr_cap_flat": climb - 4, "hr_release_descent": climb - 10}


def _phase(progress: float) -> tuple[int | None, str]:
    for limit, offset, label in _PHASES:
        if progress <= limit:
            return offset, label
    return None, "libre"


def _classify(grade: float, walk_grade: float) -> str:
    if grade >= walk_grade:
        return "stairs"
    if grade >= CLIMB_GRADE:
        return "climb"
    if grade <= DESCENT_GRADE:
        return "descent"
    return "flat"


def _local_max_grade(course: CourseProfile, start_km: float, end_km: float, descent: bool = False) -> float:
    """Steepest stretch inside [start_km, end_km] from the elevation profile
    (finer than the 1 km segments when the profile allows it)."""
    pts = course.elevation_points or []
    best = None
    for a, b in zip(pts, pts[1:], strict=False):
        if b["distance_km"] <= start_km or a["distance_km"] >= end_km:
            continue
        run = (b["distance_km"] - a["distance_km"]) * 1000
        if run < 80:  # too short to mean anything (GPS noise)
            continue
        g = (b["elevation"] - a["elevation"]) / run * 100
        if best is None or (g < best if descent else g > best):
            best = g
    return round(best, 1) if best is not None else 0.0


def build_pacing_guide(
    course: CourseProfile,
    target_time_s: int | None,
    hr_cap_climb: int | None = None,
    hr_cap_flat: int | None = None,
    hr_release_descent: int | None = None,
    walk_grade: float = DEFAULT_WALK_GRADE,
) -> dict:
    """Return {"blocks": [...], "alerts": [...], "caps": {...}, "plan_factor": f}.

    Planned times per block are the even-effort plan (segment base_time share
    of the target) when a target exists, else the model prediction — so the VAM
    and paces shown are the ones that hit the objective.
    """
    segs = course.segments
    total_km = course.total_distance_km or 1.0
    if not segs:
        return {"blocks": [], "alerts": [], "caps": {}, "plan_factor": 1.0}

    total_base = sum((s.base_time_s or s.predicted_time_s) for s in segs) or 1.0
    if target_time_s:
        def seg_time(s):
            return target_time_s * ((s.base_time_s or s.predicted_time_s) / total_base)
    else:
        def seg_time(s):
            return s.predicted_time_s

    # 1. classify each km
    rows = [{
        "seg": s,
        "cls": _classify(s.avg_gradient_pct, walk_grade),
        "time_s": seg_time(s),
    } for s in segs]

    # 2. merge consecutive kms of the same class
    blocks: list[dict] = []
    for r in rows:
        if blocks and blocks[-1]["cls"] == r["cls"]:
            blocks[-1]["rows"].append(r)
        else:
            blocks.append({"cls": r["cls"], "rows": [r]})

    # 3. absorb short blocks (except stairs) into the longer neighbour
    def _km(b):
        return sum(x["seg"].distance_m for x in b["rows"]) / 1000

    changed = True
    while changed and len(blocks) > 1:
        changed = False
        for i, b in enumerate(blocks):
            if b["cls"] == "stairs" or _km(b) >= MIN_BLOCK_KM:
                continue
            left = blocks[i - 1] if i > 0 else None
            right = blocks[i + 1] if i + 1 < len(blocks) else None
            target = None
            if left and right:
                target = left if _km(left) >= _km(right) else right
            else:
                target = left or right
            if target is None:
                continue
            if target is left:
                left["rows"].extend(b["rows"])
            else:
                right["rows"] = b["rows"] + right["rows"]
            del blocks[i]
            changed = True
            break
    # re-merge neighbours that became the same class
    merged: list[dict] = []
    for b in blocks:
        if merged and merged[-1]["cls"] == b["cls"]:
            merged[-1]["rows"].extend(b["rows"])
        else:
            merged.append(b)
    blocks = merged

    # 4. one instruction per block
    out_blocks = []
    alerts = []
    for b in blocks:
        rs = b["rows"]
        start_km = rs[0]["seg"].start_km
        end_km = rs[-1]["seg"].end_km
        dist_km = (end_km - start_km) or 0.001
        gain = sum(r["seg"].elevation_gain for r in rs)
        loss = sum(r["seg"].elevation_loss for r in rs)
        time_s = sum(r["time_s"] for r in rs)
        hours = time_s / 3600 if time_s > 0 else 0
        avg_grade = round(sum(r["seg"].avg_gradient_pct * r["seg"].distance_m for r in rs) / (dist_km * 1000), 1)
        max_grade = (min if b["cls"] == "descent" else max)(_local_max_grade(course, r["seg"].start_km, r["seg"].end_km, descent=(b["cls"] == "descent")) for r in rs)
        progress = (start_km + end_km) / 2 / total_km
        offset, phase_label = _phase(progress)
        cls = b["cls"]

        hr_cap = None
        vam = None
        pace_s = time_s / dist_km if dist_km > 0 else 0
        if cls in ("climb", "stairs"):
            hr_cap = (hr_cap_climb + offset) if (hr_cap_climb and offset is not None) else None
            vam = int(round(gain / hours)) if hours > 0 and gain > 0 else None
        elif cls == "descent":
            hr_cap = hr_release_descent if (hr_release_descent and offset is not None) else None
        else:
            hr_cap = (hr_cap_flat + offset) if (hr_cap_flat and offset is not None) else None

        # one instruction a first-timer can act on, with the numbers of this stretch inside
        pace_txt = f"{int(pace_s) // 60}:{int(pace_s) % 60:02d}/km" if pace_s else None
        if cls == "stairs":
            instr = "Trop raide pour courir : marche, mains sur les cuisses, bâtons si tu en as. Un rythme régulier, sans forcer."
        elif cls == "climb":
            instr = (f"Monte au cardio{f', sous {hr_cap} battements' if hr_cap else ''} : marche dès que ça dépasse {int(round(walk_grade))} %, cours le reste."
                     + (f" Ça fait environ {vam} m de montée par heure." if vam else ""))
        elif cls == "descent":
            instr = (f"Descends relâché, sans freiner{f', et laisse le cardio redescendre vers {hr_cap}' if hr_cap else ''}."
                     " Mange en haut, avant de descendre.")
        else:
            instr = (f"Cours régulier, environ {pace_txt}" if pace_txt else "Cours régulier") + (f", cardio sous {hr_cap}." if hr_cap else ".") + " Profites-en pour manger et boire."
        if offset is None:
            instr = "Dernière partie, plus de plafond : donne ce qui reste. " + instr

        blk = {
            "cls": cls,
            "label": {"climb": "Montée", "stairs": "Escaliers", "descent": "Descente", "flat": "Roulant"}[cls],
            "start_km": round(start_km, 1), "end_km": round(end_km, 1), "km": round(dist_km, 1),
            "gain": int(round(gain)), "loss": int(round(loss)),
            "avg_grade": avg_grade, "max_grade": max_grade,
            "time_s": int(round(time_s)), "pace_s_per_km": int(round(pace_s)),
            "vam_m_per_h": vam, "hr_cap": hr_cap, "hr_free": offset is None,
            "phase": phase_label, "instruction": instr,
        }
        out_blocks.append(blk)

    # 5. steepness alerts: any km whose steepest stretch is ≥ walk threshold
    run: list[dict] = []
    for r in rows:
        s = r["seg"]
        g = _local_max_grade(course, s.start_km, s.end_km)
        if g >= walk_grade:
            run.append({"start_km": s.start_km, "end_km": s.end_km, "grade": g, "gain": s.elevation_gain})
        elif run:
            alerts.append(_alert(run))
            run = []
    if run:
        alerts.append(_alert(run))

    return {
        "blocks": out_blocks,
        "alerts": alerts,
        "caps": {
            "hr_cap_climb": hr_cap_climb, "hr_cap_flat": hr_cap_flat,
            "hr_release_descent": hr_release_descent, "walk_grade": walk_grade,
        },
        "phases": [{"until_pct": int(round(lim * 100)) if lim <= 1 else 100, "offset": off, "label": lab} for lim, off, lab in _PHASES],
        "n_climb_km": sum(1 for r in rows if r["cls"] in ("climb", "stairs")),
        "n_stairs_km": sum(1 for r in rows if r["cls"] == "stairs"),
    }


def _alert(run: list[dict]) -> dict:
    return {
        "start_km": round(run[0]["start_km"], 1), "end_km": round(run[-1]["end_km"], 1),
        "km": round(run[-1]["end_km"] - run[0]["start_km"], 1),
        "min_grade": round(min(r["grade"] for r in run), 0),
        "max_grade": round(max(r["grade"] for r in run), 0),
        "gain": int(round(sum(r["gain"] for r in run))),
        "note": "Escaliers / pente raide : on marche, mains sur les cuisses. Bâtons.",
    }


def instruction_code(block: dict) -> str:
    """Short watch-friendly code for a block: 'MONT FC135 650m/h', 'ESCAL marche',
    'DESC FC125', 'PLAT FC131 5:45', 'LIBRE course'."""
    if block.get("hr_free"):
        return "LIBRE course"
    parts = []
    hr = f"FC{block['hr_cap']}" if block.get("hr_cap") else ""
    cls = block["cls"]
    if cls == "stairs":
        parts = ["ESCAL", "marche", hr]
    elif cls == "climb":
        parts = ["MONT", hr, f"{block['vam_m_per_h']}m/h" if block.get("vam_m_per_h") else ""]
    elif cls == "descent":
        parts = ["DESC", hr]
    else:
        pace = int(block.get("pace_s_per_km") or 0)
        parts = ["PLAT", hr, f"{pace // 60}:{pace % 60:02d}" if pace else ""]
    return " ".join(p for p in parts if p)


def leg_instructions(guide: dict, sections: list[dict]) -> list[dict]:
    """ONE instruction per leg between two checkpoints: the terrain block that
    takes the most time on that leg, plus the steep stretches inside it.

    Fewer alerts on the watch than one per terrain block: a 100-miler with
    12 aid stations gets 12 instructions, each valid until the next station.
    """
    blocks = guide.get("blocks") or []
    alerts = guide.get("alerts") or []
    out = []
    for s in sections:
        a, b = float(s["start_km"]), float(s["end_km"])
        best, best_w = None, 0.0
        for blk in blocks:
            o0, o1 = max(a, blk["start_km"]), min(b, blk["end_km"])
            if o1 <= o0:
                continue
            length = blk["end_km"] - blk["start_km"] or 1e-9
            w = blk["time_s"] * (o1 - o0) / length  # time share of the block inside the leg
            if w > best_w:
                best, best_w = blk, w
        steep = [al for al in alerts if al["end_km"] > a and al["start_km"] < b]
        if best is None:
            out.append({"from_name": s["start_name"], "to_name": s["end_name"], "code": "", "block": None, "steep": steep})
            continue
        code = instruction_code(best)
        if steep and best["cls"] != "stairs":
            code += " · ESCAL km " + "/".join(f"{al['start_km']:g}" for al in steep[:2])
        out.append({
            "from_name": s["start_name"], "to_name": s["end_name"], "start_km": a, "end_km": b,
            "code": code, "block": best, "steep": steep,
        })
    return out
