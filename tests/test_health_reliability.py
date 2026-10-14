"""Freshness/receipt regressions, with synthetic records (not clinical validation)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.health import HealthMetric
from app.models.mobile import MobileDaily, MobileDevice
from app.models.user import User
from app.routers.sante import data_version
from app.services import sante, sante_score as sc, sante_training as st
from app.services.sante_daily import daily_context
from tests.test_mobile import day, paired
from tests.test_sante_score import D, _day, _nights, _rich, night_rows


async def test_receipt_counts_only_retained_updates_and_is_private(client, db_session, test_user):
    account, _ = await paired(client, test_user)
    headers = {"Authorization": "Bearer " + account["token"]}
    at = datetime.now(timezone.utc) - timedelta(days=2)
    payload = day(date=at.date().isoformat(), measured_at=at.isoformat(), value=72)
    first = await client.post('/api/mobile/daily', json={'days': [payload]}, headers=headers)
    assert first.json()['received'] == 1
    phone = await db_session.scalar(select(MobileDevice).where(MobileDevice.user_id == test_user.id))
    sent = phone.last_sync_at
    other = User(firstname='Other')
    db_session.add(other); await db_session.flush()
    db_session.add(MobileDaily(user_id=other.id, platform='ios', date=at.date(), metric='weight',
                              value=90, sources=['Other'], measured_at=at + timedelta(minutes=1)))
    await db_session.flush()
    payload.update(value=60, measured_at=(at - timedelta(minutes=1)).isoformat())
    old = await client.post('/api/mobile/daily', json={'days': [payload]}, headers=headers)
    assert old.headers['cache-control'] == 'no-store'
    assert (old.json()['received'], old.json()['skipped']) == (0, 1)
    assert old.json()['latest']['weight']['value'] == 72
    assert phone.last_sync_at == sent  # no false successful import timestamp
    empty = await client.post('/api/mobile/daily', json={'days': []}, headers=headers)
    assert empty.json()['received'] == 0 and empty.json()['latest'] == old.json()['latest']
    assert phone.last_sync_at == sent


async def test_latest_scale_reading_beats_an_undated_daily_export(db_session, test_user):
    at = datetime.now(timezone.utc) - timedelta(days=1)
    direct = HealthMetric(user_id=test_user.id, date=at.date(), metric='weight', value=70, source='COROS')
    db_session.add(direct)
    db_session.add(MobileDaily(user_id=test_user.id, platform='ios', date=at.date(), metric='weight',
        value=72, measured_at=at, sources=['Scale'], updated_at=at))
    # A reimported older Android weighing must not win because it arrived later.
    db_session.add(MobileDaily(user_id=test_user.id, platform='android', date=at.date(), metric='weight',
        value=68, measured_at=at - timedelta(minutes=1), sources=['Scale'], updated_at=at + timedelta(hours=1)))
    await db_session.flush()
    cards = await daily_context(db_session, test_user.id, at.date())
    assert next(c for c in cards if c['key'] == 'weight')['value'].startswith('72')
    # A genuinely later, explicitly dated direct reading retains precedence.
    direct.details = {'measured_at': (at + timedelta(minutes=1)).isoformat()}
    await db_session.flush()
    cards = await daily_context(db_session, test_user.id, at.date())
    assert next(c for c in cards if c['key'] == 'weight')['value'].startswith('70')


async def test_mobile_changes_refresh_sante_even_within_one_second(db_session, test_user):
    at = datetime.now(timezone.utc).replace(microsecond=1000)
    before = await data_version(db_session, test_user.id)
    row = MobileDaily(user_id=test_user.id, platform='ios', date=at.date(), metric='weight',
                      value=70, measured_at=at, sources=['Scale'], updated_at=at)
    db_session.add(row); await db_session.flush()
    first = await data_version(db_session, test_user.id)
    row.value = 71; row.updated_at = at.replace(microsecond=2000)
    await db_session.flush()
    second = await data_version(db_session, test_user.id)
    assert len({before, first, second}) == 3


async def test_phone_only_local_next_day_is_not_hidden(db_session, test_user):
    now = datetime(2026, 10, 12, 23, 30, tzinfo=timezone.utc)
    tomorrow = now.date() + timedelta(days=1)
    db_session.add(MobileDaily(user_id=test_user.id, platform='ios', date=tomorrow, metric='weight',
                              value=70, measured_at=now, sources=['Scale']))
    await db_session.flush()
    assert await st.athlete_today(db_session, test_user.id, now) == tomorrow
    # No data: the supplied clock, not the machine's real date; in the athlete's zone once users.timezone is set,
    # else their latest activity's UTC offset, else UTC (review, 2026-10-14: the zone is NULL for every athlete
    # who signed up before it, so Europe/Paris in its place moved them all; 23:30 UTC on 12/09 stays the 12th)
    other = User(firstname='Other'); db_session.add(other); await db_session.flush()
    assert await st.athlete_today(db_session, other.id, now - timedelta(days=30)) == now.date() - timedelta(days=30)
    other.timezone = "Europe/Paris"  # 01:30 on 13/09 there
    assert await st.athlete_today(db_session, other.id, now - timedelta(days=30)) == now.date() - timedelta(days=29)
    other.timezone = "America/New_York"  # 19:30 on 12/09 there
    assert await st.athlete_today(db_session, other.id, now - timedelta(days=30)) == now.date() - timedelta(days=30)


def test_old_heart_trends_do_not_become_fresh_because_sleep_arrived():
    rows = _rich()
    rows['hrv'].pop(D); rows['hr_night'].pop(D)
    day = _day(rows)
    assert day['stats']['hr']['value'] is not None  # trend remains visible
    # last night's heart not measured: no state (it was a sleep-only score capped at 80)
    assert day['score']['parts'] == [] and day['score']['value'] is None
    assert day['score']['missing'] == {'key': 'no_heart', 'label': 'pas de bilan'}
    quality = sc.quality(day)
    assert not quality['partial'] and quality['label'] == 'Données du bilan'
    assert [r['state'] for r in quality['rows']] == ['Utilisé', 'Absente pour cette nuit', 'Absente pour cette nuit']
    card = sante._night_card(_nights(rows), 'hr', D, day)
    assert card['latest'] is None and card['trend']['value']


def test_last_night_is_not_the_seven_day_average():
    rows = _rich()
    _, detail, source = rows['hr_night'][D]
    rows['hr_night'][D] = (60, detail, source)
    day = _day(rows)
    card = sante._night_card(_nights(rows), 'hr', D, day)
    assert card['latest'].startswith('60')
    assert card['latest'] != card['trend']['value']
    assert '7' in card['trend']['short_label']


def test_quality_distinguishes_building_from_missing_and_never_claims_precision():
    """« Mesures utilisées » says each signal's place: used, building its baseline (n/14: owner, 2026-10-14:
    scientific basis — a provisional baseline made a provisional score), absent; never a percentage."""
    young = _day(night_rows(range(10)))
    quality = sc.quality(young)
    assert not quality['partial'] and not quality['provisional'] and young['score']['value'] is None
    assert [r['state'] for r in quality['rows']] == ['Utilisé', 'Repères en construction (2/14)',
                                                  'Repères en construction (2/14)']
    assert '%' not in str(quality)
    mature = sc.quality(_day(_rich()))
    assert not mature['partial'] and not mature['provisional']
    assert mature['label'] == 'Données du bilan'
    assert [r['state'] for r in mature['rows']] == ['Utilisé', 'Utilisée', 'Utilisée']
    no_hr = _rich()
    no_hr['hr_night'].pop(D)
    partial = sc.quality(_day(no_hr))
    assert partial['partial'] and partial['label'] == 'Bilan partiel'  # the indice from the VFC alone
