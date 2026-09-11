import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from aurora import remote_agent as agent
from aurora.storage import Store


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.patches = [patch.object(agent, 'ROOT', self.base/'opt'), patch.object(agent, 'CONFIG', self.base/'etc'),
                        patch.object(agent, 'UNITS', self.base/'units'), patch.object(agent, 'REPORT_PROGRESS', False)]
        for item in self.patches:
            item.start()
        self.old = dict(id='abcdef123456', server_id='111111111111', name='Old', method='iptables',
                        protocol='both', listen_ip='0.0.0.0', listen_port=12080,
                        target_host='198.51.100.1', target_port=443)
        agent.write_file(agent.CONFIG/'rules'/f"{self.old['id']}.json", json.dumps(self.old))
        agent.write_file(agent.UNITS/agent.service_name(self.old), agent.service_text(self.old))
        agent.write_file(agent.ROOT/'bin/gost', 'fake binary')

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_running_edit_cleans_old_address_family_and_method_before_install(self):
        new = {**self.old, 'method': 'gost', 'listen_ip': '::', 'listen_port': 18080, 'target_host': 'example.com', 'target_port': 8443}
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(agent, 'run', side_effect=run), patch.object(agent, 'ipt_stop') as cleanup, \
             patch.object(agent.shutil, 'which', return_value='/usr/bin/tool'), patch.object(agent.time, 'sleep'), \
             patch.object(agent, 'collect', return_value={'state': 'running', 'in': 0, 'out': 0, 'epoch': 'new'}):
            result = agent.apply(new, '# agent')
        cleanup.assert_called_once_with(self.old)
        self.assertEqual(result['state'], 'running')
        written = json.loads((agent.CONFIG/'rules'/f"{self.old['id']}.json").read_text())
        self.assertEqual(written, new)
        self.assertLess(calls.index(['systemctl', 'disable', '--now', agent.service_name(new)]),
                        calls.index(['systemctl', 'restart', agent.service_name(new)]))

    def test_stop_after_unsuccessful_edit_uses_actual_deployed_config(self):
        edited = {**self.old, 'method': 'gost', 'target_host': 'example.com'}
        with patch.object(agent, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')), \
             patch.object(agent, 'ipt_stop') as cleanup:
            agent.stop(edited)
        cleanup.assert_called_once_with(self.old)

    def test_failed_preflight_does_not_stop_previous_service(self):
        with patch.object(agent.shutil, 'which', return_value=None), patch.object(agent, 'stop') as stop:
            with self.assertRaisesRegex(RuntimeError, '缺少'):
                agent.apply({**self.old, 'target_port': 8443}, '# agent')
        stop.assert_not_called()
        self.assertEqual(agent.saved_rule(self.old), self.old)

    def test_poll_does_not_claim_new_configuration_is_running(self):
        edited = {**self.old, 'target_port': 8443}
        output = io.StringIO()
        with patch.object(agent.platform, 'system', return_value='Linux'), patch.object(agent.os, 'geteuid', return_value=0, create=True), \
             patch.object(agent, 'collect', return_value={'state': 'running', 'in': 10, 'out': 20, 'epoch': 'old'}) as collect, \
             contextlib.redirect_stdout(output):
            agent.dispatch({'action': 'collect', 'rule': edited})
        collect.assert_called_once_with(self.old)
        result = json.loads(output.getvalue())
        self.assertEqual(result['state'], 'error')
        self.assertIn('上一版', result['error'])
        self.assertEqual(result['in'], 10)

    def test_streaming_command_reports_output_before_exit(self):
        messages = []
        started = time.monotonic()
        with patch.object(agent, 'REPORT_PROGRESS', True), \
             patch.object(agent, 'progress', side_effect=lambda msg: messages.append((msg, time.monotonic()-started))):
            result = agent.run([sys.executable, '-u', '-c', "import time; print('first'); time.sleep(.6); print('last')"])
        times = dict(messages)
        self.assertEqual(result.returncode, 0)
        self.assertGreater(times['last'] - times['first'], .4)


class MigrationTests(unittest.TestCase):
    def test_v2_migration_preserves_order_and_only_infers_unique_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            with store.connect() as db:
                for index in range(2):
                    sid = f'{index+1:012x}'
                    db.execute('''INSERT INTO servers(id,name,host,port,username,auth_type,credential,fingerprint,created)
                        VALUES(?,?,?,22,'root','password','','fingerprint',?)''', (sid, sid, '192.0.2.1', index))
                db.execute("INSERT INTO destinations VALUES('aaaaaaaaaaaa','Target','198.51.100.1',443,'',0,0)")
                db.execute('''INSERT INTO rules(id,server_id,name,method,protocol,listen_ip,listen_port,target_host,target_port,created)
                    VALUES('bbbbbbbbbbbb','000000000001','Rule','iptables','tcp','0.0.0.0',12345,'198.51.100.1',443,0)''')
                db.execute('ALTER TABLE servers DROP COLUMN position')
                db.execute('ALTER TABLE rules DROP COLUMN destination_id')
            key = (Path(directory)/'master.key').read_bytes()
            migrated = Store(directory)
            self.assertEqual([s['id'] for s in migrated.rows('SELECT id FROM servers ORDER BY position')], ['000000000002', '000000000001'])
            self.assertEqual(migrated.one('SELECT destination_id FROM rules')['destination_id'], 'aaaaaaaaaaaa')
            self.assertEqual((Path(directory)/'master.key').read_bytes(), key)
            with migrated.connect() as db:
                db.execute("INSERT INTO destinations VALUES('cccccccccccc','Duplicate','198.51.100.1',443,'',1,0)")
                db.execute('ALTER TABLE rules DROP COLUMN destination_id')
            self.assertIsNone(Store(directory).one('SELECT destination_id FROM rules')['destination_id'])
