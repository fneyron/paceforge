"""Accumulated *recorded training*, separate from nocturnal recovery and fitness.

Daily EWMA time constants 7/42 days are descriptive heuristics, not individual
recovery times, injury cutoffs or performance predictions. No future session or
later HR reference participates in a day's load. All received sports count.
"""
import json
import math
from collections import defaultdict
from datetime import timedelta

from app.services import sante_training as st, viz


def history(sessions, today, rest_of=lambda d: 50.0):
    sessions = [s for s in sessions if s.end_day <= today and s.minutes > 0]
    if not sessions:
        return []
    first = min(s.end_day for s in sessions)
    start = max(first, today-timedelta(days=399))
    by = defaultdict(list)
    for s in sessions:
        by[s.end_day].append(s)
    recent = usual = 0.0
    seen_weeks = set()
    out = []
    d = start
    while d <= today:
        daily = 0.0
        measured = estimated = 0
        if by[d]:
            known = [s for s in sessions if s.end_day <= d]
            loads = st.loads(known, d, rest_of(d), st.hr_max(known, d))
            for s in by[d]:
                value, has_hr = loads[s.id]
                daily += value
                measured += has_hr
                estimated += not has_hr
            seen_weeks.add(d-timedelta(days=d.weekday()))
        recent += (daily-recent) * (1-math.exp(-1/7))
        usual += (daily-usual) * (1-math.exp(-1/42))
        ready = (d-start).days >= 42 and len(seen_weeks) >= 4
        out.append(dict(day=d, recent=recent, usual=usual if ready else None, daily=daily,
                        measured=measured, estimated=estimated))
        d += timedelta(days=1)
    return out


def card(sessions, today, rest_of=lambda d: 50.0):
    rows = history(sessions, today, rest_of)[-90:]
    if not rows:
        return None
    chart = viz.lines([r['day'] for r in rows], [
        {'name': 'Récente', 'values': [r['recent'] for r in rows]},
        {'name': 'Habituelle', 'values': [r['usual'] for r in rows], 'cls': 'is-soft'},
    ], title='Charge accumulée des activités reçues sur 90 jours')
    readouts = [[viz.num(r['recent'], unit='u.a.'), 'charge récente',
                 viz.d_long(r['day']) + (' · habituelle : '+viz.num(r['usual'], unit='u.a.') if r['usual'] is not None else ' · repères en cours')]
                for r in rows]
    chart['read'] = readouts[-1]
    data = json.loads(chart['data'])
    data['r'] = readouts
    data['a'] = [' · '.join(r) for r in readouts]
    chart['aria_now'] = data['a'][-1]
    chart['data'] = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    chart['date_now'] = viz.d_short(today)
    chart['ready'] = rows[-1]['usual'] is not None
    chart['estimated'] = sum(r['estimated'] for r in rows)
    chart['recorded'] = sum(r['measured']+r['estimated'] for r in rows)
    return viz.rest(chart, ["", "Récente et habituelle" if chart["ready"] else "Charge récente", ""], chart["summary"])
