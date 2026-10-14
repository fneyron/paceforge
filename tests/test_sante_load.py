import json
import math
from datetime import timedelta

import pytest

from app.services import sante_load
from tests.test_sante_training import S, T


def test_all_sports_accumulate_then_decay_without_seven_day_cliff():
    sessions=[S(9,20,sport='Run',id=1),S(8,30,sport='VirtualRide',id=2),S(7,25,sport='WeightTraining',hr=None,id=3)]
    rows=sante_load.history(sessions,T)
    assert len(rows)==10
    assert rows[0]['recent'] < rows[1]['recent'] < rows[2]['recent']
    assert rows[2]['estimated']==1 and rows[2]['daily']>0
    for a,b in zip(rows[2:],rows[3:]):
        assert b['recent']==pytest.approx(a['recent']*math.exp(-1/7))
    assert rows[-1]['recent']>0 and rows[-1]['usual'] is None
    assert sante_load.history([],T)==[]


def test_history_uses_no_future_workout_or_hr_bounds_and_waits_for_reference():
    sessions=[S(k,30,id=k) for k in range(100,0,-3)]
    rows=sante_load.history(sessions,T)
    before=T-timedelta(days=15)
    prior=sante_load.history(sessions,before)
    extreme=S(-1,600,hr=180,hr_peak=215,id=999)
    assert sante_load.history(sessions+[extreme],T)==rows
    assert [r for r in rows if r['day']<=before]==prior
    # owner, 2026-10-14: scientific basis — both averages start at the mean daily load of their first 28 days (from
    # 0, « habituelle » read 37 % low at day 42): the usual curve is drawn from day 28 (it was day 43)
    assert rows[26]['usual'] is None and rows[27]['usual'] is not None
    first = sum(r['daily'] for r in rows[:28]) / 28
    assert rows[0]['recent'] == pytest.approx(first + (rows[0]['daily'] - first) * (1 - math.exp(-1 / 7)))
    assert all(r['usual'] is None for r in sante_load.history([S(100,id=1)],T))
    c=sante_load.card(sessions,T)
    assert c['ready'] and len(json.loads(c['data'])['r'])==90
