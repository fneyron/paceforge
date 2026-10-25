"""Static caches must speed up navigation without freezing an obsolete build; fonts go out as fonts; the icons
weigh a few kilobytes."""
import json
import struct
from pathlib import Path

import pytest

from app.build import BUILD_ID

ROOT = Path(__file__).resolve().parent.parent


async def test_current_assets_are_reused_without_shared_session_caching(client):
    url = f"/static/vendor/htmx-2.0.4.min.js?v={BUILD_ID}"
    response = await client.get(url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert response.headers["content-encoding"] == "gzip"
    revalidated = await client.get(url, headers={"If-None-Match": response.headers["etag"]})
    assert revalidated.status_code == 304
    assert revalidated.headers["cache-control"] == response.headers["cache-control"]


@pytest.mark.parametrize("path", [
    "/static/vendor/htmx-2.0.4.min.js",
    "/static/vendor/htmx-2.0.4.min.js?v=old-build",
    "/static/sw.js",
])
async def test_unversioned_and_obsolete_urls_must_revalidate(client, path):
    response = await client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


async def test_fonts_go_out_as_fonts_uncompressed_even_without_a_system_mime_table(monkeypatch):
    """python:3.12-slim has no /etc/mime.types: Python's own table alone (as here) sent .woff2 as
    application/octet-stream, gzipped on top of its own compression. The app registers the font types itself."""
    import mimetypes

    from httpx import ASGITransport, AsyncClient
    from starlette.middleware.gzip import GZipMiddleware

    from app.main import create_app

    monkeypatch.setattr(mimetypes, "_db", mimetypes.MimeTypes())  # the built-in table only, as on the image
    assert mimetypes.guess_type("x.woff2") == (None, None)
    app = create_app()
    gzip = next(m for m in app.user_middleware if m.cls is GZipMiddleware)
    assert gzip.kwargs == {"minimum_size": 1024, "compresslevel": 6}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/static/fonts/sora-latin-wght-normal.woff2?v={BUILD_ID}",
                                    headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "font/woff2"
    assert "content-encoding" not in response.headers
    assert mimetypes.guess_type("x.woff") == ("font/woff", None)


def _png(path: Path) -> tuple[int, int, int, int]:
    """Bytes, width, height and colour type (3: a palette) from the PNG's header."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR", path.name
    width, height, _depth, colour = struct.unpack(">IIBB", data[16:26])
    return len(data), width, height, colour


def test_the_icons_are_palette_pngs_of_a_few_kilobytes():
    """They were truecolour PNGs of 178 KB (three copies of one at 512 px) and 25 KB: a palette, the background's
    noise flattened, keeps the look at a few KB. The maskable icon is its own (the mark at 80 % on the background,
    inside the safe zone), no longer a copy of the plain one."""
    img = ROOT / "app/static/img"
    for name, side in {"icon-512.png": 512, "icon-maskable-512.png": 512, "logo.png": 512, "icon-192.png": 192,
                       "apple-touch-icon.png": 180}.items():
        size, width, height, colour = _png(img / name)
        assert (width, height) == (side, side) and colour == 3 and size < 8 * 1024, name
    assert (img / "icon-maskable-512.png").read_bytes() != (img / "icon-512.png").read_bytes()
    manifest = json.loads((ROOT / "app/static/manifest.json").read_text(encoding="utf-8"))
    icons = {icon["purpose"] + " " + icon["src"] for icon in manifest["icons"]}
    assert icons == {"any /static/img/icon-192.png?v=5", "any /static/img/icon-512.png?v=5",
                     "maskable /static/img/icon-maskable-512.png?v=5"}


async def test_pages_ask_for_the_new_icons_and_setup_draws_its_mark(client):
    html = (await client.get("/setup")).text
    for url in ("/static/manifest.json?v=5", "/static/img/icon-192.png?v=5", "/static/img/apple-touch-icon.png?v=5"):
        assert f'href="{url}"' in html, url
    assert "logo.png" not in html  # the header's SVG mark, 48 px, in place of the 178 KB PNG
    assert '<a href="/" class="pf-setup-logo" aria-label="PaceForge"><span class="pf-mark"' in html
