"""Run the real Bash transaction with fixture binaries and simulated systemd/curl."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/upgrade.sh'
PACKAGE = 'aurora-lite-0.3.0-linux-amd64-debian13'

BINARY = '''#!/usr/bin/env bash
if [[ $1 == --version ]]; then
    echo 'Aurora Lite VERSION'
elif [[ $* == *doctor* ]]; then
    printf 'migrated data' > "$FIXTURE_PANEL/data/aurora.db"
    [[ $FIXTURE_MODE != doctor-fails ]] || exit 1
    echo 'aurora.db: ok'
fi
'''

HARNESS = r'''
source "$SCRIPT_PATH"
PANEL=$FIXTURE_PANEL
BACKUP_ROOT=$FIXTURE_BACKUPS
SHA256=$FIXTURE_SHA
sleep() { :; }
systemctl() {
    case "$1" in
        show)
            case "$4" in
                User) echo aurora-panel;;
                ExecStart) echo "{ path=$PANEL/aurora-lite ; argv[]=$PANEL/aurora-lite --data-dir $PANEL/data serve ; }";;
                Result) echo success;;
            esac;;
        is-active) [[ $(cat "$FIXTURE_STATE") == active ]];;
        stop)
            echo stop >> "$FIXTURE_CALLS"
            [[ $FIXTURE_MODE != stop-fails ]] || return 1
            echo inactive > "$FIXTURE_STATE";;
        start) echo start >> "$FIXTURE_CALLS"; echo active > "$FIXTURE_STATE";;
        cat) echo '[Service]';;
        *) return 1;;
    esac
}
curl() {
    local output='' argument
    while (( $# )); do
        argument=$1; shift
        if [[ $argument == -o ]]; then output=$1; shift; fi
    done
    if [[ -n $output ]]; then
        echo download >> "$FIXTURE_CALLS"
        [[ $FIXTURE_MODE != download-fails ]] || return 22
        cp "$FIXTURE_ARCHIVE" "$output"
    else
        local line current
        line=$("$PANEL/aurora-lite" --version); current=${line#Aurora Lite }
        [[ $FIXTURE_MODE != health-fails || $current != 0.3.0 ]] || return 7
        printf '{"version":"%s","initialized":true}' "$current"
    fi
}
runuser() { shift 3; "$@"; }
tar() {
    [[ $FIXTURE_MODE != backup-fails || $1 != -czf ]] || return 2
    command tar "$@"
}
main
'''


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Upgrade integration tests run inside the disposable Debian 13 CI container')
class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aurora-upgrade-test-')
        self.root = Path(self.temp.name)
        self.panel = self.root/'panel'
        (self.panel/'data').mkdir(parents=True)
        self.original = {'aurora.db':b'original business data','tasks.db':b'original tasks','master.key':b'original key'}
        for name, data in self.original.items():
            (self.panel/'data'/name).write_bytes(data)
        self.settings = b'{"host":"127.0.0.1","port":8000,"public_origin":"https://panel.example.com","secure_cookie":true}'
        (self.panel/'settings.json').write_bytes(self.settings)
        self.old_binary = BINARY.replace('VERSION', '0.2.1')
        self.write_binary(self.panel/'aurora-lite', self.old_binary)
        self.stage = self.root/PACKAGE
        self.stage.mkdir()
        self.write_binary(self.stage/'aurora-lite', BINARY.replace('VERSION', '0.3.0'))
        self.state = self.root/'state'
        self.state.write_text('active')
        self.calls = self.root/'calls'
        self.calls.touch()

    def tearDown(self):
        self.temp.cleanup()

    def write_binary(self, path, text):
        path.write_text(text)
        path.chmod(0o755)

    def run_upgrade(self, mode='success', bad_hash=False):
        digest = hashlib.sha256((self.stage/'aurora-lite').read_bytes()).hexdigest()
        (self.stage/'SHA256SUMS').write_text(digest+'  aurora-lite\n')
        archive = self.root/'package.tar.gz'
        with tarfile.open(archive, 'w:gz') as package:
            package.add(self.stage, arcname=PACKAGE)
        digest = '0'*64 if bad_hash else hashlib.sha256(archive.read_bytes()).hexdigest()
        env = {**os.environ, 'SCRIPT_PATH':str(SCRIPT),'FIXTURE_PANEL':str(self.panel),
               'FIXTURE_BACKUPS':str(self.root/'backups'),'FIXTURE_SHA':digest,'FIXTURE_ARCHIVE':str(archive),
               'FIXTURE_STATE':str(self.state),'FIXTURE_CALLS':str(self.calls),'FIXTURE_MODE':mode}
        return subprocess.run(['bash','-c',HARNESS],env=env,text=True,capture_output=True,timeout=30)

    def assert_original(self):
        self.assertEqual((self.panel/'aurora-lite').read_text(), self.old_binary)
        self.assertEqual((self.panel/'settings.json').read_bytes(), self.settings)
        for name, data in self.original.items():
            self.assertEqual((self.panel/'data'/name).read_bytes(), data)
        self.assertEqual(self.state.read_text().strip(), 'active')

    def test_success_backs_up_and_preserves_settings(self):
        result = self.run_upgrade()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('升级成功：v0.3.0',result.stdout)
        self.assertIn('Aurora Lite 0.3.0',(self.panel/'aurora-lite').read_text())
        self.assertEqual((self.panel/'settings.json').read_bytes(),self.settings)
        backup = next((self.root/'backups').iterdir())
        self.assertEqual((backup/'aurora-lite').read_text(),self.old_binary)
        with tarfile.open(backup/'data.tar.gz') as package:
            self.assertEqual(package.extractfile('data/aurora.db').read(),self.original['aurora.db'])

    def test_download_failure_does_not_stop_service(self):
        result=self.run_upgrade('download-fails')
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('stop',self.calls.read_text())
        self.assert_original()

    def test_bad_checksum_does_not_stop_service(self):
        result=self.run_upgrade(bad_hash=True)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('stop',self.calls.read_text())
        self.assert_original()

    def test_backup_failure_restarts_original(self):
        result=self.run_upgrade('backup-fails')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('start',self.calls.read_text())
        self.assert_original()

    def test_failed_migration_restores_program_and_full_data(self):
        result=self.run_upgrade('doctor-fails')
        self.assertNotEqual(result.returncode,0)
        self.assert_original()
        failed=next(self.panel.glob('data.failed-*'))
        self.assertEqual((failed/'aurora.db').read_text(),'migrated data')

    def test_failed_health_check_restores_program_and_full_data(self):
        result=self.run_upgrade('health-fails')
        self.assertNotEqual(result.returncode,0)
        self.assert_original()
        self.assertTrue(list(self.panel.glob('data.failed-*')))

    def test_already_current_is_a_noop(self):
        self.write_binary(self.panel/'aurora-lite',BINARY.replace('VERSION','0.3.0'))
        result=self.run_upgrade()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('无需重复升级',result.stdout)
        self.assertEqual(self.calls.read_text(),'')

    def test_stop_failure_preserves_original(self):
        result=self.run_upgrade('stop-fails')
        self.assertNotEqual(result.returncode,0)
        self.assert_original()


if __name__ == '__main__':
    unittest.main()
