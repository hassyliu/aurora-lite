"""Run using the system Python; automatically select the project's .venv."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    os.chdir(ROOT)
    python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        subprocess.run([sys.executable, str(ROOT / 'install.py')], check=True)
    settings_path = ROOT / 'settings.json'
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    if settings.get('public_origin'):
        os.environ['AURORA_PUBLIC_ORIGIN'] = settings['public_origin']
    if settings.get('secure_cookie'):
        os.environ['AURORA_SECURE_COOKIE'] = 'true'
    command = [str(python), '-m', 'aurora', 'serve', '--host', settings.get('host', '127.0.0.1'),
               '--port', str(settings.get('port', 8000)), *sys.argv[1:]]
    if os.name == 'nt':
        raise SystemExit(subprocess.call(command))
    os.execv(str(python), command)


if __name__ == '__main__':
    main()
