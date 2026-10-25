"""The legal pages (mentions légales, CGU, the privacy policy), the public pages' landmarks and footer, and the
sign-in forms: labelled help, errors announced and tied to their field, the legal line, the intent carried from
« Créer mon plan » through sign-up to the race plans."""
import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.user import User

LEGAL = ("/privacy", "/mentions-legales", "/cgu")
PUBLIC = ("/", "/methode") + LEGAL
AUTH = ("/auth/login", "/auth/register", "/auth/forgot-password", "/auth/reset-password?token=x",
        "/auth/check-email")


def _text(html: str) -> str:
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).replace("&nbsp;", " ").replace("&#8239;", " ")


async def test_legal_notice(client: AsyncClient):
    r = await client.get("/mentions-legales")
    assert r.status_code == 200
    text = _text(r.text)
    for expected in ("Florent Neyron, particulier", "Directeur de la publication : Florent Neyron",
                     "OVH SAS, 2 rue Kellermann, 59100 Roubaix, France", "+33 9 72 10 10 07",
                     "Gravelines", "Propriété du contenu"):
        assert expected in text, expected
    assert 'href="mailto:contact@paceforge.fr"' in r.text
    assert 'href="/privacy"' in r.text and 'href="/cgu"' in r.text


async def test_terms(client: AsyncClient):
    r = await client.get("/cgu")
    assert r.status_code == 200
    text = _text(r.text)
    for expected in ("gratuit", "tel quel, sans garantie", "estimations indicatives", "ta sécurité et tes décisions",
                     "n’est pas un dispositif médical", "ne remplace pas l’avis d’un médecin", "au moins 15 ans",
                     "supprimer à tout moment", "droit français"):
        assert expected in text, expected
    assert 'href="/privacy"' in r.text


async def test_privacy_policy_is_complete(client: AsyncClient):
    html = (await client.get("/privacy")).text
    text = _text(html)
    for expected in (
        "Florent Neyron", "Finalité", "Base légale", "Durée",
        "Consentement explicite (art. 9.2.a RGPD)", "Intérêt légitime", "Contrat",
        "Apple Santé", "Health Connect", "tests de lactate", "masse grasse",
        "valable 24 h", "1 h", "90 jours", "8 semaines",
        "OVH SAS", "Gravelines", "Strava", "États-Unis, hors UE", "COROS", "Garmin", "Open-Meteo", "Overpass",
        "OpenFreeMap", "CARTO", "Amazon Web Services",
        "Télécharger mes données", "retirer ton consentement", "rectifier", "limitation", "opposer", "directives", "CNIL",
        "sous un mois", "15 ans", "Pas de décision automatique", "paceforge_session", "ancien outil nutrition",
    ):
        assert expected in text, expected
    assert 'href="/mobile/privacy"' in html and 'href="https://www.cnil.fr"' in html
    assert "mailto:contact@paceforge.fr" in html
    # what is not true must not be said: no error tracker in production, no off-site backup yet
    assert "Sentry" not in html and "copie chiffrée" not in html
    assert "sans être utilisé, jusqu’à sa refonte" not in text


async def test_the_phone_privacy_page_agrees(client: AsyncClient):
    html = (await client.get("/mobile/privacy")).text
    assert "consentement" in html and 'href="/privacy"' in html


@pytest.mark.parametrize("path", PUBLIC + ("/nowhere",) + AUTH)
async def test_public_pages_have_one_main_landmark_and_the_legal_footer(client: AsyncClient, path):
    r = await client.get(path)
    html = r.text
    assert html.count("<main") == 1 and '<main id="main-content" tabindex="-1"' in html, path
    if path != "/nowhere":
        footer = html[html.rfind("<footer"):]
        for link in ('href="/privacy"', 'href="/cgu"', 'href="/mentions-legales"', 'href="mailto:contact@paceforge.fr"'):
            assert link in footer, (path, link)
    assert "gmail" not in html


@pytest.mark.parametrize("path", PUBLIC + ("/nowhere",))
async def test_public_header_pages_offer_the_skip_link(client: AsyncClient, path):
    html = (await client.get(path)).text
    assert '<a href="#main-content" class="pf-skip-link">Aller au contenu</a>' in html


async def test_landing_promises_only_what_it_does(client: AsyncClient):
    text = _text((await client.get("/")).text)
    assert "Gratuit · COROS ou Garmin · Strava en option" in text
    assert "COROS, Garmin ou Strava" not in text
    assert "Ton compte a été supprimé." not in text


async def test_landing_confirms_an_account_deletion(client: AsyncClient):
    html = (await client.get("/?account_deleted=1")).text
    assert re.search(r'role="status">Ton compte a été supprimé\.<', html)


async def test_methode_headings_keep_their_words_apart(client: AsyncClient):
    text = _text((await client.get("/methode")).text)
    for name in ("La pente", "La fatigue", "La nuit", "Tes arrêts"):
        assert f"{name} sur courses réelles" in text and f"{name}sur" not in text


async def test_methode_sign_up_leads_to_the_plans(client: AsyncClient):
    html = (await client.get("/methode")).text
    assert 'href="/auth/register?next=simulator"' in html


async def test_register_form_is_labelled_and_states_its_rule(client: AsyncClient):
    html = (await client.get("/auth/register")).text
    assert 'autocomplete="given-name"' in html and 'autocomplete="family-name"' in html
    assert 'autocomplete="email"' in html and 'autocomplete="new-password"' in html
    assert re.search(r'for="lastname"[^>]*>Nom <span[^>]*>\(facultatif\)</span>', html)
    assert 'aria-describedby="password-help"' in html and '<p id="password-help" class="pf-field-help">8 caractères minimum.</p>' in html
    assert "placeholder=\"8 caractères minimum\"" not in html
    assert 'autofocus' in html and 'role="alert"' not in html and "aria-invalid" not in html
    legal = re.search(r"En créant un compte, tu acceptes .*?</p>", html, re.S)[0]
    assert 'href="/cgu"' in legal and 'href="/privacy"' in legal and "politique de confidentialité" in legal


async def test_register_errors_are_announced_on_their_field(client: AsyncClient, test_user, db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    test_user.email = "taken@x.fr"
    await db_session.flush()
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        html = (await c.post("/auth/register", data={"email": "taken@x.fr", "password": "pw-12345678"})).text
        assert '<div id="form-error" class="mb-4 pf-note !text-danger" role="alert">' in html
        email = re.search(r'<input type="email"[^>]*>', html, re.S)[0]
        assert 'aria-invalid="true"' in email and 'aria-describedby="form-error"' in email
        assert "autofocus" not in html
        html = (await c.post("/auth/register", data={"email": "new@x.fr", "password": "short"})).text
        password = re.search(r'<input type="password"[^>]*>', html, re.S)[0]
        assert 'aria-invalid="true"' in password and 'aria-describedby="password-help form-error"' in password


async def test_login_errors_are_announced_on_both_fields(client: AsyncClient):
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        html = (await c.post("/auth/login", data={"email": "nobody@x.fr", "password": "wrong-password"})).text
    assert 'id="form-error"' in html and 'role="alert"' in html and "autofocus" not in html
    for field in re.findall(r'<input type="(?:email|password)"[^>]*>', html, re.S):
        assert 'aria-invalid="true"' in field and 'aria-describedby="form-error"' in field
    assert 'autocomplete="current-password"' in html


async def test_reset_form_states_its_rule(client: AsyncClient):
    html = (await client.get("/auth/reset-password?token=x")).text
    assert 'autocomplete="new-password"' in html and 'aria-describedby="password-help"' in html


async def test_create_my_plan_lands_on_the_plans_after_the_first_login(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    sent = []
    monkeypatch.setattr("app.routers.auth.send_verification_email", lambda email, token: sent.append(token) or True)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        page = (await c.get("/auth/register?next=simulator")).text
        assert '<input type="hidden" name="next" value="simulator">' in page
        form = {"email": "plan@x.fr", "password": "pw-12345678", "next": "simulator"}
        assert (await c.post("/auth/register", data=form)).status_code == 302
        assert "Email vérifié" in (await c.get("/auth/verify-email", params={"token": sent[0]})).text
        assert 'value="simulator"' in (await c.get("/auth/login")).text
        r = await c.post("/auth/login", data={"email": "plan@x.fr", "password": "pw-12345678"})
        assert r.status_code == 302 and r.headers["location"] == "/simulator"
        # used once: the next login goes home
        await c.post("/auth/logout")
        r = await c.post("/auth/login", data={"email": "plan@x.fr", "password": "pw-12345678"})
        assert r.headers["location"] == "/sante"
    assert (await db_session.execute(select(User).where(User.email == "plan@x.fr"))).scalar_one().email_verified


@pytest.mark.parametrize("next_value", ["https://evil.example/", "//evil.example", "/settings", "javascript:x"])
async def test_only_known_destinations_follow_a_sign_up(client: AsyncClient, db_session, monkeypatch, next_value):
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    monkeypatch.setattr("app.routers.auth.send_verification_email", lambda *a: True)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        assert 'name="next"' not in (await c.get("/auth/register", params={"next": next_value})).text
        await c.post("/auth/register", data={"email": "x@x.fr", "password": "pw-12345678", "next": next_value})
        user = (await db_session.execute(select(User).where(User.email == "x@x.fr"))).scalar_one()
        user.email_verified = True
        await db_session.flush()
        r = await c.post("/auth/login", data={"email": "x@x.fr", "password": "pw-12345678", "next": next_value})
        assert r.headers["location"] == "/sante"
