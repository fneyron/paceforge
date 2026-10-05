"""Public pages: landing (/, /landing) and /methode.

The figures come from app/data/model_stats.json through app.services.model_stats;
nothing on these pages may promise AI or bike features.
"""

import json
import re

import pytest
from httpx import AsyncClient

from app.dependencies import get_optional_user
from app.services import model_stats
from app.services.model_stats import NNBSP, fmt_date, fmt_dec, fmt_int, load_model_stats

FAKE = {
    "updated": "2026-03-07",
    "dataset": {
        "races": 1234,
        "events": 456,
        "finisher_results": 98765,
        "checkpoint_passages": 2345678,
        "sources": {"utmb_live": 234, "livetrail": 1000},
    },
    "fit": {"races": 131, "finisher_results": 23456, "note": "curve fitted on UTMB Live races 50-170 km with a GPS track"},
    "validation": {
        "held_out_races": 47,
        "held_out_events": 7,
        "mean_abs_gap_min_before": 30.8,
        "mean_abs_gap_min_after": 17.4,
        "races_improved": 39,
    },
}

PUBLIC_PATHS = ("/", "/landing", "/methode")


@pytest.fixture
def stats_file(tmp_path, monkeypatch):
    """Point the loader at a temporary stats file; returns a writer."""
    path = tmp_path / "model_stats.json"
    monkeypatch.setattr(model_stats, "STATS_PATH", path)

    def write(data):
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    return write


def test_french_number_formatting():
    assert fmt_int(995) == "995"
    assert fmt_int(119512) == f"119{NNBSP}512"
    assert fmt_int(1175720) == f"1{NNBSP}175{NNBSP}720"
    assert fmt_dec(21.5) == "21,5"
    assert fmt_dec(25.0) == "25"
    assert fmt_date("2026-10-04") == "4 octobre 2026"
    assert fmt_int(None) == "" and fmt_dec("x") == "" and fmt_date("bad") == ""


def test_loader_reads_the_real_file():
    raw = json.loads(model_stats.STATS_PATH.read_text(encoding="utf-8"))
    s = load_model_stats()
    assert s["dataset"]["races"] == fmt_int(raw["dataset"]["races"])
    assert s["dataset"]["finisher_results"] == fmt_int(raw["dataset"]["finisher_results"])
    assert s["validation"]["gap_after"] == fmt_dec(raw["validation"]["mean_abs_gap_min_after"])
    assert s["has_dataset"] and s["has_validation"]


def test_loader_missing_file_gives_empty_values(stats_file, tmp_path, monkeypatch):
    monkeypatch.setattr(model_stats, "STATS_PATH", tmp_path / "absent.json")
    s = load_model_stats()
    assert s["dataset"]["races"] == "" and s["validation"]["gap_after"] == ""
    assert not s["has_dataset"] and not s["has_validation"]
    assert s["validation"]["transjeju_2026"] is None


def test_loader_broken_file_gives_empty_values(stats_file):
    path = stats_file(FAKE)
    path.write_text("{not json", encoding="utf-8")
    s = load_model_stats()
    assert not s["has_dataset"]


def test_loader_picks_up_a_refresh(stats_file):
    path = stats_file(FAKE)
    assert load_model_stats()["dataset"]["races"] == f"1{NNBSP}234"
    data = json.loads(json.dumps(FAKE))
    data["dataset"]["races"] = 1300
    path.write_text(json.dumps(data), encoding="utf-8")
    import os

    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert load_model_stats()["dataset"]["races"] == f"1{NNBSP}300"


@pytest.mark.asyncio
async def test_landing_renders_real_numbers(client: AsyncClient):
    raw = json.loads(model_stats.STATS_PATH.read_text(encoding="utf-8"))
    r = await client.get("/")
    assert r.status_code == 200
    html = r.text
    assert "Tes temps de passage" in html
    assert fmt_int(raw["dataset"]["races"]) in html
    assert fmt_int(raw["dataset"]["finisher_results"]) in html
    assert fmt_dec(raw["validation"]["mean_abs_gap_min_after"]) + "&nbsp;min" in html
    assert 'href="/methode"' in html


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/landing"])
async def test_landing_numbers_come_from_the_json(client: AsyncClient, stats_file, path):
    stats_file(FAKE)
    html = (await client.get(path)).text
    for expected in (
        f"1{NNBSP}234",               # dataset.races
        f"98{NNBSP}765",              # dataset.finisher_results
        f"2{NNBSP}345{NNBSP}678",     # dataset.checkpoint_passages
        "17,4&nbsp;min",              # validation.mean_abs_gap_min_after
        "sur 47 courses jamais vues",  # validation.held_out_races
        f"23{NNBSP}456",              # fit.finisher_results
        "de 50 à 170&nbsp;km",        # fit.note
        "7 mars 2026",                # updated
    ):
        assert expected in html, expected
    # the real numbers are not hard-coded in the template
    assert "995" not in html and f"119{NNBSP}512" not in html and "21,5" not in html


@pytest.mark.asyncio
async def test_methode_numbers_come_from_the_json(client: AsyncClient, stats_file):
    stats_file(FAKE)
    r = await client.get("/methode")
    assert r.status_code == 200
    html = r.text
    assert "Comment PaceForge estime ton temps" in html
    for expected in (
        f"1{NNBSP}234", "456 événements", f"98{NNBSP}765", f"2{NNBSP}345{NNBSP}678",
        ">234<", f">1{NNBSP}000<",            # sources
        ">131<", f">23{NNBSP}456<",            # fit
        ">7<", ">47<",                         # held-out events / races
        "30,8&nbsp;min", "17,4&nbsp;min", ">39<",
        "de 50 à 170&nbsp;km",
    ):
        assert expected in html, expected
    assert "data-transjeju" not in html
    assert "995" not in html and "21,5" not in html


@pytest.mark.asyncio
async def test_methode_shows_transjeju_only_when_present(client: AsyncClient, stats_file):
    data = json.loads(json.dumps(FAKE))
    data["validation"]["transjeju_2026"] = {
        "mean_abs_gap_min_before": 14.2, "mean_abs_gap_min_after": 9.6, "finisher_results": 812,
    }
    stats_file(data)
    html = (await client.get("/methode")).text
    assert "data-transjeju" in html
    assert "Trans Jeju 2026" in html and "9,6&nbsp;min" in html and "14,2&nbsp;min" in html and "812 finishers" in html


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PUBLIC_PATHS)
async def test_public_pages_without_stats_file(client: AsyncClient, tmp_path, monkeypatch, path):
    monkeypatch.setattr(model_stats, "STATS_PATH", tmp_path / "absent.json")
    r = await client.get(path)
    assert r.status_code == 200
    assert "data-stats" not in r.text and "data-validation" not in r.text
    assert "None" not in r.text and "&nbsp;min d'écart" not in r.text


@pytest.mark.asyncio
async def test_methode_logged_in_keeps_public_layout(client: AsyncClient, test_user):
    client._transport.app.dependency_overrides[get_optional_user] = lambda: test_user  # type: ignore[attr-defined]
    r = await client.get("/methode")
    assert r.status_code == 200
    assert "Mes courses" in r.text and 'href="/simulator"' in r.text
    assert "pf-sidebar" not in r.text and "Se connecter" not in r.text


@pytest.mark.asyncio
async def test_methode_logged_out_offers_sign_up(client: AsyncClient):
    r = await client.get("/methode")
    assert r.status_code == 200
    assert 'href="/auth/register"' in r.text and 'href="/auth/login"' in r.text


def _visible_and_meta(html: str) -> str:
    """Text a visitor or a link preview can see: body text plus meta/og/title contents."""
    meta = " ".join(re.findall(r'<meta[^>]+content="([^"]*)"', html))
    title = " ".join(re.findall(r"<title>(.*?)</title>", html, re.S))
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    return f"{meta} {title} {body}"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PUBLIC_PATHS)
async def test_public_pages_make_no_ai_or_bike_claims(client: AsyncClient, path):
    html = (await client.get(path)).text
    text = _visible_and_meta(html)
    assert not re.search(r"\bIA\b", text), "AI claim on a public page"
    assert "Claude" not in html
    assert not re.search(r"intelligence artificielle", text, re.I)
    assert not re.search(r"v[ée]lo|cyclisme|triathlon", text, re.I)
    assert "coach IA" not in html


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/methode"])
async def test_public_footer_links(client: AsyncClient, path):
    html = (await client.get(path)).text
    footer = html[html.rfind("<footer"):]
    assert 'href="/methode"' in footer and 'href="/privacy"' in footer
