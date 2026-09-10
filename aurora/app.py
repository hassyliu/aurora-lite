from contextlib import asynccontextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import time

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .models import DestinationInput, DestinationOrder, Login, PasswordChange, Probe, RuleInput, ServerInput
from .remote import SSHRemote, probe
from .remote_agent import gost_config, iptables_plan, service_text
from .storage import ProcessLock, Store, password_hash, verify_password
from .worker import Worker


def create_app(data_dir=None, start_worker=True, remote=None):
    store = Store(data_dir or os.getenv('AURORA_DATA_DIR', 'data'))
    worker = Worker(store, remote or SSHRemote(store), int(os.getenv('AURORA_POLL_SECONDS', '60')))
    lock = ProcessLock(store.directory)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            lock.acquire()
            worker.start()
        try:
            yield
        finally:
            if start_worker:
                worker.stop()
                lock.release()

    app = FastAPI(title='Aurora Lite', version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.worker = store, worker
    static = Path(__file__).with_name('static')

    @app.middleware('http')
    async def protect(request, call_next):
        if request.url.path.startswith('/api/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            allowed_origin = os.getenv('AURORA_PUBLIC_ORIGIN', str(request.base_url).rstrip('/'))
            if request.headers.get('x-aurora-request') != '1' or (origin and origin != allowed_origin):
                return JSONResponse({'detail': '请求来源验证失败'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def auth(request: Request):
        cookie = request.cookies.get('aurora_session', '')
        digest = hashlib.sha256(cookie.encode()).hexdigest()
        if not cookie or not store.one('SELECT token_hash FROM sessions WHERE token_hash=? AND expires>?', (digest, time.time())):
            raise HTTPException(401, '请先登录')
        return store.one('SELECT username FROM admins')['username']

    secured = [Depends(auth)]

    def with_check_results(rows):
        for row in rows:
            row['check_result'] = json.loads(row['check_result']) if row.get('check_result') else None
        return rows

    def server_by_id(server_id):
        result = store.one('SELECT * FROM servers WHERE id=?', (server_id,))
        if not result:
            raise HTTPException(404, '服务器不存在')
        return result

    def rule_by_id(rule_id):
        result = store.one('SELECT * FROM rules WHERE id=?', (rule_id,))
        if not result:
            raise HTTPException(404, '规则不存在')
        return result

    def require_idle(server_id):
        if worker.busy(server_id):
            raise HTTPException(409, '服务器有待执行或进行中的任务，请稍后重试')

    def check_conflict(rule, exclude=None):
        server = server_by_id(rule.server_id)
        if rule.listen_port == server['port'] and rule.protocol in ('tcp', 'both'):
            raise HTTPException(422, '监听端口与该服务器的 SSH 端口冲突')
        for existing in store.rows('SELECT * FROM rules WHERE server_id=? AND listen_port=?', (rule.server_id, rule.listen_port)):
            if existing['id'] == exclude:
                continue
            overlap_protocol = rule.protocol == 'both' or existing['protocol'] == 'both' or rule.protocol == existing['protocol']
            overlap_ip = (rule.listen_ip == existing['listen_ip'] or
                          ipaddress.ip_address(rule.listen_ip).is_unspecified or
                          ipaddress.ip_address(existing['listen_ip']).is_unspecified)
            if overlap_protocol and overlap_ip:
                raise HTTPException(409, f"监听地址/端口与规则「{existing['name']}」冲突")
        if rule.listen_port == rule.target_port and rule.target_host in (rule.listen_ip, server['host']):
            raise HTTPException(422, '目标不能指向此规则自身，避免转发循环')

    @app.get('/api/status')
    def status():
        return {'initialized': store.one('SELECT username FROM admins') is not None, 'version': __version__}

    @app.post('/api/login')
    def login(body: Login, request: Request, response: Response):
        now = time.time()
        ip = request.client.host if request.client else 'local'
        with worker.guard:
            with store.connect() as db:
                db.execute('DELETE FROM login_attempts WHERE created < ?', (now - 600,))
                if db.execute('SELECT COUNT(*) FROM login_attempts WHERE ip=?', (ip,)).fetchone()[0] >= 10:
                    raise HTTPException(429, '登录尝试过多，请十分钟后再试')
                db.execute('INSERT INTO login_attempts VALUES(?,?)', (ip, now))
            admin = store.one('SELECT * FROM admins WHERE username=?', (body.username,))
            encoded = admin['password_hash'] if admin else password_hash('dummy-password')
            valid = verify_password(body.password, encoded)
            if not admin or not valid:
                raise HTTPException(401, '用户名或密码错误')
            token = secrets.token_urlsafe(32)
            store.execute('DELETE FROM sessions WHERE expires < ?', (now,))
            store.execute('INSERT INTO sessions VALUES(?,?)', (hashlib.sha256(token.encode()).hexdigest(), now + 43200))
            store.execute('DELETE FROM login_attempts WHERE ip=?', (ip,))
        response.set_cookie('aurora_session', token, httponly=True, samesite='strict', max_age=43200,
                            secure=os.getenv('AURORA_SECURE_COOKIE', '').lower() == 'true')
        return {'username': admin['username']}

    @app.get('/api/me')
    def me(username=Depends(auth)):
        return {'username': username}

    @app.post('/api/logout', dependencies=secured)
    def logout(request: Request, response: Response):
        store.execute('DELETE FROM sessions WHERE token_hash=?',
                      (hashlib.sha256(request.cookies.get('aurora_session', '').encode()).hexdigest(),))
        response.delete_cookie('aurora_session')
        return {'ok': True}

    @app.post('/api/password', dependencies=secured)
    def change_password(body: PasswordChange):
        with worker.guard:
            admin = store.one('SELECT * FROM admins')
            if not verify_password(body.current_password, admin['password_hash']):
                raise HTTPException(400, '当前密码错误')
            store.execute('UPDATE admins SET password_hash=?', (password_hash(body.new_password),))
            store.execute('DELETE FROM sessions')
        return {'ok': True}

    @app.get('/api/overview', dependencies=secured)
    def overview():
        servers = store.one("SELECT COUNT(*) AS total, SUM(state='online') AS online FROM servers")
        rules = store.one("SELECT COUNT(*) AS total, SUM(state='running') AS running, SUM(bytes_in) AS bytes_in, SUM(bytes_out) AS bytes_out FROM rules")
        samples = store.rows('''SELECT CAST(created / 3600 AS INTEGER) * 3600 AS hour,
            SUM(bytes_in) AS bytes_in, SUM(bytes_out) AS bytes_out FROM samples WHERE created>?
            GROUP BY hour ORDER BY hour''', (time.time() - 86400,))
        return {'servers': servers, 'rules': rules, 'samples': samples, 'version': __version__,
                'poll_seconds': worker.interval, 'storage': 'SQLite'}

    @app.get('/api/servers', dependencies=secured)
    def servers():
        return with_check_results(store.rows('''SELECT s.id,s.name,s.host,s.port,s.username,s.auth_type,s.fingerprint,
            s.notes,s.state,s.last_error,s.last_checked,s.check_result,s.created,COUNT(r.id) AS rule_count
            FROM servers s LEFT JOIN rules r ON r.server_id=s.id GROUP BY s.id ORDER BY s.created DESC'''))

    @app.post('/api/servers/probe', dependencies=secured)
    def host_probe(body: Probe):
        try:
            return {'fingerprint': probe(body.host, body.port)}
        except Exception as error:
            raise HTTPException(400, '读取主机指纹失败: ' + str(error)) from None

    @app.post('/api/servers', dependencies=secured)
    def add_server(body: ServerInput):
        if not body.credential:
            raise HTTPException(422, '请提供 SSH 密码或私钥')
        data = body.model_dump()
        data.update(id=secrets.token_hex(6), created=time.time())
        data['credential'], data['passphrase'] = store.seal(data['credential']), store.seal(data['passphrase'])
        with worker.guard, store.connect() as db:
            db.execute(f"INSERT INTO servers({','.join(data)}) VALUES({','.join('?' for _ in data)})", list(data.values()))
        return {'id': data['id']}

    @app.put('/api/servers/{server_id}', dependencies=secured)
    def edit_server(server_id: str, body: ServerInput):
        with worker.guard:
            old = server_by_id(server_id)
            require_idle(server_id)
            if store.one("SELECT id FROM rules WHERE server_id=? AND (state!='stopped' OR desired!='stopped')", (server_id,)):
                raise HTTPException(409, '修改服务器连接前，请先停止其全部规则')
            if not body.credential and body.auth_type != old['auth_type']:
                raise HTTPException(422, '更换认证方式时必须提供新凭据')
            data = body.model_dump()
            data['credential'] = store.seal(body.credential) if body.credential else old['credential']
            data['passphrase'] = store.seal(body.passphrase) if body.credential else old['passphrase']
            with store.connect() as db:
                db.execute(f"UPDATE servers SET {','.join(k+'=?' for k in data)},state='unchecked',last_error='',check_result='' WHERE id=?",
                           [*data.values(), server_id])
                db.execute("UPDATE rules SET check_result='' WHERE server_id=?", (server_id,))
        return {'ok': True}

    @app.delete('/api/servers/{server_id}', dependencies=secured)
    def delete_server(server_id: str):
        with worker.guard:
            server_by_id(server_id)
            require_idle(server_id)
            if store.one('SELECT id FROM rules WHERE server_id=?', (server_id,)):
                raise HTTPException(409, '请先删除该服务器的全部规则')
            store.execute('DELETE FROM servers WHERE id=?', (server_id,))
        return {'ok': True}

    @app.post('/api/servers/{server_id}/{action}', dependencies=secured)
    def server_action(server_id: str, action: str):
        if action not in ('check', 'prepare'):
            raise HTTPException(404, '操作不存在')
        with worker.guard:
            server_by_id(server_id)
            require_idle(server_id)
            return {'task_id': worker.enqueue(action, server_id)}

    @app.get('/api/rules', dependencies=secured)
    def rules():
        return with_check_results(store.rows('SELECT r.*,s.name AS server_name,s.host AS server_host FROM rules r JOIN servers s ON s.id=r.server_id ORDER BY r.created DESC'))

    @app.post('/api/rules', dependencies=secured)
    def add_rule(body: RuleInput):
        with worker.guard:
            require_idle(body.server_id)
            check_conflict(body)
            data = body.model_dump()
            data.update(id=secrets.token_hex(6), created=time.time())
            with store.connect() as db:
                db.execute(f"INSERT INTO rules({','.join(data)}) VALUES({','.join('?' for _ in data)})", list(data.values()))
        return {'id': data['id']}

    @app.put('/api/rules/{rule_id}', dependencies=secured)
    def edit_rule(rule_id: str, body: RuleInput):
        with worker.guard:
            old = rule_by_id(rule_id)
            require_idle(old['server_id'])
            if old['state'] != 'stopped' or old['desired'] != 'stopped':
                raise HTTPException(409, '请先成功停止规则，再修改配置')
            if body.server_id != old['server_id']:
                raise HTTPException(422, '规则不能直接迁移服务器，请删除后在新服务器创建')
            check_conflict(body, rule_id)
            data = body.model_dump()
            store.execute(f"UPDATE rules SET {','.join(k+'=?' for k in data)},last_error='',check_result='' WHERE id=?",
                          (*data.values(), rule_id))
        return {'ok': True}

    @app.get('/api/rules/{rule_id}/preview', dependencies=secured)
    def preview(rule_id: str):
        rule = rule_by_id(rule_id)
        if rule['method'] == 'gost':
            config = gost_config(rule)
        else:
            tool, chains, body, jumps = iptables_plan(rule)
            config = {'tool': tool, 'chains': chains, 'chain_rules': body, 'parent_jumps': jumps}
        return {'config': config, 'service': service_text(rule)}

    @app.post('/api/rules/{rule_id}/{action}', dependencies=secured)
    def rule_action(rule_id: str, action: str):
        if action not in ('apply', 'stop', 'remove', 'logs', 'diagnose'):
            raise HTTPException(404, '操作不存在')
        with worker.guard:
            rule = rule_by_id(rule_id)
            require_idle(rule['server_id'])
            if action == 'remove' and rule['state'] == 'stopped' and not rule['epoch'] and not store.one('SELECT id FROM jobs WHERE rule_id=?', (rule_id,), jobs=True):
                store.execute('DELETE FROM rules WHERE id=?', (rule_id,))
                return {'ok': True}
            return {'task_id': worker.enqueue(action, rule['server_id'], rule_id)}

    @app.get('/api/tasks', dependencies=secured)
    def tasks():
        return store.rows('SELECT * FROM jobs ORDER BY created DESC LIMIT 200', jobs=True)

    @app.get('/api/destinations', dependencies=secured)
    def destinations():
        return store.rows('SELECT * FROM destinations ORDER BY position,created,id')

    @app.post('/api/destinations', dependencies=secured)
    def add_destination(body: DestinationInput):
        destination_id = secrets.token_hex(6)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            count = db.execute('SELECT COUNT(*) FROM destinations').fetchone()[0]
            if count >= 2000:
                raise HTTPException(422, '落地地址数量已达上限（2000）')
            position = db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM destinations').fetchone()[0]
            db.execute('INSERT INTO destinations VALUES(?,?,?,?,?,?,?)',
                       (destination_id, body.name, body.host, body.port, body.notes, position, time.time()))
        return {'id': destination_id}

    @app.put('/api/destinations/order', dependencies=secured)
    def reorder_destinations(body: DestinationOrder):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = {row['id'] for row in db.execute('SELECT id FROM destinations')}
            if existing != set(body.ids):
                raise HTTPException(409, '落地列表已变化，请刷新后重新排序')
            db.executemany('UPDATE destinations SET position=? WHERE id=?', enumerate(body.ids))
        return {'ok': True}

    @app.put('/api/destinations/{destination_id}', dependencies=secured)
    def edit_destination(destination_id: str, body: DestinationInput):
        with store.connect() as db:
            changed = db.execute('UPDATE destinations SET name=?,host=?,port=?,notes=? WHERE id=?',
                                 (body.name, body.host, body.port, body.notes, destination_id))
            if not changed.rowcount:
                raise HTTPException(404, '落地地址不存在')
        return {'ok': True}

    @app.delete('/api/destinations/{destination_id}', dependencies=secured)
    def delete_destination(destination_id: str):
        with store.connect() as db:
            if not db.execute('DELETE FROM destinations WHERE id=?', (destination_id,)).rowcount:
                raise HTTPException(404, '落地地址不存在')
        return {'ok': True}

    @app.get('/api/export', dependencies=secured)
    def export():
        # Portable inventory deliberately excludes SSH credentials and login/session data.
        return {'format': 'aurora-lite-inventory-v2', 'servers': servers(), 'rules': rules(), 'destinations': destinations()}

    app.mount('/static', StaticFiles(directory=static), name='static')

    @app.get('/')
    def index():
        return FileResponse(static / 'index.html', headers={'Cache-Control': 'no-cache'})

    return app
