"""Exercise the packaged runtime, authentication and migrations on loopback only."""
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aurora import __version__


def main():
    binary = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='aurora-binary-smoke-') as temporary:
        directory = Path(temporary)
        data = directory / 'data'
        # The frozen controller must not need a Python executable on PATH.
        env = {**os.environ, 'PATH': str(directory), 'AURORA_SECURE_COOKIE': 'false'}
        env.pop('AURORA_PUBLIC_ORIGIN', None)
        command = [str(binary), '--data-dir', str(data)]
        password = secrets.token_urlsafe(24)
        result = subprocess.run([*command, 'init'], input=password+'\n'+password+'\n',
                                text=True, capture_output=True, env=env, timeout=60)
        if result.returncode:
            raise RuntimeError('Packaged initialization failed: '+result.stderr)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with (directory/'server.log').open('w') as log:
            process = subprocess.Popen([*command, 'serve', '--host', '127.0.0.1', '--port', str(port)],
                                       env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic()+45
                while True:
                    try:
                        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                        connection.request('GET', '/api/status')
                        response = connection.getresponse()
                        status = json.loads(response.read())
                        connection.close()
                        assert response.status == 200 and status['version'] == __version__
                        break
                    except (OSError, http.client.HTTPException):
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('Packaged server failed to start')
                        time.sleep(.2)
                def request(method, path, payload=None, cookie=None):
                    client = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
                    headers = {'Content-Type': 'application/json', 'X-Aurora-Request': '1',
                               'Origin': f'http://127.0.0.1:{port}'}
                    if cookie:
                        headers['Cookie'] = cookie
                    client.request(method, path, json.dumps(payload) if payload is not None else None, headers)
                    response = client.getresponse()
                    body, cookies, code = response.read(), response.getheader('Set-Cookie'), response.status
                    client.close()
                    return code, body, cookies
                assert request('GET', '/api/servers')[0] == 401
                code, _, cookie = request('POST', '/api/login', {'username': 'admin', 'password': password})
                assert code == 200 and cookie
                cookie = cookie.split(';', 1)[0]
                assert request('GET', '/api/servers', cookie=cookie)[0] == 200
                assert request('PUT', '/api/servers/order', {'ids': []}, cookie)[0] == 200
                code, content, _ = request('GET', '/static/app.js')
                assert code == 200 and b'openLiveViewer' in content
            finally:
                process.terminate()
                process.wait(timeout=20)
        backup = directory/'backup.zip'
        subprocess.run([*command, 'backup', str(backup)], env=env, check=True, timeout=60)
        with zipfile.ZipFile(backup) as package:
            assert {'aurora.db', 'tasks.db', 'master.key'} <= set(package.namelist())
        subprocess.run([*command, 'doctor'], env=env, check=True, timeout=60)
    print('Packaged binary smoke test passed: init, login, API, static assets, backup, doctor; no system Python on PATH.')


if __name__ == '__main__':
    main()
