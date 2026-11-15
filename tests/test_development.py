"""Verify data-path continuity and rejection of the wrong running installation."""
import importlib.util
import json
from pathlib import Path

import httpx

from garmin_coach import INSTALLATION_ID, __version__
from garmin_coach.config import Settings
from garmin_coach.db import Database


def test_configured_data_path_and_environment_override(tmp_path, monkeypatch):
    project = tmp_path / 'source'
    project.mkdir()
    private = tmp_path / 'private-data'
    private.mkdir()
    db = Database(private)
    db.meta('synthetic_marker', 'preserved')
    (project / '.coach-local.json').write_text(json.dumps({'data_dir': str(private)}))
    monkeypatch.setattr('garmin_coach.config.ROOT', project)
    monkeypatch.delenv('COACH_DATA_DIR', raising=False)
    settings = Settings.load()
    assert settings.data_dir == private
    assert Database(settings.data_dir).meta('synthetic_marker') == 'preserved'
    assert not (project / 'data').exists()
    alternate = tmp_path / 'explicit-data'
    monkeypatch.setenv('COACH_DATA_DIR', str(alternate))
    assert Settings.load().data_dir == alternate


def test_launcher_recognizes_its_own_server_only():
    path = Path(__file__).resolve().parents[1] / 'scripts/launch.py'
    spec = importlib.util.spec_from_file_location('development_launcher', path)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    for identity, expected in [(None, False), ('another-source', False), (INSTALLATION_ID, True)]:
        def response(request):
            body = {'local_only': True, 'history_start': '2026-01-15'}
            if identity is not None:
                body['installation_id'] = identity
            return httpx.Response(200, json=body)
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            assert launcher.ready(client) is expected
    assert __version__ == '0.3.0'
