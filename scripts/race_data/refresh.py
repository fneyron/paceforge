"""Weekly collection entry point: python -m scripts.race_data.refresh.

The catalog is seeded from the known event identifiers. Current and previous
editions are rediscovered from the public API; no hard-coded calendar year.
Recent results are checked weekly, older archives rotate through a bounded
queue. State and archives are persisted together by the race-data workflow.
"""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import date, timedelta
from pathlib import Path

from scripts.race_data import maintenance as m


def read_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def catalog_candidates(events, tenants, today):
    prefixes = set(events) | {key.rsplit("_", 1)[0] for key in tenants}
    result = {}
    for prefix in sorted(prefixes):
        m.safe_id(prefix)
        event = events.get(prefix, {})
        history = {h.get("year"): h for h in event.get("history", [])}
        if event.get("year"):
            history[event["year"]] = event
        for year in (today.year, today.year - 1):
            meta = history.get(year, {})
            ended = m.day(meta.get("endDate") or meta.get("startDate"))
            if ended and ended > today - timedelta(days=2):
                continue  # allow final rankings to settle after the event
            result[f"{prefix}_{year}"] = ended
    return result


def discover(client, events, tenants, entries, today, maximum, errors, only):
    candidates = catalog_candidates(events, tenants, today)
    if only:
        candidates = {
            key: value
            for key, value in candidates.items()
            if any(item.startswith(f"utmb/{key}/") for item in only)
        }
    due = [
        (key, ended)
        for key, ended in candidates.items()
        if m.is_due(
            entries.get("catalog/" + key, {}),
            ended or (today if key.endswith(f"_{today.year}") else None),
            today,
        )
    ]
    due.sort(
        key=lambda item: (
            entries.get("catalog/" + item[0], {}).get("attempted_at", ""),
            -(item[1] or date(today.year - 1, 1, 1)).toordinal(),
            item[0],
        )
    )
    for tenant, ended in due[:maximum]:
        entry = entries.setdefault("catalog/" + tenant, {})
        entry["attempted_at"] = m.utc_now()
        try:
            context = client.json(m.API + "/event-context", tenant)
            if context is None:
                entry.update(checked_at=m.utc_now(), status="unavailable")
                continue
            if not isinstance(context.get("selector"), list):
                raise m.IncompleteDownloadError("Invalid event catalog")
            tenants[tenant] = {
                "event": context.get("eventName"),
                "races": [
                    [r["raceId"], r.get("name"), r.get("status")] for r in context["selector"]
                ],
                "date": ended.isoformat() if ended else None,
            }
            entry.update(checked_at=m.utc_now(), status="ok")
        except m.RequestBudgetError:
            raise
        except Exception as exc:
            errors.append({"item": "catalog/" + tenant, "error": type(exc).__name__})
            entry["status"] = "failed"
    return max(0, len(due) - maximum)


def race_candidates(root, events, tenants, today):
    todo = {}
    for path in sorted(root.glob("*/*/*.json.gz")):
        record = json.loads(gzip.decompress(path.read_bytes()))
        source, edition = path.parts[-3:-1]
        cid = path.name.removesuffix(".json.gz")
        m.safe_id(edition)
        m.safe_id(cid)
        key = f"{source}/{edition}/{cid}"
        summary = record["summary"]
        todo[key] = {
            "source": source,
            "edition": edition,
            "race": cid,
            "date": summary.get("date"),
            "base": record.get("base"),
            "exists": True,
        }
    # A modern LiveTrail race uses the same JSON endpoint/format as UTMB Live.
    for tenant, context in tenants.items():
        m.safe_id(tenant)
        year = int(tenant.rsplit("_", 1)[-1])
        for cid, name, status in context.get("races", []):
            m.safe_id(cid)
            key = f"utmb/{tenant}/{cid}"
            if status != "FINISHED" or year < today.year - 1 or not m.is_running(name, tenant, cid):
                continue
            if key not in todo:
                todo[key] = {
                    "source": "utmb",
                    "edition": tenant,
                    "race": cid,
                    "date": context.get("date"),
                    "exists": False,
                }
    # Previously downloaded XML archives already carry their verified source URL.
    # The recent history also discovers new courses on the old XML platform.
    for event in events.values():
        for history in event.get("history", []):
            base = str(history.get("url") or "").rstrip("/")
            ended = m.day(history.get("endDate") or history.get("startDate"))
            if (
                history.get("year", 0) < today.year - 1
                or "/histo/" not in base
                or (ended and ended > today - timedelta(days=2))
            ):
                continue
            edition = m.safe_id(base.rsplit("/", 1)[-1])
            key = f"livetrail/{edition}/*"
            todo[key] = {
                "source": "livetrail",
                "edition": edition,
                "race": "*",
                "base": base,
                "date": ended.isoformat() if ended else None,
                "exists": False,
            }
    return todo


def select_due(todo, entries, today, maximum, only=()):
    due = [
        (key, item)
        for key, item in todo.items()
        if (not only or key in only)
        and m.is_due(entries.get(key, {}), m.day(item.get("date")), today)
    ]
    due.sort(
        key=lambda pair: (
            entries.get(pair[0], {}).get("attempted_at", ""),
            -(m.day(pair[1].get("date")) or date.min).toordinal(),
            pair[0],
        )
    )
    recent, old = [], []
    for pair in due:
        race_date = m.day(pair[1].get("date"))
        (
            recent
            if not pair[1]["exists"] or (race_date and race_date >= today - timedelta(days=90))
            else old
        ).append(pair)
    reserve = min(2, len(old), maximum // 4)
    selected = recent[: maximum - reserve]
    selected += old[: maximum - len(selected)]
    return selected, max(0, len(due) - len(selected))


def refresh(args, client=None, today=None):
    today = today or date.today()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "maintenance.json"
    state = read_json(state_path, {"version": 1, "entries": {}})
    if state.get("version") != 1:
        raise ValueError("Unsupported collection state version")
    entries = state["entries"]
    catalog_dir = Path(getattr(args, "catalog_dir", m.HERE))
    events = read_json(catalog_dir / "livetrail_events.json", {})
    tenants_path = catalog_dir / "utmb_tenants.json"
    tenants = read_json(tenants_path, {})
    report = {
        "started_at": m.utc_now(),
        "scope": args.only or "scheduled queue",
        "updated": [],
        "unchanged": [],
        "excluded": [],
        "errors": [],
    }
    own_client = client is None
    client = client or m.PublicClient(args.delay, args.max_requests)
    try:
        report["catalog_deferred"] = discover(
            client, events, tenants, entries, today, args.max_editions, report["errors"], args.only
        )
        m.save_json(tenants_path, tenants)
        todo = race_candidates(root, events, tenants, today)
        for key in args.only:
            if key not in todo:
                raise ValueError(f"Unknown requested race: {key}")
        selected, report["races_deferred"] = select_due(
            todo, entries, today, args.max_races, args.only
        )
        cache = {}
        processed = 0
        for key, item in selected:
            entry = entries.setdefault(key, {})
            entry["attempted_at"] = m.utc_now()
            try:
                if item["source"] == "livetrail":
                    base = item["base"]
                    if base not in cache:
                        cache[base] = m.xml_courses(client, base)
                    courses = cache[base]
                else:
                    courses = {item["race"]: None}
                if item["race"] != "*":
                    courses = {item["race"]: courses[item["race"]]}
                finished = True
                for cid, course in courses.items():
                    race_key = f"{item['source']}/{item['edition']}/{cid}"
                    if item["race"] == "*" and not m.is_due(
                        entries.get(race_key, {}), m.day(item.get("date")), today
                    ):
                        continue
                    if processed >= args.max_races:
                        report["races_deferred"] += 1
                        finished = False
                        break
                    processed += 1
                    path = root / item["source"] / item["edition"] / (m.safe_id(cid) + ".json.gz")
                    previous = (
                        json.loads(gzip.decompress(path.read_bytes())) if path.exists() else None
                    )
                    if item["source"] == "utmb":
                        record = m.collect_json(
                            client, item["edition"], cid, args.max_runners, previous
                        )
                    else:
                        record = m.collect_xml(
                            client,
                            item["edition"],
                            cid,
                            item["base"],
                            course,
                            args.max_runners,
                            previous,
                        )
                    if record is None:
                        report["excluded"].append(race_key)
                    else:
                        changed = m.store_archive(path, record)
                        report["updated" if changed else "unchanged"].append(race_key)
                    entries[race_key] = {
                        "checked_at": m.utc_now(),
                        "attempted_at": m.utc_now(),
                        "status": "ok" if record else "excluded",
                    }
                if finished:
                    entry.update(checked_at=m.utc_now(), status="ok")
            except m.RequestBudgetError:
                raise
            except Exception as exc:
                entry["status"] = "failed"
                report["errors"].append(
                    {"item": key, "error": type(exc).__name__, "detail": str(exc)[:180]}
                )
            m.save_json(state_path, state)
    except m.RequestBudgetError:
        report["errors"].append({"item": "collection", "error": "request_budget_reached"})
    except Exception as exc:
        report["errors"].append(
            {"item": "collection", "error": type(exc).__name__, "detail": str(exc)[:180]}
        )
    finally:
        if own_client:
            client.close()
        report.update(
            finished_at=m.utc_now(),
            requests=client.requests,
            corpus_sha256=m.digest_corpus(root),
            archives=len(list(root.glob("*/*/*.json.gz"))),
        )
        report["status"] = "partial" if report["errors"] else "complete"
        m.save_json(state_path, state)
        m.save_json(Path(args.report), report)
        print(
            json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in report.items()}),
            flush=True,
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(m.REPO / "data/races"))
    parser.add_argument("--catalog-dir", default=str(m.HERE))
    parser.add_argument("--report", default="reports/race-data/collection.json")
    parser.add_argument("--max-races", type=int, default=24)
    parser.add_argument("--max-editions", type=int, default=120)
    parser.add_argument("--max-runners", type=int, default=200)
    parser.add_argument("--max-requests", type=int, default=12000)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--only", nargs="*", default=[])
    args = parser.parse_args()
    if not (
        1 <= args.max_races <= 60
        and 1 <= args.max_editions <= 300
        and 10 <= args.max_runners <= 2000
        and 0.2 <= args.delay <= 10
        and 1 <= args.max_requests <= 30000
    ):
        parser.error("Collection limits out of bounds")
    report = refresh(args)
    raise SystemExit(1 if report["errors"] else 0)


if __name__ == "__main__":
    main()
