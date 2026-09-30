"""Find more UTMB Live events: LiveTrail event ids and earlier years as tenants.

UTMB Live names an event "<id>_<year>"; the ids are not listed anywhere, but
UTMB events are timed by LiveTrail, whose archive lists its event ids and
years. Each candidate is checked on /event-context (a real tenant answers with
its races). Writes the tenants found, merged with the known ones.

    python scripts/race_data/discover_utmb_tenants.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import time

HERE = pathlib.Path(__file__).resolve().parent
KNOWN = HERE / "utmb_tenants.json"
EVENTS = HERE / "livetrail_events.json"


def context(tenant: str) -> dict | None:
    out = subprocess.run(["curl", "-s", "-m", "20", "-A", "PaceForge research (paceforge.fr)", "-H", f"x-tenant: {tenant}",
                          "https://utmblive-api.utmb.world/event-context"], capture_output=True).stdout
    time.sleep(0.25)
    try:
        j = json.loads(out)
    except ValueError:
        return None
    return j if isinstance(j, dict) and j.get("selector") else None


def main() -> None:
    known = json.loads(KNOWN.read_text())
    events = json.loads(EVENTS.read_text())
    prefixes = {k.rsplit("_", 1)[0] for k in known}
    candidates = set()
    for eid, e in events.items():
        for h in e.get("history") or []:
            if 2019 <= (h.get("year") or 0) <= 2025:
                candidates.add(f"{eid}_{h['year']}")
    for p in prefixes:
        for y in (2019, 2020, 2021):
            candidates.add(f"{p}_{y}")
    candidates -= set(known)
    found = {}
    for t in sorted(candidates):
        j = context(t)
        if j:
            found[t] = {"event": j.get("eventName"), "races": [(r["raceId"], r["name"], r.get("status")) for r in j["selector"]]}
            print(t, j.get("eventName"), len(j["selector"]), flush=True)
    merged = known | found
    KNOWN.write_text(json.dumps(merged, indent=1, ensure_ascii=False))
    print(f"{len(candidates)} candidates tried, {len(found)} new tenants, {len(merged)} in total")


if __name__ == "__main__":
    main()
