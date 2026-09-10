"""Optional real engine integration. Set AURORA_TEST_GOST to a verified GOST v3 binary.

Only binds loopback ephemeral ports; does not SSH or modify firewall/system services.
"""
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request

from aurora.remote_agent import gost_config, parse_gost_metrics


class TCPEcho(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request.recv(65536)
        if data:
            self.request.sendall(data)


class UDPEcho(socketserver.BaseRequestHandler):
    def handle(self):
        data, channel = self.request
        channel.sendto(data, self.client_address)


def free_port():
    with socket.socket() as channel:
        channel.bind(('127.0.0.1', 0))
        return channel.getsockname()[1]


@unittest.skipUnless(os.getenv('AURORA_TEST_GOST'), 'Set AURORA_TEST_GOST for live loopback test')
class GostLiveTests(unittest.TestCase):
    def test_tcp_udp_forwarding_and_metrics(self):
        with socketserver.ThreadingTCPServer(('127.0.0.1',0),TCPEcho) as tcp:
            target_port=tcp.server_address[1]
            with socketserver.ThreadingUDPServer(('127.0.0.1',target_port),UDPEcho) as udp:
                for server in (tcp,udp):
                    threading.Thread(target=server.serve_forever,daemon=True).start()
                with tempfile.TemporaryDirectory() as temp:
                    listen_port,metrics_port=free_port(),free_port()
                    rule=dict(id='aabbccddeeff',method='gost',protocol='both',listen_ip='127.0.0.1',
                              listen_port=listen_port,target_host='127.0.0.1',target_port=target_port)
                    config=gost_config(rule)
                    # Windows test substitutes loopback HTTP for the Linux Unix socket.
                    config['metrics']['addr']=f'127.0.0.1:{metrics_port}'
                    path=Path(temp)/'gost.json'
                    path.write_text(json.dumps(config))
                    process=subprocess.Popen([os.environ['AURORA_TEST_GOST'],'-C',str(path)],
                                             stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                                             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    try:
                        ready=False
                        for _ in range(100):
                            if process.poll() is not None:
                                self.fail(process.stderr.read().decode())
                            try:
                                with socket.create_connection(('127.0.0.1',listen_port),timeout=.2):
                                    ready=True
                                    break
                            except OSError:
                                time.sleep(.05)
                        self.assertTrue(ready,'GOST did not start')
                        payload=b'aurora-native-forwarding-'*64
                        with socket.create_connection(('127.0.0.1',listen_port),timeout=3) as channel:
                            channel.sendall(payload)
                            response=b''
                            while len(response)<len(payload):
                                chunk=channel.recv(65536)
                                if not chunk:break
                                response+=chunk
                            self.assertEqual(response,payload)
                        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as channel:
                            channel.settimeout(3)
                            channel.sendto(payload,('127.0.0.1',listen_port))
                            self.assertEqual(channel.recv(65536),payload)
                        total=(0,0)
                        for _ in range(60):
                            with urllib.request.urlopen(f'http://127.0.0.1:{metrics_port}/metrics',timeout=2) as response:
                                total=parse_gost_metrics(response.read().decode())
                            if min(total)>=len(payload)*2:break
                            time.sleep(.1)
                        self.assertGreaterEqual(total[0],len(payload)*2)
                        self.assertGreaterEqual(total[1],len(payload)*2)
                    finally:
                        process.terminate()
                        try:process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        process.stderr.close()
                        tcp.shutdown()
                        udp.shutdown()


if __name__=='__main__':unittest.main()
