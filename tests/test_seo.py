"""Search engines (owner, 2026-10-17: « référencer paceforge sur Google »): robots.txt, the sitemap of the public
pages, their canonical URL and Search Console's ownership tag; the app's own pages stay out of the crawl."""
from httpx import AsyncClient

from app.config import GOOGLE_SITE_VERIFICATION as TOKEN, settings


async def test_robots_keeps_the_app_out_and_points_to_the_sitemap(client: AsyncClient):
    r = await client.get("/robots.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    lines = r.text.splitlines()
    assert lines[0] == "User-agent: *" and f"Sitemap: {settings.BASE_URL.rstrip('/')}/sitemap.xml" in lines
    for private in ("/sante", "/activities", "/settings", "/simulator", "/api/", "/auth/", "/landing"):
        assert f"Disallow: {private}" in lines
    assert not any(line in ("Disallow: /", "Disallow: /methode", "Disallow: /privacy") for line in lines)


async def test_the_sitemap_lists_the_public_pages(client: AsyncClient):
    r = await client.get("/sitemap.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    base = settings.BASE_URL.rstrip("/")
    assert r.text.count("<loc>") == 3
    for path in ("/", "/methode", "/privacy"):
        assert f"<loc>{base}{path}</loc>" in r.text


async def test_public_pages_carry_their_canonical_url_and_the_ownership_tag(client: AsyncClient):
    assert TOKEN
    for path, canonical in (("/", "/"), ("/landing", "/"), ("/methode", "/methode"), ("/privacy", "/privacy")):
        html = (await client.get(path)).text
        assert f'<link rel="canonical" href="https://paceforge.fr{canonical}">' in html, path
        assert f'<meta name="google-site-verification" content="{TOKEN}">' in html, path
