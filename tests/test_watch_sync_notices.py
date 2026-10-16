"""A useful partial import is distinct from a failed or unauthorized connection."""
from datetime import datetime, timezone

import pytest

from app.routers import sante as routes
from app.services import coros, garmin
from app.services.watch_sync_status import sync_notice
from tests import test_coros
from tests.test_coros import _link

as_user, no_commit = test_coros.as_user, test_coros.no_commit
HEADERS = {"Accept": "application/json"}


@pytest.mark.parametrize(("error", "message"), [
    (coros.PARTIAL, "COROS : historique incomplet."),
    (coros.HRV_PARTIAL, "COROS : VFC incomplète."),
    (coros.HRV_UNREADABLE, "COROS : VFC indisponible."),
    (coros.SLEEP_PARTIAL, "COROS : sommeil incomplet."),
    (coros.PHASES_PARTIAL, "COROS : phases du sommeil incomplètes."),
    (coros.ACTIVITIES_PARTIAL, "COROS : import des activités incomplet."),
    (f"{coros.PARTIAL} {coros.ACTIVITIES_PARTIAL}", "COROS : import incomplet."),
    (coros._LEGACY_PARTIAL, "COROS : historique incomplet."),
])
async def test_partial_import_is_visible_without_a_false_failure(as_user, db_session, test_user, error, message):
    await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc), last_error=error)
    page = (await as_user.get("/sante")).text
    assert 'data-sync-note role="status"' in page and message in page
    assert "data-sync-issue=" not in page and "n’a pas pu être actualisé" not in page
    response = await as_user.get("/sante/sync-status", params={"watches": "COROS"}, headers=HEADERS)
    assert response.json() == {"pending": [], "errors": [], "warnings": [message]}
    assert response.headers["cache-control"] == "no-store"
    settings = (await as_user.get("/settings")).text
    assert sync_notice("COROS", error)["label"] in settings
    assert "Dernière synchro échouée" not in settings


@pytest.mark.parametrize(("error", "needs_reauth"), [
    ("COROS ne répond pas pour l'instant.", False),
    (f"{coros.PARTIAL} Une autre erreur inconnue.", False),
    (coros.PARTIAL, True),
])
async def test_unknown_or_auth_errors_are_never_downgraded(as_user, db_session, test_user, error, needs_reauth):
    await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc),
                last_error=error, needs_reauth=needs_reauth)
    expected = "Reconnecte COROS dans Réglages." if needs_reauth else "COROS n’a pas pu être actualisé."
    page = (await as_user.get("/sante")).text
    assert f'data-sync-issue="{expected}"' in page and "data-sync-note" not in page
    result = await as_user.get("/sante/sync-status", params={"watches": "COROS"}, headers=HEADERS)
    assert result.json() == {"pending": [], "errors": [expected]}


async def test_running_retry_does_not_replay_the_previous_partial_warning(as_user, db_session, test_user):
    conn = await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc), last_error=coros.PARTIAL)
    conn.sync_claimed_at = datetime.now(timezone.utc)
    await db_session.flush()
    page = (await as_user.get("/sante")).text
    assert "data-sync-note" not in page and "data-sync-issue=" not in page
    result = await as_user.get("/sante/sync-status", params={"watches": "COROS"}, headers=HEADERS)
    assert result.json() == {"pending": ["COROS"], "errors": []}


async def test_error_to_partial_to_success_refreshes_the_displayed_status(as_user, db_session, test_user):
    conn = await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc), last_error="Network failure")
    version = await routes.data_version(db_session, test_user.id)
    for error in (coros.PARTIAL, coros.HRV_UNREADABLE, None):
        conn.last_error = error
        await db_session.flush()
        response = await as_user.get("/sante/sync-status", params={"v": version})
        assert response.headers.get("HX-Refresh") == "true"
        version = await routes.data_version(db_session, test_user.id)
        response = await as_user.get("/sante/sync-status", params={"v": version})
        assert "HX-Refresh" not in response.headers
    page = (await as_user.get("/sante")).text
    assert "data-sync-note" not in page and "data-sync-issue=" not in page


def test_provider_specific_partial_status_and_auth_priority():
    assert sync_notice("Garmin", garmin.PARTIAL)["level"] == "warning"
    assert sync_notice("COROS", garmin.PARTIAL)["level"] == "error"
    assert sync_notice("COROS", None) is None
    assert sync_notice("COROS", None, True)["kind"] == "auth"
