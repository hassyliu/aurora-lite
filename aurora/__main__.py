import argparse
from contextlib import closing
import getpass
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import zipfile

from . import __version__
from .storage import ProcessLock, Store, password_hash


def backup(store, destination):
    """Use SQLite backup API, including committed WAL pages, never raw file copies."""
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('目标备份文件已存在，请使用新文件名')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aurora-backup-') as temp:
        temp = Path(temp)
        for name, jobs in [('aurora.db', False), ('tasks.db', True)]:
            with store.connect(jobs) as source, closing(sqlite3.connect(temp / name)) as target:
                source.backup(target)
        handle = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, 'wb') as output, zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in ('aurora.db', 'tasks.db'):
                archive.write(temp / name, name)
            archive.write(store.directory / 'master.key', 'master.key')
            archive.writestr('manifest.json', json.dumps({'format': 'aurora-lite-backup-v1', 'created': time.time()}))
    os.chmod(destination, 0o600)
    return destination


def main():
    parser = argparse.ArgumentParser(description='Aurora Lite 原生中转面板')
    parser.add_argument('--version', action='version', version='Aurora Lite ' + __version__)
    parser.add_argument('--data-dir', default=os.getenv('AURORA_DATA_DIR', 'data'))
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='创建或重置管理员')
    init.add_argument('--username', default='admin')
    init.add_argument('--reset', action='store_true', help='重置现有管理员并使会话失效')
    serve = commands.add_parser('serve', help='启动面板')
    serve.add_argument('--host', default=os.getenv('AURORA_HOST', '127.0.0.1'))
    serve.add_argument('--port', type=int, default=int(os.getenv('AURORA_PORT', '8000')))
    save = commands.add_parser('backup', help='备份两个数据库及密钥；建议先停止主控')
    save.add_argument('destination')
    commands.add_parser('doctor', help='检查本地配置和数据库')
    args = parser.parse_args()
    store = Store(args.data_dir)
    if args.command == 'init':
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', args.username):
            parser.error('管理员账号只允许字母、数字、下划线、点和连字符')
        if store.one('SELECT username FROM admins') and not args.reset:
            print('管理员已存在，无需重新初始化。')
            return
        first = getpass.getpass('设置管理员密码（至少 12 位）: ')
        second = getpass.getpass('再次输入密码: ')
        if first != second or not 12 <= len(first) <= 256:
            parser.error('密码不一致或长度不符合要求')
        with store.connect() as db:
            db.execute('DELETE FROM admins')
            db.execute('DELETE FROM sessions')
            db.execute('INSERT INTO admins VALUES(?,?)', (args.username, password_hash(first)))
        print(f'管理员 {args.username} 已就绪。')
    elif args.command == 'serve':
        if not store.one('SELECT username FROM admins'):
            parser.error('请先使用 init 命令创建管理员')
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port, workers=1,
                    proxy_headers=False, server_header=False)
    elif args.command == 'backup':
        process_lock = ProcessLock(store.directory)
        process_lock.acquire()
        try:
            print('备份完成: ' + str(backup(store, args.destination)))
        finally:
            process_lock.release()
    elif args.command == 'doctor':
        for jobs, name in [(False, 'aurora.db'), (True, 'tasks.db')]:
            print(name + ': ' + store.one('PRAGMA integrity_check', jobs=jobs)['integrity_check'])
        print('管理员: ' + ('已设置' if store.one('SELECT username FROM admins') else '未设置'))
        print('数据目录: ' + str(store.directory))
        print('后台任务: SQLite 持久化；单主控进程')


if __name__ == '__main__':
    main()
