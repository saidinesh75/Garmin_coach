from __future__ import annotations
import argparse
import getpass
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from .config import Settings
from .db import Database
from .sync import SyncManager, SyncBusy


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='Garmin Coach: your private health and performance companion. Keys are never command-line arguments.')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('configure', help='Save a Garmin key through a hidden local prompt')
    sub.add_parser('status', help='Show local configuration and recent sync jobs')
    serve = sub.add_parser('serve', help='Run the localhost dashboard and daily scheduler')
    serve.add_argument('--port', type=int, default=8765)
    sync = sub.add_parser('sync', help='Sync recent days or resume historical backfill')
    sync.add_argument('--start', type=date.fromisoformat)
    sync.add_argument('--end', type=date.fromisoformat)
    sync.add_argument('--force', action='store_true', help='Re-fetch completed old dates, including empty responses')
    sync.add_argument('--backfill', action='store_true', help='Start at configured history start (15 Jan 2026)')
    args = parser.parse_args()
    settings = Settings.load()
    if args.command == 'configure':
        if not sys.stdin.isatty():
            parser.error('Run configure in an interactive local terminal so the key is not echoed')
        path = settings.data_dir / 'garmin.key'
        key = getpass.getpass('Garmin Personal API key (hidden, local only): ').strip()
        if not key:
            parser.error('No key entered; nothing changed')
        if '\n' in key or '\r' in key:
            parser.error('Key must be a single line')
        # Atomic, owner-only write. Never print or pass key to a shell command.
        from .garmin import private_json
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=str(settings.data_dir))
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(key)
            os.replace(tmp, path)
            path.chmod(0o600)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        print('Key saved locally with owner-only permissions. Restart the app if it is already running.')
        return
    if args.command == 'serve':
        import uvicorn
        from .app import create_app
        print(f'Open http://127.0.0.1:{args.port}. Keep this process running for daily sync.')
        uvicorn.run(create_app(settings), host='127.0.0.1', port=args.port, access_log=False, log_level='warning')
        return
    db = Database(settings.data_dir)
    if args.command == 'status':
        import json
        print(json.dumps({'key_configured': settings.configured(), 'history_start': str(settings.start_date),
                          'timezone': settings.timezone, 'jobs': db.jobs()}, indent=2))
        return
    if not settings.configured():
        parser.error('No secure local key is configured. Run python -m garmin_coach configure, or set GARMIN_PERSONAL_API_KEY in your local shell.')
    start = args.start or (settings.start_date if args.backfill else max(settings.start_date, settings.today() - timedelta(days=7)))
    end = args.end or settings.today()
    manager = SyncManager(settings, db)
    try:
        job_id, lock = manager.reserve(start, end, args.force)
    except (ValueError, SyncBusy) as e:
        parser.error(str(e))
    manager.run(job_id, lock, start, end, args.force)
    job = db.jobs()[0]
    print(f"Sync {job['state']}: {job['completed']}/{job['total']} resource groups. {job['message']}")
    if job['state'] != 'complete':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
