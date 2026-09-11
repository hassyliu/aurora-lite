import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from aurora.app import create_app
from aurora.remote_agent import diagnose, finish_diagnosis, probe_tcp, service_text
from aurora.storage import Store, password_hash


class InspectionRemote:
    def __init__(self):
        self.fail = False
        self.actions = []

    def execute(self, server, action, rule=None, on_progress=None):
        self.actions.append(action)
        if self.fail:
            raise RuntimeError('Authentication failed: private-test-password')
        if action == 'check':
            return dict(state='online', hostname='relay', systemd=True, iptables=True, gost=False)
        if action in ('apply', 'collect'):
            return dict(state='running', **{'in': 10, 'out': 20, 'epoch': 'test'})
        if action in ('stop', 'remove'):
            return dict(state='stopped', message='stopped')
        return {'state': 'running', 'checks': [
            {'key': 'target_tcp', 'title': '目标 TCP', 'status': 'failed', 'detail': 'Connection refused'}]}


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.remote = InspectionRemote()
        self.app = create_app(self.temp.name, start_worker=False, remote=self.remote)
        self.store = self.app.state.store
        self.worker = self.app.state.worker
        self.store.execute('INSERT INTO admins VALUES(?,?)', ('admin', password_hash('test-password-123')))
        self.client = TestClient(self.app)
        self.client.headers['X-Aurora-Request'] = '1'
        self.client.post('/api/login', json={'username': 'admin', 'password': 'test-password-123'})
        self.sid = self.client.post('/api/servers', json={
            'name': 'Relay', 'host': '127.0.0.1', 'auth_type': 'password',
            'credential': 'private-test-password', 'fingerprint': 'SHA256:' + 'a' * 43}).json()['id']

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def add_rule(self):
        result = self.client.post('/api/rules', json={
            'server_id': self.sid, 'name': 'Forward', 'method': 'iptables', 'protocol': 'both',
            'listen_port': 28080, 'target_host': '198.51.100.10', 'target_port': 443}).json()['id']
        self.worker.run_once()
        self.remote.actions.clear()
        return result

    def test_destination_order_is_atomic_and_preserves_rule_snapshots(self):
        first = self.client.post('/api/destinations', json={'name': 'IP', 'host': '198.51.100.10', 'port': 443}).json()['id']
        second = self.client.post('/api/destinations', json={'name': 'Domain', 'host': 'EXAMPLE.com', 'port': 8443}).json()['id']
        rid = self.add_rule()
        ordered = self.client.put('/api/destinations/order', json={'ids': [second, first]})
        self.assertEqual(ordered.status_code, 200)
        self.assertEqual([d['id'] for d in self.client.get('/api/destinations').json()], [second, first])
        self.assertEqual(self.client.put('/api/destinations/order', json={'ids': [first]}).status_code, 409)
        self.assertEqual(self.client.put('/api/destinations/order', json={'ids': [first, first]}).status_code, 422)
        self.client.put('/api/destinations/' + first, json={'name': 'Changed', 'host': '198.51.100.20', 'port': 80})
        self.client.delete('/api/destinations/' + first)
        self.assertEqual(self.store.one('SELECT target_host FROM rules WHERE id=?', (rid,))['target_host'], '198.51.100.10')
        self.assertEqual(Store(self.temp.name).rows('SELECT id FROM destinations ORDER BY position')[0]['id'], second)
        self.assertEqual(self.client.get('/api/export').json()['destinations'][0]['host'], 'example.com')
        self.assertEqual(self.client.post('/api/destinations', json={'name': 'bad', 'host': 'x; id', 'port': 80}).status_code, 422)
        with TestClient(self.app) as anonymous:
            self.assertEqual(anonymous.get('/api/destinations').status_code, 401)

    def test_server_check_is_persisted_and_failure_is_redacted(self):
        self.client.post('/api/servers/' + self.sid + '/check')
        self.worker.run_once()
        server = self.client.get('/api/servers').json()[0]
        self.assertEqual(server['check_result']['status'], 'passed')
        self.assertTrue(server['check_result']['components']['iptables'])
        self.assertFalse(server['check_result']['components']['gost'])
        rid = self.add_rule()
        self.client.post('/api/rules/' + rid + '/stop')
        self.worker.run_once()
        self.store.execute('UPDATE rules SET check_result=? WHERE id=?', ('{"status":"inactive"}', rid))
        response = self.client.put('/api/servers/' + self.sid, json={
            'name': 'Renamed relay', 'host': '127.0.0.2', 'auth_type': 'password',
            'credential': '', 'fingerprint': 'SHA256:' + 'a' * 43})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.client.get('/api/servers').json()[0]['check_result'])
        self.assertIsNone(self.client.get('/api/rules').json()[0]['check_result'])
        self.remote.fail = True
        self.client.post('/api/servers/' + self.sid + '/check')
        self.worker.run_once()
        server = self.client.get('/api/servers').json()[0]
        self.assertEqual(server['check_result']['status'], 'failed')
        self.assertNotIn('private-test-password', json.dumps(server))

    def test_rule_inspection_records_fault_without_changing_desired_state(self):
        rid = self.add_rule()
        self.store.execute("UPDATE rules SET desired='running',state='running' WHERE id=?", (rid,))
        self.client.post('/api/rules/' + rid + '/diagnose')
        self.worker.run_once()
        rule = self.client.get('/api/rules').json()[0]
        self.assertEqual(rule['desired'], 'running')
        self.assertEqual(rule['state'], 'running')
        self.assertEqual(rule['check_result']['status'], 'failed')
        self.assertEqual(self.remote.actions, ['diagnose'])
        self.assertIn('entry_tcp', [c['key'] for c in rule['check_result']['checks']])

    def test_interrupted_inspection_does_not_mark_service_stopped(self):
        rid = self.add_rule()
        self.store.execute("UPDATE rules SET desired='running',state='running' WHERE id=?", (rid,))
        task = self.client.post('/api/rules/' + rid + '/diagnose').json()['task_id']
        self.store.execute("UPDATE jobs SET state='running' WHERE id=?", (task,), jobs=True)
        self.worker.recover()
        rule = self.client.get('/api/rules').json()[0]
        self.assertEqual(rule['state'], 'running')
        self.assertEqual(rule['check_result']['status'], 'unknown')

    def test_additive_migration_keeps_data_and_key(self):
        rid = self.add_rule()
        credential = self.store.one('SELECT credential FROM servers')['credential']
        with self.store.connect() as db:
            for table in ('servers', 'rules'):
                db.execute(f'ALTER TABLE {table} DROP COLUMN check_result')
            db.execute('DROP TABLE destinations')
            db.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
        migrated = Store(self.temp.name)
        self.assertEqual(migrated.one('SELECT id FROM rules')['id'], rid)
        self.assertEqual(migrated.unseal(credential), 'private-test-password')
        self.assertEqual(migrated.one('SELECT value FROM meta WHERE key=?', ('schema_version',))['value'], '3')
        self.assertEqual(migrated.rows('SELECT * FROM destinations'), [])


class DiagnosticTests(unittest.TestCase):
    def test_live_tcp_listener_and_closed_port(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            self.assertEqual(probe_tcp('127.0.0.1', port)['status'], 'passed')
        self.assertEqual(probe_tcp('127.0.0.1', port)['status'], 'failed')

    def test_udp_and_same_host_iptables_never_claim_complete_success(self):
        rule = {'method': 'iptables', 'protocol': 'both', 'listen_ip': '0.0.0.0', 'listen_port': 28080}
        result = {'state': 'running', 'checks': [{'key': 'udp', 'status': 'unknown'}]}
        with patch('aurora.remote_agent.probe_tcp') as tcp:
            finished = finish_diagnosis({'host': '127.0.0.1'}, rule, result)
        tcp.assert_not_called()
        self.assertEqual(finished['status'], 'partial')
        self.assertEqual(finished['checks'][-1]['status'], 'unknown')

    def test_missing_kernel_rule_is_reported_without_mutations(self):
        rule = dict(id='abcdef123456', method='iptables', protocol='tcp', listen_ip='0.0.0.0',
                    listen_port=28080, target_host='198.51.100.10', target_port=443)
        calls = []

        def command(args, **kwargs):
            calls.append(args)
            if args[0] == 'systemctl':
                return subprocess.CompletedProcess(args, 0, 'ActiveState=active\nMainPID=0\nNeedDaemonReload=no\n', '')
            return subprocess.CompletedProcess(args, 1 if 'PREROUTING' in args else 0, '', '')

        def read(path, *args, **kwargs):
            if str(path).endswith('ip_forward'):
                return '1'
            if str(path).endswith('.service'):
                return service_text(rule)
            return json.dumps(rule)

        with patch('aurora.remote_agent.run', side_effect=command), patch.object(Path, 'read_text', read), \
             patch('aurora.remote_agent.collect', return_value={'state': 'running', 'in': 1, 'out': 1, 'epoch': 'a'}), \
             patch('aurora.remote_agent.probe_tcp', return_value={'status': 'passed', 'detail': 'TCP OK'}):
            result = diagnose(rule)
        self.assertEqual(next(c for c in result['checks'] if c['key'] == 'kernel')['status'], 'failed')
        self.assertFalse(any(flag in args for args in calls for flag in ('-A', '-D', '-I', '-F')))


if __name__ == '__main__':
    unittest.main()
