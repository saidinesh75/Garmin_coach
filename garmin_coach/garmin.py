from __future__ import annotations
import base64
import hashlib
import json
import math
import os
import tempfile
import time
from email.utils import parsedate_to_datetime
import httpx

BASE_URL = 'https://personal.connectapi.garmin.com'
TOKEN_PATH = '/personalapitoken-service/personal-api/personal-api-key/token'
DAILY_ENDPOINTS = {
    'summary': ('/usersummary-service/usersummary/daily', 'calendarDate'),
    'sleep': ('/sleep-service/sleep/dailySleep', 'date'),
    'hrv': ('/hrv-service/hrv/{day}', None),
    'heart_rate': ('/wellness-service/wellness/dailyHeartRate', 'date'),
    'body_battery': ('/wellness-service/wellness/bodyBattery/reports/daily', 'range'),
}
ACTIVITIES = '/activitylist-service/activities/search/activities'

class GarminError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def private_json(path, data):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix='.private-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

class GarminClient:
    def __init__(self, settings, transport=None, pause=time.sleep):
        self.settings, self.pause = settings, pause
        self.http = httpx.Client(base_url=BASE_URL, transport=transport, timeout=30,
                                 follow_redirects=False, trust_env=False)
        self.cache = settings.data_dir / 'token.json'
        self.token_value = ''
        self.fingerprint = ''
        self.expiry = 0
        self.last_request = 0

    def close(self):
        self.http.close()

    def token(self, force=False):
        key = self.settings.key()
        if not key:
            raise GarminError('No Garmin key configured. Run: python -m garmin_coach configure')
        fingerprint = hashlib.sha256((BASE_URL + '\0' + key).encode()).hexdigest()
        if self.fingerprint and self.fingerprint != fingerprint:
            self.token_value, self.expiry = '', 0
        self.fingerprint = fingerprint
        if not force and not self.token_value and self.cache.exists():
            try:
                if self.cache.stat().st_mode & 0o077:
                    raise GarminError('Token cache permissions must be 600')
                cached = json.loads(self.cache.read_text())
                if cached['fingerprint'] == fingerprint:
                    self.token_value, self.expiry = cached['token'], float(cached['expires_at'])
            except (KeyError, ValueError, TypeError):
                pass
        if not force and self.token_value and self.expiry > time.time() + 3600:
            return self.token_value
        # Persistent cooldown also covers repeated restarts after throttling.
        cooldown = self.settings.data_dir / 'cooldown.json'
        if cooldown.exists():
            try:
                if float(json.loads(cooldown.read_text())['until']) > time.time():
                    raise GarminError('Garmin cooldown active. Wait before retrying authentication.', 429)
            except (KeyError, ValueError, TypeError):
                pass
        response = self.request('POST', TOKEN_PATH, headers={'Authorization': 'ApiKey ' + key, 'Accept': '*/*'})
        # Official Garmin client confirms token endpoint returns raw JWT text, not OAuth JSON.
        token = response.text.strip()
        try:
            segment = token.split('.')[1]
            expiry = float(json.loads(base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4)))['exp'])
            if not math.isfinite(expiry) or expiry <= time.time():
                raise ValueError()
        except (ValueError, TypeError, KeyError, IndexError):
            raise GarminError('Garmin returned an invalid or expired token; no response body was logged') from None
        self.token_value, self.expiry = token, expiry
        private_json(self.cache, {'fingerprint': fingerprint, 'token': token, 'expires_at': expiry})
        return token

    def request(self, method, path, **kwargs):
        for attempt in range(3):
            self.pause(max(0, 1.0 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                r = self.http.request(method, path, **kwargs)
            except httpx.RequestError:
                if attempt == 2:
                    raise GarminError('Network connection failed; checkpoints preserved') from None
                self.pause(2 ** attempt)
                continue
            if r.status_code == 429:
                wait = 3600
                try:
                    v = r.json().get('retryInSeconds')
                    if isinstance(v, (int, float)) and v >= 0:
                        wait = max(wait, v)
                except (ValueError, AttributeError):
                    pass
                try:
                    header = r.headers.get('Retry-After', '')
                    wait = max(wait, float(header) if header.isdigit() else parsedate_to_datetime(header).timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    pass
                private_json(self.settings.data_dir / 'cooldown.json', {'until': time.time() + wait})
                raise GarminError('Garmin rate limit reached; paused for at least one hour. Resume later.', 429)
            if r.status_code >= 500 and attempt < 2:
                self.pause(2 ** (attempt + 1))
                continue
            if r.status_code >= 300:
                raise GarminError('Garmin HTTP %s; response omitted to protect private data' % r.status_code, r.status_code)
            return r
        raise GarminError('Garmin request failed')

    def get(self, path, params):
        cooldown = self.settings.data_dir / 'cooldown.json'
        if cooldown.exists() and float(json.loads(cooldown.read_text()).get('until', 0)) > time.time():
            raise GarminError('Garmin cooldown active; resume later', 429)
        token = self.token()
        try:
            response = self.request('GET', path, params=params, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
        except GarminError as e:
            if e.status != 401:
                raise
            token = self.token(force=True)
            response = self.request('GET', path, params=params, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
        if response.status_code == 204 or not response.content:
            return response.status_code, '', None
        try:
            return response.status_code, response.text, response.json()
        except ValueError:
            raise GarminError('Garmin returned non-JSON data; normalization stopped') from None
