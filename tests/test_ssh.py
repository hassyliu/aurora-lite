import json
import logging
import socket
import tempfile
import threading
import unittest

import paramiko

from aurora.remote import SSHRemote, fingerprint
from aurora.storage import Store


class SSHHandler(paramiko.ServerInterface):
    def __init__(self):
        self.auth_attempts=[]
        self.command=None

    def check_auth_password(self,username,password):
        self.auth_attempts.append((username,password))
        return paramiko.AUTH_SUCCESSFUL if (username,password)==('root','test-ssh-secret') else paramiko.AUTH_FAILED

    def get_allowed_auths(self,username):
        return 'password'

    def check_channel_request(self,kind,channel_id):
        return paramiko.OPEN_SUCCEEDED if kind=='session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self,channel,command):
        self.command=command
        return True


class SSHTransportTests(unittest.TestCase):
    def test_actual_ssh_transport_pinning_and_no_credential_in_payload(self):
        key=paramiko.RSAKey.generate(2048)
        handlers=[]
        scripts=[]
        ready=threading.Event()
        done=threading.Event()
        listener=socket.socket()
        listener.bind(('127.0.0.1',0))
        listener.listen(2)
        listener.settimeout(5)
        port=listener.getsockname()[1]

        def serve():
            ready.set()
            for _ in range(2):
                handler=SSHHandler()
                handlers.append(handler)
                transport=None
                try:
                    client,_=listener.accept()
                    transport=paramiko.Transport(client)
                    transport.add_server_key(key)
                    transport.start_server(server=handler)
                    channel=transport.accept(3)
                    if channel is None:continue
                    source=bytearray()
                    while True:
                        part=channel.recv(65536)
                        if not part:break
                        source.extend(part)
                    scripts.append(source.decode())
                    channel.sendall(json.dumps({'state':'online','message':'test transport OK'}).encode()+b'\n')
                    channel.send_exit_status(0)
                    channel.shutdown_write()
                    channel.close()
                except (EOFError,paramiko.SSHException,OSError):
                    pass
                finally:
                    if transport:transport.close()
            listener.close()
            done.set()

        thread=threading.Thread(target=serve,daemon=True)
        thread.start()
        ready.wait(2)
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory)
            remote=SSHRemote(store)
            server=dict(host='127.0.0.1',port=port,username='root',auth_type='password',
                        credential=store.seal('test-ssh-secret'),passphrase='',fingerprint=fingerprint(key))
            result=remote.execute(server,'check')
            self.assertEqual(result['state'],'online')
            self.assertEqual(handlers[0].command,b'python3 -')
            self.assertNotIn('test-ssh-secret',scripts[0])
            compile(scripts[0],'<generated-ssh-request>','exec')
            with self.assertRaisesRegex(paramiko.SSHException,'指纹不匹配'):
                remote.execute({**server,'fingerprint':'SHA256:'+'a'*43},'check')
        self.assertTrue(done.wait(6))
        self.assertEqual(len(handlers),2)
        self.assertEqual(handlers[1].auth_attempts,[],'Password must not be sent to unverified host')


if __name__=='__main__':unittest.main()
