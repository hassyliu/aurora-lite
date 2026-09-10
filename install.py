"""Run with the OS Python: python3 install.py [--service]. No global pip install."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description='安装 Aurora Lite 项目依赖并初始化')
    parser.add_argument('--service', action='store_true', help='注册并启动 systemd 服务（需要 root）')
    parser.add_argument('--host', default=None, help='监听地址，首次安装默认 127.0.0.1')
    parser.add_argument('--port', type=int, default=None, help='面板端口，首次安装默认 8000')
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error('需要 Python 3.10+。推荐 Debian 12+ 或 Ubuntu 22.04+ 的系统 Python。')
    if args.service and (sys.platform != 'linux' or os.geteuid() != 0):
        parser.error('--service 需要在 Debian / Ubuntu 以 root 身份执行')
    if args.service and (str(ROOT).startswith(('/root/', '/home/')) or any(c in str(ROOT) for c in '\n\r%"')):
        parser.error('系统服务请将项目放在 /opt/aurora-lite-panel 等服务账号可读取的目录中')
    os.chdir(ROOT)
    python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        print('使用系统 Python 创建项目虚拟环境…')
        try:
            venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
        except Exception as error:
            raise SystemExit('创建失败。Debian / Ubuntu 请先安装 python3-venv。\n' + str(error)) from None
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], check=True)
    subprocess.run([str(python), '-m', 'aurora', 'init'], check=True)
    settings_path = ROOT / 'settings.json'
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    settings['host'] = args.host if args.host is not None else settings.get('host', '127.0.0.1')
    settings['port'] = args.port if args.port is not None else settings.get('port', 8000)
    if not 1 <= settings['port'] <= 65535:
        parser.error('端口必须在 1–65535 范围内')
    settings_path.write_text(json.dumps(settings, indent=2), encoding='utf-8')
    if args.service:
        import pwd
        try:
            user = pwd.getpwnam('aurora-panel')
        except KeyError:
            subprocess.run(['useradd', '--system', '--home-dir', str(ROOT), '--shell', '/usr/sbin/nologin', 'aurora-panel'], check=True)
            user = pwd.getpwnam('aurora-panel')
        for path in [ROOT / 'data', *(ROOT / 'data').rglob('*')]:
            os.chown(path, user.pw_uid, user.pw_gid)
        root_text = str(ROOT)
        service = ('[Unit]\nDescription=Aurora Lite panel\nAfter=network-online.target\nWants=network-online.target\n\n'
                   '[Service]\nType=simple\nUser=aurora-panel\nGroup=aurora-panel\n'
                   f'WorkingDirectory={root_text}\nExecStart="{python}" "{ROOT / "start.py"}"\n'
                   'Restart=on-failure\nRestartSec=5\nUMask=0077\nNoNewPrivileges=yes\n'
                   'PrivateTmp=yes\nProtectSystem=strict\nProtectHome=yes\n'
                   f'ReadWritePaths="{ROOT / "data"}"\nTimeoutStopSec=780\n\n[Install]\nWantedBy=multi-user.target\n')
        Path('/etc/systemd/system/aurora-lite-panel.service').write_text(service)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'enable', 'aurora-lite-panel.service'], check=True)
        subprocess.run(['systemctl', 'restart', 'aurora-lite-panel.service'], check=True)
        print('系统服务已启动。日志：journalctl -u aurora-lite-panel -f')
    else:
        print('安装完成。运行 python3 start.py 启动面板。')
    print(f"监听地址: http://{settings['host']}:{settings['port']}")


if __name__ == '__main__':
    main()
