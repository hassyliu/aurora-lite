"""Build a Linux one-file executable in an isolated environment, without Docker."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import venv


def write_build_info(python, target):
    version = subprocess.check_output([str(target), '--version'], text=True).strip()
    dependencies = subprocess.check_output([str(python), '-m', 'pip', 'freeze'], text=True).splitlines()
    info = {'application': version, 'built_at': datetime.now(timezone.utc).isoformat(),
            'system': platform.platform(), 'architecture': platform.machine(),
            'libc': platform.libc_ver(), 'python': platform.python_version(),
            'size_bytes': target.stat().st_size,
            'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'dependencies': dependencies}
    (target.parent / 'BUILD-INFO.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')


def main():
    if sys.platform != 'linux':
        raise SystemExit('Linux 包必须在 Linux 上构建；本脚本不使用 Docker。')
    root = Path(__file__).resolve().parents[1]
    python = root / '.build-venv/bin/python'
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(python.parent.parent)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(root / 'requirements.txt'),
                    '-r', str(root / 'requirements-build.txt')], check=True, cwd=root)
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True, cwd=root)
    subprocess.run([
        str(python), '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
        '--name', 'aurora-lite', '--distpath', str(root / 'dist'),
        '--workpath', str(root / 'build'), '--specpath', str(root / 'build'),
        '--add-data', str(root / 'aurora/static') + ':aurora/static',
        '--add-data', str(root / 'aurora/remote_agent.py') + ':aurora',
        '--collect-submodules', 'uvicorn', str(root / 'binary_entry.py'),
    ], check=True, cwd=root)
    target = root / 'dist/aurora-lite'
    os.chmod(target, 0o755)
    write_build_info(python, target)
    print('可执行文件: ' + str(target), flush=True)


if __name__ == '__main__':
    main()
