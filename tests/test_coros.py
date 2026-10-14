"""COROS: prose parsers on the real tool texts, synthetic sleep stages, OAuth
(PKCE, resource, rotation, invalid_grant, invalid_client), the MCP client
(JSON and SSE), sync into the health tables, HRV scale consistency, routes,
migration. No network: every HTTP call goes to an httpx.MockTransport."""

import base64
import hashlib
import importlib.util
import json
import pathlib
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import decrypt_secret, encrypt_secret
from app.dependencies import get_current_user
from app.models.coros import CorosConnection, OAuthClient
from app.models.health import HealthMetric, HealthSample
from app.models.user import User
from app.services import coros
from app.services.health import current_form, hrv_same_scale
from tests import owner_coros as oc

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "t4c5d6e7f8a9_add_coros.py"

# ── the real tool outputs (captured 2026-09-30) ─────────────────────────────

RHR = """Resting Heart Rate — Last 3 days
========================

2026-09-30: 39 bpm
2026-09-29: 40 bpm
2026-09-28: No data
"""


def hrv_text(nights: dict, tz: int = 8, every_min: int = 10, value=lambda i: 70 + 3 * (i % 7)) -> str:
    """querySleepHrv's text for nights {wake day: (start, end)} (local): the
    assessment COROS prints (never read) and one reading every `every_min`
    minutes inside each window plus two daytime readings, grouped by local
    day as COROS groups them, `tz` in quarter hours."""
    by_day: dict = {}
    i = 0
    for start, end in nights.values():
        t = start + timedelta(minutes=4)
        pts = []
        while t < end:
            pts.append(t)
            t += timedelta(minutes=every_min)
        pts += [end + timedelta(hours=5), end + timedelta(hours=6)]  # the afternoon: never the night's
        for p in pts:
            ts = int((p - timedelta(minutes=15 * tz)).replace(tzinfo=timezone.utc).timestamp())
            by_day.setdefault(p.date(), []).append(f"  timestamp={ts}, timezone={tz}, hrv={value(i)} ms, status=4, "
                                                   "confidence=100000")
            i += 1
    days = sorted(by_day)
    head = [f"Sleep HRV — {days[0]} to {days[-1]}", "========================", "",
            "HRV Assessment — Last days", "========================", ""]
    for d in sorted(nights, reverse=True):
        head += [f"{d}:", "  HRV Avg: 83 ms — Normal", "  Normal Range: 70 - 84 ms", "  Baseline: 77 ms"]
    body = ["", "Sleep HRV Time Series — Last days", "========================", ""]
    for d in days:
        body += [f"{d}:"] + by_day[d]
    return "\n".join(head + body) + "\n"


SLEEP_OVERVIEW = """Sleep Overview
========================
Note: each record below is dated by its wake-up day.

2026-09-27
Sleep Score: -1
Daily Sleep: 1h 22min (incl. naps)
Sleep metrics scope: daily
Naps Total (asleep): 1h 22min
Naps Period (incl. awake): 1h 29min
Nap Window: 2026-09-27 01:23 - 2026-09-27 02:52

2026-09-28
Sleep Score: 0
Sleep detail for this day is not available yet.

2026-09-29
Sleep Score: 89
Daily Sleep: 8h 46min (incl. naps)
Main Sleep (asleep): 8h 46min
Main Sleep Period (incl. awake): 9h 0min
Sleep metrics scope: daily
Deep Sleep Ratio: 11%
Light Sleep Ratio: 63%
REM Ratio: 23%
Awake Ratio: 3%
Awake Time: 14 min
Awake Count (>5 min): 0
Main Sleep Window: 2026-09-29 00:31 - 2026-09-29 09:31
Naps Total: 0 min

2026-09-30
Sleep Score: 91
Daily Sleep: 9h 48min (incl. naps)
Main Sleep (asleep): 9h 48min
Main Sleep Period (incl. awake): 10h 2min
Sleep metrics scope: daily
Deep Sleep Ratio: 13%
Light Sleep Ratio: 57%
REM Ratio: 28%
Awake Ratio: 2%
Awake Time: 14 min
Awake Count (>5 min): 0
Main Sleep Window: 2026-09-29 23:52 - 2026-09-30 09:54
Naps Total: 0 min
"""

DAILY = """Daily Health Data — Last 2 days | Resting HR: 35 bpm | HRV Baseline: 42 ms
Note: sleep entries are dated by their wake-up day.

--- 20260929 ---
Steps: 18,055 | Calories: 786 kcal | Exercise: 39 min
Stress: Avg 23
Sleep Summary:
  Total: 9h 0min | Deep: 1h 0min | Light: 5h 41min | REM: 2h 5min | Awake: 14 min
  Sleep HR: Avg 35 bpm | Min 30 bpm | Max 53 bpm

--- 20260930 ---
Steps: 1,487 | Calories: 92 kcal | Exercise: 0 min
Stress: Avg 13
Sleep Summary:
  Total: 10h 2min | Deep: 1h 16min | Light: 5h 43min | REM: 2h 49min | Awake: 14 min
  Sleep HR: Avg 35 bpm | Min 30 bpm | Max 52 bpm
"""

AVG_HR = """Average Heart Rate — Last 10 days
========================

2026-10-04: 53 bpm (Min: 36, Max: 98)
2026-10-03: 119 bpm (Min: 54, Max: 142)
2026-10-02: 90 bpm (Min: 39, Max: 141)
"""

DAILY_60 = """Daily Health Data — Last 60 days | Resting HR: 35 bpm | HRV Baseline: 42 ms
Note: sleep entries are dated by their wake-up day.

--- 20260802 ---
Steps: 19,835 | Calories: 1,474 kcal | Exercise: 1h 46min
Stress: Avg 59

--- 20260810 ---
Steps: 0 | Calories: 0 kcal | Exercise: 0 min

--- 20260925 ---
Steps: 12,103 | Calories: 363 kcal | Exercise: 3 min
Stress: Avg 28
Sleep Summary:
  Total: 1h 29min | Deep: 10 min | Light: 59 min | REM: 13 min | Awake: 7 min
  Sleep HR: Avg 34 bpm | Min 31 bpm | Max 44 bpm
"""

D30, D29, D28 = date(2026, 9, 30), date(2026, 9, 29), date(2026, 9, 28)

# the owner's real getActivityDetail answer (2026-09-30)
DETAIL = """🏃 Outdoor Run Activity Details
========================================

Workout Time: 30:06
Distance: 6.17 km
Total Time: 30:12
Average Pace: 4:53 /km
Moving Average Pace: 4:46 /km
Average Heart Rate: 132 bpm
Average Cadence: 163 spm
Average Power: 266 W
Elevation Gain / Loss: 110 m / 121 m
Calories: 436 kcal
Training Load: 73"""


# the owner's real queryActivityLapData answer for that run (2026-10-08, laps trimmed): its whole-activity row
LAPS = oc.LAPS_2026_09_30


def _shift(text: str, days: int) -> str:
    """The same text with every date moved by `days` (keeps DB tests on today's
    clock: raw samples older than 400 days are pruned)."""
    def iso(m):
        return (date(int(m[1]), int(m[2]), int(m[3])) + timedelta(days=days)).isoformat()

    def compact(m):
        return (date(int(m[1]), int(m[2]), int(m[3])) + timedelta(days=days)).strftime("%Y%m%d")
    text = re.sub(r"(\d{4})-(\d{2})-(\d{2})", iso, text)
    return re.sub(r"\b(\d{4})(\d{2})(\d{2})\b", compact, text)


def _rows(samples):
    return [SimpleNamespace(start_at=s.start, end_at=s.end, value=s.value, source=s.source, kind=s.kind)
            for s in samples]


# ── parsers ─────────────────────────────────────────────────────────────────

def test_parse_sleep_overview_and_daily_summary():
    ov = coros.parse_sleep_overview(SLEEP_OVERVIEW)
    assert set(ov) == {D29, D30}  # the nap-only day and the « not available yet » day: no night
    n = ov[D30]
    assert n["start"] == datetime(2026, 9, 29, 23, 52) and n["end"] == datetime(2026, 9, 30, 9, 54)
    assert (n["asleep"], n["period"], n["awake"], n["daily"]) == (588, 602, 14, 588)
    assert "score" not in n and "ratios" not in n  # brand score and daily-scope ratios are not read
    daily = coros.parse_daily_sleep(DAILY)
    assert daily[D30] == {"total": 602, "deep": 76, "core": 343, "rem": 169, "awake": 14,
                          "hr": {"avg": 35, "min": 30, "max": 52}}
    assert coros.parse_daily_sleep(DAILY_60)[date(2026, 9, 25)]["total"] == 89  # the lone nap's summary


def test_parse_avg_hr_and_daily_activity():
    hr = coros.parse_avg_hr(AVG_HR)
    assert hr[date(2026, 10, 4)] == {"avg": 53, "min": 36, "max": 98} and len(hr) == 3
    assert coros.parse_avg_hr("2026-10-04: 53 bpm") == {date(2026, 10, 4): {"avg": 53, "min": None, "max": None}}
    act = coros.parse_daily_activity(DAILY_60)
    assert act[date(2026, 8, 2)] == {"steps": 19835, "kcal": 1474, "exercise": 106}  # no stress score
    assert act[date(2026, 8, 10)] == {"steps": 0, "kcal": 0, "exercise": 0}


def test_build_daily_keeps_plausible_values_only():
    today = date(2026, 10, 5)
    data = {"hr_day": coros.parse_avg_hr(AVG_HR), "activity": coros.parse_daily_activity(DAILY_60)}
    rows = {(r.metric, r.day): r for r in coros.build_daily(data, today)}
    assert rows[("hr_day", date(2026, 10, 3))].details == {"min": 54, "max": 142}
    assert rows[("steps", date(2026, 8, 2))].details == {"kcal": 1474, "exercise": 106}
    assert ("steps", date(2026, 8, 10)) not in rows  # not worn
    assert all(len(r.metric) <= 12 for r in rows.values())  # HealthMetric.metric is String(12)
    assert not {m for m, _ in rows} & {"load", "recovery", "stress", "fitness", "hrv_norm", "sleep_score",
                                        "vo2max", "rhr"}
    assert coros.build_daily({"hr_day": {today: {"avg": 999, "min": None, "max": None}}}, today) == []


def test_parsers_survive_garbled_text():
    junk = "Erreur interne\n2026-13-45: 39 bpm\n2026-09-30:\n  HRV Avg: n/a\n--- 2026 ---\nVO2max: —"
    assert coros.parse_hrv_points(junk) == [] and coros.parse_hrv_points("") == []
    assert coros.parse_sleep_overview(junk) == {} and coros.parse_daily_sleep(junk) == {}
    assert coros.parse_avg_hr(junk) == {} and coros.parse_daily_activity(junk) == {}
    assert coros.parse_naps(junk) == {}
    bad_window = SLEEP_OVERVIEW.replace("2026-09-30 09:54", "2026-09-29 09:54")  # ends before it starts
    assert set(coros.parse_sleep_overview(bad_window)) == {D29}
    assert coros.parse_duration("9h 48min") == 588 and coros.parse_duration("14 min") == 14
    assert coros.parse_duration("—") is None


def test_hrv_answer_requires_readable_raw_lines_not_only_a_successful_response():
    header = "Sleep HRV Time Series\n"
    assert coros.hrv_answer_read(header + "2026-10-13:\n  timestamp=1791878400, timezone=8, hrv=72 ms\n")
    assert coros.hrv_answer_read(header + "No readings for this period.\n")
    assert coros.hrv_answer_read(header + "2026-10-13: No data available\n")
    assert not coros.hrv_answer_read(header + "2026-10-13:\n  timestamp=1791878400, offset=8, hrv=72 ms\n")
    assert not coros.hrv_answer_read(header + "2026-10-13:\n  hrv=72 ms, timestamp=1791878400, timezone=8\n")
    assert not coros.hrv_answer_read(header + "2026-10-13:\n  time=1791878400, timezone=8, hrv=72 ms\n")
    assert not coros.hrv_answer_read(header + "2026-10-13:\n  timestamp=1791878400, timezone=8, hrv=72 ms\n"
                                   "  time=1791878700, timezone=8, hrv=95 ms\n")
    assert not coros.hrv_answer_read("Unexpected response")
    assert not coros.hrv_answer_read("")


def test_tool_text_decodes_the_json_quoted_text_coros_sends():
    # what COROS really sends: the text is itself a JSON string literal
    quoted = {"content": [{"type": "text", "text": json.dumps(RHR, ensure_ascii=True)}]}
    assert coros.tool_text(quoted) == RHR
    assert coros.tool_text({"content": [{"type": "text", "text": '"broken'}]}) == '"broken'


def test_rpc_response_json_and_sse():
    result = {"content": [{"type": "text", "text": RHR}], "isError": False}
    body = json.dumps({"jsonrpc": "2.0", "id": 3, "result": result})
    assert coros.tool_text(coros.parse_rpc_response("application/json", body, 3)) == RHR
    sse = ('event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
           f"event: message\ndata: {body}\n\n")
    assert coros.tool_text(coros.parse_rpc_response("text/event-stream", sse, 3)) == RHR
    with pytest.raises(coros.ToolError):
        coros.tool_text({"content": [{"type": "text", "text": "days must be <= 7"}], "isError": True})
    with pytest.raises(coros.CorosError):
        coros.parse_rpc_response("application/json", '{"jsonrpc":"2.0","id":3,"error":{"code":-32602}}', 3)
    with pytest.raises(coros.CorosError):
        coros.parse_rpc_response("application/json", "<html>", 3)


# ── HRV scale in the fitness signal ─────────────────────────────────────────

def test_hrv_same_scale_follows_the_latest_day():
    vals = {D28: 40.0, D29: 42.0, D30: 85.0}
    assert hrv_same_scale(vals, {D30: "COROS"}) == {D30: 85.0}
    assert hrv_same_scale(vals, {D28: "COROS"}) == {D29: 42.0, D30: 85.0}
    assert hrv_same_scale({}, {}) == {}


async def test_form_never_compares_coros_to_an_apple_baseline(db_session: AsyncSession, test_user: User):
    today = date.today()
    pf = {"method": "ln_mean_main"}
    for k in range(67):
        d = today - timedelta(days=k)
        coros_day = k < 5
        db_session.add(HealthMetric(user_id=test_user.id, date=d, metric="hrv", n_samples=1,
                                    value=80.0 + (k % 3) if coros_day else 40.0 + (k % 3),
                                    source="COROS" if coros_day else None, details=pf if coros_day else None))
        db_session.add(HealthMetric(user_id=test_user.id, date=d, metric="hr_night", value=50 + (k % 2),
                                    n_samples=1))
    await db_session.flush()
    form = await current_form(db_session, test_user.id, today)
    # 5 COROS days: too few for a COROS baseline, and the Apple days are left out
    assert 80 <= form["hrv_7d"] <= 82 and form["hrv_baseline"] is None and form["hrv_delta_pct"] is None
    assert form["status"] == "ok" and not any("VFC" in r for r in form["reasons"])

    # once COROS covers the baseline too (the 60-day backfill), it's COROS vs COROS
    rows = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "hrv"))).scalars().all()
    for m in rows:
        m.source, m.value, m.details = "COROS", 80.0 + (today - m.date).days % 3, pf
    await db_session.flush()
    form = await current_form(db_session, test_user.id, today)
    assert 80 <= form["hrv_baseline"] <= 82 and abs(form["hrv_delta_pct"]) < 3

    # the watch's own average (rows written before 2026-10) is never read as PaceForge's
    for m in rows:
        m.details = None
    await db_session.flush()
    assert (await current_form(db_session, test_user.id, today))["hrv_7d"] is None


# ── a fake COROS (discovery, OAuth, MCP) ────────────────────────────────────

class FakeCoros:
    PRM = {"resource": "https://mcpus.coros.com/mcp", "authorization_servers": ["https://mcpus.coros.com"],
           "scopes_supported": ["openid", "mcp.tools", "offline_access"]}
    META = {"issuer": "https://mcpus.coros.com",
            "authorization_endpoint": "https://mcpus.coros.com/oauth2/authorize",
            "token_endpoint": "https://mcpus.coros.com/oauth2/token",
            "revocation_endpoint": "https://mcpus.coros.com/oauth2/revoke",
            "registration_endpoint": "https://mcpus.coros.com/connect/register",
            "code_challenge_methods_supported": ["S256"]}

    def __init__(self, shift_days: int = 0):
        self.texts = {name: _shift(t, shift_days) for name, t in (
            ("querySleepOverview", SLEEP_OVERVIEW), ("queryDailyHealthData", DAILY), ("queryAvgHeartRate", AVG_HR))}
        # the heart-rate text was captured on 10-05, the others on 09-30
        self.texts["queryAvgHeartRate"] = _shift(self.texts["queryAvgHeartRate"], -5)
        # the raw HRV readings of the two nights, on the shifted dates (UTC+9, as captured in Korea)
        ov = coros.parse_sleep_overview(self.texts["querySleepOverview"])
        self.texts["querySleepHrv"] = hrv_text({d: (n["start"], n["end"]) for d, n in ov.items()}, tz=36)
        self.n = 1
        self.access, self.refresh = "at-1", "rt-1"
        self.registrations = 0
        self.token_calls: list[dict] = []
        self.tool_calls: list[tuple[str, dict]] = []
        self.revoked: list[dict] = []
        self.challenge: str | None = None
        self.sse = False
        self.max_days: dict[str, int] = {}
        self.code_error: str | None = None
        self.refresh_error: str | None = None
        self.mcp_status: int | None = None
        self.timeouts: set[str] = set()  # tools whose call times out
        self.tool_status: dict[str, int] = {}  # tools answered with this HTTP status
        # sessions, as querySportRecords lists them: {label, code, type, name, start (UTC), seconds, km, hr}
        self.sessions: list[dict] = []
        self.laps = LAPS  # queryActivityLapData's answer (the owner's real JSON, its max HR 171)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url.endswith("/.well-known/oauth-protected-resource/mcp"):
            return httpx.Response(200, json=self.PRM)
        if url == "https://mcpus.coros.com/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=self.META)
        if url == self.META["registration_endpoint"]:
            self.registrations += 1
            body = json.loads(request.content)
            assert body["token_endpoint_auth_method"] == "none" and body["redirect_uris"]
            return httpx.Response(201, json={"client_id": f"cid-{self.registrations}"})
        if url == self.META["token_endpoint"]:
            return self._token({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        if url == self.META["revocation_endpoint"]:
            self.revoked.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
            return httpx.Response(401)
        if url == self.PRM["resource"]:
            return self._mcp(request)
        return httpx.Response(404)

    def _rotate(self) -> dict:
        self.n += 1
        self.access, self.refresh = f"at-{self.n}", f"rt-{self.n}"
        return {"access_token": self.access, "refresh_token": self.refresh, "expires_in": 30 * 86400,
                "token_type": "Bearer"}

    def _token(self, form: dict) -> httpx.Response:
        self.token_calls.append(form)
        assert form["resource"] == self.PRM["resource"]
        if form["grant_type"] == "authorization_code":
            if self.code_error:
                return httpx.Response(400, json={"error": self.code_error})
            digest = hashlib.sha256(form["code_verifier"].encode()).digest()
            assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == self.challenge
            assert form["redirect_uri"].endswith("/coros/callback") and form["code"] == "the-code"
            return httpx.Response(200, json=self._rotate())
        if self.refresh_error or form["refresh_token"] != self.refresh:  # rotated: the old one is dead
            return httpx.Response(400, json={"error": self.refresh_error or "invalid_grant"})
        return httpx.Response(200, json=self._rotate())

    def _mcp(self, request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(200)
        if request.headers.get("authorization") != f"Bearer {self.access}":
            return httpx.Response(401)
        if self.mcp_status:
            return httpx.Response(self.mcp_status)
        msg = json.loads(request.content)
        if msg["method"] == "notifications/initialized":
            assert request.headers["mcp-session-id"] == "sess-1"
            return httpx.Response(202)
        if msg["method"] == "initialize":
            assert msg["params"]["protocolVersion"] == "2025-06-18"
            assert msg["params"]["clientInfo"]["name"] == "PaceForge"
            return self._reply(msg["id"], {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                               {"Mcp-Session-Id": "sess-1"})
        assert request.headers["mcp-session-id"] == "sess-1"
        assert "text/event-stream" in request.headers["accept"]
        name, args = msg["params"]["name"], msg["params"]["arguments"]
        self.tool_calls.append((name, args))
        if name in self.timeouts:
            raise httpx.ReadTimeout("timed out", request=request)
        if name in self.tool_status:
            return httpx.Response(self.tool_status[name])
        limit = self.max_days.get(name)
        if limit and args.get("days", 0) > limit:
            return self._reply(msg["id"], {"content": [{"type": "text", "text": f"days must be <= {limit}"}],
                                           "isError": True})
        text = (self.records(args) if name == "querySportRecords" else DETAIL if name == "getActivityDetail"
                else self.laps if name == "queryActivityLapData" else self.texts[name])
        return self._reply(msg["id"], {"content": [{"type": "text", "text": text}], "isError": False})

    def records(self, args: dict) -> str:
        """querySportRecords' text for the sessions inside [startDate, endDate] (the real format)."""
        lo, hi = (datetime.strptime(args[k], "%Y%m%d").date() for k in ("startDate", "endDate"))
        picked = [x for x in self.sessions if lo <= x["start"].date() <= hi]
        out = [f"Sport Records — {lo} to {hi} ({len(picked)} records)", "========================", ""]
        for i, x in enumerate(picked, 1):
            t0 = int(x["start"].timestamp())
            h, rem = divmod(x["seconds"], 3600)
            out += [f"{i}. {x.get('type', 'Outdoor Run')} — {x['start'].date()}",
                    f"   Location: {x.get('name', 'District de Namjeju Course')}",
                    f"   Time Window: startTimestamp={t0} | endTimestamp={t0 + x['seconds'] + 30}",
                    f"   Duration: {h}:{rem // 60:02d}:{rem % 60:02d} | Distance: {x['km']:.2f} km",
                    f"   Average Pace: 5:00 /km | Avg HR: {x.get('hr', 130)} bpm | Calories: 1,225 kcal",
                    f"   LabelId: {x['label']} | SportType: {x.get('code', 100)}", ""]
        return "\n".join(out)

    def _reply(self, rid, result, headers=None) -> httpx.Response:
        body = {"jsonrpc": "2.0", "id": rid, "result": result}
        if self.sse:
            return httpx.Response(200, headers={"content-type": "text/event-stream", **(headers or {})},
                                  content=f"event: message\ndata: {json.dumps(body)}\n\n".encode())
        return httpx.Response(200, json=body, headers=headers)


@pytest.fixture
def fake(monkeypatch) -> FakeCoros:
    f = FakeCoros(shift_days=(datetime.now(timezone.utc).date() - D30).days)
    monkeypatch.setattr(coros, "_transport", httpx.MockTransport(f.handler))
    monkeypatch.setattr(coros, "CALL_DELAY_S", 0)
    return f


@pytest.fixture
def no_commit(db_session: AsyncSession, monkeypatch):
    """The service commits (tokens must survive a later rollback): in tests,
    flush instead, so each test's rollback still cleans up."""
    monkeypatch.setattr(db_session, "commit", db_session.flush)


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User, no_commit):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:  # Secure session cookie
        yield c


@pytest.fixture
def queued(monkeypatch) -> list:
    """The syncs queued for the worker (app.tasks.coros_sync), instead of a broker."""
    from app.tasks.coros_sync import sync_coros_user

    jobs: list = []
    monkeypatch.setattr(sync_coros_user, "apply_async", lambda *, args, retry: jobs.append(args))
    return jobs


async def _work(db: AsyncSession, job: list):
    """What the worker does with a queued job, on the test's session."""
    from app.tasks import coros_sync

    @asynccontextmanager
    async def session():
        yield db
    with patch("app.database.get_task_session", session):
        return await coros_sync._run_one(*job)


async def _link(db: AsyncSession, user: User, access="at-1", refresh="rt-1", expires_in=timedelta(days=30),
                **extra) -> CorosConnection:
    conn = CorosConnection(
        user_id=user.id, issuer=FakeCoros.META["issuer"], client_id="cid-1",
        access_token_encrypted=encrypt_secret(access), refresh_token_encrypted=encrypt_secret(refresh),
        expires_at=datetime.now(timezone.utc) + expires_in, resource_url=FakeCoros.PRM["resource"],
        token_endpoint=FakeCoros.META["token_endpoint"],
        revocation_endpoint=FakeCoros.META["revocation_endpoint"], **extra)
    db.add(conn)
    await db.flush()
    return conn


# ── OAuth ───────────────────────────────────────────────────────────────────

async def test_connect_uses_the_chosen_region(as_user: AsyncClient, fake, monkeypatch):
    seen = []
    orig = fake.handler

    def spy(request):
        seen.append(str(request.url))
        return orig(request)
    monkeypatch.setattr(coros, "_transport", httpx.MockTransport(spy))
    await as_user.get("/coros/connect?region=europe")
    assert seen[0] == "https://mcpeu.coros.com/.well-known/oauth-protected-resource/mcp"
    seen.clear()
    await as_user.get("/coros/connect?region=nowhere")  # unknown: the last choice stays
    assert seen[0].startswith("https://mcpeu.coros.com/")


async def test_connect_redirects_to_coros_with_pkce_state_and_resource(as_user: AsyncClient, db_session: AsyncSession, fake):
    r = await as_user.get("/coros/connect")
    assert r.status_code == 302
    loc = urlsplit(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "https://mcpus.coros.com/oauth2/authorize"
    assert q["response_type"] == "code" and q["client_id"] == "cid-1"
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43
    assert len(q["state"]) >= 20 and q["resource"] == "https://mcpus.coros.com/mcp"
    assert q["scope"] == "openid mcp.tools offline_access" and "%20" in loc.query
    assert q["redirect_uri"] == coros.redirect_uri() and q["redirect_uri"].endswith("/coros/callback")
    # the client is registered once for the whole app, then reused
    await as_user.get("/coros/connect")
    assert fake.registrations == 1
    assert (await db_session.execute(select(func.count(OAuthClient.id)))).scalar() == 1


async def _authorize(c: AsyncClient, fake: FakeCoros) -> str:
    r = await c.get("/coros/connect")
    q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    fake.challenge = q["code_challenge"]
    return q["state"]


async def test_callback_stores_encrypted_tokens_and_starts_the_sync(as_user: AsyncClient, db_session: AsyncSession,
                                                                    test_user: User, fake, monkeypatch):
    scheduled = []
    monkeypatch.setattr(coros, "schedule_sync", scheduled.append)
    state = await _authorize(as_user, fake)
    r = await as_user.get(f"/coros/callback?code=the-code&state={state}")
    assert r.status_code == 303 and r.headers["location"] == "/settings#coros"
    conn = (await db_session.execute(select(CorosConnection))).scalar_one()
    assert conn.user_id == test_user.id and scheduled == [test_user.id]
    assert b"at-2" not in conn.access_token_encrypted and decrypt_secret(conn.access_token_encrypted) == "at-2"
    assert decrypt_secret(conn.refresh_token_encrypted) == "rt-2"
    assert conn.resource_url == "https://mcpus.coros.com/mcp" and conn.token_endpoint.endswith("/oauth2/token")
    assert conn.expires_at is not None and not conn.needs_reauth
    page = (await as_user.get("/settings")).text
    # Réglages manage the link and sync on demand (Santé shows no sync status: owner, 2026-10-08)
    assert "Connecté" in page and "Première synchro en cours" in page and "Synchroniser maintenant" in page
    # the state is single use
    r = await as_user.get(f"/coros/callback?code=the-code&state={state}")
    assert "expiré" in (await as_user.get("/settings")).text


async def test_callback_rejects_a_wrong_state_or_a_refusal(as_user: AsyncClient, db_session: AsyncSession, fake):
    await _authorize(as_user, fake)
    r = await as_user.get("/coros/callback?code=the-code&state=forged")
    assert r.status_code == 303 and not fake.token_calls
    state = await _authorize(as_user, fake)
    await as_user.get(f"/coros/callback?error=access_denied&state={state}")
    assert "Connexion à COROS annulée" in (await as_user.get("/settings")).text
    assert (await db_session.execute(select(CorosConnection))).scalar_one_or_none() is None


async def test_invalid_client_registers_again_once(as_user: AsyncClient, db_session: AsyncSession, fake):
    fake.code_error = "invalid_client"
    state = await _authorize(as_user, fake)
    r = await as_user.get(f"/coros/callback?code=the-code&state={state}")
    assert r.status_code == 302 and r.headers["location"] == "/coros/connect"
    assert (await db_session.execute(select(func.count(OAuthClient.id)))).scalar() == 0
    state = await _authorize(as_user, fake)  # the retry: a fresh client
    assert fake.registrations == 2
    r = await as_user.get(f"/coros/callback?code=the-code&state={state}")
    assert r.status_code == 303 and "a échoué" in (await as_user.get("/settings")).text  # no loop


async def test_refresh_rotates_and_persists_the_new_refresh_token(db_session: AsyncSession, test_user: User,
                                                                 fake, no_commit):
    conn = await _link(db_session, test_user, expires_in=timedelta(hours=2))  # < 1 day left
    async with coros.http_client() as client:
        assert await coros.access_token(db_session, conn, client) == "at-2"
        assert fake.token_calls[-1]["refresh_token"] == "rt-1" and fake.token_calls[-1]["client_id"] == "cid-1"
        assert decrypt_secret(conn.refresh_token_encrypted) == "rt-2"
        # the server rejected a token another worker already replaced: no second refresh
        assert await coros.access_token(db_session, conn, client, rejected="at-1") == "at-2"
        assert len(fake.token_calls) == 1
        # rejected for real: refresh with the rotated token, not the dead one
        assert await coros.access_token(db_session, conn, client, rejected="at-2") == "at-3"
        assert fake.token_calls[-1]["refresh_token"] == "rt-2"
    assert decrypt_secret(conn.refresh_token_encrypted) == "rt-3"


async def test_invalid_grant_asks_to_reconnect(as_user: AsyncClient, db_session: AsyncSession, test_user: User, fake):
    conn = await _link(db_session, test_user, expires_in=timedelta(hours=-1))
    fake.refresh_error = "invalid_grant"
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"] is False and conn.needs_reauth and "reconnecte-toi" in conn.last_error
    assert conn.sync_claimed_at is None and not fake.tool_calls
    page = (await as_user.get("/settings")).text
    assert "À reconnecter" in page and "Reconnecter" in page and "Synchroniser maintenant" not in page
    assert await coros.run_sync(db_session, conn) is None  # no more tries until reconnected


# ── sync ────────────────────────────────────────────────────────────────────

async def test_first_sync_backfills_then_last_week(db_session: AsyncSession, test_user: User, fake, no_commit):
    fake.max_days = {"queryDailyHealthData": 7}  # refuse 60: fall back to 7
    conn = await _link(db_session, test_user)
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"], outcome
    hrv_calls = [a for n, a in fake.tool_calls if n == "querySleepHrv"]
    assert len(hrv_calls) == 9 and all(a["days"] <= 7 for a in hrv_calls)  # 60 days, 7 per call
    assert [a for n, a in fake.tool_calls if n == "queryDailyHealthData"] == [{"days": 60}, {"days": 7}]
    assert len([1 for n, _ in fake.tool_calls if n == "querySleepOverview"]) == 2
    assert [a for n, a in fake.tool_calls if n == "queryAvgHeartRate"] == [{"days": 60}]
    # no brand value is asked for
    assert not {n for n, _ in fake.tool_calls} & {"queryRestingHeartRate", "queryTrainingLoadAssessment",
                                                 "queryRecoveryStatus", "queryFitnessAssessmentOverview",
                                                 "queryStressTimeSeries"}
    assert len(fake.tool_calls) <= coros.MAX_CALLS
    assert conn.last_sync_at is not None and conn.last_error == coros.PARTIAL and conn.sync_claimed_at is None

    today = datetime.now(timezone.utc).date()
    rows = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.date == today))).scalars().all()
    by = {m.metric: m for m in rows}
    assert set(by) == {"sleep", "hrv", "hr_night", "steps"} and all(m.source == "COROS" for m in rows)
    y = (today - timedelta(days=1)).isoformat()
    # its stage minutes from the « Sleep Summary » of the main sleep (total 10h02 = the main period), never the
    # « daily » ratios; marked read (the 60 days are read again once for nights written before)
    assert by["sleep"].value == 588 and by["sleep"].details == {
        "main_start": f"{y}T23:52", "main_end": f"{today}T09:54", "period": 602, "bedtime": "23:52",
        "wake": "09:54", "timeline": False, "tz": 540, "daily": 588,
        "stages": {"deep": 76, "light": 343, "rem": 169, "awake": 14}, "stages_read": True, "naps_folded": True}
    hrv = by["hrv"]  # PaceForge's: the readings inside the window, the afternoon ones out
    assert 70 <= hrv.value <= 88 and hrv.details == {"n": 60, "tz": 540, "method": "ln_mean_main"}
    assert by["hr_night"].value == 35 and by["hr_night"].details == {
        "min": 30, "max": 52, "method": "coros_sleep_summary", "nap_day": False}
    assert by["steps"].value == 1487 and by["steps"].details == {"kcal": 92, "exercise": 0}
    nap = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "nap"))).scalar_one()
    d27 = today - timedelta(days=3)  # the nap-only day stays a nap, never a night
    assert nap.date == d27 and nap.value == 82 and nap.details == {
        "period": 89, "windows": [[f"{d27}T01:23", f"{d27}T02:52"]]}
    assert (await db_session.execute(select(func.count(HealthMetric.id)).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "sleep", HealthMetric.date == d27))).scalar() == 0
    hr = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "hr_day"))).scalars().all()
    assert {m.date: (m.value, m.details["max"]) for m in hr}[today - timedelta(days=1)] == (53, 98)
    # no stage interval is written (COROS has no timeline)
    assert (await db_session.execute(select(func.count(HealthSample.id)).where(
        HealthSample.user_id == test_user.id))).scalar() == 0

    fake.tool_calls.clear()
    fake.max_days.clear()  # finish the historical daily details before moving to the recent week
    fake.sse = True  # same answers as server-sent events
    conn.last_sync_at -= timedelta(days=1)  # the next day: the history owed is read whole again
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"] and outcome["result"]["inserted"] == 0 and outcome["result"]["updated"] == 0
    assert [a for nm, a in fake.tool_calls if nm == "queryAvgHeartRate"] == [{"days": 60}]
    assert conn.last_error is None
    fake.tool_calls.clear()
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert [a for nm, a in fake.tool_calls if nm == "queryAvgHeartRate"] == [{"days": 7}]
    assert len([1 for nm, _ in fake.tool_calls if nm == "querySleepHrv"]) == 1

    # COROS revises a night: the row is rewritten, not doubled
    fake.texts["querySleepOverview"] = fake.texts["querySleepOverview"].replace(" 09:54", " 09:40")
    await coros.run_sync(db_session, conn)
    sleep = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.date == today, HealthMetric.metric == "sleep"))).scalar_one()
    assert sleep.details["main_end"] == f"{today}T09:40" and sleep.details["wake"] == "09:40"


async def test_a_resync_deletes_the_stale_nap_row_only(db_session: AsyncSession, test_user: User, fake, no_commit,
                                                       monkeypatch):
    """Owner's report 2026-10-09: a nap an earlier sync stored (before COROS had the night) stayed shown over the
    night, store_daily never deleting a row a later sync no longer yields. A re-sync deletes it; another watch's
    rows, a day COROS called « not available yet » and the days this sync did not read stay; a sync whose
    overview answer it could not read deletes nothing."""
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]  # the 60 days
    today = datetime.now(timezone.utc).date()
    y = today - timedelta(days=1)

    async def seed():
        for day, value, source in ((today, 57, "COROS"), (y, 40, "Garmin"), (today - timedelta(days=2), 30, "COROS"),
                                   (today - timedelta(days=20), 20, "COROS")):
            db_session.add(HealthMetric(user_id=test_user.id, date=day, metric="nap", value=value, source=source,
                                        n_samples=1, details={"windows": [[f"{y}T23:00", f"{today}T00:10"]]}))
        await db_session.flush()

    async def naps():
        return {(m.date, m.source) for m in (await db_session.execute(select(HealthMetric).where(
            HealthMetric.user_id == test_user.id, HealthMetric.metric == "nap"))).scalars()}

    await seed()
    d27 = today - timedelta(days=3)  # COROS's own nap-only day
    # the overview answer not understood: nothing counts as read, nothing is deleted
    parse_naps = coros.parse_naps
    monkeypatch.setattr(coros, "parse_naps", lambda text: 1 / 0)
    assert (await coros.run_sync(db_session, conn))["result"]["deleted"] == 0
    monkeypatch.setattr(coros, "parse_naps", parse_naps)
    outcome = await coros.run_sync(db_session, conn)  # the last 7 days
    assert outcome["ok"] and outcome["result"]["deleted"] == 1
    assert await naps() == {(y, "Garmin"), (today - timedelta(days=2), "COROS"), (today - timedelta(days=20), "COROS"),
                            (d27, "COROS")}
    sleep = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.date == today, HealthMetric.metric == "sleep"))).scalar_one()
    assert sleep.value == 588 and sleep.details["naps_folded"]  # the night itself untouched


async def _values(db: AsyncSession, user: User) -> dict:
    """{metric: {day: value}} of the user's COROS nightly rows."""
    out: dict = {}
    for m in (await db.execute(select(HealthMetric).where(
            HealthMetric.user_id == user.id, HealthMetric.source == "COROS",
            HealthMetric.metric.in_(("sleep", "nap", "hrv", "hr_night"))))).scalars():
        out.setdefault(m.metric, {})[m.date] = m.value
    return out


async def test_a_resync_whose_answers_read_differently_deletes_nothing(db_session: AsyncSession, test_user: User,
                                                                       fake, no_commit):
    """Reviewers' probes (2026-10-09): a day counts as read only when its parser read it. An HRV series renamed,
    an empty HRV answer, readings gone from the series, « Sleep summary » in lower case, the window's dates
    written with slashes: the updates stop, as on main, and nothing is deleted — not even held back by the
    safety net (nothing is judged stale)."""
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]  # the 60 days
    before = await _values(db_session, test_user)
    assert set(before) == {"sleep", "nap", "hrv", "hr_night"}
    texts = dict(fake.texts)
    for tool, change in (
            ("querySleepHrv", lambda t: t.replace("Sleep HRV Time Series", "Sleep HRV Readings")),
            ("querySleepHrv", lambda t: "Sleep HRV\nNo readings for this period.\n"),
            ("querySleepHrv", lambda t: re.sub(r"(?m)^\s*timestamp=.*\n", "", t)),
            ("queryDailyHealthData", lambda t: t.replace("Sleep Summary", "Sleep summary")),
            ("querySleepOverview", lambda t: re.sub(r"Window: (\d{4})-(\d{2})-(\d{2}) (\S+) - (\d{4})-(\d{2})-(\d{2})",
                                                    r"Window: \1/\2/\3 \4 - \5/\6/\7", t))):
        fake.texts = {**texts, tool: change(texts[tool])}
        out = await coros.run_sync(db_session, conn)
        assert out["ok"] and (out["result"]["deleted"], out["result"]["stale_kept"]) == (0, 0), tool
        assert await _values(db_session, test_user) == before, tool


async def test_a_night_keeps_its_nap_whatever_a_later_sync_reads(db_session: AsyncSession, test_user: User, fake,
                                                                 no_commit, monkeypatch):
    """Reviewers' findings 2 and 6 (2026-10-09): a nap ending before midnight is listed under the day before.
    Folded when stored, a first sync deleted its row and a later one whose window starts on the night's day (the
    7-day window slides) or whose parse_naps failed rewrote the night without it: its minutes lost. Stored as
    COROS gives them and folded when read, the night keeps them."""
    from app.services import nights as nt
    today = datetime.now(timezone.utc).date()
    y, yy = today - timedelta(days=1), today - timedelta(days=2)
    both = f"""Sleep Overview
========================

{y}
Sleep Score: 80
Daily Sleep: 8h 10min (incl. naps)
Main Sleep (asleep): 7h 50min
Main Sleep Period (incl. awake): 8h 0min
Main Sleep Window: {yy} 23:00 - {y} 07:00
Naps Total (asleep): 20 min
Naps Period (incl. awake): 25 min
Nap Window: {y} 22:40 - {y} 23:05

{today}
Sleep Score: 80
Daily Sleep: 7h 20min (incl. naps)
Main Sleep (asleep): 7h 20min
Main Sleep Period (incl. awake): 7h 30min
Main Sleep Window: {y} 23:30 - {today} 07:00
Naps Total: 0 min
"""
    alone = both[both.index(f"{today}\n"):]  # the window starts on the night's day: the day before not read

    async def last_night():
        rows = await nt.read_rows(db_session, test_user.id, today - timedelta(days=10), today, nt.SANTE_ROWS)
        n = nt.build_nights(rows, today)[today]
        return n.start, n.asleep, nt.day_tst24(nt.build_nights(rows, today), today)

    fake.texts["querySleepOverview"] = both
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]
    folded = (datetime.combine(y, datetime.min.time()).replace(hour=22, minute=40), 460, 460)
    assert await last_night() == folded and (await _values(db_session, test_user))["nap"] == {y: 20}
    fake.texts["querySleepOverview"] = alone
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert await last_night() == folded and (await _values(db_session, test_user))["sleep"][today] == 440
    fake.texts["querySleepOverview"] = both
    monkeypatch.setattr(coros, "parse_naps", lambda text: 1 / 0)
    out = await coros.run_sync(db_session, conn)
    assert out["ok"] and out["result"]["deleted"] == 0
    assert await last_night() == folded and (await _values(db_session, test_user))["nap"] == {y: 20}


async def test_expired_mcp_token_is_refreshed_once(db_session: AsyncSession, test_user: User, fake, no_commit):
    conn = await _link(db_session, test_user, access="at-0")  # looks valid, but the server says no
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"] and decrypt_secret(conn.access_token_encrypted) == "at-2"
    assert [c["grant_type"] for c in fake.token_calls] == ["refresh_token"]


async def test_sync_failure_is_shown_in_plain_french(as_user: AsyncClient, db_session: AsyncSession, test_user: User, fake):
    conn = await _link(db_session, test_user)
    fake.mcp_status = 503
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"] is False and conn.last_error == "COROS ne répond pas pour l'instant (erreur 503)."
    assert not conn.needs_reauth and conn.last_sync_at is None
    assert "Dernière synchro échouée" in (await as_user.get("/settings")).text


async def test_coros_going_down_mid_sync_keeps_the_nights(db_session: AsyncSession, test_user: User,
                                                          fake, no_commit):
    """The nights come first; when COROS then stops answering (3 timeouts or 5xx in a row), the
    sync stops calling and keeps what arrived instead of throwing it all away. The history it
    cut short is owed: said so, and asked for in full on the next sync."""
    fake.tool_status = {"queryDailyHealthData": 503}  # a 5xx counts toward the outage like a timeout
    fake.timeouts = set(fake.texts) - {"querySleepOverview", "querySleepHrv", "queryDailyHealthData"}
    conn = await _link(db_session, test_user)
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"], outcome
    today = datetime.now(timezone.utc).date()
    rows = (await db_session.execute(select(HealthMetric).where(HealthMetric.user_id == test_user.id))).scalars().all()
    by = {(m.metric, m.date): m.value for m in rows}
    assert by[("sleep", today)] == 588 and ("hrv", today) in by
    lost = [n for n, _ in fake.tool_calls if n in fake.timeouts or n in fake.tool_status]
    assert len(lost) == 3  # then it stopped calling
    assert conn.last_sync_at is not None and conn.last_error == coros.PARTIAL

    fake.timeouts, fake.tool_status = set(), {}
    before = len(fake.tool_calls)
    conn.last_sync_at -= timedelta(days=1)  # the next day: the history owed is read whole again
    assert (await coros.run_sync(db_session, conn))["ok"]
    again = fake.tool_calls[before:]
    assert {"days": 60} in [a for n, a in again if n == "queryAvgHeartRate"]  # the whole history
    assert conn.last_error is None
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert [a for n, a in fake.tool_calls if n == "queryAvgHeartRate"][-1] == {"days": 7}  # then a week


async def test_a_timeout_or_a_5xx_loses_that_call_not_the_night(db_session: AsyncSession, test_user: User,
                                                                 fake, no_commit):
    fake.tool_status = {"queryAvgHeartRate": 502}
    conn = await _link(db_session, test_user)
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"], outcome
    assert [a for n, a in fake.tool_calls if n == "queryAvgHeartRate"] == [{"days": 60}, {"days": 7}]
    names = [n for n, _ in fake.tool_calls]
    assert "queryDailyHealthData" in names and names[-1] == "querySportRecords"  # the sync went on to the end
    today = datetime.now(timezone.utc).date()
    rows = (await db_session.execute(select(HealthMetric).where(HealthMetric.user_id == test_user.id))).scalars().all()
    by = {(m.metric, m.date): m.value for m in rows}
    assert by[("sleep", today)] == 588 and by[("hr_night", today)] == 35 and ("hrv", today) in by
    assert not any(m == "hr_day" for m, _ in by)

    # COROS down: the sync stops after 3 lost calls in a row, and says so
    fake.timeouts = set(fake.texts)
    before = len(fake.tool_calls)
    outcome = await coros.run_sync(db_session, conn)
    assert outcome == {"ok": False, "error": "COROS ne répond pas pour l'instant."}
    assert len(fake.tool_calls) - before == 3  # not one 30 s timeout per tool


@pytest.mark.parametrize("days", [7, 60])
async def test_failed_sleep_overview_retries_recent_window_and_recovers_oct13_hrv(monkeypatch, days):
    """Raw COROS points cannot produce a VFC without their actual sleep window."""
    today = date(2026, 10, 13)
    texts = FakeCoros(shift_days=(today - D30).days).texts
    calls = []
    monkeypatch.setattr(coros, "CALL_DELAY_S", 0)

    class MCP:
        async def initialize(self):
            pass

        async def call_tool(self, name, args):
            calls.append((name, args))
            if name == "querySleepOverview":
                a, b = [datetime.strptime(args[k], "%Y%m%d").date() for k in ("startDate", "endDate")]
                if (b - a).days > 3:
                    raise coros.McpUnavailable("Synthetic sleep-tool outage")
            return texts[name]

    data, _, _, stopped = await coros._fetch(MCP(), days, today)
    sleeps = [args for name, args in calls if name == "querySleepOverview"]
    assert len(sleeps) == (3 if days == 60 else 2)  # one recent retry, never a retry per historical chunk
    assert sleeps[-1] == {"startDate": "20261011", "endDate": "20261014"}
    assert [name for name, _ in calls][:4] == (["querySleepOverview", "querySleepOverview", "querySleepHrv",
                                               "querySleepOverview"] if days == 60 else
                                              ["querySleepOverview", "querySleepHrv", "querySleepOverview",
                                               "queryAvgHeartRate"])
    assert not stopped  # the sleep tool alone being down cannot hide an available HRV series
    latest = next(r for r in coros.build_daily(data, today) if r.metric == "hrv" and r.day == today)
    assert latest.details["n"] == 60 and latest.details["method"] == coros.HRV_METHOD
    assert {today, today - timedelta(days=1)} <= data["overview_days"]
    assert not {today, today - timedelta(days=1)} & data["overview_failed_days"]
    assert data["overview_failed_days"]  # the unread older range still needs another sync


@pytest.mark.parametrize("backfill", [False, True])
async def test_persistent_sleep_failure_preserves_values_and_is_retryable_partial(db_session, test_user, fake,
                                                                                no_commit, backfill):
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]
    before = await _values(db_session, test_user)
    if backfill:
        conn.last_error = coros.PARTIAL  # an unfinished historical import is still owed
        conn.last_sync_at -= timedelta(days=1)  # the next day: the history owed is read whole again
    fake.tool_status["querySleepOverview"] = 503
    fake.tool_calls.clear()
    result = await coros.run_sync(db_session, conn)
    assert result["ok"] and result["result"]["sleep_incomplete"]
    assert not result["result"]["hrv_incomplete"]  # raw readings arrived, their dependency did not
    assert conn.last_error == (coros.PARTIAL if backfill else coros.SLEEP_PARTIAL)
    assert await _values(db_session, test_user) == before
    assert result["result"]["deleted"] == 0
    assert len([1 for name, _ in fake.tool_calls if name == "querySleepOverview"]) == (3 if backfill else 2)
    assert any(name == "queryDailyHealthData" for name, _ in fake.tool_calls)
    status = await coros.coros_status(db_session, test_user.id)
    assert status["partial"] and status["retryable_partial"] and status["last_sync_at"] is not None
    fake.tool_status.clear()
    if backfill:  # the history still owed comes with the next day's whole read, never a week's read
        assert (await coros.run_sync(db_session, conn))["ok"] and conn.last_error == coros.PARTIAL
        conn.last_sync_at -= timedelta(days=1)
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert conn.last_error is None  # a later successful read clears the partial failure


@pytest.mark.parametrize("text", ["", "Sleep Overview\nNo data for this period.\n",
    "Sleep Overview\n2026-10-13\nSleep detail for this day is not available yet.\n"])
async def test_empty_sleep_overview_does_not_retry_or_invent_hrv(db_session, test_user, fake, no_commit, text):
    conn = await _link(db_session, test_user)
    fake.texts["querySleepOverview"] = text
    result = await coros.run_sync(db_session, conn)
    assert result["ok"] and not result["result"]["sleep_incomplete"]
    assert conn.last_error is None
    assert not {"sleep", "hrv"} & set(await _values(db_session, test_user))
    assert len([1 for name, _ in fake.tool_calls if name == "querySleepOverview"]) == 2  # 60-day import, no retries


@pytest.mark.parametrize("first_sync", [False, True])
async def test_latest_hrv_retries_a_failed_wide_request_once(db_session, test_user, fake, no_commit, monkeypatch,
                                                           first_sync):
    conn = await _link(db_session, test_user)
    if not first_sync:
        assert (await coros.run_sync(db_session, conn))["ok"]
    today = datetime.now(timezone.utc).date()
    await db_session.execute(sa.delete(HealthMetric).where(HealthMetric.metric == "hrv"))
    original = coros.McpSession.call_tool

    async def short_only(mcp, tool, args):
        if tool == "querySleepHrv" and args["days"] > 2:
            fake.tool_calls.append((tool, args))
            raise coros.McpUnavailable("Temporary COROS failure")
        return await original(mcp, tool, args)

    monkeypatch.setattr(coros.McpSession, "call_tool", short_only)
    fake.tool_calls.clear()
    result = await coros.run_sync(db_session, conn)
    hrv_calls = [a for n, a in fake.tool_calls if n == "querySleepHrv"]
    assert result["ok"] and [a["days"] for a in hrv_calls] == ([7, 2, 7, 7, 7] if first_sync else [7, 2])
    assert hrv_calls[1]["startDate"] == (today - timedelta(days=1)).strftime("%Y%m%d")
    assert hrv_calls[1]["endDate"] == today.strftime("%Y%m%d")
    assert today in (await _values(db_session, test_user))["hrv"]
    # The latest value recovered; the unread remainder is still reported honestly.
    assert conn.last_error == (coros.PARTIAL if first_sync else coros.HRV_PARTIAL) and conn.last_sync_at is not None
    status = await coros.coros_status(db_session, test_user.id)
    assert status["retryable_partial"] and status["partial"]
    from app.routers.sante import STALE_NIGHT, _sync_stale
    scheduled = []
    monkeypatch.setattr(coros, "schedule_sync", lambda uid: scheduled.append(uid) or True)
    assert not _sync_stale(test_user.id, [(coros, status)], STALE_NIGHT)
    conn.last_sync_at = datetime.now(timezone.utc) - STALE_NIGHT - timedelta(minutes=1)
    await db_session.flush()
    assert _sync_stale(test_user.id, [(coros, await coros.coros_status(db_session, test_user.id))], STALE_NIGHT)
    assert scheduled == [test_user.id]


async def test_unreadable_hrv_preserves_values_and_reports_partial_sync(db_session, test_user, fake, no_commit,
                                                                        as_user):
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]
    before = (await _values(db_session, test_user))["hrv"]
    fake.texts["querySleepHrv"] = fake.texts["querySleepHrv"].replace("Sleep HRV Time Series", "Sleep HRV Readings")
    fake.tool_calls.clear()
    result = await coros.run_sync(db_session, conn)
    assert result["ok"] and result["result"]["hrv_unreadable"]
    assert conn.last_error == coros.HRV_UNREADABLE
    assert (await _values(db_session, test_user))["hrv"] == before
    assert [a["days"] for n, a in fake.tool_calls if n == "querySleepHrv"] == [7, 2]
    status = await coros.coros_status(db_session, test_user.id)
    assert status["partial"] and not status["retryable_partial"]
    assert "Synchro incomplète" in (await as_user.get("/settings")).text


@pytest.mark.parametrize("text", [
    "Sleep HRV\nNo readings for this period.\n",
    "Sleep HRV Time Series\n================\n",
    "HRV Assessment\n2026-10-13:\n  HRV Avg: 83 ms — Normal\n",
])
async def test_empty_hrv_and_assessment_without_raw_points_are_not_sync_failures(db_session, test_user, fake,
                                                                               no_commit, text):
    conn = await _link(db_session, test_user)
    fake.texts["querySleepHrv"] = text
    result = await coros.run_sync(db_session, conn)
    assert result["ok"] and not result["result"]["hrv_incomplete"]
    assert conn.last_error is None
    assert "hrv" not in await _values(db_session, test_user)  # never substitute COROS's arithmetic average
    assert len([a for n, a in fake.tool_calls if n == "querySleepHrv"]) == 9  # no extra retries
    status = await coros.coros_status(db_session, test_user.id)
    assert "pas encore reçue" in status["hrv_note"]


async def test_latest_hrv_status_uses_accepted_measurement_date_not_sync_or_other_watch(db_session, test_user,
                                                                                     no_commit):
    today = datetime.now(timezone.utc).date()
    await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc))
    for metric, day, source, details in (
        ("sleep", today, "COROS", {}),
        ("hrv", today, "COROS", {}),  # legacy vendor average, not PaceForge's nightly RMSSD
        ("hrv", today - timedelta(days=1), "COROS", {"method": coros.HRV_METHOD}),
        ("hrv", today, "Garmin", {"method": coros.HRV_METHOD}),
    ):
        db_session.add(HealthMetric(user_id=test_user.id, metric=metric, date=day, value=80,
                                   source=source, details=details))
    await db_session.flush()
    status = await coros.coros_status(db_session, test_user.id)
    assert f"nuit du {today.strftime('%d/%m')} pas encore reçue" in status["hrv_note"]
    assert f"Dernière VFC reçue : {(today - timedelta(days=1)).strftime('%d/%m/%Y')}" in status["hrv_note"]
    from app.services.sante import _night_state
    assert (await _night_state(db_session, test_user.id, today, {"COROS"}))["COROS"]["hrv_pending"]
    current = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.date == today,
        HealthMetric.metric == "hrv", HealthMetric.source == "COROS"))).scalar_one()
    current.details = {"method": coros.HRV_METHOD}
    await db_session.flush()
    assert not (await _night_state(db_session, test_user.id, today, {"COROS"}))["COROS"]["hrv_pending"]


async def test_a_claimed_sync_is_not_run_twice(db_session: AsyncSession, test_user: User, fake, no_commit):
    conn = await _link(db_session, test_user, sync_claimed_at=datetime.now(timezone.utc))
    assert await coros.run_sync(db_session, conn) is None and not fake.tool_calls
    conn.sync_claimed_at = datetime.now(timezone.utc) - timedelta(hours=1)  # a crashed worker's claim
    await db_session.flush()
    assert (await coros.run_sync(db_session, conn))["ok"]



async def test_a_sync_that_fails_at_once_lets_go_of_the_link(db_session: AsyncSession, test_user: User, fake,
                                                              no_commit, monkeypatch):
    """COROS down before the sync touched the database: the claim is released, so « Synchroniser
    maintenant » pressed again is a new try, not « déjà en cours » for the claim's 15 min."""
    conn = await _link(db_session, test_user)
    await db_session.refresh(conn)  # as read for a request: sync_claimed_at loaded (None)

    async def down(db, c):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(coros, "sync_connection", down)
    assert (await coros.run_sync(db_session, conn))["ok"] is False
    claimed = (await db_session.execute(
        select(CorosConnection.sync_claimed_at).where(CorosConnection.id == conn.id))).scalar_one()
    assert claimed is None
    assert (await coros.run_sync(db_session, conn)) is not None  # tried again

# ── routes ──────────────────────────────────────────────────────────────────

async def test_routes_require_login(client: AsyncClient):
    for method, path in (("GET", "/coros/connect"), ("GET", "/coros/callback?code=x&state=y"),
                         ("POST", "/sante/sync"), ("POST", "/settings/coros/disconnect")):
        r = await client.request(method, path)
        assert r.status_code == 303 and r.headers["location"] == "/auth/login", path


async def test_settings_block_manual_sync_and_disconnect(as_user: AsyncClient, db_session: AsyncSession,
                                                         test_user: User, fake, queued):
    page = (await as_user.get("/settings")).text
    assert "Tes nuits et tes activités arrivent automatiquement depuis ta montre COROS." in page
    assert 'href="/sante"' in page
    assert 'href="/coros/connect?region=monde"' in page and "Connecter COROS" in page

    await _link(db_session, test_user)
    # Santé: no sync status, no button (owner, 2026-10-08: « c'est affiché déjà dans Réglages »)
    sante = (await as_user.get("/sante")).text
    assert "Synchroniser maintenant" not in sante and "synchro" not in sante.lower()
    # Réglages: the button next to « Dernière synchro … »; its answer replaces the content of a persistent live
    # region (said to a screen reader), the button never swapped (its focus kept), its spinner on itself (UX4)
    settings_page = (await as_user.get("/settings")).text
    block = settings_page.split('id="coros"')[1].split('id="garmin"')[0]
    assert '<div id="coros-sync-state" class="pf-watch-state" role="status" aria-live="polite">' in block
    assert ('<button id="coros-sync-btn" type="button" hx-post="/settings/coros/sync" hx-target="#coros-sync-state" '
            'hx-swap="innerHTML"') in block and "hx-indicator" not in block and "hx-disabled-elt" not in block
    assert block.index("coros-sync-state") < block.index("coros-sync-btn") and "Synchroniser maintenant" in block
    r = await as_user.post("/settings/coros/sync")
    assert r.status_code == 200 and "HX-Refresh" not in r.headers and "<button" not in r.text
    # the request only queues it (the worker syncs) and the state follows it until it is done
    assert len(queued) == 1 and not fake.tool_calls
    assert 'hx-get="/settings/coros/sync" hx-trigger="every 3s"' in r.text and "Première synchro en cours…" in r.text
    assert (await _work(db_session, queued.pop()))["ok"]
    r = await as_user.get("/settings/coros/sync")
    assert "Dernière synchro <b class=\"text-gray-700\">à l&#39;instant</b>" in r.text and "every 3s" not in r.text
    r = await as_user.post("/settings/coros/sync")
    assert "Synchronisation en cours…" in r.text and 'hx-trigger="every 3s"' in r.text
    assert (await _work(db_session, queued.pop()))["ok"]
    assert "Synchronisation en cours…" not in (await as_user.get("/settings/coros/sync")).text
    settings_page = (await as_user.get("/settings")).text
    assert "Dernière synchro" in settings_page and settings_page.count("Synchroniser maintenant") == 1
    # a Santé page left open since v4 still posts /sante/sync: harmless, it reloads (without the button)
    r = await as_user.post("/sante/sync")
    assert r.status_code == 200 and r.headers.get("HX-Refresh") == "true"
    assert (await as_user.post("/settings/strava/sync")).status_code == 404

    n = (await db_session.execute(select(func.count(HealthMetric.id)).where(HealthMetric.user_id == test_user.id))).scalar()
    r = await as_user.post("/settings/coros/disconnect")
    assert r.status_code == 303 and r.headers["location"] == "/settings#coros"
    assert (await db_session.execute(select(CorosConnection))).scalar_one_or_none() is None
    assert fake.revoked and fake.revoked[0]["token_type_hint"] == "refresh_token"  # tried, even if refused
    n2 = (await db_session.execute(select(func.count(HealthMetric.id)).where(HealthMetric.user_id == test_user.id))).scalar()
    assert n == n2 > 0  # imported data stays
    page = (await as_user.get("/settings")).text
    assert "COROS déconnecté" in page and "Connecter COROS" in page


async def test_link_made_before_the_daily_values_backfills_them(db_session: AsyncSession, test_user: User,
                                                                fake, no_commit):
    """A link that already has its nights (synced before the daily values were
    read) gets the 60 days of load, steps… once."""
    conn = await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc) - timedelta(days=1))
    db_session.add(HealthSample(user_id=test_user.id, metric="hrv", kind="", source="COROS", value=80,
                                start_at=datetime.now() - timedelta(days=3), end_at=datetime.now() - timedelta(days=3)))
    await db_session.flush()
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert [a for n, a in fake.tool_calls if n == "queryAvgHeartRate"] == [{"days": 60}]
    fake.tool_calls.clear()
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=7)
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert [a for n, a in fake.tool_calls if n == "queryAvgHeartRate"] == [{"days": 7}]


# ── migration ───────────────────────────────────────────────────────────────

def test_migration_is_the_head_and_round_trips():
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    spec = importlib.util.spec_from_file_location("mig_coros", MIG)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.down_revision == "s3b4c5d6e7f8"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    assert mig.revision in {r.revision for r in ScriptDirectory.from_config(cfg).walk_revisions()}

    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255))"))
        c.execute(sa.text("INSERT INTO users (id, email) VALUES (1, 'a@b.c')"))
        with Operations.context(MigrationContext.configure(c)):
            mig.upgrade()
        insp = sa.inspect(c)
        assert {"coros_connections", "oauth_clients"} <= set(insp.get_table_names())
        cols = {col["name"] for col in insp.get_columns("coros_connections")}
        assert {"access_token_encrypted", "refresh_token_encrypted", "expires_at", "resource_url",
                "token_endpoint", "last_sync_at", "last_error", "needs_reauth"} <= cols
        row = ("INSERT INTO coros_connections (user_id, issuer, client_id, access_token_encrypted, resource_url, "
               "token_endpoint, connected_at) VALUES (1, 'i', 'c', x'00', 'r', 't', '2026-09-30 08:00:00')")
        c.execute(sa.text(row))
        with pytest.raises(sa.exc.IntegrityError):
            c.execute(sa.text(row))  # one link per athlete
        with Operations.context(MigrationContext.configure(c)):
            mig.downgrade()
        assert "coros_connections" not in sa.inspect(c).get_table_names()


async def test_sync_backfills_again_while_no_daily_value_arrived(db_session: AsyncSession, test_user: User, fake, no_commit):
    conn = await _link(db_session, test_user)
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(days=1)  # synced once, but read nothing
    await db_session.flush()
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"], outcome
    assert len([1 for n, _ in fake.tool_calls if n == "querySleepHrv"]) == 9  # the 60 days again


async def test_the_window_reaches_the_athletes_today_ahead_of_utc(db_session: AsyncSession, test_user: User,
                                                                   fake, no_commit):
    """In Asia this morning's night is dated « tomorrow » for the server: it is
    asked for too, and the window keeps its size."""
    conn = await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc) - timedelta(hours=3))
    db_session.add(HealthMetric(user_id=test_user.id, date=date.today(), metric="steps", value=1,
                                source="COROS", n_samples=1))
    await db_session.flush()
    assert (await coros.run_sync(db_session, conn))["ok"]
    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).strftime("%Y%m%d")
    sleep = [a for n, a in fake.tool_calls if n == "querySleepOverview"]
    assert sleep and sleep[0]["endDate"] == tomorrow
    hrv = [a for n, a in fake.tool_calls if n == "querySleepHrv"]
    assert len(hrv) == 1 and hrv[0]["endDate"] == tomorrow and hrv[0]["days"] == 7


def test_naps_are_read_apart_from_the_night():
    """The owner's real 7 Oct: a 5h50 night, then a 2h20 nap from 06:42 to 09:07."""
    text = """Sleep Overview
========================

2026-10-06
Sleep Score: 66
Main Sleep (asleep): 5h 33min
Main Sleep Window: 2026-10-06 01:01 - 2026-10-06 06:45
Naps Total: 0 min

2026-10-07
Sleep Score: 89
Daily Sleep: 8h 10min (incl. naps)
Main Sleep (asleep): 5h 50min
Main Sleep Period (incl. awake): 6h 3min
Main Sleep Window: 2026-10-06 23:35 - 2026-10-07 05:38
Naps Total (asleep): 2h 20min
Naps Period (incl. awake): 2h 25min
Nap Window: 2026-10-07 06:42 - 2026-10-07 09:07
"""
    naps = coros.parse_naps(text)
    assert list(naps) == [date(2026, 10, 7)]
    assert naps[date(2026, 10, 7)] == {"asleep": 140, "period": 145, "legacy": False,
                                       "windows": [(datetime(2026, 10, 7, 6, 42), datetime(2026, 10, 7, 9, 7))]}
    ov = coros.parse_sleep_overview(text)
    assert ov[date(2026, 10, 7)]["asleep"] == 350  # the night alone
    [d] = coros.nap_dailies(naps, ov)
    assert (d.metric, d.day, d.value, d.details) == ("nap", date(2026, 10, 7), 140, {
        "period": 145, "windows": [["2026-10-07T06:42", "2026-10-07T09:07"]]})


# ── v4.1: the sleep stages, the re-read, Réglages' sync ─────────────────────

async def _latest_sleep(db, user, day=None):
    return (await db.execute(select(HealthMetric).where(
        HealthMetric.user_id == user.id, HealthMetric.source == "COROS", HealthMetric.metric == "sleep",
        HealthMetric.date == (day or datetime.now(timezone.utc).date())))).scalar_one()


@pytest.mark.parametrize("failure", ["503", "renamed_summary", "renamed_total", "renamed_light_and_no_hr"])
async def test_unread_daily_details_keep_phases_visible_then_accept_corrections_and_explicit_empty(
        db_session, test_user, fake, no_commit, as_user, failure):
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]
    row = await _latest_sleep(db_session, test_user)
    original = dict(row.details["stages"])
    text = fake.texts["queryDailyHealthData"]
    if failure == "503":
        fake.tool_status["queryDailyHealthData"] = 503
    elif failure == "renamed_summary":
        fake.texts["queryDailyHealthData"] = text.replace("Sleep Summary", "Sleep summary")
    elif failure == "renamed_total":
        fake.texts["queryDailyHealthData"] = text.replace("Total:", "Duration:")
    else:
        fake.texts["queryDailyHealthData"] = re.sub(r"Sleep HR:[^\n]*", "Sleep HR: No data",
                                                    text.replace("Light:", "Light Sleep:"))
    result = await coros.run_sync(db_session, conn)
    assert result["ok"] and result["result"]["phases_incomplete"]
    assert conn.last_error == coros.PHASES_PARTIAL
    assert row.details["stages"] == original and row.details["stages_read"]
    assert (await coros.coros_status(db_session, test_user.id))["retryable_partial"]
    html = (await as_user.get("/sante")).text
    assert 'class="pf-phases-bar"' in html and 'aria-label="Phases de la nuit"' in html
    fake.tool_status.clear()
    fake.texts["queryDailyHealthData"] = text.replace("Deep: 1h 16min | Light: 5h 43min",
                                                       "Deep: 1h 6min | Light: 5h 53min")
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert row.details["stages"] == {**original, "deep": 66, "light": 353}
    assert conn.last_error is None
    fake.texts["queryDailyHealthData"] = "Daily Health Data\nNo data for this period.\n"
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert "stages" not in row.details and row.details["stages_read"]
    assert conn.last_error is None


@pytest.mark.parametrize("before,after", [("23:52", "23:42"), ("09:54", "10:04"),
    ("Main Sleep Period (incl. awake): 10h 2min", "Main Sleep Period (incl. awake): 10h 12min"),
    ("Main Sleep (asleep): 9h 48min", "Main Sleep (asleep): 9h 38min")])
async def test_unread_phases_are_not_attached_to_a_corrected_episode(db_session, test_user, fake, no_commit,
                                                                   before, after):
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]
    row = await _latest_sleep(db_session, test_user)
    assert row.details.get("stages")
    fake.texts["querySleepOverview"] = fake.texts["querySleepOverview"].replace(before, after)
    fake.tool_status["queryDailyHealthData"] = 503
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert "stages" not in row.details and "stages_read" not in row.details


async def test_phase_preservation_is_scoped_to_user_source_and_day(db_session, test_user):
    today = datetime.now(timezone.utc).date()
    other = User(firstname="Other athlete"); db_session.add(other); await db_session.flush()
    details = {"main_start": f"{today - timedelta(days=1)}T23:00", "main_end": f"{today}T07:00", "period": 480}
    phases = {"deep": 60, "light": 300, "rem": 100, "awake": 20}
    for uid, source, day in [(other.id, "COROS", today), (test_user.id, "Garmin", today),
                             (test_user.id, "COROS", today - timedelta(days=1))]:
        db_session.add(HealthMetric(user_id=uid, source=source, date=day, metric="sleep", value=460,
                                   details={**details, "stages": phases, "stages_read": True}))
    await db_session.flush()
    rows = [coros.Daily("sleep", today, 460, dict(details))]
    await coros.preserve_unread_stages(db_session, test_user.id, rows, None)
    assert rows[0].details == details


async def test_daily_backfill_fallback_keeps_old_phases_but_history_stays_owed(db_session, test_user, fake,
                                                                           no_commit, monkeypatch):
    today = datetime.now(timezone.utc).date()
    old_day = today - timedelta(days=20)
    recent_daily = fake.texts["queryDailyHealthData"]
    fake.texts["querySleepOverview"] += "\n" + _shift(SLEEP_OVERVIEW, (old_day - D30).days)
    fake.texts["queryDailyHealthData"] += "\n" + _shift(DAILY, (old_day - D30).days)
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]
    row = await _latest_sleep(db_session, test_user, old_day)
    original = dict(row.details)
    conn.last_error = coros.PARTIAL
    conn.last_sync_at -= timedelta(days=1)  # the next day: the history owed is read whole again
    fake.max_days["queryDailyHealthData"] = 7
    raw_call = coros.McpSession.call_tool

    async def limited_daily(mcp, name, args):
        result = await raw_call(mcp, name, args)
        return recent_daily if name == "queryDailyHealthData" and args["days"] == 7 else result

    monkeypatch.setattr(coros.McpSession, "call_tool", limited_daily)
    fake.tool_calls.clear()
    result = await coros.run_sync(db_session, conn)
    assert result["ok"] and result["result"]["phases_incomplete"] and result["result"]["backfill_partial"]
    assert row.details == original and conn.last_error == coros.PARTIAL
    assert [a["days"] for n, a in fake.tool_calls if n == "queryDailyHealthData"] == [60, 7]
    fake.max_days.clear(); fake.tool_calls.clear()
    conn.last_sync_at -= timedelta(days=1)  # the next day: the history owed is read whole again
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert [a["days"] for n, a in fake.tool_calls if n == "queryDailyHealthData"] == [60]
    assert conn.last_error is None


def test_the_stages_come_from_the_main_nights_sleep_summary_only():
    """The owner's real answers: each main night's stage minutes from its « Sleep Summary » when that summary is the
    main sleep's (its total is the main period within 15 min, H, the hr_night guard) and its parts add up to it;
    never the nap-only day's summary, never the « Sleep metrics scope: daily » ratios."""
    from tests import owner_coros as oc

    ov = coros.parse_sleep_overview(oc.OVERVIEW_2026)
    daily = coros.parse_daily_sleep(oc.DAILY)
    rows = {r.day: r.details for r in coros.sleep_dailies(ov, {}, daily, (date(2026, 9, 20), date(2026, 10, 8)))}
    assert rows[date(2026, 10, 7)]["stages"] == {"deep": 49, "light": 205, "rem": 96, "awake": 13}  # 6h03 = 6h03
    assert rows[date(2026, 10, 6)]["stages"] == {"deep": 39, "light": 218, "rem": 76, "awake": 11}
    assert "stages" not in rows[date(2026, 9, 29)] and rows[date(2026, 9, 29)]["stages_read"]  # no summary that day
    assert date(2026, 9, 25) not in rows  # the nap-only day: no main night, its summary never a night's stages
    # a summary that is not the main sleep's (its total 20 min off the main period), or whose parts don't add up
    o7 = ov[date(2026, 10, 7)]
    assert coros.night_stages({**daily[date(2026, 10, 7)], "total": 383}, o7) is None
    assert coros.night_stages({**daily[date(2026, 10, 7)], "deep": 80}, o7) is None
    assert coros.night_stages({"total": 363, "deep": 49, "rem": 96}, o7) is None  # no light sleep: not a summary
    # a day the daily answer did not cover: its night is not marked read (it is asked again)
    rows = {r.day: r.details for r in coros.sleep_dailies(ov, {}, daily, (date(2026, 10, 7), date(2026, 10, 8)))}
    assert "stages_read" not in rows[date(2026, 10, 6)] and rows[date(2026, 10, 7)]["stages_read"]


async def test_nights_written_before_the_stages_are_read_again_once(db_session: AsyncSession, test_user: User,
                                                                     fake, no_commit):
    """A link whose nights were written before the stages were stored (no « stages_read ») reads its 60 days once
    more, then a week again: the owner's nights get their stages."""
    from app.services.health import STAGES_READ, nights_upgraded

    conn = await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc) - timedelta(days=1))
    today = datetime.now(timezone.utc).date()
    for k in (1, 2):
        d = today - timedelta(days=k)
        db_session.add(HealthMetric(user_id=test_user.id, date=d, metric="sleep", value=450, source="COROS", n_samples=1,
                                    details={"main_start": f"{d - timedelta(days=1)}T23:00", "main_end": f"{d}T07:00"}))
    db_session.add(HealthMetric(user_id=test_user.id, date=today - timedelta(days=1), metric="steps", value=9000,
                                source="COROS", n_samples=1, details={}))
    await db_session.flush()
    assert await nights_upgraded(db_session, test_user.id, "COROS")  # the main window is there…
    assert not await nights_upgraded(db_session, test_user.id, "COROS", key=STAGES_READ)  # …not the stages
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert [a for n, a in fake.tool_calls if n == "queryAvgHeartRate"] == [{"days": 60}]
    assert await nights_upgraded(db_session, test_user.id, "COROS", key=STAGES_READ)
    fake.tool_calls.clear()
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert [a for n, a in fake.tool_calls if n == "queryAvgHeartRate"] == [{"days": 7}]  # once only


async def test_reglages_sync_says_what_went_wrong(as_user: AsyncClient, db_session: AsyncSession, test_user: User,
                                                  fake, queued):
    conn = await _link(db_session, test_user)
    fake.mcp_status = 503
    r = await as_user.post("/settings/coros/sync")
    assert "HX-Refresh" not in r.headers
    assert (await _work(db_session, queued.pop()))["ok"] is False
    r = await as_user.get("/settings/coros/sync")
    assert "Dernière synchro échouée : COROS ne répond pas pour l&#39;instant (erreur 503)." in r.text
    fake.mcp_status = None
    conn.sync_claimed_at = datetime.now(timezone.utc)  # another worker is on it
    await db_session.flush()
    r = await as_user.post("/settings/coros/sync")
    assert "Une synchro est déjà en cours : réessaie dans une minute." in r.text and not queued
    conn.sync_claimed_at = None
    conn.needs_reauth = True  # to reconnect: the whole block changes (its reconnect button), the page reloads
    await db_session.flush()
    r = await as_user.post("/settings/coros/sync")
    assert r.headers.get("HX-Refresh") == "true"
    page = (await as_user.get("/settings")).text
    assert "À reconnecter" in page and "coros-sync-btn" not in page


# ── real commits (savepoint-bound session: no commit swapped for flush) ─────

async def test_a_rotated_refresh_token_survives_a_later_rollback(committing_session: AsyncSession, fake):
    """COROS rotates the refresh token on each use (the old one is then dead): the new pair is committed at
    once, so a sync that fails afterwards and rolls back never loses it."""
    db = committing_session
    user = User(email="rotate@x.fr")
    db.add(user)
    await db.commit()
    conn = await _link(db, user, expires_in=timedelta(hours=2))  # < 1 day left: refreshed
    await db.commit()
    async with coros.http_client() as client:
        assert await coros.access_token(db, conn, client) == "at-2"
    conn.last_error = "écrit puis annulé"
    await db.rollback()  # what a crashed sync does next
    await db.refresh(conn)
    assert conn.last_error is None
    assert decrypt_secret(conn.refresh_token_encrypted) == "rt-2" and decrypt_secret(conn.access_token_encrypted) == "at-2"


async def test_tokens_no_key_reads_ask_to_reconnect_and_still_disconnect(as_user: AsyncClient,
                                                                         db_session: AsyncSession,
                                                                         test_user: User, fake):
    """Tokens no configured key decrypts (app.crypto): « reconnecte-toi », never « la synchro a échoué »."""
    from cryptography.fernet import Fernet

    conn = await _link(db_session, test_user)
    other = Fernet(Fernet.generate_key())
    conn.access_token_encrypted, conn.refresh_token_encrypted = other.encrypt(b"at-1"), other.encrypt(b"rt-1")
    await db_session.flush()
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"] is False and conn.needs_reauth and conn.last_error == coros.EXPIRED
    assert not fake.tool_calls and not fake.token_calls
    page = (await as_user.get("/settings")).text
    assert "À reconnecter" in page and "Reconnecter" in page
    r = await as_user.post("/settings/coros/disconnect")
    assert r.status_code == 303 and not fake.revoked  # nothing to revoke with: just forgotten
    assert (await db_session.get(CorosConnection, conn.id)) is None


# ── syncs run by the worker, never by a web request ─────────────────────────

async def test_a_queued_sync_runs_once_in_the_worker(db_session: AsyncSession, test_user: User, fake, no_commit,
                                                     queued):
    conn = await _link(db_session, test_user)
    assert await coros.queue_sync(db_session, conn)
    assert len(queued) == 1 and queued[0][0] == conn.id and not fake.tool_calls
    assert (await coros.coros_status(db_session, test_user.id))["syncing"]  # polling sees this attempt at once
    assert not await coros.queue_sync(db_session, conn) and len(queued) == 1  # reserved: not queued twice
    job = queued.pop()
    assert (await _work(db_session, job))["ok"] and fake.tool_calls
    assert conn.sync_claimed_at is None and conn.last_sync_at is not None
    calls = len(fake.tool_calls)
    assert await _work(db_session, job) is None and len(fake.tool_calls) == calls  # a redelivery does nothing


async def test_a_sync_that_cannot_be_queued_lets_go_and_says_so(as_user: AsyncClient, db_session: AsyncSession,
                                                                test_user: User, fake, monkeypatch):
    from app.tasks.coros_sync import sync_coros_user

    def unavailable(**kwargs):
        raise ConnectionError("broker unavailable")
    monkeypatch.setattr(sync_coros_user, "apply_async", unavailable)
    conn = await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc), last_error=coros.PARTIAL)
    r = await as_user.post("/settings/coros/sync")
    assert "La synchro n&#39;a pas pu démarrer. Réessaie dans un instant." in r.text and "every 3s" not in r.text
    assert conn.sync_claimed_at is None and conn.last_error == coros.PARTIAL and not fake.tool_calls


async def test_connecting_or_opening_sante_queues_the_sync_for_the_worker(db_session: AsyncSession, test_user: User,
                                                                         fake, no_commit, queued, monkeypatch):
    import asyncio

    from app.config import settings

    monkeypatch.setattr(settings, "COROS_SYNC", True)
    conn = await _link(db_session, test_user)

    @asynccontextmanager
    async def session():
        yield db_session
    monkeypatch.setattr("app.database.async_session_factory", session)
    assert coros.schedule_sync(test_user.id)
    await asyncio.gather(*list(coros._pending))
    assert len(queued) == 1 and conn.sync_claimed_at is not None and not fake.tool_calls


# ── a history that keeps failing: read whole at most once a day ─────────────

def _asked(fake: FakeCoros, tool: str) -> list:
    return [a for n, a in fake.tool_calls if n == tool]


async def test_a_history_cut_short_is_read_whole_once_a_day_and_stays_owed(db_session: AsyncSession, test_user: User,
                                                                           fake, no_commit):
    """COROS cutting every 60-day read short (its daily answer refused beyond a week) used to make every sync,
    every 2 hours, a 60-day read (45 calls). Now: the 60 days once a day, the last week in between, and the
    history stays owed until it has arrived."""
    fake.max_days = {"queryDailyHealthData": 7}
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]  # the first sync: the 60 days, cut short
    assert len(_asked(fake, "querySleepHrv")) == 9 and conn.last_error == coros.PARTIAL
    fake.tool_calls.clear()
    assert (await coros.run_sync(db_session, conn))["ok"]  # 2 hours later, the same day: the last week only
    assert len(_asked(fake, "querySleepHrv")) == 1 and _asked(fake, "queryAvgHeartRate") == [{"days": 7}]
    assert conn.last_error == coros.PARTIAL  # still owed, still said
    fake.tool_calls.clear()
    conn.last_sync_at -= timedelta(days=1)  # the next day: the 60 days again, still cut short
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert len(_asked(fake, "querySleepHrv")) == 9 and conn.last_error == coros.PARTIAL
    fake.max_days.clear()
    fake.tool_calls.clear()
    conn.last_sync_at -= timedelta(days=1)  # COROS answers again: the history arrives, nothing is owed
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert len(_asked(fake, "querySleepHrv")) == 9 and conn.last_error is None
    fake.tool_calls.clear()
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert len(_asked(fake, "querySleepHrv")) == 1 and conn.last_error is None


async def test_an_hrv_format_not_understood_is_not_a_60_day_read_every_sync(db_session: AsyncSession,
                                                                             test_user: User, fake, no_commit):
    conn = await _link(db_session, test_user)
    fake.texts["querySleepHrv"] = fake.texts["querySleepHrv"].replace("Sleep HRV Time Series", "Sleep HRV Readings")
    assert (await coros.run_sync(db_session, conn))["ok"] and conn.last_error == coros.HRV_UNREADABLE
    fake.tool_calls.clear()
    assert (await coros.run_sync(db_session, conn))["ok"]  # the same day
    assert [a["days"] for a in _asked(fake, "querySleepHrv")] == [7, 2] and conn.last_error == coros.HRV_UNREADABLE


async def test_the_pkce_verifier_is_not_readable_in_the_session_cookie(as_user: AsyncClient, fake):
    """The session cookie is signed, not encrypted: the code verifier (the proof that this browser started the
    link) is kept in it encrypted, for 10 minutes."""
    r = await as_user.get("/coros/connect")
    challenge = parse_qs(urlsplit(r.headers["location"]).query)["code_challenge"][0]
    cookie = as_user.cookies.get("paceforge_session")
    pending = json.loads(base64.b64decode(cookie.split(".")[0]))["coros_oauth"]
    stored = pending["verifier"]
    assert base64.urlsafe_b64encode(hashlib.sha256(stored.encode()).digest()).rstrip(b"=").decode() != challenge
    assert decrypt_secret(stored.encode()) and len(stored) > 100


async def test_a_connect_left_for_more_than_10_minutes_starts_again(as_user: AsyncClient, db_session: AsyncSession,
                                                                    fake, monkeypatch):
    from app.routers import coros as coros_router

    state = await _authorize(as_user, fake)
    monkeypatch.setattr(coros_router, "_PENDING_TTL_S", -1)  # its encrypted verifier is now too old
    r = await as_user.get(f"/coros/callback?code=the-code&state={state}")
    assert r.status_code == 303 and not fake.token_calls
    assert "La connexion à COROS a expiré. Réessaie." in (await as_user.get("/settings")).text
    assert (await db_session.execute(select(CorosConnection))).scalar_one_or_none() is None
