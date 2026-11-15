from __future__ import annotations
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()

class Database:
    def __init__(self, data_dir):
        self.path = data_dir / 'coach.sqlite3'
        with self.connect() as c:
            c.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS raw (
                  id INTEGER PRIMARY KEY, endpoint TEXT NOT NULL, params TEXT NOT NULL,
                  fetched_at TEXT NOT NULL, status INTEGER NOT NULL, sha256 TEXT NOT NULL,
                  body TEXT NOT NULL, UNIQUE(endpoint,params,sha256,status));
                CREATE TABLE IF NOT EXISTS daily (
                  day TEXT PRIMARY KEY, values_json TEXT NOT NULL, sources_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS activities (
                  id INTEGER PRIMARY KEY, day TEXT NOT NULL, values_json TEXT NOT NULL,
                  source_id INTEGER NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workouts (
                  activity_id INTEGER PRIMARY KEY, body TEXT NOT NULL, source_id INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints (
                  resource TEXT NOT NULL, day TEXT NOT NULL, status TEXT NOT NULL,
                  updated_at TEXT NOT NULL, PRIMARY KEY(resource,day));
                CREATE TABLE IF NOT EXISTS jobs (
                  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT,
                  start_day TEXT NOT NULL, end_day TEXT NOT NULL, state TEXT NOT NULL,
                  completed INTEGER DEFAULT 0, total INTEGER DEFAULT 0, message TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                PRAGMA user_version=1;
            ''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(str(self.path), timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def raw(self, endpoint, params, status, body):
        digest = hashlib.sha256(body.encode()).hexdigest()
        params = json.dumps(params, sort_keys=True)
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO raw(endpoint,params,fetched_at,status,sha256,body) VALUES(?,?,?,?,?,?)',
                      (endpoint, params, now(), status, digest, body))
            return c.execute('SELECT id FROM raw WHERE endpoint=? AND params=? AND sha256=? AND status=?',
                             (endpoint, params, digest, status)).fetchone()[0]

    def merge_day(self, day, values, source_id):
        with self.connect() as c:
            row = c.execute('SELECT * FROM daily WHERE day=?', (day,)).fetchone()
            data, sources = (json.loads(row['values_json']), json.loads(row['sources_json'])) if row else ({}, {})
            # A successful replacement may clear a formerly present measurement.
            data.update(values)
            sources.update({key: source_id for key in values})
            c.execute('INSERT OR REPLACE INTO daily VALUES(?,?,?,?)',
                      (day, json.dumps(data), json.dumps(sources), now()))

    def days(self, start, end):
        with self.connect() as c:
            return [dict(day=r['day'], **json.loads(r['values_json']), sources=json.loads(r['sources_json']))
                    for r in c.execute('SELECT * FROM daily WHERE day BETWEEN ? AND ? ORDER BY day', (start, end))]

    def activities(self, start, end):
        with self.connect() as c:
            return [dict(id=r['id'], day=r['day'], **json.loads(r['values_json']), source_id=r['source_id'])
                    for r in c.execute('SELECT * FROM activities WHERE day BETWEEN ? AND ? ORDER BY day DESC,id DESC', (start, end))]

    def checkpoint(self, resource, day, status):
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)', (resource, day, status, now()))

    def done(self, resource, day):
        with self.connect() as c:
            return c.execute('SELECT 1 FROM checkpoints WHERE resource=? AND day=? AND status IN ("ok","empty")',
                             (resource, day)).fetchone() is not None

    def meta(self, key, value=None):
        with self.connect() as c:
            if value is not None:
                c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, str(value)))
                return value
            row = c.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return row[0] if row else None

    def jobs(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 12')]
