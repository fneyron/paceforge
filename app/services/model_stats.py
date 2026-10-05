"""Dataset and validation numbers shown on the public pages (landing, /methode).

The numbers live in ``app/data/model_stats.json``, refreshed by the race-data
scripts. This module only reads that file (cached on its mtime, so a refresh is
picked up without a restart) and formats the numbers the French way for the
templates. A missing or broken file gives empty values: the templates then hide
the figures instead of showing stale or invented ones.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATS_PATH = Path(__file__).resolve().parent.parent / "data" / "model_stats.json"

NNBSP = " "  # narrow no-break space: French thousands separator
_MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

_cache: dict[str, Any] = {"key": None, "raw": {}}


def fmt_int(value: Any) -> str:
    """119512 -> '119 512' (narrow no-break spaces). Empty string if not a number."""
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return ""
    return f"{n:,}".replace(",", NNBSP)


def fmt_dec(value: Any, digits: int = 1) -> str:
    """21.5 -> '21,5'; 21.0 -> '21'. Empty string if not a number."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return ""
    s = f"{x:.{digits}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    whole, _, frac = s.partition(".")
    whole = fmt_int(whole) if whole.lstrip("-").isdigit() else whole
    return f"{whole},{frac}" if frac else whole


def fmt_date(value: Any) -> str:
    """'2026-10-04' -> '4 octobre 2026'. Empty string if unreadable."""
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return ""
    return f"{d.day} {_MONTHS_FR[d.month - 1]} {d.year}"


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_raw(path: Path) -> dict:
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return {}
    if _cache["key"] == key:
        return _cache["raw"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("not an object")
    except (OSError, ValueError):
        logger.warning("model_stats: cannot read %s", path)
        raw = {}
    _cache["key"], _cache["raw"] = key, raw
    return raw


def _section(raw: dict, name: str) -> dict:
    sec = raw.get(name)
    return sec if isinstance(sec, dict) else {}


def _km_range(note: Any) -> tuple[str, str]:
    """'... UTMB Live races 40-180 km ...' -> ('40', '180'); ('', '') if absent."""
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*km", str(note or ""))
    return (m.group(1), m.group(2)) if m else ("", "")


def _transjeju(val: dict) -> dict | None:
    """Optional single-race check on the Trans Jeju 2026, shown only with a gap figure."""
    tj = val.get("transjeju_2026")
    if not isinstance(tj, dict):
        return None
    after = _num(tj.get("mean_abs_gap_min_after", tj.get("mean_abs_gap_min")))
    if after is None:
        return None
    before = _num(tj.get("mean_abs_gap_min_before"))
    runners = _num(tj.get("finisher_results", tj.get("finishers", tj.get("runners"))))
    return {
        "gap_after": fmt_dec(after),
        "gap_before": fmt_dec(before) if before is not None else "",
        "finishers": fmt_int(runners) if runners else "",
    }


def load_model_stats(path: Path | None = None) -> dict:
    """Formatted numbers for the templates. Every value is a string ('' = unknown)."""
    raw = _read_raw(path or STATS_PATH)
    ds, fit, val = _section(raw, "dataset"), _section(raw, "fit"), _section(raw, "validation")
    src = ds.get("sources") if isinstance(ds.get("sources"), dict) else {}
    km_min, km_max = _km_range(fit.get("note"))

    stats = {
        "updated": fmt_date(raw.get("updated")),
        "dataset": {
            "races": fmt_int(ds.get("races")),
            "events": fmt_int(ds.get("events")),
            "finisher_results": fmt_int(ds.get("finisher_results")),
            "checkpoint_passages": fmt_int(ds.get("checkpoint_passages")),
            "utmb_live": fmt_int(src.get("utmb_live")),
            "livetrail": fmt_int(src.get("livetrail")),
        },
        "fit": {
            "races": fmt_int(fit.get("races")),
            "finisher_results": fmt_int(fit.get("finisher_results")),
            "km_min": km_min,
            "km_max": km_max,
        },
        "validation": {
            "held_out_races": fmt_int(val.get("held_out_races")),
            "held_out_events": fmt_int(val.get("held_out_events")),
            "gap_before": fmt_dec(val.get("mean_abs_gap_min_before")),
            "gap_after": fmt_dec(val.get("mean_abs_gap_min_after")),
            "races_improved": fmt_int(val.get("races_improved")),
            "transjeju_2026": _transjeju(val),
        },
    }
    # width of the "after" bar relative to the "before" one (method page)
    before, after = _num(val.get("mean_abs_gap_min_before")), _num(val.get("mean_abs_gap_min_after"))
    stats["validation"]["bar_after_pct"] = (
        max(5, min(100, round(after / before * 100))) if before and after and before > 0 else 0
    )
    stats["has_dataset"] = bool(stats["dataset"]["races"] and stats["dataset"]["finisher_results"])
    v = stats["validation"]
    stats["has_validation"] = bool(v["gap_after"] and v["held_out_races"])
    return stats
