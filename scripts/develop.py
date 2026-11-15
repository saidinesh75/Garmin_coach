"""Use a private local path configuration; never copy secrets or environments."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    try:
        config = json.loads((ROOT / '.coach-local.json').read_text())
        python = Path(config['python']).expanduser()
        data = Path(config['data_dir']).expanduser()
        if not python.is_file() or not data.is_dir():
            raise ValueError('Configured runtime or data directory is missing')
    except (OSError, ValueError, KeyError, TypeError):
        print('Local development paths are missing or invalid. See README.md; your saved data has not been moved.')
        return 1
    os.chdir(ROOT)
    env = os.environ.copy()
    # Pin the configured data location for this launcher; never silently use a new empty database.
    env['COACH_DATA_DIR'] = str(data)
    if len(sys.argv) > 1:
        args = [str(python)] + sys.argv[1:]
    else:
        args = [str(python), str(ROOT / 'scripts/launch.py')]
    os.execve(str(python), args, env)


if __name__ == '__main__':
    raise SystemExit(main())
