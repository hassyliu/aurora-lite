import base64
import hashlib
import io
import json
from pathlib import Path
import socket
import time

import paramiko


def fingerprint(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


def probe(host, port):
    with socket.create_connection((host, port), timeout=10) as sock:
        transport = paramiko.Transport(sock)
        try:
            transport.start_client(timeout=10)
            return fingerprint(transport.get_remote_server_key())
        finally:
            transport.close()


class PinnedHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected):
        self.expected = expected

    def missing_host_key(self, client, hostname, key):
        if fingerprint(key) != self.expected:
            raise paramiko.SSHException('SSH 主机指纹不匹配，请核对服务器身份后更新指纹。')


class SSHRemote:
    def __init__(self, store):
        self.store = store

    def execute(self, server, action, rule=None, on_progress=None):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(PinnedHostKey(server['fingerprint']))
        credential = self.store.unseal(server['credential'])
        passphrase = self.store.unseal(server['passphrase'])
        args = dict(hostname=server['host'], port=server['port'], username=server['username'],
                    timeout=12, auth_timeout=12, banner_timeout=12, allow_agent=False, look_for_keys=False)
        if server['auth_type'] == 'password':
            args['password'] = credential
        else:
            for key_type in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
                try:
                    args['pkey'] = key_type.from_private_key(io.StringIO(credential), password=passphrase or None)
                    break
                except (paramiko.SSHException, ValueError):
                    pass
            if 'pkey' not in args:
                raise ValueError('无法读取 SSH 私钥，请检查私钥格式和口令')
        try:
            if on_progress:
                on_progress('正在连接 SSH…')
            client.connect(**args)
            if on_progress:
                on_progress('SSH 已连接，开始执行远程操作。')
            source = Path(__file__).with_name('remote_agent.py').read_text(encoding='utf-8')
            payload = {'action': action, 'rule': rule, 'progress': on_progress is not None}
            if action == 'apply':
                payload['agent_source'] = source
            encoded = base64.b64encode(json.dumps(payload).encode()).decode()
            script = source + '\nimport base64\ndispatch(json.loads(base64.b64decode(' + repr(encoded) + ')))\n'
            channel = client.get_transport().open_session(timeout=15)
            channel.settimeout(15)
            channel.exec_command('python3 -' if server['username'] == 'root' else 'sudo -n python3 -')
            channel.sendall(script.encode())
            channel.shutdown_write()
            output, errors = bytearray(), bytearray()
            pending = bytearray()
            result = None
            received = 0
            deadline = time.monotonic() + (720 if action == 'prepare' else 150)
            while True:
                if channel.recv_ready():
                    part = channel.recv(65536)
                    received += len(part)
                    pending.extend(part)
                    while b'\n' in pending:
                        line, _, rest = pending.partition(b'\n')
                        pending = bytearray(rest)
                        try:
                            record = json.loads(line)
                        except (ValueError, UnicodeError):
                            output.extend(line + b'\n')
                            continue
                        if isinstance(record, dict) and 'progress' in record:
                            if on_progress:
                                on_progress(record['progress'])
                        else:
                            result = record
                if channel.recv_stderr_ready():
                    errors.extend(channel.recv_stderr(65536))
                if received + len(errors) > 2_000_000:
                    raise RuntimeError('远程输出超过上限')
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError('远程操作超时；状态尚未确认，请检查后重新停止或下发')
                time.sleep(0.03)
            if channel.recv_exit_status() != 0:
                message = errors.decode(errors='replace')[-6000:] or output.decode(errors='replace')[-6000:]
                raise RuntimeError(message or '远程操作失败')
            if pending.strip():
                result = json.loads(pending)
            if not isinstance(result, dict):
                raise RuntimeError('远程操作未返回有效结果')
            return result
        finally:
            client.close()
