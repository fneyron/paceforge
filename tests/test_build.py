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


def test_build_covers_what_a_deploy_ships(tmp_path):
    """The deploy's smoke test waits for this fingerprint: a change to the code, the migrations or a lock file
    must move it, a change to anything else (tests, docs, and the Dockerfile and compose file Coolify rewrites at
    build time) must not."""
    from app.build import DEPLOYED

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("app")
    (tmp_path / "alembic" / "versions").mkdir(parents=True)
    (tmp_path / "alembic" / "versions" / "0001_a.py").write_text("up")
    for name in DEPLOYED[2:]:
        (tmp_path / name).write_text(name)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("t")
    original = fingerprint(tmp_path, DEPLOYED)
    (tmp_path / "tests" / "test_x.py").write_text("changed")
    (tmp_path / "README.md").write_text("doc")
    (tmp_path / "app" / "__pycache__").mkdir()
    (tmp_path / "app" / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"bytecode")
    (tmp_path / "Dockerfile").write_text("FROM python\nARG COOLIFY_FQDN")  # what Coolify builds from
    (tmp_path / "docker-compose.prod.yml").write_text("services: {}")
    assert fingerprint(tmp_path, DEPLOYED) == original
    for name in ("app/main.py", "alembic/versions/0001_a.py", "pyproject.toml", "requirements.lock"):
        before = fingerprint(tmp_path, DEPLOYED)
        (tmp_path / name).write_text("new " + name)
        assert fingerprint(tmp_path, DEPLOYED) != before, name


def test_an_image_reports_the_build_id_it_fixed_before_minifying(tmp_path):
    """app/BUILD_ID (written by the Dockerfile before it minifies the CSS) is the build id when it holds one; a tree
    without it, or with anything else in it, fingerprints itself. It never counts in a fingerprint."""
    from app.build import FROZEN, frozen

    assert FROZEN.name == "BUILD_ID" and FROZEN.parent.name == "app" and not FROZEN.exists()  # never in a tree
    kept = tmp_path / "BUILD_ID"
    assert frozen(kept) is None
    kept.write_text("ab" * 32 + "\n")
    assert frozen(kept) == "ab" * 32
    for junk in ("", "not a build id", "AB" * 32, "ab" * 31):
        kept.write_text(junk)
        assert frozen(kept) is None, junk
    (tmp_path / "app.py").write_text("code")
    before = fingerprint(tmp_path)
    kept.write_text("cd" * 32)
    assert fingerprint(tmp_path) == before
