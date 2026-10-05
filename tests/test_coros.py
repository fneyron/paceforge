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
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
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
from app.services.health import aggregate_days, current_form, hrv_same_scale

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "t4c5d6e7f8a9_add_coros.py"

# ── the real tool outputs (captured 2026-09-30) ─────────────────────────────

RHR = """Resting Heart Rate — Last 3 days
========================

2026-09-30: 39 bpm
2026-09-29: 40 bpm
2026-09-28: No data
"""

HRV = """Sleep HRV — 2026-09-28 to 2026-09-30
========================
Note: dates are wake-up days (each value comes from the night that ended that morning).

HRV Assessment — Last 3 days
========================

2026-09-30:
  HRV Avg: 83 ms — Normal
  Normal Range: 70 - 84 ms
  Baseline: 77 ms
2026-09-29:
  HRV Avg: 90 ms — Above normal
  Normal Range: 70 - 84 ms
  Baseline: 77 ms
2026-09-28:
  No data

Sleep HRV Time Series — Last 3 days
========================

2026-09-28: no official sleep HRV value for this day; raw HRV points omitted.
2026-09-29:
  timestamp=1790610290, timezone=36, hrv=77 ms, status=4, confidence=100000
  timestamp=1790610590, timezone=36, hrv=81 ms, status=4, confidence=100000
"""

SLEEP_OVERVIEW = """Sleep Overview
========================
Note: each record below is dated by its wake-up day.

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

FITNESS = """Fitness Assessment Overview
========================

VO2max: 61
Running Level: 97
Threshold Pace: 3:20 /km
5 km Prediction: 15:50
10 km Prediction: 32:34
Half Marathon Prediction: 1:10:54
Marathon Prediction: 2:25:03
"""

# captured 2026-10-05
RECOVERY = """Recovery Status
========================

Recovery: 84%
Level: Moderate training recommended
Estimated Full Recovery: 45h
"""

LOAD = """Training Load Assessment
========================

2026-10-05
Comment: Excessive
Short-Term Load: 179
Long-Term Load: 109
Load Ratio: 1.64

2026-10-04
Comment: Excessive
Short-Term Load: 208
Long-Term Load: 112
Load Ratio: 1.85

2026-10-02
Comment: Maintaining
Short-Term Load: 105
Long-Term Load: 114
Load Ratio: 0.92

2026-10-01
Comment: Performance
Short-Term Load: 69
Long-Term Load: 109
Load Ratio: 0.63

2026-09-23
Comment: Optimized
Short-Term Load: 128
Long-Term Load: 123
Load Ratio: 1.04
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

def test_parse_resting_hr_skips_no_data():
    assert coros.parse_rhr(RHR) == {D30: 39.0, D29: 40.0}


def test_parse_hrv_uses_the_assessment_only():
    assert coros.parse_hrv(HRV) == {D30: 83.0, D29: 90.0}  # 09-28 "No data", time series ignored


def test_parse_sleep_overview_and_daily_stages():
    ov = coros.parse_sleep_overview(SLEEP_OVERVIEW)
    assert set(ov) == {D29, D30}
    n = ov[D30]
    assert n["start"] == datetime(2026, 9, 29, 23, 52) and n["end"] == datetime(2026, 9, 30, 9, 54)
    assert (n["asleep"], n["period"], n["awake"]) == (588, 602, 14)
    assert n["ratios"] == {"deep": 13, "core": 57, "rem": 28, "awake": 2}
    daily = coros.parse_daily_sleep(DAILY)
    assert daily[D30] == {"deep": 76, "core": 343, "rem": 169, "awake": 14}
    assert daily[D29] == {"deep": 60, "core": 341, "rem": 125, "awake": 14}
    assert coros.parse_vo2max(FITNESS) == 61


def test_parse_training_load():
    load = coros.parse_training_load(LOAD)
    assert sorted(load) == [date(2026, 9, 23), date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 4),
                            date(2026, 10, 5)]  # days without a value are simply absent
    assert load[date(2026, 10, 5)] == {"short": 179, "long": 109, "ratio": 1.64, "comment": "Excessive"}
    assert load[date(2026, 10, 1)]["comment"] == "Performance"
    no_ratio = LOAD.replace("Load Ratio: 1.64\n", "")
    assert coros.parse_training_load(no_ratio)[date(2026, 10, 5)]["ratio"] == 1.64  # computed


def test_parse_recovery_avg_hr_daily_activity_fitness():
    assert coros.parse_recovery(RECOVERY) == {"pct": 84, "level": "Moderate training recommended", "full_h": 45}
    assert coros.parse_recovery("Recovery: 100%\nLevel: Full\nEstimated Full Recovery: 0h")["full_h"] == 0
    hr = coros.parse_avg_hr(AVG_HR)
    assert hr[date(2026, 10, 4)] == {"avg": 53, "min": 36, "max": 98} and len(hr) == 3
    assert coros.parse_avg_hr("2026-10-04: 53 bpm") == {date(2026, 10, 4): {"avg": 53, "min": None, "max": None}}
    act = coros.parse_daily_activity(DAILY_60)
    assert act[date(2026, 8, 2)] == {"steps": 19835, "kcal": 1474, "exercise": 106, "stress": 59}
    assert act[date(2026, 8, 10)] == {"steps": 0, "kcal": 0, "exercise": 0}  # no stress line
    assert coros.parse_daily_activity(DAILY)[D29] == {"steps": 18055, "kcal": 786, "exercise": 39, "stress": 23}
    assert coros.parse_fitness(FITNESS) == {"vo2max": 61, "level": 97, "threshold_s": 200, "pred": {
        "5k": 950, "10k": 1954, "half": 4254, "marathon": 8703}}
    one_line = "VO2max: 61 / Running Level: 97 / Half Marathon Prediction: 1:10:54 / Marathon Prediction: 2:25:03"
    assert coros.parse_fitness(one_line)["pred"] == {"half": 4254, "marathon": 8703}
    assert coros.parse_hrv_range(HRV) == {D30: {"lo": 70, "hi": 84, "base": 77}, D29: {"lo": 70, "hi": 84, "base": 77}}


def test_build_daily_keeps_plausible_values_only():
    today = date(2026, 10, 5)
    data = {"load": coros.parse_training_load(LOAD), "recovery": coros.parse_recovery(RECOVERY),
            "hr_day": coros.parse_avg_hr(AVG_HR), "activity": coros.parse_daily_activity(DAILY_60),
            "hrv_range": coros.parse_hrv_range(HRV), "fitness": coros.parse_fitness(FITNESS)}
    rows = {(r.metric, r.day): r for r in coros.build_daily(data, today)}
    assert rows[("load", today)].value == 179
    assert rows[("load", today)].details == {"long": 109, "ratio": 1.64, "comment": "Excessive"}
    assert rows[("recovery", today)].value == 84 and rows[("recovery", today)].details["full_h"] == 45
    assert rows[("hr_day", date(2026, 10, 3))].details == {"min": 54, "max": 142}
    assert rows[("steps", date(2026, 8, 2))].details == {"kcal": 1474, "exercise": 106}
    assert ("steps", date(2026, 8, 10)) not in rows and ("stress", date(2026, 8, 10)) not in rows  # not worn
    assert rows[("stress", date(2026, 9, 25))].value == 28
    assert rows[("hrv_norm", D30)].value == 77 and rows[("hrv_norm", D30)].details == {"lo": 70, "hi": 84}
    fit = rows[("fitness", today)]
    assert fit.value == 97 and fit.details["threshold_s"] == 200 and fit.details["pred"]["marathon"] == 8703
    assert all(len(r.metric) <= 12 for r in rows.values())  # HealthMetric.metric is String(12)
    odd = coros.build_daily({"recovery": {"pct": 840, "level": None, "full_h": None},
                             "hr_day": {today: {"avg": 999, "min": None, "max": None}},
                             "fitness": {"threshold_s": 5, "pred": {"5k": 3}}}, today)
    assert odd == []


def test_parsers_survive_garbled_text():
    junk = "Erreur interne\n2026-13-45: 39 bpm\n2026-09-30:\n  HRV Avg: n/a\n--- 2026 ---\nVO2max: —"
    assert coros.parse_rhr(junk) == {} and coros.parse_hrv(junk) == {}
    assert coros.parse_sleep_overview(junk) == {} and coros.parse_daily_sleep(junk) == {}
    assert coros.parse_vo2max(junk) is None and coros.parse_rhr("") == {}
    assert coros.parse_training_load(junk) == {} and coros.parse_recovery(junk) is None
    assert coros.parse_avg_hr(junk) == {} and coros.parse_daily_activity(junk) == {}
    assert coros.parse_fitness(junk) == {} and coros.parse_hrv_range(junk) == {}
    assert coros.parse_training_load("2026-10-05\nComment: Excessive\nShort-Term Load: n/a") == {}
    bad_window = SLEEP_OVERVIEW.replace("2026-09-30 09:54", "2026-09-29 09:54")  # ends before it starts
    assert set(coros.parse_sleep_overview(bad_window)) == {D29}
    assert coros.parse_duration("9h 48min") == 588 and coros.parse_duration("14 min") == 14
    assert coros.parse_duration("—") is None


def test_tool_text_decodes_the_json_quoted_text_coros_sends():
    # what COROS really sends: the text is itself a JSON string literal
    quoted = {"content": [{"type": "text", "text": json.dumps(RHR, ensure_ascii=True)}]}
    assert coros.tool_text(quoted) == RHR
    assert coros.parse_rhr(coros.tool_text(quoted))
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


# ── samples ─────────────────────────────────────────────────────────────────

def test_sleep_stages_rebuild_the_night():
    samples = coros.build_samples({}, {}, coros.parse_sleep_overview(SLEEP_OVERVIEW),
                                  coros.parse_daily_sleep(DAILY), None, D30)
    assert all(s.source == "COROS" and s.metric == "sleep" for s in samples)
    night = aggregate_days("sleep", _rows(samples), [D30, D29])
    n = night[D30]
    assert n["value"] == 588 and n["source"] == "COROS"
    assert n["details"]["bedtime"] == "23:52" and n["details"]["wake"] == "09:54"
    assert (n["details"]["deep"], n["details"]["core"], n["details"]["rem"], n["details"]["awake"]) == (76, 343, 169, 14)
    assert night[D29]["value"] == 526 and night[D29]["details"]["bedtime"] == "00:31"
    assert night[D29]["details"]["wake"] == "09:31"


def test_sleep_stages_from_ratios_without_daily_data():
    ov = coros.parse_sleep_overview(SLEEP_OVERVIEW)[D30]
    stages = coros.night_stages(ov, None)
    assert stages == {"deep": 78, "core": 342, "rem": 168, "awake": 14}
    n = aggregate_days("sleep", _rows(coros.sleep_samples(ov, stages)), [D30])[D30]
    assert n["value"] == 588 and n["details"]["wake"] == "09:54"
    # daily minutes that don't add up to the main sleep (a nap counted in) → ratios
    assert coros.night_stages(ov, {"deep": 90, "core": 400, "rem": 200, "awake": 14})["deep"] == 78


def test_hrv_rhr_vo2_points_and_aggregation():
    samples = coros.build_samples(coros.parse_hrv(HRV), coros.parse_rhr(RHR), {}, {}, 61, D30)
    by = {(s.metric, s.start.date()): s for s in samples}
    assert by[("hrv", D30)].start == datetime(2026, 9, 30, 5, 0) and by[("hrv", D30)].value == 83
    assert by[("rhr", D30)].start == datetime(2026, 9, 30, 23, 59)
    assert by[("vo2max", D30)].start == datetime(2026, 9, 30, 12, 0) and by[("vo2max", D30)].value == 61
    apple = [SimpleNamespace(start_at=datetime(2026, 9, 30, 3, 10), end_at=datetime(2026, 9, 30, 3, 10),
                             value=45.0, source="Apple Watch", kind=""),
             SimpleNamespace(start_at=datetime(2026, 9, 30, 9, 0), end_at=datetime(2026, 9, 30, 9, 0),
                             value=52.0, source="Apple Watch", kind="")]
    hrv = aggregate_days("hrv", _rows([by[("hrv", D30)]]) + apple[:1], [D30])[D30]
    assert hrv["value"] == 83 and hrv["source"] == "COROS"  # never averaged with Apple's SDNN
    rhr = aggregate_days("rhr", _rows([by[("rhr", D30)]]) + apple[1:], [D30])[D30]
    assert rhr["value"] == 39  # 23:59: the last of the day
    assert aggregate_days("hrv", apple[:1], [D30])[D30].get("source") is None  # Apple days unchanged


# ── HRV scale in the fitness signal ─────────────────────────────────────────

def test_hrv_same_scale_follows_the_latest_day():
    vals = {D28: 40.0, D29: 42.0, D30: 85.0}
    assert hrv_same_scale(vals, {D30: "COROS"}) == {D30: 85.0}
    assert hrv_same_scale(vals, {D28: "COROS"}) == {D29: 42.0, D30: 85.0}
    assert hrv_same_scale({}, {}) == {}


async def test_form_never_compares_coros_to_an_apple_baseline(db_session: AsyncSession, test_user: User):
    today = date.today()
    for k in range(67):
        d = today - timedelta(days=k)
        coros_day = k < 5
        db_session.add(HealthMetric(user_id=test_user.id, date=d, metric="hrv", n_samples=1,
                                    value=80.0 + (k % 3) if coros_day else 40.0 + (k % 3),
                                    source="COROS" if coros_day else None))
        db_session.add(HealthMetric(user_id=test_user.id, date=d, metric="rhr", value=50 + (k % 2), n_samples=1))
    await db_session.flush()
    form = await current_form(db_session, test_user.id, today)
    # 5 COROS days: too few for a COROS baseline, and the Apple days are left out
    assert 80 <= form["hrv_7d"] <= 82 and form["hrv_baseline"] is None and form["hrv_delta_pct"] is None
    assert form["status"] == "ok" and not any("VFC" in r for r in form["reasons"])

    # once COROS covers the baseline too (the 60-day backfill), it's COROS vs COROS
    rows = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "hrv"))).scalars().all()
    for m in rows:
        m.source, m.value = "COROS", 80.0 + (today - m.date).days % 3
    await db_session.flush()
    form = await current_form(db_session, test_user.id, today)
    assert 80 <= form["hrv_baseline"] <= 82 and abs(form["hrv_delta_pct"]) < 3


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
            ("queryRestingHeartRate", RHR), ("querySleepHrv", HRV), ("querySleepOverview", SLEEP_OVERVIEW),
            ("queryDailyHealthData", DAILY), ("queryFitnessAssessmentOverview", FITNESS),
            ("queryRecoveryStatus", RECOVERY), ("queryTrainingLoadAssessment", LOAD),
            ("queryAvgHeartRate", AVG_HR))}
        # the load and heart-rate texts were captured on 10-05, the others on 09-30
        for name in ("queryTrainingLoadAssessment", "queryAvgHeartRate"):
            self.texts[name] = _shift(self.texts[name], -5)
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
        limit = self.max_days.get(name)
        if limit and args.get("days", 0) > limit:
            return self._reply(msg["id"], {"content": [{"type": "text", "text": f"days must be <= {limit}"}],
                                           "isError": True})
        return self._reply(msg["id"], {"content": [{"type": "text", "text": self.texts[name]}], "isError": False})

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
    assert "COROS connecté" in page and "première synchro en cours" in page and "Synchroniser maintenant" in page
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
    fake.max_days = {"queryRestingHeartRate": 7, "queryDailyHealthData": 7,  # refuse 60: fall back to 7
                     "queryTrainingLoadAssessment": 14}
    conn = await _link(db_session, test_user)
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"], outcome
    hrv_calls = [a for n, a in fake.tool_calls if n == "querySleepHrv"]
    assert len(hrv_calls) == 9 and all(a["days"] <= 7 for a in hrv_calls)  # 60 days, 7 per call
    assert [a for n, a in fake.tool_calls if n == "queryRestingHeartRate"] == [{"days": 60}, {"days": 7}]
    assert len([1 for n, _ in fake.tool_calls if n == "querySleepOverview"]) == 2
    assert [a for n, a in fake.tool_calls if n == "queryTrainingLoadAssessment"] == [{"days": 60}, {"days": 14}]
    assert [a for n, a in fake.tool_calls if n == "queryAvgHeartRate"] == [{"days": 60}]
    assert [a for n, a in fake.tool_calls if n == "queryRecoveryStatus"] == [{}]
    assert len(fake.tool_calls) <= coros.MAX_CALLS
    assert conn.last_sync_at is not None and conn.last_error is None and conn.sync_claimed_at is None

    today = datetime.now(timezone.utc).date()
    rows = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.date == today))).scalars().all()
    by = {m.metric: m for m in rows}
    assert by["hrv"].value == 83 and by["hrv"].source == "COROS"
    assert by["rhr"].value == 39 and by["vo2max"].value == 61
    assert by["sleep"].value == 588 and by["sleep"].details["bedtime"] == "23:52"
    assert by["sleep"].details["wake"] == "09:54" and by["sleep"].source == "COROS"
    # the daily values, straight from COROS
    assert by["load"].value == 179 and by["load"].details == {"long": 109, "ratio": 1.64, "comment": "Excessive"}
    assert by["recovery"].value == 84 and by["recovery"].details == {
        "level": "Moderate training recommended", "full_h": 45}
    assert by["steps"].value == 1487 and by["steps"].details == {"kcal": 92, "exercise": 0}
    assert by["stress"].value == 13 and by["hrv_norm"].details == {"lo": 70, "hi": 84}
    assert by["fitness"].details["level"] == 97 and by["fitness"].details["pred"]["10k"] == 1954
    assert all(m.source == "COROS" for m in rows)
    hr = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "hr_day"))).scalars().all()
    assert {m.date: (m.value, m.details["max"]) for m in hr}[today - timedelta(days=1)] == (53, 98)
    loads = (await db_session.execute(select(func.count(HealthMetric.id)).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "load"))).scalar()
    assert loads == 5
    n = (await db_session.execute(select(func.count(HealthSample.id)).where(HealthSample.user_id == test_user.id))).scalar()

    fake.tool_calls.clear()
    fake.sse = True  # same answers as server-sent events
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"] and outcome["result"]["inserted"] == 0 and outcome["result"]["updated"] == 0
    assert [a for nm, a in fake.tool_calls if nm == "queryRestingHeartRate"] == [{"days": 7}]
    assert [a for nm, a in fake.tool_calls if nm == "queryTrainingLoadAssessment"] == [{"days": 7}]
    assert len([1 for nm, _ in fake.tool_calls if nm == "querySleepHrv"]) == 1
    n2 = (await db_session.execute(select(func.count(HealthSample.id)).where(HealthSample.user_id == test_user.id))).scalar()
    assert n == n2

    # COROS revises a night: the old intervals are replaced, not added up
    daily = fake.texts["queryDailyHealthData"]
    fake.texts["queryDailyHealthData"] = daily.replace("Deep: 1h 16min", "Deep: 1h 20min").replace(
        "Light: 5h 43min", "Light: 5h 39min")
    await coros.run_sync(db_session, conn)
    sleep = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.date == today, HealthMetric.metric == "sleep"))).scalar_one()
    assert sleep.value == 588 and sleep.details["deep"] == 80 and sleep.details["core"] == 339


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


async def test_a_claimed_sync_is_not_run_twice(db_session: AsyncSession, test_user: User, fake, no_commit):
    conn = await _link(db_session, test_user, sync_claimed_at=datetime.now(timezone.utc))
    assert await coros.run_sync(db_session, conn) is None and not fake.tool_calls
    conn.sync_claimed_at = datetime.now(timezone.utc) - timedelta(hours=1)  # a crashed worker's claim
    await db_session.flush()
    assert (await coros.run_sync(db_session, conn))["ok"]


# ── routes ──────────────────────────────────────────────────────────────────

async def test_routes_require_login(client: AsyncClient):
    for method, path in (("GET", "/coros/connect"), ("GET", "/coros/callback?code=x&state=y"),
                         ("POST", "/settings/coros/sync"), ("POST", "/settings/coros/disconnect")):
        r = await client.request(method, path)
        assert r.status_code == 307 and r.headers["location"] == "/", path


async def test_settings_block_manual_sync_and_disconnect(as_user: AsyncClient, db_session: AsyncSession,
                                                         test_user: User, fake):
    page = (await as_user.get("/settings")).text
    assert "ta VFC, ton sommeil, tes pas et ta VO2 max arrivent automatiquement depuis ta montre COROS." in page
    assert 'href="/sante"' in page
    assert 'href="/coros/connect?region=monde"' in page and "Connecter COROS" in page

    await _link(db_session, test_user)
    r = await as_user.post("/settings/coros/sync")
    assert r.status_code == 200 and 'id="coros-status"' in r.text
    assert "Synchro terminée" in r.text and "dernière synchro" in r.text and "Reçu de COROS : VFC 2 j" in r.text
    assert "Charge 5 j" in r.text and "HX-Refresh" not in r.headers
    # from the Santé page, a finished sync reloads it to show the new values
    r = await as_user.post("/settings/coros/sync", headers={"HX-Request": "true", "HX-Current-URL": "https://test/sante"})
    assert r.headers.get("HX-Refresh") == "true"

    n = (await db_session.execute(select(func.count(HealthSample.id)).where(HealthSample.user_id == test_user.id))).scalar()
    r = await as_user.post("/settings/coros/disconnect")
    assert r.status_code == 303 and r.headers["location"] == "/settings#coros"
    assert (await db_session.execute(select(CorosConnection))).scalar_one_or_none() is None
    assert fake.revoked and fake.revoked[0]["token_type_hint"] == "refresh_token"  # tried, even if refused
    n2 = (await db_session.execute(select(func.count(HealthSample.id)).where(HealthSample.user_id == test_user.id))).scalar()
    assert n == n2 > 0  # imported data stays
    page = (await as_user.get("/settings")).text
    assert "COROS déconnecté" in page and "Connecter COROS" in page


async def test_link_made_before_the_daily_values_backfills_them(db_session: AsyncSession, test_user: User,
                                                                fake, no_commit):
    """A link that already has its nights (synced before the daily values were
    read) gets the 60 days of load, steps… once."""
    conn = await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc) - timedelta(hours=7))
    db_session.add(HealthSample(user_id=test_user.id, metric="hrv", kind="", source="COROS", value=80,
                                start_at=datetime.now() - timedelta(days=3), end_at=datetime.now() - timedelta(days=3)))
    await db_session.flush()
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert [a for n, a in fake.tool_calls if n == "queryTrainingLoadAssessment"] == [{"days": 60}]
    fake.tool_calls.clear()
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=7)
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert [a for n, a in fake.tool_calls if n == "queryTrainingLoadAssessment"] == [{"days": 7}]


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
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=7)  # synced once, but read nothing
    await db_session.flush()
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"], outcome
    assert len([1 for n, _ in fake.tool_calls if n == "querySleepHrv"]) == 9  # the 60 days again
