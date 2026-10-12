"""Measured capillary blood lactate, not sweat, wearable thresholds or fatigue.

Plot observations in acquisition order. No fitted LT1/LT2, universal 2/4 mmol/L
zones or recovery penalty. Protocol and sampling context must stay visible.
"""
import math
from datetime import date, timedelta


def number(value, lo, hi, name):
    try:
        n = float(str(value).strip().replace(',', '.'))
    except (TypeError, ValueError):
        raise ValueError(f'{name} : saisis un nombre.') from None
    if not math.isfinite(n) or not lo <= n <= hi:
        raise ValueError(f'{name} : valeur attendue entre {lo} et {hi}.')
    return n


def intensity(value, mode):
    if str(value).strip().lower() in ('repos', '0'):
        return 0.0
    if mode == 'pace':
        try:
            m, s = str(value).strip().split(':')
            seconds = int(m)*60 + int(s)
            if not 0 <= int(s) < 60 or not 90 <= seconds <= 1800:
                raise ValueError()
            return 3600/seconds  # km/h, even when the input is pace
        except (ValueError, TypeError):
            raise ValueError('Allure : utilise min:sec, par exemple 5:00, ou « repos ».') from None
    return number(value, 0, 2500 if mode == 'power' else 40, 'Puissance' if mode == 'power' else 'Vitesse')


def effort_label(value, mode):
    if value == 0:
        return 'Repos'
    if mode == 'pace':
        m, s = divmod(round(3600/value), 60)
        return f'{m}:{s:02d}'
    return f'{value:g}'.replace('.', ',')


def parse(form, today=None):
    mode = form.get('mode')
    if mode not in ('pace', 'speed', 'power'):
        raise ValueError('Choisis une allure, une vitesse ou une puissance.')
    try:
        tested_on = date.fromisoformat(form.get('tested_on', ''))
    except ValueError:
        raise ValueError('Renseigne la date du test.') from None
    if not date(1970,1,1) <= tested_on <= (today or date.today())+timedelta(days=1):
        raise ValueError('La date du test est hors de la période autorisée.')
    data = dict(tested_on=tested_on, mode=mode)
    for key, label, limit in (('device', 'Lecteur', 80), ('protocol', 'Protocole', 160), ('notes', 'Contexte', 1000)):
        value = str(form.get(key, '')).strip()
        if len(value) > limit or (key != 'notes' and not value):
            raise ValueError(f'{label} : entre 1 et {limit} caractères.' if key != 'notes' else 'Contexte : 1 000 caractères maximum.')
        data[key] = value
    columns = {k: form.getlist(k) for k in ('intensity', 'lactate', 'hr', 'minutes')}
    if len({len(v) for v in columns.values()}) != 1 or not 1 <= len(columns['lactate']) <= 30:
        raise ValueError('Ajoute de 1 à 30 paliers.')
    rows = []
    for i in range(len(columns['lactate'])):
        values = {k: str(v[i]).strip() for k,v in columns.items()}
        if not any(values.values()):
            continue
        if not values['lactate'] or not values['intensity']:
            raise ValueError(f'Palier {i+1} : renseigne l’intensité et le lactate.')
        rows.append(dict(intensity=intensity(values['intensity'], mode),
                         lactate=number(values['lactate'], .1, 30, f'Lactate du palier {i+1} (mmol/L)'),
                         hr=number(values['hr'], 25, 230, 'FC (bpm)') if values['hr'] else None,
                         minutes=number(values['minutes'], .1, 180, 'Durée du palier (min)') if values['minutes'] else None))
    if not rows:
        raise ValueError('Renseigne au moins un palier.')
    # The reader's verified published range; LO/HI cannot become a made-up number.
    if data['device'].strip().casefold() == 'lactate pro 2' and any(not .5 <= r['lactate'] <= 25 for r in rows):
        raise ValueError('Lactate Pro 2 : saisis une valeur mesurée entre 0,5 et 25 mmol/L. LO et HI ne sont pas des valeurs numériques.')
    data['stages'] = rows
    return data


def compatible(a, b):
    # We still show both contexts; matching text cannot establish equal conditions.
    return (a.id != b.id and (a.mode == 'power') == (b.mode == 'power')
            and a.protocol.casefold().strip() == b.protocol.casefold().strip()
            and a.device.casefold().strip() == b.device.casefold().strip()
            and [r.get('minutes') for r in a.stages] == [r.get('minutes') for r in b.stages]
            and all(r.get('minutes') is not None for r in a.stages))


def number_label(value):
    return f'{value:.2f}'.rstrip('0').rstrip('.').replace('.', ',')


def chart(test, previous=None):
    tests = [test] + ([previous] if previous else [])
    efforts = sorted({r['intensity'] for t in tests for r in t.stages})
    pad = max((efforts[-1]-efforts[0])*.06, .5 if test.mode != 'power' else 5)
    xmin, xmax = max(0, efforts[0]-pad), efforts[-1]+pad
    ymax = max([r['lactate'] for t in tests for r in t.stages]+[2])*1.12
    x = lambda v: round(34+(v-xmin)/(xmax-xmin)*270, 2)
    y = lambda v: round(158-v/ymax*138, 2)
    curves = []
    for t in tests:
        pts = [dict(x=x(r['intensity']), y=y(r['lactate']), **r, label=number_label(r['intensity'])) for r in t.stages]
        curves.append(dict(points=pts, path=' '.join(f"{'M' if i == 0 else 'L'} {p['x']} {p['y']}" for i,p in enumerate(pts)),
                           date=t.tested_on.strftime('%d/%m/%Y'), previous=t is previous))
    unit = 'W' if test.mode == 'power' else 'km/h'
    ticks = sorted({efforts[round(i*(len(efforts)-1)/3)] for i in range(4)})
    return dict(curves=curves, unit=unit, input_unit={'pace': 'min/km', 'speed': 'km/h', 'power': 'W'}[test.mode],
                xt=[dict(x=x(v), label=number_label(v)) for v in ticks],
                yt=[dict(y=y(v), label=f'{v:.1f}'.replace('.',',')) for v in (0, ymax/3, ymax*2/3, ymax)])


def form_values(test=None):
    if test is None:
        return dict(tested_on=date.today().isoformat(), mode='pace', device='Lactate Pro', protocol='', notes='',
                    stages=[dict(intensity='', lactate='', hr='', minutes='') for _ in range(5)])
    return dict(tested_on=test.tested_on.isoformat(), mode=test.mode, device=test.device, protocol=test.protocol, notes=test.notes,
                stages=[dict(intensity=effort_label(r['intensity'], test.mode), lactate=r['lactate'],
                             hr=r.get('hr') or '', minutes=r.get('minutes') or '') for r in test.stages])
