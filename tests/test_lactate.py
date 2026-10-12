import importlib.util
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, select, text
from starlette.datastructures import FormData

from app.models.lactate import LactateTest
from app.models.user import User
from app.services import lactate
from tests.test_mobile import login


def fields(**overrides):
    return dict(tested_on=date.today().isoformat(),mode='pace',device='Lactate Pro 2',protocol='Tapis 4 min',
        notes='Prélèvement en fin de palier',intensity=['6:00','5:00','4:00'],lactate=['1,2','2,1','4,5'],
        minutes=['4','4','4'],hr=['135','145','165'],**overrides)


def data(**overrides):
    f=fields(); f.update(overrides)
    return FormData([(k,item) for k,v in f.items() for item in (v if isinstance(v,list) else [v])])


def test_units_observed_order_and_no_automatic_thresholds():
    values=lactate.parse(data(lactate=['2,1','1,8','4,2']))
    assert [p['intensity'] for p in values['stages']]==[10,12,15]
    assert [p['lactate'] for p in values['stages']]==[2.1,1.8,4.2]
    test=SimpleNamespace(id=1,**values)
    chart=lactate.chart(test)
    assert chart['unit']=='km/h' and chart['input_unit']=='min/km' and len(chart['curves'][0]['points'])==3
    assert not {'lt1','lt2','fatigue','recovery','threshold'} & set(chart)
    assert lactate.parse(data(mode='power',intensity=['0','200','250']))['stages'][1]['intensity']==200
    assert lactate.form_values(test)['stages'][0]['intensity']=='6:00'


@pytest.mark.parametrize('changes',[
    {'lactate':['NaN','2','3']},{'lactate':['inf','2','3']},{'lactate':['LO','2','3']},
    {'lactate':['0.2','2','3']},{'lactate':['26','2','3']},{'mode':'bpm'},
    {'intensity':['4:90','5:00','6:00']},{'minutes':['-2','4','4']},
    {'hr':['300','145','155']},{'protocol':''},{'notes':'x'*1001},
    {'lactate':['1']},{'tested_on':'2099-01-01'},
])
def test_invalid_values_never_become_measurements(changes):
    with pytest.raises(ValueError): lactate.parse(data(**changes))


def test_comparison_requires_compatible_context_and_known_durations():
    values=lactate.parse(data()); a=SimpleNamespace(id=1,**values); b=SimpleNamespace(id=2,**values)
    assert lactate.compatible(a,b)
    b.mode='power'; assert not lactate.compatible(a,b)
    b.mode='speed'; assert lactate.compatible(a,b)
    b.device='Other'; assert not lactate.compatible(a,b)
    b.device=a.device; b.protocol='3 min'; assert not lactate.compatible(a,b)
    b.protocol=a.protocol; b.stages=[dict(p,minutes=None) for p in b.stages]; assert not lactate.compatible(a,b)


async def test_crud_keeps_owner_csrf_and_escaped_context(client,db_session,test_user):
    assert (await client.get('/sante/lactate')).status_code==303
    login(client,test_user)
    page=await client.get('/sante/lactate'); assert page.status_code==200
    csrf=re.search(r'name="csrf_token" value="([^"]+)"',page.text)[1]
    f=fields(); f.update(csrf_token=csrf,notes='<script>alert(1)</script>')
    assert (await client.post('/sante/lactate',data={**f,'csrf_token':'bad'})).status_code==403
    saved=await client.post('/sante/lactate',data=f)
    assert saved.status_code==303
    url=saved.headers['location']; row=await db_session.scalar(select(LactateTest))
    assert row.user_id==test_user.id and len(row.stages)==3
    response=await client.get(url)
    assert response.status_code==200 and '&lt;script&gt;' in response.text
    assert 'Courbe de lactate' in response.text and 'Lactate Pro 2' in response.text
    # Bad edit preserves original and the submitted form for correction.
    bad={**f,'test_id':str(row.id),'lactate':['1','bad','3']}
    response=await client.post('/sante/lactate',data=bad)
    assert response.status_code==400 and 'value="bad"' in response.text
    assert row.stages[1]['lactate']==2.1
    f.update(test_id=str(row.id),lactate=['1','2','3'])
    assert (await client.post('/sante/lactate',data=f)).status_code==303
    assert row.stages[2]['lactate']==3
    other=User(firstname='Other'); db_session.add(other); await db_session.flush()
    client.cookies.clear(); login(client,other)
    assert (await client.get(url)).status_code==404
    page=await client.get('/sante/lactate'); other_csrf=re.search(r'name="csrf_token" value="([^"]+)"',page.text)[1]
    assert (await client.post('/sante/lactate',data={**f,'csrf_token':other_csrf})).status_code==404
    assert (await client.post(url+'/supprimer',data={'csrf_token':other_csrf})).status_code==404
    client.cookies.clear(); login(client,test_user)
    page=await client.get(url+'/modifier'); csrf=re.search(r'name="csrf_token" value="([^"]+)"',page.text)[1]
    assert (await client.post(url+'/supprimer',data={'csrf_token':csrf})).status_code==303
    assert await db_session.scalar(select(LactateTest)) is None


def test_migration_roundtrip(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec=importlib.util.spec_from_file_location('lactate_migration',Path('alembic/versions/f6c7d8e9f0a1_lactate_tests.py'))
    migration=importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    engine=create_engine('sqlite:///'+str(tmp_path/'lactate.db'))
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
        conn.execute(text('INSERT INTO users VALUES (7)'))
        migration.op=Operations(MigrationContext.configure(conn)); migration.upgrade()
        assert 'lactate_tests' in inspect(conn).get_table_names()
        conn.execute(text("INSERT INTO lactate_tests (user_id,tested_on,mode,device,protocol,notes,stages) VALUES (7,'2026-10-01','pace','Lactate Pro','4 min','','[]')"))
        migration.downgrade()
        assert 'lactate_tests' not in inspect(conn).get_table_names()
        assert conn.execute(text('SELECT id FROM users')).scalar()==7
    engine.dispose()
