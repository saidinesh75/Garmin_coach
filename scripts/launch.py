"""Finder launcher: configure privately once, start the server, open the browser."""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx
from garmin_coach.config import Settings
from garmin_coach import INSTALLATION_ID

URL = 'http://127.0.0.1:8765'


def active_installation(client):
    try:
        r = client.get(URL + '/api/status')
        data = r.json()
        if r.status_code == 200 and data.get('local_only') is True and 'history_start' in data:
            return data.get('installation_id', 'older-installation')
        return None
    except (httpx.HTTPError, ValueError, AttributeError):
        return None


def ready(client):
    return active_installation(client) == INSTALLATION_ID


def open_dashboard():
    subprocess.run(['/usr/bin/open', URL], check=True)


def main():
    os.umask(0o077)
    os.chdir(ROOT)
    with httpx.Client(timeout=1, trust_env=False) as client:
        active = active_installation(client)
        if active and active != INSTALLATION_ID:
            print('Another Garmin Coach installation is running. Stop it with Control+C in its Terminal window, then reopen this launcher.')
            print('This launcher runs the development project and preserves your configured data location.')
            return 1
        if active == INSTALLATION_ID:
            print('Garmin Coach is already running. Opening your dashboard.')
            open_dashboard()
            return 0
        settings = Settings.load()
        if not settings.configured():
            print('First-time setup: enter your Garmin key at the hidden local prompt below.\n')
            result = subprocess.run([sys.executable, '-m', 'garmin_coach', 'configure'])
            if result.returncode:
                return result.returncode
        print('\nStarting Garmin Coach. Your browser will open automatically.')
        print('Keep this window open. Press Control+C here to stop the app.\n')
        server = subprocess.Popen([sys.executable, '-m', 'garmin_coach', 'serve'], cwd=str(ROOT))
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    print('The server stopped before it was ready. Check the error above.')
                    return server.returncode or 1
                if ready(client):
                    open_dashboard()
                    return server.wait()
                time.sleep(.3)
            print('The server did not become ready within 30 seconds.')
            return 1
        except KeyboardInterrupt:
            print('\nStopping Garmin Coach…')
            return 0
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError):
        print('Could not open the app. Check your local configuration and file permissions.')
        raise SystemExit(1)
