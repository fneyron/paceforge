"""Refresh failures cannot damage the corpus or approve a worse pacing model."""

import copy
import gzip
import json
import subprocess
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from scripts.race_data import maintenance as m
from scripts.race_data import refresh, state_store
from scripts.race_data.review_model import review


def race_record():
    points = [
        {
            "pointId": str(i),
            "name": f"CP {i}",
            "distance": km * 1000,
            "altitude": alt,
            "gainElevation": gain,
            "latitude": 45.0,
            "longitude": 6.0,
        }
        for i, km, alt, gain in ((0, 0, 100, 0), (1, 25, 600, 800), (2, 50, 100, 1200))
    ]
    passings = [
        [str(i), f"2026-10-01T{hour}:00:00+00:00", None, 0]
        for i, hour in ((0, "06"), (1, "11"), (2, "16"))
    ]
    return {
        "summary": {
            "tenant": "example_2026",
            "race": "50k",
            "name": "Mountain 50k",
            "date": "2026-10-01",
            "distance_km": 50,
            "elevation_gain": 1200,
        },
        "info": {
            "name": "Mountain 50k",
            "distance": 50,
            "elevationGain": 1200,
            "startDate": "2026-10-01T06:00:00+00:00",
        },
        "points": points,
        "runners": {
            str(i): {"time_s": 36000, "passings": copy.deepcopy(passings)} for i in range(12)
        },
    }


class Source:
    requests = 0

    def __init__(self, missing_bib=None):
        self.record = race_record()
        self.missing_bib = missing_bib
        self.calls = []

    def json(self, url, tenant=None):
        self.requests += 1
        self.calls.append(url)
        record = self.record
        if url.endswith("/event-context"):
            return {"selector": [{"raceId": "50k", "name": "Mountain 50k", "status": "FINISHED"}]}
        if url.endswith("/static"):
            return {"info": record["info"], "points": record["points"]}
        if "/progressive?" in url:
            return {"runners": [{"bib": bib, "isFinisher": True} for bib in record["runners"]]}
        if "/runners/" in url:
            bib = url.split("/runners/")[1].split("?")[0]
            if bib == self.missing_bib:
                return None
            return {
                "resume": {"raceTime": "10:00:00", "info": {"name": "Not retained"}},
                "detail": {
                    "passings": [
                        {"pointId": p[0], "datetimeIn": p[1], "datetimeOut": p[2]}
                        for p in record["runners"][bib]["passings"]
                    ]
                },
            }
        raise AssertionError(url)


def prepare(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "utmb_tenants.json").write_text(
        json.dumps({"example_2026": {"races": [["50k", "Mountain 50k", "FINISHED"]]}})
    )
    (catalog / "livetrail_events.json").write_text(
        json.dumps({"example": {"id": "example", "year": 2026, "endDate": "2026-10-01"}})
    )
    monkeypatch.setattr(m, "HERE", catalog)
    root = tmp_path / "races"
    path = root / "utmb/example_2026/50k.json.gz"
    assert m.store_archive(path, race_record())
    args = SimpleNamespace(
        root=str(root),
        report=str(tmp_path / "report.json"),
        max_races=2,
        max_editions=1,
        max_runners=10,
        only=["utmb/example_2026/50k"],
    )
    return path, args


def test_refresh_fetches_existing_runner_times_and_keeps_larger_sample(tmp_path, monkeypatch):
    path, args = prepare(tmp_path, monkeypatch)
    source = Source()
    source.record["runners"]["0"]["passings"][1][1] = "2026-10-01T11:05:00+00:00"
    report = refresh.refresh(args, source, date(2026, 10, 10))
    updated = json.loads(gzip.decompress(path.read_bytes()))
    assert report["status"] == "complete" and len(report["updated"]) == 1
    assert len(updated["runners"]) == 12  # manual limit 10 never cuts an existing sample
    assert updated["runners"]["0"]["passings"][1][1].endswith("11:05:00+00:00")
    assert len([url for url in source.calls if "/runners/" in url]) == 12
    assert "Not retained" not in json.dumps(updated)


def test_partial_download_preserves_archive_and_is_not_stamped_success(tmp_path, monkeypatch):
    path, args = prepare(tmp_path, monkeypatch)
    before = path.read_bytes()
    report = refresh.refresh(args, Source(missing_bib="5"), date(2026, 10, 10))
    assert report["status"] == "partial" and report["errors"]
    assert path.read_bytes() == before
    state = json.loads((path.parents[2] / "maintenance.json").read_text())
    entry = state["entries"]["utmb/example_2026/50k"]
    assert entry["status"] == "failed" and "checked_at" not in entry


def test_missing_finish_or_non_monotonic_timing_cannot_replace_archive(tmp_path):
    path = tmp_path / "utmb/example_2026/50k.json.gz"
    record = race_record()
    m.store_archive(path, record)
    original = path.read_bytes()
    for passings in (
        record["runners"]["0"]["passings"][:-1],
        list(reversed(record["runners"]["0"]["passings"])),
    ):
        bad = copy.deepcopy(record)
        bad["runners"]["0"]["passings"] = passings
        if (
            len(passings) == 3
        ):  # order of API rows is irrelevant; timestamps themselves must be monotonic
            bad["runners"]["0"]["passings"][1][1] = "2026-10-01T18:00:00+00:00"
        with pytest.raises(m.IncompleteDownloadError):
            m.store_archive(path, bad)
        assert path.read_bytes() == original
    assert not list(path.parent.glob("tmp*.json.gz"))


def test_catalog_follows_calendar_year_and_excludes_unfinished_events():
    events = {
        "new": {"year": 2027, "endDate": "2027-05-20"},
        "done": {"year": 2027, "endDate": "2027-04-01"},
    }
    candidates = refresh.catalog_candidates(events, {"legacy_2024": {}}, date(2027, 5, 1))
    assert "new_2027" not in candidates and "done_2027" in candidates
    assert (
        "legacy_2027" in candidates
        and "legacy_2026" in candidates
        and "legacy_2024" not in candidates
    )


def test_recent_results_rechecked_weekly_old_archives_rotate():
    today = date(2026, 10, 10)
    entry = {"checked_at": "2026-10-03T03:30:00+00:00"}
    assert m.is_due(entry, date(2026, 10, 1), today)
    assert not m.is_due(entry, date(2024, 10, 1), today)
    assert not m.is_due({"checked_at": "2026-10-09"}, date(2026, 10, 1), today)
    todo = {f"race{i}": {"exists": True, "date": "2026-10-01"} for i in range(8)}
    todo["old"] = {"exists": True, "date": "2024-10-01"}
    selected, deferred = refresh.select_due(todo, {}, today, 4)
    assert "old" in dict(selected) and len(selected) == 4 and deferred == 5


def test_http_retry_is_bounded_and_respects_request_budget(monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    client = m.PublicClient(max_requests=2)
    client.client.close()
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "1"})

    client.client = httpx.Client(transport=httpx.MockTransport(handle))
    with pytest.raises(m.RequestBudgetError):
        client.get("https://utmblive-api.utmb.world/event-context")
    assert len(calls) == 2
    client.close()


def good_candidate():
    rows = {f"race{i}": {"current": 20.0, "fit": 19.0} for i in range(20)}
    buckets = {str(i): {"current": 20.0, "fit": 19.0} for i in range(4)}
    return {
        "train_events": [f"train{i}" for i in range(8)],
        "test_events": [f"test{i}" for i in range(4)],
        "selected": {"model": "fit_hours"},
        "validation": {
            "held_out_races": 20,
            "held_out_runners": 1500,
            "mean_abs_gap": {"current": 20.0, "fit": 19.0},
            "by_group": copy.deepcopy(buckets),
            "by_band": copy.deepcopy(buckets),
            "per_race": rows,
            "guards": {
                f"utmb/transjeju_{year}/100m": {"current": 10.0, "fit_hours": 9.0}
                for year in (2025, 2026)
            },
            "livetrail": {
                "courses_scored": 30,
                "mean_abs_gap": {"current": 20.0, "fit_hours": 19.0},
            },
        },
    }


def test_eligible_candidate_still_requires_production_validation():
    result = review(good_candidate())
    assert result["status"] == "eligible_for_review" and result["failed_checks"] == []
    assert result["published"] is False and result["production_path_verified"] is False


@pytest.mark.parametrize(
    "change", ["small_gain", "regression", "missing_guard", "nan", "leakage", "missing_band"]
)
def test_quality_gate_rejects_bad_or_incomplete_evidence(change):
    candidate = good_candidate()
    data = candidate["validation"]
    if change == "small_gain":
        data["mean_abs_gap"]["fit"] = 19.98
    elif change == "regression":
        data["by_group"]["0"]["fit"] = 25
    elif change == "missing_guard":
        data["guards"] = {}
    elif change == "nan":
        data["mean_abs_gap"]["fit"] = float("nan")
    elif change == "leakage":
        candidate["test_events"][0] = "train0"
    else:
        data["by_band"].pop("0")
    assert review(candidate)["status"] == "rejected"


def test_persistent_branch_roundtrip_does_not_change_application(tmp_path, monkeypatch):
    bare, repo = tmp_path / "remote.git", tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    monkeypatch.chdir(repo)
    state_store.git("config", "user.email", "test@example.com")
    state_store.git("config", "user.name", "Test")
    state_store.git("remote", "add", "origin", str(bare))
    (repo / "app.py").write_text("application stays here")
    archive = repo / "data/races/utmb/example_2026/race.json.gz"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"original")
    state_store.git("add", ".")
    state_store.git("commit", "-m", "seed")
    state_store.git("push", "origin", "main")
    original_main = state_store.git("rev-parse", "HEAD")
    archive.write_bytes(b"updated")
    saved = state_store.save()
    assert saved != original_main and state_store.git("rev-parse", "HEAD") == original_main
    assert "app.py" not in state_store.git("ls-tree", "--name-only", saved)
    archive.write_bytes(b"local stale copy")
    (repo / "app.py").write_text("unrelated local edit")
    state_store.restore()
    assert archive.read_bytes() == b"updated"
    assert (repo / "app.py").read_text() == "unrelated local edit"
    archive.write_bytes(b"second update")
    next_commit = state_store.save()
    assert state_store.git("rev-parse", next_commit + "^") == saved
