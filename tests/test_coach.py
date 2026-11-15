"""Synthetic fixtures for tests only. Never loaded into the application database."""
import base64
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from garmin_coach.config import Settings
from garmin_coach.db import Database
from garmin_coach.garmin import GarminClient, GarminError, DAILY_ENDPOINTS, TOKEN_PATH, ACTIVITIES
from garmin_coach.normalize import normalize, activity
from garmin_coach.sync import SyncManager, SyncBusy
from garmin_coach.analytics import analyze
from garmin_coach.app import create_app
from garmin_coach.insights import prioritize


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv('GARMIN_PERSONAL_API_KEY', 'TEST-ONLY-NOT-A-REAL-KEY')
    return Settings(tmp_path, daily_sync=False)


def jwt():
    segment = base64.urlsafe_b64encode(json.dumps({'exp': time.time() + 86400}).encode()).decode().rstrip('=')
    return 'test.' + segment + '.test'


def test_auth_raw_jwt_and_persistent_cache(settings):
    calls = []
    token = jwt()
    def handle(request):
        calls.append(request)
        if request.method == 'POST':
            assert request.url.path == TOKEN_PATH
            assert request.headers['authorization'] == 'ApiKey TEST-ONLY-NOT-A-REAL-KEY'
            return httpx.Response(200, text=token)
        assert request.headers['authorization'] == 'Bearer ' + token
        return httpx.Response(200, json={'totalSteps': 0})
    for _ in range(2):
        c = GarminClient(settings, httpx.MockTransport(handle), pause=lambda _: None)
        assert c.get('/usersummary-service/usersummary/daily', {'calendarDate': '2026-01-15'})[2] == {'totalSteps': 0}
        c.close()
    assert len([r for r in calls if r.method == 'POST']) == 1
    assert (settings.data_dir / 'token.json').stat().st_mode & 0o777 == 0o600


def test_401_refresh_once(settings):
    posts, gets = [], []
    def handle(r):
        if r.method == 'POST':
            posts.append(r)
            return httpx.Response(200, text=jwt())
        gets.append(r)
        return httpx.Response(401 if len(gets) == 1 else 204)
    c = GarminClient(settings, httpx.MockTransport(handle), pause=lambda _: None)
    assert c.get('/test', {}) == (204, '', None)
    assert len(posts) == 2 and len(gets) == 2
    c.close()


def test_rate_limit_persists_cooldown_and_redacts(settings):
    calls=[]
    def handle(r):
        calls.append(r)
        return httpx.Response(429, json={'retryInSeconds': -1, 'secret': 'NEVER-SHOW'})
    c=GarminClient(settings,httpx.MockTransport(handle),pause=lambda _:None)
    with pytest.raises(GarminError) as exc:
        c.token()
    assert 'NEVER-SHOW' not in str(exc.value)
    with pytest.raises(GarminError):
        c.token()
    assert len(calls)==1
    assert json.loads((settings.data_dir/'cooldown.json').read_text())['until']>=time.time()+3500
    c.close()


def test_retry_network_and_server(settings):
    calls=[]
    def handle(r):
        calls.append(r)
        return httpx.Response(503 if len(calls)<3 else 200, text=jwt())
    c=GarminClient(settings,httpx.MockTransport(handle),pause=lambda _:None)
    assert c.token().startswith('test.')
    assert len(calls)==3
    c.close()


def test_normalize_missing_and_sentinels():
    d=normalize('summary', {'totalSteps':0,'averageStressLevel':-1,'restingHeartRate':0,'bodyBatteryAtWakeTime':85})
    assert d['steps']==0 and d['stress'] is None and d['resting_hr_bpm'] is None and d['body_battery_wake']==85
    assert normalize('sleep',{'sleepTimeSeconds':25200,'sleepScores':{'overall':{'value':80}}})['sleep_hours']==7
    assert normalize('sleep',{})=={}
    assert normalize('hrv',{'hrvSummary':{'lastNightAvg':42}})['hrv_ms']==42
    assert activity({'activityId':123,'startTimeLocal':'2026-01-15 09:00:00','duration':600,'distance':1000})[2]['duration_seconds']==600


class FakeGarmin:
    calls=[]
    def __init__(self,settings):pass
    def close(self):pass
    def get(self,path,params):
        self.calls.append((path,params))
        day=params.get('calendarDate') or params.get('date') or params.get('startDate') or path.rsplit('/',1)[-1]
        if path==ACTIVITIES:
            body=[{'activityId':123,'startTimeLocal':day+' 09:00:00','duration':1800,'distance':5000,'activityType':{'typeKey':'running'}}]
        elif path.endswith('/workouts'):
            body=[{'workoutName':'SYNTHETIC TEST WORKOUT','steps':[]}]
        elif 'bodyBattery/reports' in path:
            body=[]
        elif 'dailySleep' in path:
            body={'calendarDate':day,'sleepTimeSeconds':25200}
        elif '/hrv/' in path:
            return 204,'',None
        else:
            body={'calendarDate':day,'totalSteps':5000,'restingHeartRate':55}
        return 200,json.dumps(body),body


def run_sync(manager,day,force=False):
    job,lock=manager.reserve(day,day,force)
    manager.run(job,lock,day,day,force)


def test_sync_resume_upsert_raw_workouts_and_empty(settings):
    db=Database(settings.data_dir);FakeGarmin.calls=[]
    manager=SyncManager(settings,db,FakeGarmin)
    day=date(2026,1,15)
    run_sync(manager,day)
    first=len(FakeGarmin.calls)
    assert db.jobs()[0]['state']=='complete'
    assert db.done('hrv',str(day))
    assert db.days(str(day),str(day))[0]['sleep_hours']==7
    run_sync(manager,day)
    assert len(FakeGarmin.calls)==first
    run_sync(manager,day,True)
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM activities').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM workouts').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM raw').fetchone()[0]==7
    assert db.activities(str(day),str(day))[0]['distance_m']==5000


def test_single_writer(settings):
    db=Database(settings.data_dir);manager=SyncManager(settings,db,FakeGarmin)
    _,lock=manager.reserve(date(2026,1,15),date(2026,1,15))
    try:
        with pytest.raises(SyncBusy):
            manager.reserve(date(2026,1,15),date(2026,1,15))
    finally:
        lock.close()


def test_analytics_calendar_baseline_not_row_count(settings):
    db=Database(settings.data_dir)
    today=date(2026,3,1);anchor=today-timedelta(days=1)
    for offset in range(35):
        day=anchor-timedelta(days=offset)
        db.merge_day(str(day),{'hrv_ms':40 if offset<7 else 60,'resting_hr_bpm':60 if offset<7 else 55},1)
    db.merge_day(str(today),{'hrv_ms':999},1)
    a=analyze(db,date(2026,2,1),today,today)
    h=a['metrics']['hrv_ms']
    assert h['current']==40 and h['baseline']==60 and h['baseline_n']==28 and h['recent_n']==7
    assert any(i['id']=='hrv_ms' and i['level']=='notice' for i in a['insights'])
    assert a['training']['coverage_complete'] is False
    assert 'today' in a['warnings'][-1].lower()


def test_empty_and_sparse_do_not_invent(settings):
    db=Database(settings.data_dir);today=date(2026,3,1)
    a=analyze(db,date(2026,2,1),today,today)
    assert a['insights']==[] and a['days_with_readings']==0
    assert a['metrics']['hrv_ms']['current'] is None
    db.merge_day('2026-02-01',{'hrv_ms':40},1)
    a=analyze(db,date(2026,2,1),today,today)
    assert a['insights']==[] and a['stale'] is True
    assert a['metrics']['hrv_ms']['current'] is None


def test_api_security_empty_dates_export_and_workout(settings,monkeypatch):
    monkeypatch.delenv('GARMIN_PERSONAL_API_KEY')
    app=create_app(settings)
    with TestClient(app) as c:
        assert c.get('/').status_code==200
        assert c.get('/static/app.js').status_code==200
        status=c.get('/api/status').json()
        token=status['session_token']
        assert status['key_configured'] is False
        assert c.get('/api/dashboard').json()['days']==[]
        assert c.get('/api/dashboard?start=2026-02-01&end=2026-01-15').status_code==422
        assert c.get('/api/dashboard?start=2020-01-01').status_code==422
        body={'start':'2026-01-15','end':'2026-01-16'}
        assert c.post('/api/sync',json=body).status_code==403
        assert c.post('/api/sync',json=body,headers={'X-Coach-Token':token}).status_code==409
        assert c.get('/api/status',headers={'Host':'evil.example'}).status_code==400
        assert c.get('/api/status',headers={'Origin':'https://evil.example'}).status_code==403
        assert c.get('/api/raw/999').status_code==404
        assert c.get('/api/workouts/123').status_code==404
        assert c.get('/api/export').json()['daily']==[]
        assert c.get('/').headers['cache-control']=='no-store'


def test_endpoints_match_bundled_official_openapi():
    spec=json.loads((Path(__file__).parent.parent/'docs/garmin-openapi.json').read_text())
    assert spec['servers'][0]['url']=='https://personal.connectapi.garmin.com'
    for template,query in DAILY_ENDPOINTS.values():
        path=template.replace('{day}','{calendarDate}')
        assert path in spec['paths']
        parameters=spec['paths'][path]['get'].get('parameters',[])
        if query and query!='range':
            assert query in [p['name'] for p in parameters]
    assert ACTIVITIES in spec['paths']
    assert '/activity-service/activity/{activityId}/workouts' in spec['paths']


def test_ai_off_does_not_send_data(settings):
    assert prioritize(settings,{'insights':[]})['mode']=='rules'


def test_key_file_permissions_and_no_key_in_status(settings,monkeypatch):
    monkeypatch.delenv('GARMIN_PERSONAL_API_KEY')
    path=settings.data_dir/'garmin.key';path.write_text('SYNTHETIC-KEY');path.chmod(0o644)
    assert not settings.configured()
    path.chmod(0o600)
    assert settings.key()=='SYNTHETIC-KEY'
    with TestClient(create_app(settings)) as c:
        assert 'SYNTHETIC-KEY' not in c.get('/api/status').text


def test_sync_pauses_then_resumes_without_repeating_completed_resources(settings):
    db=Database(settings.data_dir)
    class Interrupted(FakeGarmin):
        fail=True
        calls=[]
        def get(self,path,params):
            if '/hrv/' in path and self.fail:
                raise GarminError('throttled',429)
            return super().get(path,params)
    manager=SyncManager(settings,db,Interrupted)
    day=date(2026,1,15)
    run_sync(manager,day)
    assert db.jobs()[0]['state']=='paused'
    assert db.done('summary',str(day)) and db.done('sleep',str(day))
    completed_before=len([x for x in Interrupted.calls if 'usersummary/daily' in x[0]])
    Interrupted.fail=False
    run_sync(manager,day)
    assert db.jobs()[0]['state']=='complete'
    assert len([x for x in Interrupted.calls if 'usersummary/daily' in x[0]])==completed_before


def test_activity_pagination_and_parent_filter(settings):
    db=Database(settings.data_dir)
    class Pages:
        offsets=[]
        def get(self,path,params):
            if path==ACTIVITIES:
                assert params['excludeChildren']=='true'
                self.offsets.append(params['start'])
                n=100 if params['start']==0 else 1
                body=[{'activityId':params['start']+i+1,'startTimeLocal':'2026-01-15 09:00:00','duration':60} for i in range(n)]
            else:
                body=[]
            return 200,json.dumps(body),body
    client=Pages()
    SyncManager(settings,db).sync_activities(client,'2026-01-15')
    assert client.offsets==[0,100]
    assert len(db.activities('2026-01-15','2026-01-15'))==101


def test_recent_days_refresh_even_when_checkpointed(settings):
    db=Database(settings.data_dir);FakeGarmin.calls=[]
    manager=SyncManager(settings,db,FakeGarmin)
    run_sync(manager,settings.today())
    count=len(FakeGarmin.calls)
    run_sync(manager,settings.today())
    assert len(FakeGarmin.calls)==2*count


def test_local_ai_validates_ids_and_only_sends_aggregates(settings,monkeypatch):
    settings.llm_model='local-test-model'
    cards=[{'id':'hrv_ms','title':'HRV','observation':'Evidence summary','action':'Take it easy','confidence':'limited'}]
    captured=[]
    class LocalClient:
        def __init__(self,**kwargs):
            assert kwargs['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,json):
            captured.append((url,json))
            return httpx.Response(200,json={'response':'{"focus_ids":["invented-reading"]}'},request=httpx.Request('POST',url))
    monkeypatch.setattr('garmin_coach.insights.httpx.Client',LocalClient)
    result=prioritize(settings,{'insights':cards})
    assert result['mode']=='fallback'
    assert result['focus_ids']==['hrv_ms']
    assert captured[0][0]=='http://127.0.0.1:11434/api/generate'
    assert 'TEST-ONLY' not in json.dumps(captured)


def test_successful_http_flow_with_real_ingestion_pipeline(settings):
    app=create_app(settings)
    app.state.sync.client_factory=FakeGarmin
    with TestClient(app) as c:
        token=c.get('/api/status').json()['session_token']
        r=c.post('/api/sync',json={'start':'2026-01-15','end':'2026-01-15'},headers={'X-Coach-Token':token})
        assert r.status_code==202
        app.state.sync.thread.join(timeout=5)
        assert c.get('/api/status').json()['jobs'][0]['state']=='complete'
        d=c.get('/api/dashboard?start=2026-01-15&end=2026-01-15').json()
        assert d['days'][0]['sleep_hours']==7
        source=d['days'][0]['sources']['sleep_hours']
        assert json.loads(c.get('/api/raw/'+str(source)).json()['body'])['sleepTimeSeconds']==25200
        assert c.get('/api/workouts/123').json()['workouts'][0]['workoutName']=='SYNTHETIC TEST WORKOUT'


def test_schema_errors_not_marked_complete(settings):
    db=Database(settings.data_dir)
    class Malformed(FakeGarmin):
        def get(self,path,params):
            if 'dailySleep' in path:
                return 200,'[]',[]
            if '/hrv/' in path:
                return 200,'"wrong-type"','wrong-type'
            return super().get(path,params)
    manager=SyncManager(settings,db,Malformed)
    run_sync(manager,date(2026,1,15))
    assert db.jobs()[0]['state']=='partial'
    assert not db.done('hrv','2026-01-15')


def test_key_rotation_invalidates_cached_token(settings,monkeypatch):
    calls=[]
    def handle(r):
        calls.append(r)
        return httpx.Response(200,text=jwt())
    for key in ['SYNTHETIC-A','SYNTHETIC-B']:
        monkeypatch.setenv('GARMIN_PERSONAL_API_KEY',key)
        c=GarminClient(settings,httpx.MockTransport(handle),pause=lambda _:None)
        c.token();c.close()
    assert len(calls)==2
