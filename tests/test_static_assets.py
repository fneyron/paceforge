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
