from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.lactate import LactateTest
from app.models.user import User
from app.routers.mobile import check_csrf, csrf
from app.services import lactate

router = APIRouter(prefix='/sante/lactate', tags=['lactate'])
templates = Jinja2Templates(directory='app/templates')


async def owned(db, user, test_id):
    row = await db.scalar(select(LactateTest).where(LactateTest.user_id == user.id, LactateTest.id == test_id))
    if row is None:
        raise HTTPException(404, 'Test introuvable.')
    return row


async def render(request, user, db, *, selected=None, edit=None, values=None, error=None, compare=None):
    tests = (await db.scalars(select(LactateTest).where(LactateTest.user_id == user.id)
                             .order_by(LactateTest.tested_on.desc(), LactateTest.id.desc()).limit(200))).all()
    current = selected or (tests[0] if tests else None)
    candidates = [t for t in tests if current and lactate.compatible(current, t)]
    previous = next((t for t in candidates if t.id == compare), None)
    return templates.TemplateResponse(request, 'lactate.html', context=dict(user=user, tests=tests, current=current,
        chart=lactate.chart(current, previous) if current else None, previous=previous, candidates=candidates,
        values=values or lactate.form_values(edit), edit=edit, error=error, csrf=csrf(request),
        effort_label=lactate.effort_label, number_label=lactate.number_label), status_code=400 if error else 200,
        headers={'Cache-Control': 'no-store'})


@router.get('')
async def index(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await render(request, user, db)


@router.get('/{test_id}')
async def detail(request: Request, test_id: int, compare: int | None = None,
                 user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await render(request, user, db, selected=await owned(db,user,test_id), compare=compare)


@router.get('/{test_id}/modifier')
async def edit(request: Request, test_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    row = await owned(db,user,test_id)
    return await render(request, user, db, selected=row, edit=row)


@router.post('')
async def save(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Bounded form parser; no files, no client-owned user ID or computed score.
    form = await request.form(max_fields=140, max_files=0, max_part_size=8192)
    check_csrf(request, str(form.get('csrf_token', '')))
    row = None
    if form.get('test_id'):
        try:
            test_id = int(form['test_id'])
        except (ValueError, TypeError):
            raise HTTPException(400, 'Test invalide.') from None
        row = await owned(db, user, test_id)
    try:
        values = lactate.parse(form)
    except ValueError as exc:
        vals = {k: str(form.get(k,''))[:1000] for k in ('tested_on','mode','device','protocol','notes')}
        vals['stages'] = [dict(zip(('intensity','lactate','hr','minutes'), fields)) for fields in
                          zip(*(form.getlist(k)[:30] for k in ('intensity','lactate','hr','minutes')))]
        return await render(request, user, db, selected=row, edit=row, values=vals, error=str(exc))
    if row is None:
        row = LactateTest(user_id=user.id, **values)
        db.add(row)
    else:
        for key,value in values.items():
            setattr(row,key,value)
    await db.flush()
    return RedirectResponse(f'/sante/lactate/{row.id}', status_code=303)


@router.post('/{test_id}/supprimer')
async def delete(request: Request, test_id: int, csrf_token: str = Form(),
                 user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    check_csrf(request, csrf_token)
    await db.delete(await owned(db,user,test_id))
    return RedirectResponse('/sante/lactate', status_code=303)
