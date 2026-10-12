import json

from app.dependencies import get_current_user
from app.models.user import User
from tests.test_simulator_routes import _create_route, as_user  # noqa: F401


async def test_offline_snapshot_matches_saved_plan_and_live_clock(as_user, test_user):
    route_id = await _create_route(as_user)
    url = f'/api/simulator/routes/{route_id}/offline'
    response = await as_user.get(url)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    snapshot = response.json()
    assert snapshot['user_id'] == test_user.id and snapshot['route_id'] == route_id
    assert snapshot['start_s'] == 21 * 3600 and snapshot['race_date'] == '2026-10-02'
    html = (await as_user.get(f'/simulator/routes/{route_id}')).text
    online = json.loads(html.split('id="plan-data">')[1].split('</script>')[0])
    assert [(p['name'], p['clock_s'], p['cutoff_margin_s']) for p in snapshot['points']] == [
        (p['name'], p['clock_s'], p['cutoff_margin_s']) for p in online['points']]
    col = next(p for p in snapshot['points'] if p['name'] == 'Col')
    assert col['kind'] == 'full' and col['drop_bag'] and col['cutoff_clock'] == '10:30'
    assert 'access_token' not in response.text and 'weather' not in response.text
    await as_user.post(f'/api/simulator/routes/{route_id}/live', data={'anchor_km': 20, 'anchor_clock': '00:58'})
    live = (await as_user.get(url)).json()
    village = next(p for p in live['points'] if p['name'] == 'Village')
    assert village['clock_s'] == 86400 + 58 * 60
    assert village['cutoff_margin_s'] == 16 * 3600 - (3 * 3600 + 58 * 60)


async def test_offline_plan_requires_owner_and_authentication(as_user):
    route_id = await _create_route(as_user)
    url = f'/api/simulator/routes/{route_id}/offline'
    app = as_user._transport.app
    app.dependency_overrides[get_current_user] = lambda: User(id=900001)
    assert (await as_user.get(url)).status_code == 404
    del app.dependency_overrides[get_current_user]
    assert (await as_user.get(url)).status_code in (302, 303, 401)
