"""BEST editors share the existing administrator authentication and CSRF policy."""
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import date

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from admin_roles import audit
from restaurant_rankings import (NOTICE, LIVE, items, matches, metadata, order_state,
                                 publication_error, public_rankings, renumber, subtype, subtypes)
from taxonomy_slugs import valid_slug


class StaleRanking(ValueError):
    pass


def register(app, templates, db, guard, csrf, flash, decorate):
    base = '/admin/best/rankings'

    def render(request, name, status=200, **context):
        return templates.TemplateResponse(request=request, name=name, status_code=status,
                                          context={'ranking_notice': NOTICE, **context})

    def find(conn, ranking_id):
        row = conn.execute('SELECT * FROM restaurant_rankings WHERE id=?', (ranking_id,)).fetchone()
        return dict(row) if row else None

    def details(request, ranking=None, status=200, error='', data=None):
        with closing(db()) as conn:
            choices = subtypes(conn)
            selected, candidates, warnings = [], [], []
            state = None
            q = request.query_params.get('q','').strip()[:100]
            if ranking:
                state = order_state(conn, ranking)
                term = subtype(conn, ranking['subtype_taxonomy_id'])
                if not term:
                    warnings.append('기준 세부유형이 비활성 상태이거나 구조가 변경되었습니다. 공개 화면에서 숨겨집니다.')
                if ranking['deleted_at']:
                    warnings.append('삭제 보관 중인 랭킹입니다. 복원하면 같은 ID와 주소를 유지합니다.')
                for item in items(conn, ranking['id']):
                    place = conn.execute('SELECT * FROM places WHERE id=?',(item['place_id'],)).fetchone()
                    valid = matches(conn,item['place_id'],ranking['subtype_taxonomy_id'])
                    selected.append(dict(item, place=decorate(place) if place else None, valid=valid))
                    if not valid:
                        warnings.append('삭제·병합되었거나 세부유형이 달라진 장소가 포함되어 있습니다. 방문자에게 표시하지 않습니다.')
                    elif not place['published']:
                        warnings.append('비공개 장소가 포함되어 있습니다. 방문자에게 표시하지 않습니다.')
                visible_count = sum(item['valid'] and item['place']['published'] for item in selected)
                if 0 < visible_count < 3:
                    warnings.append('비교 장소가 적습니다. 3곳 이상 등록을 권장합니다.')
                if term and not ranking['deleted_at']:
                    candidates = [dict(r) for r in conn.execute("""SELECT p.id,p.name,p.region,p.published,
                        EXISTS(SELECT 1 FROM restaurant_ranking_items i WHERE i.ranking_id=? AND i.place_id=p.id) AS added
                        FROM places p JOIN categories c ON c.id=p.category_id
                        WHERE c.slug='food' AND """+LIVE+""" AND instr(lower(p.name),lower(?))>0
                        AND EXISTS(SELECT 1 FROM place_category_links l WHERE l.place_id=p.id AND l.category_id=?)
                        ORDER BY p.published DESC,p.name,p.id LIMIT 100""",(ranking['id'],q,ranking['subtype_taxonomy_id']))]
                ranking['subtype_name'] = conn.execute('SELECT name FROM categories WHERE id=?',(ranking['subtype_taxonomy_id'],)).fetchone()[0]
        return render(request,'admin/ranking_form.html',status,ranking=ranking,values=data or ranking or {},
                      subtypes=choices,selected_items=selected,candidates=candidates,warnings=list(dict.fromkeys(warnings)),
                      order_states={str(ranking['id']):state} if ranking else {},state=state,q=q,error=error)

    @app.get('/best/ranking/{slug}')
    async def public_detail(request: Request, slug: str):
        with closing(db()) as conn:
            ranking = next((r for r in public_rankings(conn) if r['slug']==slug),None)
        if not ranking:
            return HTMLResponse('랭킹을 찾을 수 없습니다.',status_code=404)
        places = [dict(decorate(r,public=True), ranking_reason=r['ranking_reason'],
                       ranking_item_id=r['ranking_item_id']) for r in ranking.pop('visible_items')]
        return render(request,'ranking_detail.html',ranking=ranking,places=places,seo=metadata(ranking,places))

    @app.get(base)
    async def listing(request: Request):
        if response := guard(request): return response
        q = request.query_params.get('q','').strip()[:100]
        archived = request.query_params.get('archived')=='1'
        with closing(db()) as conn:
            rows = [dict(r) for r in conn.execute("""SELECT r.*,c.name AS subtype_name,
                (SELECT count(*) FROM restaurant_ranking_items i WHERE i.ranking_id=r.id) AS place_count
                FROM restaurant_rankings r JOIN categories c ON c.id=r.subtype_taxonomy_id
                WHERE r.deleted_at IS """+('NOT NULL' if archived else 'NULL')+"""
                AND instr(lower(r.title||' '||c.name),lower(?))>0 ORDER BY r.id""",(q,))]
            for row in rows:
                row['state'] = order_state(conn,find(conn,row['id']))
        return render(request,'admin/rankings.html',rankings=rows,q=q,archived=archived)

    @app.get(base+'/new')
    async def new(request: Request):
        if response := guard(request): return response
        return details(request)

    @app.get(base+'/{ranking_id}/edit')
    async def edit(request: Request, ranking_id:int):
        if response := guard(request): return response
        with closing(db()) as conn: ranking=find(conn,ranking_id)
        return details(request,ranking) if ranking else HTMLResponse('랭킹을 찾을 수 없습니다.',status_code=404)

    def version(conn, ranking, form):
        if form.get('version') != order_state(conn,ranking)['version']:
            raise StaleRanking('다른 작업에서 랭킹이 변경되었습니다. 최신 화면을 다시 불러와주세요.')

    def text(form,key,limit,required=False):
        value = str(form.get(key,'')).strip()
        if len(value)>limit or (required and not value):
            raise ValueError(f'{dict(title="제목",intro="소개",criteria_note="평가 기준",reason="순위 이유")[key]}은(는) '+(f'1~{limit}자' if required else f'{limit}자 이내')+'로 입력해주세요.')
        return value

    @app.post(base+'/new')
    @app.post(base+'/{ranking_id}/edit')
    async def save(request: Request, ranking_id:int=0):
        if response := guard(request): return response
        form = await request.form(max_fields=20)
        if not csrf(request,form): return HTMLResponse('CSRF 검증에 실패했습니다.',status_code=400)
        original = None
        try:
            with closing(db()) as conn,conn:
                conn.execute('BEGIN IMMEDIATE')
                original=find(conn,ranking_id) if ranking_id else None
                if ranking_id and (not original or original['deleted_at']):
                    return HTMLResponse('수정할 랭킹을 찾을 수 없습니다.',status_code=404)
                if original: version(conn,original,form)
                title,intro,criteria = text(form,'title',120,True),text(form,'intro',2000),text(form,'criteria_note',2000)
                reference = str(form.get('reference_date','')).strip()
                if reference:
                    try:
                        reference=date.fromisoformat(reference).isoformat()
                    except ValueError:
                        raise ValueError('평가 기준일을 올바른 날짜로 입력해주세요.') from None
                term_id = int(form.get('subtype_taxonomy_id',0))
                if original and term_id != original['subtype_taxonomy_id']:
                    raise ValueError('랭킹의 기준 세부유형은 변경할 수 없습니다. 해당 유형의 랭킹을 따로 만들어주세요.')
                term=subtype(conn,term_id)
                if not original and not term: raise ValueError('활성 맛집 세부유형을 선택해주세요. 대분류로는 랭킹을 만들 수 없습니다.')
                if not original:
                    if conn.execute('SELECT 1 FROM restaurant_rankings WHERE subtype_taxonomy_id=?',(term_id,)).fetchone():
                        raise ValueError('이 세부유형의 랭킹이 이미 있습니다. 기존 랭킹 또는 삭제 보관함에서 관리해주세요.')
                    # Reuse the taxonomy's stable ASCII slug and existing numeric suffix convention.
                    stem=term['slug'] if valid_slug(term['slug']) else 'ranking-'+uuid.uuid4().hex[:12]
                    slug,n=stem,2
                    while conn.execute('SELECT 1 FROM restaurant_rankings WHERE slug=?',(slug,)).fetchone():
                        slug=f'{stem}-{n}';n+=1
                    ranking_id=conn.execute('INSERT INTO restaurant_rankings(subtype_taxonomy_id,title,slug,intro,criteria_note,reference_date) VALUES(?,?,?,?,?,?)',
                                            (term_id,title,slug,intro,criteria,reference or None)).lastrowid
                conn.execute('UPDATE restaurant_rankings SET title=?,intro=?,criteria_note=?,reference_date=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                             (title,intro,criteria,reference or None,ranking_id))
                public=form.get('is_public')=='1'
                if public and (error:=publication_error(conn,find(conn,ranking_id))): raise ValueError(error)
                conn.execute('UPDATE restaurant_rankings SET is_public=? WHERE id=?',(int(public),ranking_id))
                audit(conn,request,'맛집 랭킹 수정' if original else '맛집 랭킹 생성','맛집 랭킹',ranking_id,title)
                if original and public != bool(original['is_public']): audit(conn,request,'맛집 랭킹 공개' if public else '맛집 랭킹 비공개','맛집 랭킹',ranking_id,title)
        except (ValueError,TypeError,sqlite3.IntegrityError) as exc:
            error = str(exc) if isinstance(exc,ValueError) else '이미 사용 중인 랭킹 또는 연결입니다. 최신 화면을 확인해주세요.'
            return details(request,original,409 if isinstance(exc,StaleRanking) else 400,error,dict(form))
        flash(request,'랭킹을 저장했습니다.','success')
        return RedirectResponse(f'{base}/{ranking_id}/edit',303)

    @app.post(base+'/{ranking_id}/{action}')
    async def mutate(request: Request,ranking_id:int,action:str):
        if response := guard(request): return response
        form=await request.form(max_fields=20)
        if not csrf(request,form): return HTMLResponse('CSRF 검증에 실패했습니다.',status_code=400)
        json_response=action=='reorder'
        try:
            with closing(db()) as conn,conn:
                conn.execute('BEGIN IMMEDIATE')
                ranking=find(conn,ranking_id)
                if not ranking or (ranking['deleted_at'] and action!='restore'):
                    return HTMLResponse('랭킹을 찾을 수 없습니다.',status_code=404)
                version(conn,ranking,form)
                if action=='add':
                    place_id=int(form.get('place_id',0))
                    if not subtype(conn,ranking['subtype_taxonomy_id']) or not matches(conn,place_id,ranking['subtype_taxonomy_id']):
                        raise ValueError('선택한 세부유형이 실제로 연결된 현재 맛집만 추가할 수 있습니다.')
                    conn.execute('INSERT INTO restaurant_ranking_items(ranking_id,place_id,sort_order) VALUES(?,?,?)',
                                 (ranking_id,place_id,len(items(conn,ranking_id))+1))
                    label='랭킹 장소 추가'
                elif action in ('remove','reason'):
                    item_id=int(form.get('item_id',0))
                    if not conn.execute('SELECT 1 FROM restaurant_ranking_items WHERE id=? AND ranking_id=?',(item_id,ranking_id)).fetchone():
                        raise ValueError('이 랭킹에 포함된 장소를 선택해주세요.')
                    if action=='remove':
                        conn.execute('DELETE FROM restaurant_ranking_items WHERE id=? AND ranking_id=?',(item_id,ranking_id));renumber(conn,ranking_id)
                        label='랭킹 장소 제거'
                        if not items(conn,ranking_id): conn.execute('UPDATE restaurant_rankings SET is_public=0 WHERE id=?',(ranking_id,))
                    else:
                        conn.execute('UPDATE restaurant_ranking_items SET reason=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND ranking_id=?',
                                     (text(form,'reason',2000),item_id,ranking_id));label='랭킹 순위 이유 수정'
                elif action=='reorder':
                    ids=json.loads(form.get('ordered_ids','[]'));current=[r['id'] for r in items(conn,ranking_id)]
                    if not isinstance(ids,list) or not ids or len(ids)>2000 or any(type(i) is not int for i in ids) or len(ids)!=len(set(ids)) or set(ids)!=set(current):
                        raise ValueError('이 랭킹의 전체 장소를 중복 없이 보내주세요.')
                    renumber(conn,ranking_id,ids);label='랭킹 순위 변경'
                elif action=='toggle':
                    if not ranking['is_public'] and (error:=publication_error(conn,ranking)): raise ValueError(error)
                    conn.execute('UPDATE restaurant_rankings SET is_public=1-is_public WHERE id=?',(ranking_id,));label='맛집 랭킹 비공개' if ranking['is_public'] else '맛집 랭킹 공개'
                elif action=='delete':
                    conn.execute('UPDATE restaurant_rankings SET deleted_at=CURRENT_TIMESTAMP,is_public=0 WHERE id=?',(ranking_id,));label='맛집 랭킹 삭제 보관'
                elif action=='restore':
                    conn.execute('UPDATE restaurant_rankings SET deleted_at=NULL,is_public=0 WHERE id=?',(ranking_id,));label='맛집 랭킹 복원'
                else:
                    return HTMLResponse('지원하지 않는 작업입니다.',status_code=404)
                conn.execute('UPDATE restaurant_rankings SET updated_at=CURRENT_TIMESTAMP WHERE id=?',(ranking_id,))
                audit(conn,request,label,'맛집 랭킹',ranking_id,ranking['title'])
                result=order_state(conn,find(conn,ranking_id))
        except (ValueError,TypeError,sqlite3.IntegrityError) as exc:
            message=str(exc) if isinstance(exc,ValueError) else '이미 추가된 장소이거나 입력값이 올바르지 않습니다.'
            status=409 if isinstance(exc,StaleRanking) else 400
            if json_response: return JSONResponse({'error':message},status_code=status)
            with closing(db()) as conn: ranking=find(conn,ranking_id)
            return details(request,ranking,status,message)
        if json_response: return JSONResponse({**result,'message':'순위를 저장했습니다.'})
        flash(request,label+' 완료','success')
        return RedirectResponse(base if action=='delete' else f'{base}/{ranking_id}/edit',303)
