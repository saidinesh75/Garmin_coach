from __future__ import annotations
import os
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent

@dataclass
class Settings:
    data_dir: Path
    timezone: str = 'Asia/Kolkata'
    start_date: date = date(2026, 1, 15)
    daily_sync: bool = True
    sync_hour: int = 9
    llm_model: str = ''
    llm_port: int = 11434

    @classmethod
    def load(cls):
        local = {}
        try:
            local = json.loads((ROOT / '.coach-local.json').read_text())
            if not isinstance(local, dict):
                local = {}
        except (OSError, ValueError):
            pass
        default_data = local.get('data_dir') or str(ROOT / 'data')
        data_dir = Path(os.environ.get('COACH_DATA_DIR', default_data)).expanduser()
        local_model = ''
        try:
            saved = json.loads((data_dir / 'local-ai.json').read_text())
            if saved.get('model') == 'qwen3:1.7b':
                local_model = saved['model']
        except (OSError, ValueError, AttributeError):
            pass
        s = cls(data_dir,
                os.getenv('COACH_TIMEZONE', 'Asia/Kolkata'),
                date.fromisoformat(os.getenv('COACH_START_DATE', '2026-01-15')),
                os.getenv('COACH_DAILY_SYNC', 'true').lower() == 'true',
                int(os.getenv('COACH_SYNC_HOUR', '9')),
                os.getenv('COACH_LLM_MODEL', local_model), int(os.getenv('COACH_LLM_PORT', '11434')))
        ZoneInfo(s.timezone)
        if not 0 <= s.sync_hour <= 23 or not 1 <= s.llm_port <= 65535:
            raise ValueError('Invalid sync hour or local model port')
        s.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        s.data_dir.chmod(0o700)
        return s

    def today(self):
        return datetime.now(ZoneInfo(self.timezone)).date()

    def key(self):
        key = os.getenv('GARMIN_PERSONAL_API_KEY', '').strip()
        if key:
            return key
        path = Path(os.getenv('GARMIN_API_KEY_FILE', str(self.data_dir / 'garmin.key'))).expanduser()
        if not path.exists():
            return ''
        if path.stat().st_mode & 0o077:
            raise ValueError('Garmin key file must have permissions 600')
        return path.read_text().strip()

    def configured(self):
        try:
            return bool(self.key())
        except (OSError, ValueError):
            return False
