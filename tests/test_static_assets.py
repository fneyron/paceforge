"""Static caches must speed up navigation without freezing an obsolete build."""
import pytest

from app.build import BUILD_ID


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
