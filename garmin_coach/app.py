from __future__ import annotations
import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal, Optional
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import Settings
from .db import Database
from .sync import SyncManager, SyncBusy
from .analytics import analyze
from .insights import prioritize
from .coach import LocalCoach, evidence_package
from . import __version__, INSTALLATION_ID


class SyncRequest(BaseModel):
    start: date
    end: date
    force: bool = False


class CoachRequest(BaseModel):
    end: Optional[date] = None
    kind: Literal['daily', 'weekly'] = 'daily'
    question: str = Field(default='', max_length=800)
    energy: Literal['unspecified', 'low', 'usual', 'high'] = 'unspecified'
    soreness: Literal['unspecified', 'none', 'mild', 'unusual'] = 'unspecified'
    notes: str = Field(default='', max_length=400)


def create_app(settings=None):
    settings = settings or Settings.load()
    db = Database(settings.data_dir)
    sync = SyncManager(settings, db)
    coach = LocalCoach(settings, db)
    csrf = secrets.token_urlsafe(32)

    async def scheduler():
        while True:
            local = datetime.now(ZoneInfo(settings.timezone))
            today = local.date()
            if settings.daily_sync and settings.configured() and local.hour >= settings.sync_hour:
                last = db.meta('daily_attempt')
                if last != today.isoformat():
                    try:
                        sync.start(max(settings.start_date, today - timedelta(days=7)), today)
                        db.meta('daily_attempt', today.isoformat())
                    except SyncBusy:
                        pass
                    except ValueError:
                        pass
            await asyncio.sleep(30)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(scheduler())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(title='Garmin Coach', lifespan=lifespan)
    app.state.settings, app.state.db, app.state.sync = settings, db, sync
    app.state.coach = coach
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'])

    @app.middleware('http')
    async def local_guard(request: Request, call_next):
        origin = request.headers.get('origin')
        expected_origin = str(request.base_url).rstrip('/')
        if origin and origin != expected_origin:
            return JSONResponse({'detail': 'Cross-origin access denied'}, status_code=403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not secrets.compare_digest(request.headers.get('x-coach-token', ''), csrf):
            return JSONResponse({'detail': 'Invalid local session token'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.get('/api/status')
    def status():
        jobs = db.jobs()
        running = sync.thread is not None and sync.thread.is_alive()
        return {'key_configured': settings.configured(), 'timezone': settings.timezone,
                'history_start': settings.start_date.isoformat(), 'today': settings.today().isoformat(),
                'daily_sync': settings.daily_sync, 'sync_hour': settings.sync_hour,
                'jobs': jobs, 'running': running, 'session_token': csrf,
                'llm_enabled': bool(settings.llm_model), 'local_only': True,
                'app_version': __version__, 'installation_id': INSTALLATION_ID}

    def bounds(start, end):
        today = settings.today()
        end = end or today
        start = start or max(settings.start_date, end - timedelta(days=29))
        if start > end or start < settings.start_date or end > today:
            raise HTTPException(422, 'Choose dates from history start through today')
        return start, end

    @app.get('/api/dashboard')
    def dashboard(start: date = None, end: date = None):
        start, end = bounds(start, end)
        return analyze(db, start, end, settings.today())

    @app.post('/api/sync', status_code=202)
    def start_sync(body: SyncRequest):
        if not settings.configured():
            raise HTTPException(409, 'Set your key locally: python -m garmin_coach configure')
        try:
            job = sync.start(body.start, body.end, body.force)
            return {'job_id': job}
        except SyncBusy as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post('/api/insights')
    def ai_insights(body: SyncRequest):
        start, end = bounds(body.start, body.end)
        return prioritize(settings, analyze(db, start, end, settings.today()))

    @app.get('/api/coach/status')
    def coach_status():
        return coach.status()

    @app.post('/api/coach')
    def coach_report(body: CoachRequest):
        _, end = bounds(None, body.end)
        return coach.generate(end, body.kind, body.question.strip(),
                              {'energy': body.energy, 'soreness': body.soreness, 'notes': body.notes.strip()})

    @app.get('/api/coach/export')
    def coach_export(end: date = None):
        _, end = bounds(None, end)
        return JSONResponse({'instructions': 'Explain these wearable comparisons cautiously. Distinguish observations from possible explanations. Do not diagnose or infer missing data.',
                             'evidence': evidence_package(db, end, settings.today())},
                            headers={'Content-Disposition': 'attachment; filename="garmin-coach-for-chatgpt.json"'})

    @app.get('/api/raw/{raw_id}')
    def raw(raw_id: int):
        with db.connect() as c:
            row = c.execute('SELECT * FROM raw WHERE id=?', (raw_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Raw response not found')
        result = dict(row)
        result['params'] = json.loads(result['params'])
        return result

    @app.get('/api/workouts/{activity_id}')
    def workout(activity_id: int):
        with db.connect() as c:
            row = c.execute('SELECT * FROM workouts WHERE activity_id=?', (activity_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Workout not synced')
        return {'activity_id': activity_id, 'workouts': json.loads(row['body']), 'raw_id': row['source_id']}

    @app.get('/api/export')
    def export(start: date = None, end: date = None):
        start, end = bounds(start, end)
        return JSONResponse({'schema_version': 1, 'daily': db.days(start.isoformat(), end.isoformat()),
                             'activities': db.activities(start.isoformat(), end.isoformat())},
                            headers={'Content-Disposition': 'attachment; filename="garmin-coach-export.json"'})

    static = Path(__file__).parent / 'static'
    app.mount('/static', StaticFiles(directory=static), name='static')

    @app.get('/')
    def index():
        return FileResponse(static / 'index.html')

    return app
