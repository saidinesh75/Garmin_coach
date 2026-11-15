from __future__ import annotations
import fcntl
import json
import threading
from datetime import date, timedelta
from .db import now
from .garmin import GarminClient, GarminError, DAILY_ENDPOINTS, ACTIVITIES
from .normalize import normalize, activity


def date_range(start, end):
    while start <= end:
        yield start.isoformat()
        start += timedelta(days=1)


class SyncBusy(Exception):
    pass


class SyncManager:
    def __init__(self, settings, db, client_factory=GarminClient):
        self.settings, self.db, self.client_factory = settings, db, client_factory
        self.thread = None

    def reserve(self, start, end, force=False):
        if start > end or start < self.settings.start_date or end > self.settings.today():
            raise ValueError('Choose dates from the configured history start through today')
        lock = open(self.settings.data_dir / 'sync.lock', 'a+')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise SyncBusy('A sync is already running') from None
        try:
            with self.db.connect() as c:
                # A process crash releases flock. Reconcile abandoned job records on next run.
                c.execute('UPDATE jobs SET state="interrupted",ended_at=?,message="Process stopped; resume uses saved checkpoints" WHERE state="running"', (now(),))
                total = ((end - start).days + 1) * (len(DAILY_ENDPOINTS) + 1)
                cursor = c.execute('INSERT INTO jobs(started_at,start_day,end_day,state,total,message) VALUES(?,?,?,"running",?,?)',
                                   (now(), start.isoformat(), end.isoformat(), total, 'Starting Garmin sync'))
                job_id = cursor.lastrowid
            return job_id, lock
        except Exception:
            lock.close()
            raise

    def start(self, start, end, force=False):
        job_id, lock = self.reserve(start, end, force)
        self.thread = threading.Thread(target=self.run, args=(job_id, lock, start, end, force), daemon=True)
        self.thread.start()
        return job_id

    def run(self, job_id, lock, start, end, force=False):
        client = None
        completed, errors = 0, 0
        try:
            client = self.client_factory(self.settings)
            for day in date_range(start, end):
                for resource, (template, query) in DAILY_ENDPOINTS.items():
                    recent = date.fromisoformat(day) >= self.settings.today() - timedelta(days=7)
                    if not force and not recent and self.db.done(resource, day):
                        completed += 1
                        continue
                    path = template.format(day=day)
                    params = {'startDate': day, 'endDate': day} if query == 'range' else {query: day} if query else {}
                    try:
                        status, text, body = client.get(path, params)
                        source = self.db.raw(path, params, status, text)
                        if isinstance(body, dict) and body.get('calendarDate') not in (None, day):
                            raise ValueError('Returned date does not match request')
                        values = normalize(resource, body)
                        if values:
                            self.db.merge_day(day, values, source)
                        self.db.checkpoint(resource, day, 'empty' if body is None or body == {} or body == [] else 'ok')
                    except GarminError as e:
                        self.db.checkpoint(resource, day, 'http_%s' % e.status if e.status else 'connection_error')
                        # 404 is not silently treated as missing data: endpoint errors stay visible.
                        if e.status in (401, 403, 429) or e.status == 0:
                            raise
                        errors += 1
                    except (ValueError, TypeError, KeyError, AttributeError):
                        self.db.checkpoint(resource, day, 'schema_error')
                        errors += 1
                    completed += 1
                    self.progress(job_id, completed, day + ' · ' + resource)
                if force or recent or not self.db.done('activities', day):
                    try:
                        self.sync_activities(client, day)
                        self.db.checkpoint('activities', day, 'ok')
                    except GarminError as e:
                        self.db.checkpoint('activities', day, 'http_%s' % e.status if e.status else 'connection_error')
                        if e.status in (401, 403, 429) or e.status == 0:
                            raise
                        errors += 1
                    except (ValueError, TypeError, KeyError):
                        self.db.checkpoint('activities', day, 'schema_error')
                        errors += 1
                completed += 1
                self.progress(job_id, completed, day + ' · activities')
            self.finish(job_id, 'partial' if errors else 'complete', '%s resource errors; inspect data coverage' % errors if errors else 'Sync completed')
        except GarminError as e:
            self.finish(job_id, 'paused' if e.status == 429 else 'failed', str(e))
        except Exception:
            self.finish(job_id, 'failed', 'Unexpected local error; checkpoints preserved. Check setup and retry.')
        finally:
            if client:
                client.close()
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def sync_activities(self, client, day):
        offset = 0
        seen = set()
        while True:
            params = {'startDate': day, 'endDate': day, 'start': offset, 'limit': 100,
                      'sortOrder': 'asc', 'excludeChildren': 'true'}
            status, text, body = client.get(ACTIVITIES, params)
            source = self.db.raw(ACTIVITIES, params, status, text)
            if body is None:
                return
            if not isinstance(body, list):
                raise ValueError('Activity response must be a list')
            for item in body:
                activity_id, local_day, values = activity(item)
                if local_day != day or activity_id in seen:
                    raise ValueError('Activity date or pagination mismatch')
                seen.add(activity_id)
                with self.db.connect() as c:
                    c.execute('INSERT OR REPLACE INTO activities VALUES(?,?,?,?,?)',
                              (activity_id, local_day, json.dumps(values), source, now()))
                path = '/activity-service/activity/%s/workouts' % activity_id
                status_w, text_w, workouts = client.get(path, {})
                source_w = self.db.raw(path, {}, status_w, text_w)
                if workouts is not None and not isinstance(workouts, list):
                    raise ValueError('Workout response must be a list')
                with self.db.connect() as c:
                    c.execute('INSERT OR REPLACE INTO workouts VALUES(?,?,?)',
                              (activity_id, json.dumps(workouts or []), source_w))
            if len(body) < 100:
                break
            offset += len(body)
            if offset > 10000:
                raise ValueError('Activity pagination safety limit reached')

    def progress(self, job, completed, message):
        with self.db.connect() as c:
            c.execute('UPDATE jobs SET completed=?,message=? WHERE id=?', (completed, message, job))

    def finish(self, job, state, message):
        with self.db.connect() as c:
            c.execute('UPDATE jobs SET state=?,message=?,ended_at=? WHERE id=?', (state, message, now(), job))
