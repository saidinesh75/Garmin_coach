"""Synthetic evidence and mock Ollama only; no personal readings or model downloads."""
import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from garmin_coach.app import create_app
from garmin_coach.coach import LocalCoach, evidence_package, validate_output
from garmin_coach.config import Settings
from garmin_coach.db import Database


@pytest.fixture
def setup(tmp_path):
    settings = Settings(tmp_path, daily_sync=False, llm_model='qwen3:1.7b')
    db = Database(tmp_path)
    for offset in range(1, 36):
        db.merge_day(str(settings.today() - timedelta(days=offset)),
                     {'sleep_hours': 6 if offset <= 7 else 7.5,
                      'hrv_ms': 40 if offset <= 7 else 60}, 999)
    return settings, db


def output():
    return {'observations': [{'text': 'Sleep and HRV shifted together; the cause is uncertain.',
                              'evidence_ids': ['sleep_hours', 'hrv_ms']}],
            'suggestions': [{'text': 'Consider an easier day if you also feel tired.',
                             'evidence_ids': ['hrv_ms']}],
            'question': 'How rested do you feel?'}


def mock_ollama(monkeypatch, report=None):
    captured = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False
            assert kwargs['follow_redirects'] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url):
            return httpx.Response(200, json={'models': [{'name': 'qwen3:1.7b'}]}, request=httpx.Request('GET', url))
        def post(self, url, json):
            captured.append((url, json))
            return httpx.Response(200, json={'done': True, 'done_reason': 'stop',
                'response': __import__('json').dumps(report if report is not None else output())},
                request=httpx.Request('POST', url))
    monkeypatch.setattr('garmin_coach.coach.httpx.Client', Client)
    return captured


def test_generation_cache_invalidation_and_memory_bounds(setup, monkeypatch):
    settings, db = setup
    captured = mock_ollama(monkeypatch)
    service = LocalCoach(settings, db)
    first = service.generate(settings.today())
    assert first['mode'] == 'local_ai' and not first['cached']
    assert service.generate(settings.today())['cached']
    assert len(captured) == 1
    url, request = captured[0]
    assert url == 'http://127.0.0.1:11434/api/generate'
    assert request['think'] is False and request['keep_alive'] == 0
    assert request['options']['num_ctx'] == 4096
    assert 'raw_id' not in request['prompt'] and 'sources' not in request['prompt']
    db.merge_day(str(settings.today() - timedelta(days=1)), {'hrv_ms': 45}, 1000)
    assert not service.generate(settings.today())['cached']
    assert len(captured) == 2
    assert not service.generate(settings.today(), checkin={'energy': 'low'})['cached']
    assert not service.generate(settings.today(), kind='weekly')['cached']


@pytest.mark.parametrize('mutation', ['unknown', 'number', 'shape'])
def test_rejects_invalid_generated_output(setup, monkeypatch, mutation):
    settings, db = setup
    report = output()
    if mutation == 'unknown': report['observations'][0]['evidence_ids'] = ['invented_metric']
    elif mutation == 'number': report['observations'][0]['text'] = 'Your HRV is 999 ms.'
    else: report['suggestions'] = 'not a list'
    mock_ollama(monkeypatch, report)
    assert LocalCoach(settings, db).generate(settings.today())['mode'] == 'fallback'
    assert db.meta('local_coach_daily') is None


def test_empty_data_does_not_call_model(tmp_path, monkeypatch):
    settings = Settings(tmp_path, llm_model='qwen3:1.7b')
    service = LocalCoach(settings, Database(tmp_path))
    monkeypatch.setattr(service, 'status', lambda: pytest.fail('Must not contact model without evidence'))
    assert service.generate(settings.today())['mode'] == 'insufficient_data'


def test_historical_report_ignores_current_checkin(setup, monkeypatch):
    settings, db = setup
    calls = mock_ollama(monkeypatch)
    result = LocalCoach(settings, db).generate(settings.today() - timedelta(days=1),
                                            question='What changed?', checkin={'notes': 'CURRENT-ONLY'})
    assert result['mode'] == 'local_ai'
    payload = json.loads(calls[0][1]['prompt'])
    assert payload['user_checkin'] is None and payload['evidence']['historical'] is True


def test_unavailable_and_busy(setup, monkeypatch):
    settings, db = setup
    service = LocalCoach(settings, db)
    settings.llm_model = ''
    assert service.generate(settings.today())['mode'] == 'unavailable'
    service.lock.acquire()
    try: assert service.generate(settings.today())['mode'] == 'busy'
    finally: service.lock.release()
    settings.llm_model = 'qwen3-cloud'
    assert service.status()['ready'] is False


def test_local_config_and_environment_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv('COACH_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('COACH_LLM_MODEL', raising=False)
    (tmp_path / 'local-ai.json').write_text('{"model":"qwen3:1.7b"}')
    assert Settings.load().llm_model == 'qwen3:1.7b'
    monkeypatch.setenv('COACH_LLM_MODEL', '')
    assert Settings.load().llm_model == ''


def test_coach_api_security_bounds_and_export(setup, monkeypatch):
    settings, db = setup
    app = create_app(settings)
    monkeypatch.setattr(app.state.coach, 'generate', lambda *args: {'mode': 'synthetic-test'})
    with TestClient(app) as client:
        headers = {'X-Coach-Token': client.get('/api/status').json()['session_token']}
        assert client.post('/api/coach', json={}).status_code == 403
        assert client.post('/api/coach', json={'question': 'x' * 801}, headers=headers).status_code == 422
        assert client.post('/api/coach', json={'end': '2100-01-01'}, headers=headers).status_code == 422
        assert client.post('/api/coach', json={}, headers=headers).json()['mode'] == 'synthetic-test'
        exported = client.get('/api/coach/export').json()
        assert exported['evidence']['facts']['sleep_hours']['current'] == 6
        assert 'source_id' not in json.dumps(exported)
        assert client.get('/static/coach.js').status_code == 200
