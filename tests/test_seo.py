"""Search engines (owner, 2026-10-17: « référencer paceforge sur Google »): robots.txt, the sitemap of the public
pages, their canonical URL and Search Console's ownership tag; the app's own pages stay out of the crawl, the
sign-in pages say noindex themselves."""
import re

import pytest
from httpx import AsyncClient

from app.config import GOOGLE_SITE_VERIFICATION as TOKEN, settings
from app.main import PUBLIC_PAGES

AUTH_PAGES = ("/auth/login", "/auth/register", "/auth/forgot-password", "/auth/reset-password?token=x",
              "/auth/check-email")


async def test_robots_keeps_the_app_out_and_points_to_the_sitemap(client: AsyncClient):
    r = await client.get("/robots.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    lines = r.text.splitlines()
    assert lines[0] == "User-agent: *" and f"Sitemap: {settings.BASE_URL.rstrip('/')}/sitemap.xml" in lines
    for private in ("/sante", "/activities", "/settings", "/simulator", "/api/", "/landing"):
        assert f"Disallow: {private}" in lines
    # the sign-in pages are crawlable, so that their noindex is read
    assert not any(line in ("Disallow: /", "Disallow: /methode", "Disallow: /privacy", "Disallow: /auth/",
                            "Disallow: /cgu", "Disallow: /mentions-legales") for line in lines)


async def test_the_sitemap_lists_the_public_pages(client: AsyncClient):
    r = await client.get("/sitemap.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    base = settings.BASE_URL.rstrip("/")
    assert r.text.count("<loc>") == 5 == len(PUBLIC_PAGES)
    for path in ("/", "/methode", "/privacy", "/mentions-legales", "/cgu"):
        assert f"<loc>{base}{path}</loc>" in r.text


async def test_public_pages_carry_their_canonical_url_and_the_ownership_tag(client: AsyncClient):
    assert TOKEN
    for path, canonical in (("/landing", "/"),) + tuple((p, p) for p in PUBLIC_PAGES):
        html = (await client.get(path)).text
        assert f'<link rel="canonical" href="https://paceforge.fr{canonical}">' in html, path
        assert f'<meta name="google-site-verification" content="{TOKEN}">' in html, path


@pytest.mark.parametrize("path", PUBLIC_PAGES)
async def test_public_pages_have_their_own_short_title_and_description(client: AsyncClient, path):
    html = (await client.get(path)).text
    title = re.search(r"<title>(.*?)</title>", html, re.S)[1].strip()
    og_title = re.search(r'<meta property="og:title" content="([^"]*)">', html)[1]
    description = re.search(r'<meta name="description" content="([^"]*)">', html)[1]
    assert title == og_title and title.endswith(" · PaceForge") and len(title) <= 60, title
    assert description and len(description) <= 160, description
    assert '<meta property="og:site_name" content="PaceForge">' in html
    assert '<meta property="og:locale" content="fr_FR">' in html
    assert '<meta name="robots"' not in html


async def test_public_pages_have_distinct_titles(client: AsyncClient):
    titles = {re.search(r"<title>(.*?)</title>", (await client.get(p)).text, re.S)[1] for p in PUBLIC_PAGES}
    assert len(titles) == len(PUBLIC_PAGES)


@pytest.mark.parametrize("path", AUTH_PAGES)
async def test_sign_in_pages_are_not_indexed(client: AsyncClient, path):
    r = await client.get(path)
    assert r.status_code == 200
    assert '<meta name="robots" content="noindex">' in r.text
    assert "rel=\"canonical\"" not in r.text
