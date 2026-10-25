"""Request bodies refused past their route's cap before anything reads them (app.middleware.body_limit): a declared
size answered at once, a streamed body cut as it goes past, the routes' own caps consistent with the table."""
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.main import create_app
from app.middleware.body_limit import DEFAULT_LIMIT, MiB, body_limit
from tests.test_security import _gpx, as_user  # noqa: F401  (as_user: a fixture)

ROOT = Path(__file__).resolve().parent.parent


async def _raw_post(path: str, length: int, content_type: str = "multipart/form-data; boundary=x") -> tuple[list, list]:
    """The app called as uvicorn would, a body announced but never sent: what it answered, and how many times it
    asked for the body."""
    app = create_app()
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "https",
             "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": [(b"host", b"paceforge.fr"), (b"content-type", content_type.encode()),
                         (b"content-length", str(length).encode())],
             "client": ("203.0.113.9", 5000), "server": ("paceforge.fr", 443)}
    asked, sent = [], []

    async def receive():
        asked.append(1)
        return {"type": "http.request", "body": b"x" * 65536, "more_body": True}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent, asked


def _status(sent: list) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _body(sent: list) -> str:
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body").decode()


async def test_a_declared_body_over_its_cap_is_refused_unread():
    """The audit's case: 40 MB posted to the GPX import without a session was spooled to disk in full before the
    sign-in redirect. Now a 413 at once, the body never read; the GPX forms get their message in their error box."""
    sent, asked = await _raw_post("/partials/simulator/gpx-upload", 40 * MiB)
    assert _status(sent) == 413 and not asked
    assert "Ce fichier GPX est trop lourd (12 Mo au plus)." in _body(sent)
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert headers["content-type"].startswith("text/html") and headers["x-content-type-options"] == "nosniff"

    sent, asked = await _raw_post("/webhook/strava", DEFAULT_LIMIT + 1, "application/json")
    assert _status(sent) == 413 and not asked and '"detail":"Envoi trop volumineux."' in _body(sent)
    sent, asked = await _raw_post("/auth/login", 2 * MiB, "application/x-www-form-urlencoded")
    assert _status(sent) == 413 and not asked


async def test_a_streamed_body_is_cut_as_it_goes_past_its_cap(client: AsyncClient):
    """Without a Content-Length (chunked), the bytes are counted as they come: past the cap, a 413, whatever the
    route would have answered (the webhook acknowledges a body it cannot read; the import redirects to sign-in)."""
    async def chunks(total: int, head: bytes = b""):
        yield head
        for _ in range(total // 65536 + 1):
            yield b" " * 65536

    response = await client.post("/webhook/strava", content=chunks(DEFAULT_LIMIT + 1),
                                 headers={"Content-Type": "application/json"})
    assert response.status_code == 413 and response.json() == {"detail": "Envoi trop volumineux."}
    part = (b'--x\r\nContent-Disposition: form-data; name="gpx_file"; filename="t.gpx"\r\n'
            b"Content-Type: application/gpx+xml\r\n\r\n")  # a file part that never ends
    response = await client.post("/partials/simulator/gpx-upload", content=chunks(14 * MiB, part),
                                 headers={"Content-Type": "multipart/form-data; boundary=x"})
    assert response.status_code == 413 and "trop lourd" in response.text
    # under the cap the route answers as before (an unreadable event is acknowledged, never queued)
    response = await client.post("/webhook/strava", content=chunks(200_000),
                                 headers={"Content-Type": "application/json"})
    assert response.status_code == 200 and response.json() == {"status": "ok"}


async def test_the_webhook_refuses_a_body_over_a_megabyte(client: AsyncClient):
    response = await client.post("/webhook/strava", content=b"{" + b" " * DEFAULT_LIMIT + b"}",
                                 headers={"Content-Type": "application/json"})
    assert response.status_code == 413


async def test_a_gpx_import_still_goes_through(as_user: AsyncClient):  # noqa: F811
    r = await as_user.post("/partials/simulator/gpx-upload",
                           files={"gpx_file": ("t.gpx", _gpx(), "application/gpx+xml")})
    assert r.status_code == 204 and r.headers["hx-redirect"].startswith("/simulator/routes/")


def test_each_route_that_takes_a_file_or_a_plan_has_room_for_it():
    """The table and the routes' own caps agree: a GPX at simulator.MAX_GPX_BYTES reaches its route (which explains
    a heavier one itself), the phone's caps are its routes', everything else 1 MB."""
    from app.routers import mobile, simulator

    gpx_forms = ("/partials/simulator/gpx-upload", "/partials/simulator/bike-gpx-upload",
                 "/api/simulator/routes/12/reimport")
    for path in gpx_forms:
        assert simulator.MAX_GPX_BYTES < body_limit(path) <= simulator.MAX_GPX_BYTES + 2 * MiB, path
    assert body_limit("/api/simulator/triathlon") > 2 * simulator.MAX_GPX_BYTES
    assert body_limit("/api/mobile/nights") == mobile.MAX_NIGHT_BYTES
    assert body_limit("/api/mobile/daily") == mobile.MAX_DAILY_BYTES
    # a plan not saved yet: its course URL-encoded, route_validation reading 8 M characters at most
    assert body_limit("/api/simulator/routes") >= 8_000_000
    assert body_limit("/partials/simulator/passage-times") >= 8_000_000
    for path in ("/webhook/strava", "/auth/login", "/settings", "/api/simulator/routes/12/params", "/lactate",
                 "/api/simulator/routes/12/reimport/x", "/api/mobile/exchange"):
        assert body_limit(path) == DEFAULT_LIMIT, path


@pytest.mark.parametrize("name", ["app.js"])
def test_the_gpx_forms_show_the_refusal_in_their_error_box(name):
    """htmx swaps no error answer by default: app.js lets a 413 in HTML through, as a 429, never a JSON one."""
    script = (ROOT / "app/static/js" / name).read_text(encoding="utf-8")
    assert "status === 413" in script and "text\\/html" in script
