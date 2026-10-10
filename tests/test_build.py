from app.build import BUILD_ID, fingerprint


def test_build_identifies_content_and_paths_not_checkout_times(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("old code")
    original = fingerprint(tmp_path)
    source.touch()
    (tmp_path / ".env").write_text("a runtime secret")
    (tmp_path / "cache.pyc").write_bytes(b"runtime bytecode")
    assert fingerprint(tmp_path) == original
    source.write_text("new code")
    changed = fingerprint(tmp_path)
    assert changed != original
    source.rename(tmp_path / "other.py")
    assert fingerprint(tmp_path) != changed


async def test_health_and_asset_urls_share_the_deployed_build(client):
    response = await client.get("/health")
    assert response.status_code == 200 and response.json()["status"] == "ok"
    assert response.json()["build"] == BUILD_ID and len(BUILD_ID) == 64
    assert response.headers["cache-control"] == "no-store"
    page = (await client.get("/auth/login")).text
    assert f"/static/css/tailwind.css?v={BUILD_ID}" in page
