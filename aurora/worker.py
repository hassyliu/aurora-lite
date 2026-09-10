import json
import logging
import secrets
import threading
import time

from .remote_agent import finish_diagnosis

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, store, remote, interval=60):
        self.store, self.remote = store, remote
        self.interval = max(15, interval)
        self.guard = threading.RLock()
        self.shutdown = threading.Event()
        self.thread = None
        self.last_poll = 0
        self.sampling = set()

    def busy(self, server_id):
        return server_id in self.sampling or self.store.one("SELECT id FROM jobs WHERE server_id=? AND state IN ('pending','running')",
                              (server_id,), jobs=True) is not None

    def enqueue(self, action, server_id, rule_id=None):
        with self.guard:
            if self.busy(server_id):
                raise ValueError('这台服务器仍有任务在执行，请等待任务完成')
            task_id = secrets.token_hex(6)
            self.store.execute('INSERT INTO jobs(id,action,server_id,rule_id,created) VALUES(?,?,?,?,?)',
                               (task_id, action, server_id, rule_id, time.time()), jobs=True)
            if rule_id and action in ('apply', 'stop', 'remove'):
                self.store.execute("UPDATE rules SET check_result='' WHERE id=?", (rule_id,))
            return task_id

    def save_check(self, table, record_id, result):
        if table not in ('servers', 'rules'):
            raise ValueError('Invalid check record type')
        self.store.execute(f'UPDATE {table} SET check_result=? WHERE id=?',
                           (json.dumps(result, ensure_ascii=False), record_id))

    def recover(self):
        running = self.store.rows("SELECT * FROM jobs WHERE state='running'", jobs=True)
        for job in running:
            self.store.execute("UPDATE jobs SET state='interrupted', log=?, finished=? WHERE id=?",
                               ('主控在任务执行期间重启，远程状态需重新检查；没有自动重放操作。', time.time(), job['id']), jobs=True)
            if job['action'] in ('check', 'diagnose'):
                self.save_check('rules' if job['rule_id'] else 'servers', job['rule_id'] or job['server_id'],
                                {'status': 'unknown', 'summary': '检查中断，请重新检测',
                                 'checked_at': time.time(), 'checks': []})
            elif job['rule_id']:
                self.store.execute("UPDATE rules SET state='unknown',last_error=? WHERE id=?",
                                   ('任务中断，请检查或重新停止/下发', job['rule_id']))

    def start(self):
        self.recover()
        self.thread = threading.Thread(target=self.loop, name='aurora-worker', daemon=True)
        self.thread.start()

    def stop(self):
        self.shutdown.set()
        if self.thread:
            self.thread.join()

    def sample_rule(self, server, rule):
        result = self.remote.execute(server, 'collect', rule)
        if result['state'] == 'running':
            self.store.sample(rule['id'], result['in'], result['out'], result['epoch'])
        expected_running = rule['desired'] == 'running'
        error = result.get('error', '') if expected_running else ''
        self.store.execute('UPDATE rules SET state=?,last_error=? WHERE id=?',
                           (result['state'], error, rule['id']))
        return result

    def run_once(self):
        with self.guard:
            job = self.store.one("SELECT * FROM jobs WHERE state='pending' ORDER BY created LIMIT 1", jobs=True)
            if not job:
                return False
            self.store.execute("UPDATE jobs SET state='running',started=? WHERE id=?",
                               (time.time(), job['id']), jobs=True)
        server = self.store.one('SELECT * FROM servers WHERE id=?', (job['server_id'],))
        rule = self.store.one('SELECT * FROM rules WHERE id=?', (job['rule_id'],)) if job['rule_id'] else None
        started = time.monotonic()
        try:
            if not server or (job['rule_id'] and not rule):
                raise ValueError('任务引用的服务器或规则不存在')
            action = job['action']
            if rule and action in ('apply', 'stop', 'remove'):
                # Best-effort final sample before counters reset. Failure never fabricates data.
                if rule['state'] == 'running':
                    try:
                        self.sample_rule(server, rule)
                    except Exception:
                        logger.info('Final traffic sample unavailable for %s', rule['id'])
                self.store.execute('UPDATE rules SET desired=? WHERE id=?',
                                   ('running' if action == 'apply' else 'stopped', rule['id']))
            result = self.remote.execute(server, action, rule)
            if action == 'diagnose':
                result = finish_diagnosis(server, rule, result)
                self.save_check('rules', rule['id'], result)
                self.store.execute("UPDATE rules SET state=?,last_error='' WHERE id=?", (result['state'], rule['id']))
                if 'in' in result:
                    self.store.sample(rule['id'], result['in'], result['out'], result['epoch'])
            elif action == 'check':
                self.save_check('servers', server['id'], {
                    'status': 'passed', 'summary': '连接成功，SSH 认证及管理权限正常',
                    'hostname': result.get('hostname', ''), 'checked_at': time.time(),
                    'latency_ms': round((time.monotonic() - started) * 1000),
                    'components': {name: bool(result.get(name)) for name in ('systemd', 'iptables', 'gost')},
                })
            with self.guard:
                if rule and action == 'remove':
                    self.store.execute('DELETE FROM rules WHERE id=?', (rule['id'],))
                elif rule and action in ('apply', 'stop'):
                    self.store.execute("UPDATE rules SET state=?,last_error='' WHERE id=?",
                                       (result['state'], rule['id']))
                    if action == 'apply' and 'in' in result:
                        self.store.sample(rule['id'], result['in'], result['out'], result['epoch'])
                self.store.execute("UPDATE servers SET state='online',last_error='',last_checked=? WHERE id=?",
                                   (time.time(), server['id']))
                self.store.execute("UPDATE jobs SET state='succeeded',log=?,finished=? WHERE id=?",
                                   (result.get('message') or json.dumps(result, ensure_ascii=False), time.time(), job['id']), jobs=True)
        except Exception as error:
            message = str(error)[-16000:]
            if server:
                for field in ('credential', 'passphrase'):
                    secret = self.store.unseal(server[field])
                    if secret:
                        message = message.replace(secret, '[已隐藏凭据]')
            with self.guard:
                self.store.execute("UPDATE jobs SET state='failed',log=?,finished=? WHERE id=?",
                                   (message, time.time(), job['id']), jobs=True)
                if job['action'] in ('check', 'diagnose'):
                    self.save_check('rules' if rule else 'servers', rule['id'] if rule else job['server_id'],
                                    {'status': 'unknown' if rule else 'failed', 'summary': '检测未完成' if rule else '连接检查失败',
                                     'detail': message, 'checked_at': time.time(), 'checks': []})
                if rule and job['action'] == 'diagnose':
                    self.store.execute("UPDATE rules SET state='unknown',last_error=? WHERE id=?", (message, rule['id']))
                elif rule and job['action'] != 'logs':
                    self.store.execute("UPDATE rules SET state='error',last_error=? WHERE id=?", (message, rule['id']))
                if server:
                    self.store.execute("UPDATE servers SET state='error',last_error=?,last_checked=? WHERE id=?",
                                       (message, time.time(), server['id']))
        return True

    def poll(self):
        rules = self.store.rows("SELECT * FROM rules WHERE desired='running'")
        for rule in rules:
            if self.shutdown.is_set():
                break
            # Reserve this server without holding a lock during slow SSH I/O.
            with self.guard:
                if self.busy(rule['server_id']):
                    continue
                self.sampling.add(rule['server_id'])
                server = self.store.one('SELECT * FROM servers WHERE id=?', (rule['server_id'],))
            try:
                self.sample_rule(server, rule)
                self.store.execute("UPDATE servers SET state='online',last_error='',last_checked=? WHERE id=?",
                                   (time.time(), server['id']))
            except Exception as error:
                self.store.execute("UPDATE rules SET state='unknown',last_error=? WHERE id=?", (str(error)[-3000:], rule['id']))
                self.store.execute("UPDATE servers SET state='error',last_error=?,last_checked=? WHERE id=?",
                                   (str(error)[-3000:], time.time(), server['id']))
            finally:
                with self.guard:
                    self.sampling.discard(rule['server_id'])

    def loop(self):
        while not self.shutdown.is_set():
            try:
                if self.run_once():
                    continue
                if time.monotonic() - self.last_poll >= self.interval:
                    self.poll()
                    self.last_poll = time.monotonic()
                self.store.execute('DELETE FROM jobs WHERE finished < ?', (time.time() - 30 * 86400,), jobs=True)
            except Exception:
                logger.exception('Background worker failed; persisted tasks remain inspectable')
            self.shutdown.wait(1)
