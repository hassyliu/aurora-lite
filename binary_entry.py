"""Standalone launcher. Persistent settings/data live beside the executable."""
import json
import os
from pathlib import Path
import sys


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '--internal-tcp-probe':
        from aurora.remote_agent import TCP_PROBE_CODE
        sys.argv = sys.argv[1:]
        # Execute only our fixed, bundled probe program, never a user script.
        exec(TCP_PROBE_CODE, {'__name__': '__aurora_tcp_probe__'})
        return

    root = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
    os.environ.setdefault('AURORA_DATA_DIR', str(root / 'data'))
    settings_path = root / 'settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
    for key, variable in [('host', 'AURORA_HOST'), ('port', 'AURORA_PORT'),
                          ('public_origin', 'AURORA_PUBLIC_ORIGIN')]:
        if settings.get(key) is not None and settings.get(key) != '':
            os.environ.setdefault(variable, str(settings[key]))
    if settings.get('secure_cookie'):
        os.environ.setdefault('AURORA_SECURE_COOKIE', 'true')
    if len(sys.argv) == 1:
        sys.argv.append('serve')
    from aurora.__main__ import main as cli
    cli()


if __name__ == '__main__':
    main()
