"""Standalone stdlib helper executed on Debian/Ubuntu over authenticated SSH.

Only owns /opt/aurora-lite, /etc/aurora-lite, aurora-lite-*.service and AL* chains.
All external commands use argument lists, never user-supplied shell commands.
"""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request

ROOT = Path('/opt/aurora-lite')
CONFIG = Path('/etc/aurora-lite')
UNITS = Path('/etc/systemd/system')
GOST_VERSION = '3.3.0'
REPORT_PROGRESS = False


def progress(message):
    if REPORT_PROGRESS:
        print(json.dumps({'progress': str(message)}, ensure_ascii=True), flush=True)


def run(args, check=True, timeout=60):
    command = [str(a) for a in args]
    env = {**os.environ, 'LC_ALL': 'C', 'DEBIAN_FRONTEND': 'noninteractive'}
    if REPORT_PROGRESS:
        progress('$ ' + ' '.join(command))
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors='replace', env=env) as process:
            output = []

            def read_output():
                for line in process.stdout:
                    output.append(line)
                    progress(line.rstrip())

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise
            finally:
                reader.join(timeout=5)
            result = subprocess.CompletedProcess(command, process.returncode, ''.join(output), '')
    else:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} 执行失败: {(result.stderr or result.stdout)[-3000:]}")
    return result


def write_file(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(content, encoding='utf-8')
    temp.chmod(mode)
    temp.replace(path)


def validate_rule(rule):
    if not re.fullmatch('[a-f0-9]{12}', rule['id']):
        raise ValueError('Invalid rule ID')
    if rule['method'] not in ('iptables', 'gost') or rule['protocol'] not in ('tcp', 'udp', 'both'):
        raise ValueError('Unsupported forwarding method/protocol')
    listen = ipaddress.ip_address(rule['listen_ip'])
    for field in ('listen_port', 'target_port'):
        if type(rule[field]) is not int or not 1 <= rule[field] <= 65535:
            raise ValueError('Invalid port')
    host = rule['target_host']
    try:
        target = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', host):
            raise ValueError('Invalid destination') from None
        target = None
    if rule['method'] == 'iptables' and (target is None or target.version != listen.version):
        raise ValueError('iptables requires same-family IP addresses')
    return rule


def protocols(rule):
    return ['tcp', 'udp'] if rule['protocol'] == 'both' else [rule['protocol']]


def address(host, port):
    return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'


def gost_config(rule):
    validate_rule(rule)
    return {
        'services': [
            {'name': f"{rule['id']}-{p}",
             'addr': address(rule['listen_ip'], rule['listen_port']),
             'handler': {'type': p}, 'listener': {'type': p},
             'forwarder': {'nodes': [{'name': 'target',
                                     'addr': address(rule['target_host'], rule['target_port'])}]}}
            for p in protocols(rule)],
        'metrics': {'addr': f"unix:///run/aurora-lite-{rule['id']}/metrics.sock", 'path': '/metrics'},
        'log': {'level': 'info'},
    }


def iptables_plan(rule):
    """Return private chains plus narrowly scoped parent jumps, for both IP families."""
    validate_rule(rule)
    tool = 'ip6tables' if ':' in rule['listen_ip'] else 'iptables'
    rid = rule['id']
    nat, masq, forward = f'AL{rid}N', f'AL{rid}M', f'AL{rid}F'
    destination = [] if ipaddress.ip_address(rule['listen_ip']).is_unspecified else ['-d', rule['listen_ip']]
    original = [] if not destination else ['--ctorigdst', rule['listen_ip']]
    chains = [('nat', nat), ('nat', masq), ('filter', forward)]
    # DNAT port translation requires a protocol on the target rule itself;
    # the protocol matched by the parent jump is not inherited by iptables.
    body = [
        ('nat', nat, ['-p', p, '-j', 'DNAT', '--to-destination', address(rule['target_host'], rule['target_port'])])
        for p in protocols(rule)
    ] + [
        ('nat', masq, ['-j', 'MASQUERADE']),
        ('filter', forward, ['-m', 'conntrack', '--ctdir', 'ORIGINAL', '-j', 'ACCEPT']),
        ('filter', forward, ['-m', 'conntrack', '--ctdir', 'REPLY', '-j', 'ACCEPT']),
    ]
    jumps = []
    for protocol in protocols(rule):
        ct = ['-p', protocol, '-m', 'conntrack', '--ctstate', 'DNAT',
              '--ctorigdstport', str(rule['listen_port']),
              '--ctreplsrc', rule['target_host'], '--ctreplsrcport', str(rule['target_port']), *original]
        jumps.extend([
            ('nat', 'PREROUTING', ['-p', protocol, *destination, '--dport', str(rule['listen_port']),
                                   '-m', 'addrtype', '--dst-type', 'LOCAL', '-j', nat]),
            ('nat', 'POSTROUTING', [*ct, '--ctdir', 'ORIGINAL', '-j', masq]),
            ('filter', 'FORWARD', [*ct, '-j', forward]),
        ])
    return tool, chains, body, jumps


def ipt_start(rule):
    tool, chains, body, jumps = iptables_plan(rule)
    # Runtime forwarding is a prerequisite; the enabled unit restores it after reboot.
    setting = 'net.ipv6.conf.all.forwarding=1' if tool == 'ip6tables' else 'net.ipv4.ip_forward=1'
    run(['sysctl', '-w', setting])
    for table, chain in chains:
        if run([tool, '-w', '10', '-t', table, '-S', chain], check=False).returncode:
            run([tool, '-w', '10', '-t', table, '-N', chain])
        run([tool, '-w', '10', '-t', table, '-F', chain])
    for table, chain, args in body:
        run([tool, '-w', '10', '-t', table, '-A', chain, *args])
    try:
        for table, parent, args in jumps:
            if run([tool, '-w', '10', '-t', table, '-C', parent, *args], check=False).returncode:
                run([tool, '-w', '10', '-t', table, '-I', parent, '1', *args])
    except Exception:
        ipt_stop(rule)
        raise


def ipt_stop(rule):
    tool, chains, body, jumps = iptables_plan(rule)
    errors = []
    for table, parent, args in reversed(jumps):
        while run([tool, '-w', '10', '-t', table, '-C', parent, *args], check=False).returncode == 0:
            result = run([tool, '-w', '10', '-t', table, '-D', parent, *args], check=False)
            if result.returncode:
                errors.append(result.stderr)
                break
    # Remove only connections with this rule's original port and translated endpoint.
    for protocol in protocols(rule):
        args = ['conntrack', '-D', '-f', 'ipv6' if tool == 'ip6tables' else 'ipv4',
                '--dst-nat', '-p', protocol, '--orig-port-dst', str(rule['listen_port']),
                '--reply-src', rule['target_host'], '--reply-port-src', str(rule['target_port'])]
        if not ipaddress.ip_address(rule['listen_ip']).is_unspecified:
            args += ['--orig-dst', rule['listen_ip']]
        result = run(args, check=False)
        if result.returncode not in (0, 1):
            errors.append(result.stderr)
    for table, chain in reversed(chains):
        if run([tool, '-w', '10', '-t', table, '-S', chain], check=False).returncode == 0:
            run([tool, '-w', '10', '-t', table, '-F', chain])
            result = run([tool, '-w', '10', '-t', table, '-X', chain], check=False)
            if result.returncode:
                errors.append(result.stderr)
    if errors:
        raise RuntimeError('规则清理不完整: ' + '\n'.join(errors))


def service_name(rule):
    validate_rule(rule)
    return f"aurora-lite-{rule['id']}.service"


def service_text(rule):
    name = service_name(rule)
    rid = rule['id']
    base = ('[Unit]\nDescription=Aurora Lite forwarding rule ' + rid +
            '\nWants=network-online.target\nAfter=network-online.target\n\n[Service]\n')
    if rule['method'] == 'iptables':
        base += (f'Type=oneshot\nRemainAfterExit=yes\n'
                 f'ExecStart=/usr/bin/python3 /opt/aurora-lite/agent.py start /etc/aurora-lite/rules/{rid}.json\n'
                 f'ExecStop=/usr/bin/python3 /opt/aurora-lite/agent.py stop /etc/aurora-lite/rules/{rid}.json\n')
    else:
        base += (f'Type=simple\nExecStart=/opt/aurora-lite/bin/gost -C /etc/aurora-lite/gost/{rid}.json\n'
                 f'Restart=on-failure\nRestartSec=3\nRuntimeDirectory=aurora-lite-{rid}\n'
                 'RuntimeDirectoryMode=0700\nNoNewPrivileges=yes\nProtectSystem=strict\n'
                 'ProtectHome=yes\nPrivateTmp=yes\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE\n')
    return base + 'TimeoutStartSec=60\nTimeoutStopSec=45\n\n[Install]\nWantedBy=multi-user.target\n'


def download_gost():
    machine = platform.machine().lower()
    arch = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(machine)
    if not arch:
        raise RuntimeError('GOST 自动安装目前支持 AMD64 / ARM64')
    filename = f'gost_{GOST_VERSION}_linux_{arch}.tar.gz'
    base = f'https://github.com/go-gost/gost/releases/download/v{GOST_VERSION}/'
    with tempfile.TemporaryDirectory(prefix='aurora-gost-') as directory:
        archive = Path(directory) / filename
        urllib.request.urlretrieve(base + filename, archive)
        with urllib.request.urlopen(base + 'checksums.txt', timeout=60) as response:
            checksums = response.read().decode()
        expected = next((line.split()[0] for line in checksums.splitlines()
                         if line.split() and line.split()[-1].lstrip('*') == filename), None)
        if not expected or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise RuntimeError('GOST 文件校验失败')
        with tarfile.open(archive, 'r:gz') as package:
            member = next((item for item in package if item.name in ('gost', './gost') and item.isfile()), None)
            if not member:
                raise RuntimeError('GOST 压缩包格式异常')
            destination = ROOT / 'bin' / 'gost'
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp = destination.with_suffix('.new')
            with package.extractfile(member) as source, open(temp, 'wb') as output:
                shutil.copyfileobj(source, output)
            temp.chmod(0o755)
            temp.replace(destination)


def prepare():
    if not Path('/etc/debian_version').exists() or not shutil.which('systemctl'):
        raise RuntimeError('仅支持使用 systemd 的 Debian / Ubuntu')
    run(['apt-get', 'update'], timeout=300)
    run(['apt-get', 'install', '-y', 'iptables', 'conntrack', 'curl', 'ca-certificates', 'iproute2'], timeout=300)
    progress('正在下载并校验 GOST ' + GOST_VERSION)
    download_gost()
    return {'message': f'iptables、conntrack 与 GOST {GOST_VERSION} 已安装', 'state': 'online'}


def parse_gost_metrics(text):
    totals = {'in': 0, 'out': 0}
    for line in text.splitlines():
        if line.startswith('#'):
            continue
        match = re.match(r'gost_service_transfer_(input|output)_bytes_total(?:\{[^}]*\})?\s+([0-9.eE+\-]+)', line)
        if match:
            key = 'in' if match[1] == 'input' else 'out'
            totals[key] += int(float(match[2]))
    return totals['in'], totals['out']


def collect(rule):
    name = service_name(rule)
    result = run(['systemctl', 'show', name, '--property=ActiveState,InvocationID'], check=False)
    properties = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    active = properties.get('ActiveState')
    epoch = properties.get('InvocationID', '')
    if active != 'active':
        return {'state': 'stopped' if active == 'inactive' else 'error', 'in': 0, 'out': 0,
                'epoch': epoch, 'error': f'服务状态：{active or "unknown"}'}
    if rule['method'] == 'gost':
        metrics = run(['curl', '--fail', '--silent', '--show-error', '--max-time', '5',
                       '--unix-socket', f"/run/aurora-lite-{rule['id']}/metrics.sock",
                       'http://localhost/metrics']).stdout
        incoming, outgoing = parse_gost_metrics(metrics)
    else:
        tool, _, _, _ = iptables_plan(rule)
        chain = f"AL{rule['id']}F"
        counters = run([tool, '-w', '10', '-t', 'filter', '-L', chain, '-n', '-v', '-x']).stdout
        counts = [int(line.split()[1]) for line in counters.splitlines()
                  if len(line.split()) > 2 and line.split()[0].isdigit() and line.split()[1].isdigit()]
        if len(counts) != 2:
            raise RuntimeError('流量计数规则缺失，请重新下发规则')
        incoming, outgoing = counts
    return {'state': 'running', 'in': incoming, 'out': outgoing, 'epoch': epoch}


def host_identity():
    try:
        identity = Path('/etc/machine-id').read_bytes().strip()
        return hashlib.sha256(identity).hexdigest() if identity else None
    except OSError:
        return None


TCP_PROBE_CODE = '''import json,socket,sys,time
start=time.monotonic()
try:
    with socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=3) as connection:
        result={'status':'passed','detail':'TCP 连接成功','peer':connection.getpeername()[0]}
except OSError as error:
    result={'status':'failed','detail':str(error)}
result['latency_ms']=round((time.monotonic()-start)*1000)
print(json.dumps(result))
'''


def probe_tcp(host, port):
    """Bound DNS + all connect attempts; connect without sending application data."""
    command = ([sys.executable, '--internal-tcp-probe', host, str(port)]
               if getattr(sys, 'frozen', False)
               else [sys.executable, '-c', TCP_PROBE_CODE, host, str(port)])
    try:
        result = subprocess.run(command,
                                capture_output=True, text=True, timeout=7,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode:
            return {'status': 'unknown', 'detail': 'TCP 检查进程未正常完成'}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {'status': 'failed', 'detail': '解析或 TCP 连接超时（7 秒）'}


def diagnose(rule):
    """Read-only observations on the relay; does not apply, stop or repair a rule."""
    validate_rule(rule)
    checks = []

    def add(key, title, status, detail):
        checks.append({'key': key, 'title': title, 'status': status, 'detail': detail})

    unit = run(['systemctl', 'show', service_name(rule),
                '--property=ActiveState,MainPID,NeedDaemonReload'], check=False)
    properties = dict(line.split('=', 1) for line in unit.stdout.splitlines() if '=' in line)
    active = properties.get('ActiveState', 'unknown')
    observed = 'running' if active == 'active' else 'stopped' if active == 'inactive' else 'error'
    add('service', '中转服务', 'passed' if observed == 'running' else 'failed',
        '系统服务正在运行' if observed == 'running' else f'系统服务未运行（{active}）')
    result = {'state': observed, 'checks': checks, 'host_identity': host_identity()}
    if observed != 'running':
        return result

    try:
        saved = json.loads((CONFIG / 'rules' / f"{rule['id']}.json").read_text())
        fields = ('id', 'method', 'protocol', 'listen_ip', 'listen_port', 'target_host', 'target_port')
        matched = all(saved.get(key) == rule[key] for key in fields)
        matched = matched and (UNITS / service_name(rule)).read_text() == service_text(rule)
        matched = matched and properties.get('NeedDaemonReload') != 'yes'
        if rule['method'] == 'gost':
            matched = matched and json.loads((CONFIG / 'gost' / f"{rule['id']}.json").read_text()) == gost_config(rule)
        add('config', '下发配置', 'passed' if matched else 'failed',
            '远程配置与当前规则一致' if matched else '远程配置与面板不一致，请重新下发')
    except (OSError, ValueError):
        add('config', '下发配置', 'failed', '远程配置缺失或无法解析，请重新下发')

    if rule['method'] == 'iptables':
        tool, chains, body, jumps = iptables_plan(rule)
        missing = 0
        for table, chain, args in body + jumps:
            if run([tool, '-w', '2', '-t', table, '-C', chain, *args], check=False, timeout=5).returncode:
                missing += 1
        setting = '/proc/sys/net/ipv6/conf/all/forwarding' if tool == 'ip6tables' else '/proc/sys/net/ipv4/ip_forward'
        forwarding = Path(setting).read_text().strip() == '1'
        add('kernel', '内核转发规则', 'passed' if not missing and forwarding else 'failed',
            'DNAT、MASQUERADE、FORWARD 与 IP 转发均已就绪' if not missing and forwarding
            else f'缺失 {missing} 项规则；系统 IP 转发{"已开启" if forwarding else "未开启"}')
    elif shutil.which('ss'):
        listeners = run(['ss', '-H', '-lntup']).stdout.splitlines()
        pid = properties.get('MainPID', '0')
        for protocol in protocols(rule):
            found = False
            for line in listeners:
                parts = line.split()
                if len(parts) < 5 or parts[0] != protocol or not re.search(r'pid=' + re.escape(pid) + r'[,)]', line):
                    continue
                bind_host, _, bind_port = parts[4].rpartition(':')
                if bind_port == str(rule['listen_port']) and bind_host.strip('[]') in (rule['listen_ip'], '*'):
                    found = pid != '0'
            add('listener_' + protocol, protocol.upper() + ' 监听', 'passed' if found else 'failed',
                'GOST 进程已绑定指定端口' if found else 'GOST 进程未监听指定地址和端口')
    else:
        add('listener', '端口监听', 'unknown', '缺少 ss 工具，请在服务器页安装中转组件')

    try:
        sample = collect(rule)
        if sample['state'] == 'running':
            result.update({key: sample[key] for key in ('in', 'out', 'epoch')})
            add('metrics', '运行指标', 'passed', '流量计数接口正常')
        else:
            result['state'] = sample['state']
            add('metrics', '运行指标', 'failed', '检测过程中服务停止了')
    except Exception as error:
        add('metrics', '运行指标', 'failed', str(error)[-500:])

    if rule['protocol'] in ('tcp', 'both'):
        checks.append({'key': 'target_tcp', 'title': '目标 TCP（中转机视角）',
                       **probe_tcp(rule['target_host'], rule['target_port'])})
    if rule['protocol'] in ('udp', 'both'):
        add('udp', 'UDP 实际转发', 'unknown', 'UDP 无通用握手；需实际业务请求和响应确认，端口就绪不代表业务已通')
    return result


def finish_diagnosis(server, rule, result):
    """Add a controller-side entry probe without claiming an end-to-end payload test."""
    remote_identity = result.pop('host_identity', None)
    if rule['protocol'] in ('tcp', 'both') and result['state'] == 'running':
        local_id = host_identity()
        same_host = bool(local_id and remote_identity == local_id)
        try:
            same_host = same_host or ipaddress.ip_address(server['host']).is_loopback
        except ValueError:
            same_host = same_host or server['host'].lower() == 'localhost'
        if rule['method'] == 'iptables' and same_host:
            entry = {'status': 'unknown', 'detail': '主控与中转机同机，iptables 入站转发需从另一台设备验证'}
        else:
            host = server['host'] if ipaddress.ip_address(rule['listen_ip']).is_unspecified else rule['listen_ip']
            entry = probe_tcp(host, rule['listen_port'])
        result['checks'].append({'key': 'entry_tcp', 'title': '入口 TCP（主控视角）', **entry})
    if result['state'] == 'stopped':
        status, summary = 'inactive', '转发服务未运行'
    elif any(item['status'] == 'failed' for item in result['checks']):
        status, summary = 'failed', '检测发现异常'
    elif any(item['status'] == 'unknown' for item in result['checks']):
        status, summary = 'partial', '已完成检查，部分路径待验证'
    else:
        status, summary = 'passed', '服务、配置及 TCP 连通检查通过'
    result.update(status=status, summary=summary, checked_at=time.time(),
                  note='连通检查仅建立 TCP 连接，不验证应用响应或实际转发的数据内容。'
                  if rule['protocol'] in ('tcp', 'both') else 'UDP 的最终连通性需通过实际业务请求和响应确认。')
    return result


def saved_rule(rule):
    path = CONFIG / 'rules' / f"{rule['id']}.json"
    if not path.exists():
        return rule
    saved = validate_rule(json.loads(path.read_text()))
    if saved['id'] != rule['id']:
        raise RuntimeError('远程规则 ID 不一致，已停止操作')
    return saved


def same_forwarding(first, second):
    return all(first.get(key) == second.get(key) for key in
               ('method', 'protocol', 'listen_ip', 'listen_port', 'target_host', 'target_port'))


def stop(rule, remove=False):
    # The on-disk rule describes what was actually deployed, including after an interrupted edit.
    rule = saved_rule(rule)
    name = service_name(rule)
    unit_path = UNITS / name
    if unit_path.exists():
        run(['systemctl', 'disable', '--now', name])
    # Cleanup is also attempted after an interrupted/partially applied operation.
    if rule['method'] == 'iptables':
        ipt_stop(rule)
    if remove:
        for path in (unit_path, CONFIG / 'rules' / f"{rule['id']}.json",
                     CONFIG / 'gost' / f"{rule['id']}.json"):
            path.unlink(missing_ok=True)
        run(['systemctl', 'daemon-reload'])
    return {'message': '规则已移除' if remove else '规则已停止', 'state': 'stopped'}


def apply(rule, source):
    name = service_name(rule)
    for binary in (['iptables', 'ip6tables', 'conntrack', 'sysctl'] if rule['method'] == 'iptables' else ['curl']):
        if not shutil.which(binary):
            raise RuntimeError(f'缺少 {binary}，请先执行“安装中转组件”')
    if rule['method'] == 'gost' and not (ROOT / 'bin' / 'gost').exists():
        raise RuntimeError('缺少 GOST，请先执行“安装中转组件”')
    if (CONFIG / 'rules' / f"{rule['id']}.json").exists():
        progress('正在停止并清理上一版转发配置…')
        stop(rule, remove=True)
    elif (UNITS / name).exists():
        raise RuntimeError('存在服务但缺少对应规则配置，请先恢复远程规则文件再更新')
    progress('正在写入并启动新转发配置…')
    write_file(ROOT / 'agent.py', source, 0o700)
    write_file(CONFIG / 'rules' / f"{rule['id']}.json", json.dumps(rule))
    if rule['method'] == 'gost':
        write_file(CONFIG / 'gost' / f"{rule['id']}.json", json.dumps(gost_config(rule)))
    write_file(UNITS / name, service_text(rule), 0o644)
    run(['systemctl', 'daemon-reload'])
    try:
        run(['systemctl', 'enable', name])
        run(['systemctl', 'restart', name])
        # Type=simple can report started before configuration or port binding fails.
        for attempt in range(10):
            time.sleep(0.3)
            try:
                result = collect(rule)
                if result['state'] == 'running':
                    return {**result, 'message': '规则已下发，系统服务及计数接口正常'}
            except RuntimeError:
                if attempt == 9:
                    raise
        raise RuntimeError('服务未正常启动，请检查端口占用及任务日志')
    except Exception as error:
        log = run(['journalctl', '-u', name, '-n', '25', '--no-pager'], check=False).stdout
        try:
            stop(rule)
        except Exception as cleanup_error:
            log += '\n清理失败: ' + str(cleanup_error)
        raise RuntimeError(str(error) + '\n' + log[-5000:]) from None


def dispatch(request):
    global REPORT_PROGRESS
    REPORT_PROGRESS = bool(request.get('progress'))
    if platform.system() != 'Linux' or os.geteuid() != 0:
        raise RuntimeError('中转操作需要 Linux root 或免密 sudo 权限')
    action = request['action']
    if action == 'check':
        result = {'state': 'online', 'message': 'SSH 与管理员权限正常',
                  'hostname': platform.node(), 'systemd': shutil.which('systemctl') is not None,
                  'iptables': shutil.which('iptables') is not None,
                  'conntrack': shutil.which('conntrack') is not None,
                  'gost': (ROOT / 'bin' / 'gost').exists()}
    elif action == 'prepare':
        result = prepare()
    else:
        rule = validate_rule(request['rule'])
        if action == 'apply':
            result = apply(rule, request['agent_source'])
        elif action in ('stop', 'remove'):
            result = stop(rule, remove=action == 'remove')
        elif action == 'collect':
            deployed = saved_rule(rule)
            result = collect(deployed)
            result['counters_valid'] = result['state'] == 'running'
            if not same_forwarding(rule, deployed):
                result.update(state='error', error='新配置尚未成功下发，远程仍保留上一版配置，请重新下发')
        elif action == 'diagnose':
            result = diagnose(rule)
        elif action == 'logs':
            result = {'message': run(['journalctl', '-u', service_name(rule), '-n', '80', '--no-pager'], check=False).stdout[-16000:]}
        else:
            raise ValueError('Unsupported action')
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == '__main__' and len(sys.argv) > 1:
    current = validate_rule(json.loads(Path(sys.argv[2]).read_text()))
    if sys.argv[1] == 'start':
        ipt_start(current)
    elif sys.argv[1] == 'stop':
        ipt_stop(current)
    else:
        raise ValueError('Unsupported service action')
