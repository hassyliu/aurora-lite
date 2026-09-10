import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from aurora.app import create_app
from aurora.models import RuleInput
from aurora.remote_agent import gost_config, iptables_plan, parse_gost_metrics, service_text
from aurora.storage import ProcessLock, Store, password_hash
from aurora.__main__ import backup

PASSWORD = 'local-testing-password-123'


class FakeRemote:
    def __init__(self):
        self.calls = []
        self.fail = False

    def execute(self, server, action, rule=None):
        self.calls.append((server['id'], action, rule['id'] if rule else None))
        if self.fail:
            raise RuntimeError('SSH connection failed')
        if action in ('apply', 'collect'):
            return {'state': 'running', 'in': 120, 'out': 450, 'epoch': 'boot-one', 'message': 'applied'}
        if action in ('stop', 'remove'):
            return {'state': 'stopped', 'message': action}
        return {'state': 'online', 'message': action}


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.remote = FakeRemote()
        self.app = create_app(self.temp.name, start_worker=False, remote=self.remote)
        self.store = self.app.state.store
        self.worker = self.app.state.worker
        self.store.execute('INSERT INTO admins VALUES(?,?)', ('admin', password_hash(PASSWORD)))
        self.client = TestClient(self.app)
        self.client.headers['X-Aurora-Request'] = '1'
        response = self.client.post('/api/login', json={'username': 'admin', 'password': PASSWORD})
        self.assertEqual(response.status_code, 200)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def server(self):
        data = dict(name='Singapore', host='192.0.2.10', port=22, username='root', auth_type='password',
                    credential='private-ssh-password', fingerprint='SHA256:' + 'a' * 43, notes='test')
        response = self.client.post('/api/servers', json=data)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id'], data

    def rule(self, server_id, **updates):
        data = dict(server_id=server_id, name='Forward', method='iptables', protocol='tcp',
                    listen_ip='0.0.0.0', listen_port=12080, target_host='198.51.100.1', target_port=443)
        data.update(updates)
        response = self.client.post('/api/rules', json=data)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id'], data

    def test_private_endpoints_and_csrf(self):
        anonymous = TestClient(self.app)
        self.assertEqual(anonymous.get('/api/servers').status_code, 401)
        self.assertEqual(self.client.post('/api/servers', json={}, headers={'X-Aurora-Request': ''}).status_code, 403)
        self.assertEqual(self.client.post('/api/logout', headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertIn('HttpOnly', self.client.post('/api/login', json={'username':'admin','password':PASSWORD}).headers['set-cookie'])
        anonymous.close()

    def test_credentials_encrypted_and_never_returned(self):
        sid, data = self.server()
        raw = self.store.one('SELECT * FROM servers WHERE id=?', (sid,))
        self.assertNotEqual(raw['credential'], data['credential'])
        self.assertEqual(self.store.unseal(raw['credential']), data['credential'])
        self.assertNotIn(data['credential'], self.client.get('/api/servers').text)
        self.assertNotIn('credential', self.client.get('/api/export').text)
        data['credential'] = ''
        self.assertEqual(self.client.put('/api/servers/'+sid, json=data).status_code, 200)
        self.assertEqual(self.store.one('SELECT credential FROM servers WHERE id=?', (sid,))['credential'], raw['credential'])

    def test_only_two_methods_and_no_shell_fragments(self):
        sid, _ = self.server()
        _, data = self.rule(sid)
        for field, value in [('method','socat'), ('target_host','example.com; touch /tmp/pwned'), ('listen_ip','$(id)'), ('listen_port',65536)]:
            with self.subTest(field=field):
                self.assertEqual(self.client.post('/api/rules', json={**data, field:value}).status_code, 422)

    def test_port_conflicts_and_ssh_protection(self):
        sid, _ = self.server()
        _, data = self.rule(sid)
        self.assertEqual(self.client.post('/api/rules', json={**data,'method':'gost'}).status_code, 409)
        self.assertEqual(self.client.post('/api/rules', json={**data,'listen_port':22}).status_code, 422)
        self.assertEqual(self.client.post('/api/rules', json={**data,'protocol':'udp'}).status_code, 200)
        self.assertEqual(self.client.post('/api/rules', json={**data,'listen_ip':'::','target_host':'2001:db8::10'}).status_code, 409)

    def test_native_rule_lifecycle_and_busy_edit(self):
        sid, _ = self.server()
        rid, data = self.rule(sid)
        result = self.client.post(f'/api/rules/{rid}/apply')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.put(f'/api/rules/{rid}', json=data).status_code, 409)
        self.assertEqual(self.client.post(f'/api/rules/{rid}/stop').status_code, 409)
        self.worker.run_once()
        rule = self.store.one('SELECT * FROM rules WHERE id=?', (rid,))
        self.assertEqual(rule['state'], 'running')
        self.assertEqual(rule['bytes_in'], 120)
        self.assertEqual(self.client.put(f'/api/rules/{rid}', json=data).status_code, 409)
        self.client.post(f'/api/rules/{rid}/stop')
        self.worker.run_once()
        self.assertEqual(self.store.one('SELECT * FROM rules WHERE id=?', (rid,))['bytes_in'], 120)
        self.assertEqual(self.client.put(f'/api/rules/{rid}', json={**data,'target_port':8443}).status_code, 200)
        self.assertEqual(self.client.delete(f'/api/servers/{sid}').status_code, 409)
        self.client.post(f'/api/rules/{rid}/remove')
        self.worker.run_once()
        self.assertEqual(self.client.get('/api/rules').json(), [])
        self.assertEqual(self.client.delete(f'/api/servers/{sid}').status_code, 200)

    def test_failure_does_not_claim_success_and_persists_restart(self):
        sid, _ = self.server()
        rid, _ = self.rule(sid)
        self.remote.fail = True
        task = self.client.post(f'/api/rules/{rid}/apply').json()['task_id']
        self.worker.run_once()
        self.assertEqual(self.store.one('SELECT * FROM jobs WHERE id=?', (task,), jobs=True)['state'], 'failed')
        self.assertEqual(self.store.one('SELECT state FROM rules WHERE id=?', (rid,))['state'], 'error')
        self.store.execute("UPDATE jobs SET state='running' WHERE id=?", (task,), jobs=True)
        self.worker.recover()
        persisted = Store(self.temp.name)
        self.assertEqual(persisted.one('SELECT state FROM jobs WHERE id=?', (task,), jobs=True)['state'], 'interrupted')
        self.assertEqual(persisted.one('SELECT state FROM rules WHERE id=?', (rid,))['state'], 'unknown')

    def test_counter_generation_reset_and_backup(self):
        sid, _ = self.server()
        rid, _ = self.rule(sid)
        self.store.sample(rid, 100, 200, 'first')
        self.store.sample(rid, 150, 250, 'first')
        # Even when new counters exceed old values, a new epoch adds the whole new count.
        self.store.sample(rid, 500, 600, 'second')
        self.store.sample(rid, 10, 20, 'second')
        rule = self.store.one('SELECT * FROM rules WHERE id=?', (rid,))
        self.assertEqual((rule['bytes_in'],rule['bytes_out']), (660,870))
        archive = backup(self.store, Path(self.temp.name)/'backup.zip')
        restore = Path(self.temp.name)/'restored'
        with zipfile.ZipFile(archive) as saved:
            saved.extractall(restore)
        recovered = Store(restore)
        self.assertEqual(recovered.one('SELECT bytes_in FROM rules')['bytes_in'], 660)
        self.assertEqual(recovered.unseal(recovered.one('SELECT credential FROM servers')['credential']), 'private-ssh-password')

    def test_password_change_revokes_all_sessions(self):
        response = self.client.post('/api/password', json={'current_password':PASSWORD,'new_password':'another-long-password'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/me').status_code, 401)
        self.assertEqual(self.client.post('/api/login', json={'username':'admin','password':'another-long-password'}).status_code, 200)

    def test_login_rate_limit(self):
        for index in range(10):
            response = self.client.post('/api/login', json={'username':'admin','password':'wrong'})
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.post('/api/login', json={'username':'admin','password':PASSWORD}).status_code, 429)

    def test_single_instance_lock_and_missing_key(self):
        first, second = ProcessLock(self.temp.name), ProcessLock(self.temp.name)
        first.acquire()
        try:
            with self.assertRaises(RuntimeError):
                second.acquire()
        finally:
            first.release()
        second.acquire()
        second.release()
        (Path(self.temp.name)/'master.key').unlink()
        with self.assertRaises(RuntimeError):
            Store(self.temp.name)


class RuleCompilerTests(unittest.TestCase):
    def setUp(self):
        self.rule = dict(id='abcdef123456', server_id='111111111111', name='example', method='iptables',
                         protocol='both',listen_ip='0.0.0.0',listen_port=10080,target_host='198.51.100.1',target_port=443)

    def test_only_owned_chains_and_scoped_forwarding(self):
        tool, chains, body, jumps = iptables_plan(self.rule)
        self.assertEqual(tool,'iptables')
        self.assertTrue(all(chain.startswith('ALabcdef123456') for _,chain in chains))
        self.assertEqual(len(jumps),6)
        for table,parent,args in jumps:
            if parent=='FORWARD':
                self.assertIn('--ctstate',args)
                self.assertIn('DNAT',args)
                self.assertIn('--ctorigdstport',args)
                self.assertIn('--ctreplsrc',args)
            if parent=='PREROUTING':
                self.assertIn('LOCAL',args)
        self.assertEqual([args[-1] for _,chain,args in body if chain.endswith('F')],['ACCEPT','ACCEPT'])
        self.assertNotIn(' -F ', service_text(self.rule))

    def test_ipv6_gost_domain_and_protocol_configs(self):
        ipv6={**self.rule,'listen_ip':'::','target_host':'2001:db8::1'}
        self.assertEqual(iptables_plan(ipv6)[0],'ip6tables')
        self.assertIn('[2001:db8::1]:443',json.dumps(iptables_plan(ipv6)))
        with self.assertRaises(ValueError):
            RuleInput(**{k:v for k,v in {**self.rule,'target_host':'example.com'}.items() if k!='id'})
        config=gost_config({**self.rule,'method':'gost','listen_ip':'::','target_host':'example.com'})
        self.assertEqual([s['listener']['type'] for s in config['services']],['tcp','udp'])
        self.assertEqual(config['services'][0]['addr'],'[::]:10080')
        self.assertTrue(config['metrics']['addr'].startswith('unix:///run/'))

    def test_gost_actual_metric_names_and_sums(self):
        text='''# HELP ignored
gost_service_transfer_input_bytes_total{service="a",host=""} 128
gost_service_transfer_input_bytes_total{service="b"} 2.56e2
gost_service_transfer_output_bytes_total{service="a"} 1024
gost_service_requests_total{service="a"} 99
'''
        self.assertEqual(parse_gost_metrics(text),(384,1024))


if __name__ == '__main__':
    unittest.main()
