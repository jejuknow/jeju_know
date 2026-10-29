"""Anonymous, first-party analytics. No identities, IPs, raw UA or free-text queries."""
import hmac
import json
import logging
import re
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import date, datetime, time as daytime, timedelta, timezone

from fastapi import Request
from fastapi.responses import Response
from itsdangerous import URLSafeTimedSerializer, BadSignature
from starlette.concurrency import run_in_threadpool
from admin_settings import catalog
from auth_security import current_user

KST = timezone(timedelta(hours=9), 'Asia/Seoul')
VISITOR_COOKIE = 'jejuknow_visitor_id'
TEST_COOKIE = 'jejuknow_analytics_test'
VISITOR_AGE = 90 * 86400
SESSION_AGE = 30 * 60
RETENTION = 365 * 86400
FEATURE_EVENTS = {'/map':'map_view','/new':'new_view','/news':'issue_view','/best':'best_view'}
FEATURE_NAMES = [('find_search','FIND'),('map_view','MAP'),('new_view','NEW'),('issue_view','ISSUE'),('best_view','BEST')]
BOT = re.compile(r'bot\b|crawler|spider|slurp|bingpreview|facebookexternalhit|headless|lighthouse|pagespeed|monitor|uptime|curl/|wget/', re.I)


def is_admin(request):
    user = current_user(request)
    return bool(request.session.get('admin_id') or (user and user['role']=='admin'))


def public_path(path):
    return path in ('/','/find','/places','/map','/new','/news','/best') or bool(re.fullmatch(r'/(?:place|news|best)/[^/?#]{1,200}', path))


def device_type(agent):
    # Tablets, including iPad's desktop UA, are included in mobile.
    return 'mobile' if re.search(r'Android|iPhone|iPad|iPod|Mobile|Tablet', agent, re.I) else 'desktop'


def utc_bounds(start, end):
    return (int(datetime.combine(start, daytime.min, KST).timestamp()),
            int(datetime.combine(end+timedelta(days=1), daytime.min, KST).timestamp()))


def period(params, now=None):
    today = datetime.fromtimestamp(now if now is not None else time.time(), KST).date()
    name = params.get('period','30d')
    if name == 'custom':
        try:
            start, end = date.fromisoformat(params.get('start','')), date.fromisoformat(params.get('end',''))
            if end < start or (end-start).days > 365 or end > today:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('시작일·종료일을 확인해주세요. 오늘까지 최대 366일을 선택할 수 있습니다.')
    elif name == 'today': start = end = today
    elif name == 'yesterday': start = end = today-timedelta(days=1)
    elif name in ('7d','30d'):
        end = today; start = today-timedelta(days=6 if name=='7d' else 29)
    else:
        raise ValueError('올바른 통계 기간을 선택해주세요.')
    return dict(name=name,start=start.isoformat(),end=end.isoformat(),bounds=utc_bounds(start,end),today=today)


def filter_values(conn, raw):
    """Allow only current official taxonomy values, never arbitrary strings."""
    if not isinstance(raw, dict): return {}
    regions = {x['value'] for x in catalog(conn,'region')}
    companions = {x['value'] for x in catalog(conn,'companion')}
    categories = {r[0] for r in conn.execute("SELECT slug FROM categories WHERE type='place' AND is_active=1")}
    features = {r[0] for r in conn.execute("SELECT slug FROM categories WHERE type='cafe_feature' AND is_active=1 AND is_legacy=0 AND parent_id=(SELECT id FROM categories WHERE type='place' AND slug='cafe')")}
    result = {}
    for field, allowed in [('region',regions),('category',categories)]:
        value = raw.get(field)
        if isinstance(value,str) and value in allowed: result[field] = value
    for field, allowed, source in [('companion',companions,'companions'),('cafe_features',features,'terms')]:
        values = raw.get(source,[])
        if field=='cafe_features' and result.get('category')!='cafe': continue
        if isinstance(values,list):
            result[field] = sorted({v for v in values[:40] if isinstance(v,str) and v in allowed})
    return result


def report(conn, params, now=None):
    now = int(time.time()) if now is None else now
    selected = period(params,now)
    test = int(params.get('mode')=='test')
    low, high = selected['bounds']
    base = 'is_test=? AND created_at>=? AND created_at<?'
    args = (test,low,high)
    totals = conn.execute('SELECT count(*),count(DISTINCT visitor_id) FROM analytics_events WHERE '+base+" AND event_type='page_view'",args).fetchone()
    views, visitors = totals
    fixed = {}
    for key in ('today','yesterday','7d','30d'):
        left,right = period({'period':key},now)['bounds']
        fixed[key] = conn.execute("SELECT count(DISTINCT visitor_id) FROM analytics_events WHERE is_test=? AND event_type='page_view' AND created_at>=? AND created_at<?",(test,left,right)).fetchone()[0]
    counts = dict(conn.execute('SELECT event_type,count(*) FROM analytics_events WHERE '+base+' GROUP BY event_type',args))
    features = sorted([dict(label=label,count=counts.get(kind,0)) for kind,label in FEATURE_NAMES],key=lambda x:-x['count'])
    maximum = max([x['count'] for x in features]+[1])
    for item in features: item['percent'] = round(item['count']*100/maximum,1)
    daily = dict(conn.execute("SELECT date(created_at,'unixepoch','+9 hours'),count(DISTINCT visitor_id) FROM analytics_events WHERE "+base+" AND event_type='page_view' GROUP BY 1",args))
    start,end = date.fromisoformat(selected['start']),date.fromisoformat(selected['end'])
    days = [dict(date=(start+timedelta(days=i)).isoformat(),count=daily.get((start+timedelta(days=i)).isoformat(),0)) for i in range((end-start).days+1)]
    peak = max([d['count'] for d in days]+[1])
    for i,d in enumerate(days):
        d['x'] = round(24+i*712/max(1,len(days)-1),2); d['y'] = round(150-d['count']*125/peak,2)
    devices = dict(conn.execute('SELECT device_type,count(*) FROM analytics_events WHERE '+base+" AND event_type='page_view' GROUP BY device_type",args))
    labels = {'region':{x['value']:x['label'] for x in catalog(conn,'region',True)},
              'companion':{x['value']:x['label'] for x in catalog(conn,'companion',True)}}
    labels['category'] = dict(conn.execute("SELECT slug,name FROM categories WHERE type='place'"))
    labels['cafe_features'] = dict(conn.execute("SELECT slug,name FROM categories WHERE type='cafe_feature'"))
    rankings = {}
    for field in ('region','category','cafe_features','companion'):
        # Field names are the fixed allowlist above, never request SQL.
        expression = f"json_extract(e.filters,'$.{field}')"
        if field in ('cafe_features','companion'):
            sql = f"SELECT j.value,count(*) FROM analytics_events e,json_each({expression}) j WHERE {base} AND event_type='find_search' GROUP BY j.value ORDER BY count(*) DESC,j.value LIMIT 5"
        else:
            sql = f"SELECT {expression} value,count(*) FROM analytics_events e WHERE {base} AND event_type='find_search' AND {expression} IS NOT NULL GROUP BY value ORDER BY count(*) DESC,value LIMIT 5"
        rankings[field] = [dict(label=labels[field].get(r[0],'삭제된 분류'),count=r[1]) for r in conn.execute(sql,args)]
    started = conn.execute("SELECT value FROM analytics_meta WHERE key='started_at'").fetchone()[0]
    return dict(period=selected,is_test=test,visitors=visitors,views=views,average=round(views/visitors,1) if visitors else 0,
                fixed=fixed,features=features,rankings=rankings,searches=counts.get('find_search',0),days=days,peak=peak,
                points=' '.join(f"{d['x']},{d['y']}" for d in days),
                devices=[dict(label=label,count=devices.get(kind,0),percent=round(devices.get(kind,0)*100/views,1) if views else 0) for kind,label in [('mobile','모바일'),('desktop','PC')]],
                started=datetime.fromtimestamp(started,KST).strftime('%Y.%m.%d %H:%M:%S'))


class Analytics:
    def __init__(self, app, database, secret, admin_guard, templates):
        self.database, self.secure = database, not app.state.testing
        self.signer = URLSafeTimedSerializer(secret,salt='jejuno-analytics-page-v1')
        self.visitor_signer = URLSafeTimedSerializer(secret,salt='jejuno-analytics-visitor-v1')
        self.templates, self.guard = templates, admin_guard
        app.add_api_route('/api/analytics/events',self.collect,methods=['POST'])
        app.add_api_route('/admin/analytics',self.page,methods=['GET'])

    def connect(self):
        conn = sqlite3.connect(self.database,timeout=0.25)
        conn.row_factory = sqlite3.Row
        return conn

    def context(self, request):
        if not public_path(request.url.path) or is_admin(request) or BOT.search(request.headers.get('user-agent','')):
            return None
        test = request.cookies.get(TEST_COOKIE)=='1'
        if request.query_params.get('analytics_test') in ('0','1'):
            test = request.query_params['analytics_test']=='1'
        data = dict(path=request.url.path,page=str(uuid.uuid4()),csrf=request.session['csrf_token'],test=test)
        return self.signer.dumps(data)

    async def collect(self, request: Request):
        if is_admin(request) or BOT.search(request.headers.get('user-agent','')):
            return Response(status_code=204)
        origin = request.headers.get('origin','')
        expected = str(request.base_url).rstrip('/')
        if origin != expected or request.headers.get('sec-fetch-site','same-origin') not in ('same-origin','none'):
            return Response(status_code=403)
        try:
            token = request.headers.get('x-analytics-token','')
            if len(token)>2000: raise ValueError()
            page = self.signer.loads(token,max_age=86400)
            if not hmac.compare_digest(str(page['csrf']),str(request.session.get('csrf_token',''))) or not public_path(page['path']):
                return Response(status_code=403)
            size = 0; chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size>4096: return Response(status_code=413)
                chunks.append(chunk)
            data = json.loads(b''.join(chunks))
            if not isinstance(data,dict) or data.get('type') not in ('page_view','find_search'): raise ValueError()
            if data['type']=='find_search' and page['path'] not in ('/','/find'): raise ValueError()
            event_id = str(uuid.UUID(str(data.get('id',''))))
        except (BadSignature,ValueError,TypeError,KeyError):
            return Response(status_code=400)
        now = int(time.time())
        cookie = request.cookies.get(VISITOR_COOKIE,'')
        issued = now
        try:
            visitor,stamp = self.visitor_signer.loads(cookie,max_age=VISITOR_AGE,return_timestamp=True)
            visitor = str(uuid.UUID(visitor)); issued = int(stamp.timestamp())
        except (BadSignature,ValueError,TypeError):
            visitor = str(uuid.uuid4()); cookie = self.visitor_signer.dumps(visitor)
        agent = request.headers.get('user-agent','')
        device = device_type(agent)
        # Safari on iPad may send a Mac UA; touch capability is a boolean only.
        if data.get('touch_tablet') is True and 'Macintosh' in agent: device = 'mobile'
        try:
            accepted = await run_in_threadpool(self.store,page,data,event_id,visitor,device,now)
        except sqlite3.Error:
            logging.getLogger(__name__).warning('Analytics storage temporarily unavailable')
            return Response(status_code=503)
        response = Response(status_code=204 if accepted else 429,headers={'Cache-Control':'no-store'})
        response.set_cookie(VISITOR_COOKIE,cookie,max_age=max(1,VISITOR_AGE-(now-issued)),secure=self.secure,httponly=True,samesite='lax')
        if page['test']: response.set_cookie(TEST_COOKIE,'1',secure=self.secure,httponly=True,samesite='lax')
        elif request.cookies.get(TEST_COOKIE): response.delete_cookie(TEST_COOKIE,secure=self.secure,httponly=True,samesite='lax')
        return response

    def store(self,page,data,event_id,visitor,device,now):
        kind = data['type']; key = page['page'] if kind=='page_view' else event_id
        with closing(self.connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM analytics_events WHERE event_key=? AND event_type=?',(key,kind)).fetchone(): return True
            filters = json.dumps(filter_values(conn,data.get('filters')),ensure_ascii=False,sort_keys=True) if kind=='find_search' else '{}'
            row = conn.execute('SELECT * FROM analytics_sessions WHERE visitor_id=?',(visitor,)).fetchone()
            session = row['session_id'] if row and now-row['last_seen']<SESSION_AGE else str(uuid.uuid4())
            attempts = row['attempts'] if row and row['minute']==now//60 else 0
            if attempts>=120: return False
            conn.execute('INSERT OR REPLACE INTO analytics_sessions VALUES (?,?,?,?,?)',(visitor,session,now,now//60,attempts+1))
            if kind=='find_search' and conn.execute("SELECT 1 FROM analytics_events WHERE visitor_id=? AND event_type='find_search' AND filters=? AND is_test=? AND created_at>?",(visitor,filters,int(page['test']),now-2)).fetchone(): return True
            feature = {'/':'home','/find':'find','/places':'find','/map':'map','/new':'new','/news':'issue','/best':'best'}.get(page['path'],page['path'].split('/')[1])
            values = (key,kind,visitor,session,page['path'],feature,device,filters,int(page['test']),now)
            sql = 'INSERT OR IGNORE INTO analytics_events(event_key,event_type,visitor_id,session_id,path,feature,device_type,filters,is_test,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)'
            conn.execute(sql,values)
            if kind=='page_view' and page['path'] in FEATURE_EVENTS:
                conn.execute(sql,(key,FEATURE_EVENTS[page['path']],*values[2:]))
            cleanup = conn.execute("SELECT value FROM analytics_meta WHERE key='last_cleanup'").fetchone()[0]
            if now-cleanup>=3600:
                # Bounded maintenance only for this module's data, no scheduler.
                conn.execute('DELETE FROM analytics_events WHERE id IN (SELECT id FROM analytics_events WHERE created_at<? OR (is_test=1 AND created_at<?) LIMIT 1000)',(now-RETENTION,now-7*86400))
                conn.execute('DELETE FROM analytics_sessions WHERE visitor_id IN (SELECT visitor_id FROM analytics_sessions WHERE last_seen<? LIMIT 1000)',(now-VISITOR_AGE,))
                conn.execute("UPDATE analytics_meta SET value=? WHERE key='last_cleanup'",(now,))
        return True

    def page(self,request: Request):
        if response := self.guard(request): return response
        error = None
        with closing(self.connect()) as conn:
            try: stats = report(conn,request.query_params)
            except ValueError as exc:
                error = str(exc); stats = report(conn,{'period':'30d','mode':request.query_params.get('mode')})
        return self.templates.TemplateResponse(request=request,name='admin/analytics.html',context={'analytics':stats,'period_error':error},status_code=400 if error else 200)
