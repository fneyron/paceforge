"""Bounded public-result refreshes. Existing archives survive failed downloads.

The JSON API also serves modern LiveTrail events (not only UTMB-branded races).
Its records use the existing ``utmb`` dataset format; older LiveTrail XML
archives retain their own format. No names or contact details are stored.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from scripts.race_data import collect_livetrail as lt
from scripts.race_data import collect_utmb as ut
from scripts.race_data.dataset import is_running

API = ut.API
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RECENT_DAYS = 90


class IncompleteDownloadError(ValueError):
    pass


class RequestBudgetError(RuntimeError):
    pass


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".refresh-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def save_json(path: Path, data: dict) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode())


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Unsupported race or edition identifier")
    return value


def day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class PublicClient:
    def __init__(self, delay: float = 0.5, max_requests: int = 12000):
        self.delay, self.max_requests, self.requests = delay, max_requests, 0
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "PaceForge research (https://paceforge.fr)"},
        )

    def close(self):
        self.client.close()

    def get(self, url: str, tenant: str | None = None):
        if urlsplit(url).scheme != "https":
            raise ValueError("Public results must use HTTPS")
        for attempt in range(3):
            if self.requests >= self.max_requests:
                raise RequestBudgetError("Weekly request budget reached")
            self.requests += 1
            time.sleep(self.delay)
            try:
                response = self.client.get(url, headers={"X-Tenant": tenant} if tenant else {})
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                status = (
                    exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                )
                if attempt == 2 or (status and status != 429 and status < 500):
                    raise
                retry = response.headers.get("Retry-After", "") if status else ""
                time.sleep(min(120, max(2 ** (attempt + 1), int(retry) if retry.isdigit() else 0)))
        raise AssertionError("unreachable")

    def json(self, url: str, tenant: str | None = None):
        response = self.get(url, tenant)
        return response.json() if response is not None else None

    def xml(self, url: str):
        response = self.get(url)
        root = lt.xml(response.text) if response is not None else None
        if root is None:
            raise IncompleteDownloadError("Missing or invalid XML")
        return root


def sample(finishers: list[dict], maximum: int, previous: dict | None):
    # Never reduce an existing sample just because a scheduled/manual run has a lower limit.
    count = max(maximum, len((previous or {}).get("runners", {})))
    return ut.pick(finishers, count, 0.0)


def collect_json(client, tenant: str, race_id: str, maximum: int, previous=None):
    static = client.json(f"{API}/races/{quote(race_id, safe='')}/static", tenant)
    if not static or not isinstance(static.get("info"), dict):
        raise IncompleteDownloadError("Missing race metadata")
    info = static["info"]
    if not 40 <= (info.get("distance") or 0) <= 180 or not is_running(
        info.get("name"), tenant, race_id
    ):
        return None
    rankings = client.json(
        f"{API}/races/{quote(race_id, safe='')}/progressive?type=FINAL_RANKING&limit=10000", tenant
    )
    if not rankings or not isinstance(rankings.get("runners"), list):
        raise IncompleteDownloadError("Missing final ranking")
    finishers = [r for r in rankings["runners"] if r.get("isFinisher")]
    if len(finishers) < 10:
        raise IncompleteDownloadError("Fewer than ten finishers in the final ranking")
    points = static.get("points") or []
    if len(points) < 3:
        raise IncompleteDownloadError("Missing checkpoints")
    track = client.json(info["track"]) if info.get("track") else None
    if info.get("track") and (not track or not track.get("segments")):
        raise IncompleteDownloadError("GPS track download failed")
    if previous and previous.get("track") and not track:
        raise IncompleteDownloadError("Previously available GPS track is missing")
    compact = ut.compact_track(track) if track else None
    if track and len(compact) <= 50:
        raise IncompleteDownloadError("GPS track has too few usable points")
    runners = {}
    for finisher in sample(finishers, maximum, previous):
        bib = str(finisher["bib"])
        raw = client.json(f"{API}/runners/{quote(bib, safe='')}?locale=en", tenant)
        resume = (raw or {}).get("resume") or {}
        passings = ((raw or {}).get("detail") or {}).get("passings")
        total = ut.secs(resume.get("raceTime"))
        if not passings or not total or total <= 0:
            raise IncompleteDownloadError("Incomplete finisher timing")
        runners[bib] = {
            "sex": (resume.get("info") or {}).get("sex"),
            "index": (resume.get("info") or {}).get("index"),
            "age": (resume.get("info") or {}).get("age"),
            "rank": (resume.get("ranking") or {}).get("scratch"),
            "time_s": total,
            "passings": [
                [
                    p.get("pointId"),
                    p.get("datetimeIn"),
                    p.get("datetimeOut"),
                    p.get("restTimeSeconds"),
                ]
                for p in passings
            ],
        }
    return {
        "summary": {
            "tenant": tenant,
            "race": race_id,
            "name": info.get("name"),
            "date": str(info.get("startDate") or "")[:10],
            "distance_km": info["distance"],
            "elevation_gain": info.get("elevationGain"),
            "finishers": len(finishers),
            "checkpoints": len(points),
            "track": bool(track),
            "runners_sampled": len(runners),
        },
        "info": {
            k: info.get(k)
            for k in ("name", "distance", "elevationGain", "startDate", "eventTimezone", "category")
        },
        "points": [
            {
                k: p.get(k)
                for k in (
                    "pointId",
                    "name",
                    "shortName",
                    "distance",
                    "altitude",
                    "gainElevation",
                    "latitude",
                    "longitude",
                    "cutoff",
                    "isPublic",
                )
            }
            | {
                "services": [
                    s.get("name") if isinstance(s, dict) else s for s in p.get("services") or []
                ]
            }
            for p in points
        ],
        "profile": (static.get("profile") or {}).get("profile"),
        "track": compact,
        "track_stats": (track or {}).get("stats"),
        "runners": runners,
    }


def xml_courses(client, base: str):
    host = urlsplit(base).hostname or ""
    if host != "livetrail.net" and not host.endswith(".livetrail.net"):
        raise ValueError("Unsupported archive host")
    root = client.xml(base.rstrip("/") + "/parcours.php")
    names = {c.get("id"): c.get("n") for c in root.iter("c") if c.get("id")}
    result = {}
    for group in root.iter("points"):
        cid = group.get("course")
        points = [
            {
                "id": p.get("idpt"),
                "name": p.get("n"),
                "km": lt.num(p.get("km")),
                "dplus": lt.num(p.get("d")),
                "alt": lt.num(p.get("a")),
                "lat": lt.num(p.get("lat")),
                "lon": lt.num(p.get("lon")),
            }
            for p in group.iter("pt")
        ]
        if cid and points:
            result[safe_id(cid)] = {"name": names.get(cid), "points": points}
    if not result:
        raise IncompleteDownloadError("No archive courses")
    return result


def collect_xml(
    client, edition: str, cid: str, base: str, course: dict, maximum: int, previous=None
):
    points = course["points"]
    km = max((p["km"] or 0 for p in points), default=0)
    if not 40 <= km <= 180 or not is_running(course.get("name"), edition, cid):
        return None
    root = client.xml(f"{base}/classement.php?course={quote(cid, safe='')}&cat=scratch")
    finishers = []
    for runner in root.findall(".//classement/c"):
        total = lt.hms(runner.get("tps"))
        if runner.get("doss") and total and total > 0:
            finishers.append(
                {
                    "bib": runner.get("doss"),
                    "rank": int(runner.get("class") or 0),
                    "sex": runner.get("sx"),
                    "time_s": total,
                }
            )
    if len(finishers) < 10:
        raise IncompleteDownloadError("Fewer than ten archive finishers")
    runners = {}
    for finisher in sample(finishers, maximum, previous):
        bib = finisher["bib"]
        root = client.xml(f"{base}/coureur.php?rech={quote(bib, safe='')}")
        passings = []
        for point in root.findall(".//pass/e"):
            arrival, departure = lt.num(point.get("tn")), lt.num(point.get("tnd"))
            passings.append(
                [
                    point.get("idpt"),
                    round(arrival * 3600) if arrival is not None else None,
                    round(departure * 3600) if departure is not None else None,
                ]
            )
        if not passings:
            raise IncompleteDownloadError("Incomplete archive finisher timing")
        runners[bib] = finisher | {"passings": passings}
    return {
        "summary": {
            "edition": edition,
            "course": cid,
            "name": course.get("name"),
            "distance_km": km,
            "elevation_gain": max(p["dplus"] or 0 for p in points),
            "checkpoints": len(points),
            "finishers": len(finishers),
            "runners_sampled": len(runners),
        },
        "base": base,
        "points": points,
        "runners": runners,
    }


def store_archive(path: Path, record: dict) -> bool:
    """Validate the SAME reader used by fitting, then atomically replace; no partial commit."""
    from scripts.race_data import dataset

    payload = json.dumps(
        record, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(suffix=".json.gz", dir=path.parent)
    os.close(fd)
    tmp = Path(temporary)
    try:
        tmp.write_bytes(gzip.compress(payload, mtime=0))
        parsed = (
            dataset.load_utmb(tmp)
            if path.parent.parent.name == "utmb"
            else dataset.load_livetrail(tmp)
        )
        if (
            not parsed
            or len(parsed["runners"]) < 10
            or len(parsed["runners"]) != len(record["runners"])
        ):
            raise IncompleteDownloadError("Incomplete checkpoints after parsing")
        if not dataset.plausible_running(parsed, record["summary"].get("elevation_gain")):
            raise IncompleteDownloadError("Implausible running times")
        for runner in parsed["runners"]:
            times = [t for t in runner["arr"] if t is not None]
            if any(t < 0 for t in times) or any(
                b < a for a, b in zip(times, times[1:], strict=False)
            ):
                raise IncompleteDownloadError("Non-monotonic checkpoint timing")
        changed = not path.exists() or json.loads(gzip.decompress(path.read_bytes())) != record
        if changed:
            os.replace(tmp, path)
        return changed
    finally:
        tmp.unlink(missing_ok=True)


def digest_corpus(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.glob("*/*/*.json.gz")):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def is_due(entry: dict, race_date: date | None, today: date) -> bool:
    last = day(entry.get("checked_at"))
    interval = 6 if race_date and race_date >= today - timedelta(days=RECENT_DAYS) else 90
    return last is None or (today - last).days >= interval


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
