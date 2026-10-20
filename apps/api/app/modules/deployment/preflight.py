"""Fixed, read-only remote probe and conservative deployment readiness assessment."""
import json
import re
import shlex

from app.errors import APIError

# This program never reads configuration contents, writes files or invokes sudo.
PROBE = r'''
import importlib.util, json, os, re, shutil, stat, subprocess, sys
release = sys.argv[1]
if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', release):
    raise SystemExit(2)
root = '/opt/campus-ai'
def kind(path):
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return 'missing'
    except PermissionError:
        return 'unknown'
    if stat.S_ISLNK(info.st_mode): return 'link'
    if stat.S_ISDIR(info.st_mode): return 'directory'
    if stat.S_ISREG(info.st_mode): return 'file'
    return 'other'
paths = {p: kind(p) for p in ('/opt', root, root + '/releases', root + '/shared', root + '/current', root + '/releases/' + release)}
safe = all(paths[p] in ('missing', 'directory') for p in ('/opt', root, root + '/releases', root + '/shared')) and paths['/opt'] == 'directory'
anchor = root + '/releases'
while not os.path.exists(anchor): anchor = os.path.dirname(anchor)
current_safe = False
if safe and paths[root + '/current'] == 'link':
    target = os.path.realpath(root + '/current')
    current_safe = (os.path.dirname(target) == root + '/releases' and kind(target) == 'directory'
                    and bool(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', os.path.basename(target))))
    if current_safe:
        info = os.lstat(target)
        current_safe = info.st_uid in (0, os.getuid()) and not (info.st_mode & 0o022)
permissions_safe = safe
for p in ('/opt', root, root + '/releases', root + '/shared'):
    if paths[p] == 'directory':
        info = os.lstat(p)
        permissions_safe = permissions_safe and info.st_uid in (0, os.getuid()) and not (info.st_mode & 0o022)
config = root + '/shared/config.local.toml'
config_ok = False
if safe and kind(config) == 'file':
    info = os.lstat(config)
    config_ok = info.st_uid in (0, os.getuid()) and not (info.st_mode & 0o077) and os.access(config, os.R_OK)
services = {}
for name in ('campus-api.service', 'campus-worker.service', 'nginx.service'):
    try:
        result = subprocess.run(['/usr/bin/systemctl', 'show', '--no-pager', '--property=LoadState,ActiveState', name], capture_output=True, timeout=1)
        values = dict(line.split('=', 1) for line in result.stdout.decode(errors='replace').splitlines() if '=' in line)
        services[name] = {'load': values.get('LoadState', 'unknown'), 'active': values.get('ActiveState', 'unknown')}
    except (OSError, subprocess.TimeoutExpired):
        services[name] = {'load': 'unknown', 'active': 'unknown'}
ports = set()
ports_known = True
for path in ('/proc/net/tcp', '/proc/net/tcp6'):
    try:
        with open(path) as source:
            next(source)
            for index, line in enumerate(source):
                if index >= 65536: raise ValueError()
                fields = line.split()
                if fields[3] == '0A': ports.add(int(fields[1].split(':')[1], 16))
    except (OSError, ValueError, IndexError, StopIteration):
        ports_known = False
print(json.dumps({'paths_safe': safe, 'permissions_safe': bool(permissions_safe),
    'root_exists': paths[root] != 'missing', 'release_exists': paths[root + '/releases/' + release] != 'missing',
    'current_exists': paths[root + '/current'] != 'missing', 'current_safe': current_safe,
    'root_writable': safe and os.access(anchor, os.W_OK | os.X_OK),
    'free_bytes': shutil.disk_usage(anchor).free, 'config_private_readable': config_ok,
    'python_supported': sys.version_info >= (3, 11),
    'venv_available': importlib.util.find_spec('venv') is not None and importlib.util.find_spec('ensurepip') is not None,
    'services': services, 'ports_known': ports_known, 'api_port_busy': 8000 in ports,
    'http_port_busy': 80 in ports, 'https_port_busy': 443 in ports}))
'''

BOOL_FIELDS = {'paths_safe', 'permissions_safe', 'root_exists', 'release_exists', 'current_exists', 'current_safe',
               'root_writable', 'config_private_readable', 'python_supported', 'venv_available', 'ports_known', 'api_port_busy',
               'http_port_busy', 'https_port_busy'}
SERVICES = ('campus-api.service', 'campus-worker.service', 'nginx.service')


def probe_command(release_id):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', release_id):
        raise ValueError('Invalid release ID')
    return 'python3 -c ' + shlex.quote(PROBE) + ' ' + shlex.quote(release_id)


def parse_probe(status, output):
    try:
        data = json.loads(output) if status == 0 else None
        if (not isinstance(data, dict) or set(data) != BOOL_FIELDS | {'free_bytes', 'services'}
                or any(type(data[key]) is not bool for key in BOOL_FIELDS)
                or type(data['free_bytes']) is not int or not 0 <= data['free_bytes'] <= 2**63 - 1
                or not isinstance(data['services'], dict) or set(data['services']) != set(SERVICES)):
            raise ValueError()
        for service in data['services'].values():
            if (not isinstance(service, dict) or set(service) != {'load', 'active'}
                    or service['load'] not in ('loaded', 'not-found', 'masked', 'error', 'bad-setting', 'merged', 'stub', 'unknown')
                    or service['active'] not in ('active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading', 'maintenance', 'refreshing', 'unknown')):
                raise ValueError()
        return data
    except (ValueError, TypeError, RecursionError):
        raise APIError(502, 'DEPLOYMENT_PROBE_FAILED', '服务器部署检查未返回完整有效结果；已停止生成计划。') from None


def assess_preflight(operation, facts, artifact):
    blockers = []
    warnings = []
    def block(code, message):
        blockers.append({'code': code, 'message': message})
    if facts is None:
        block('PROBE_UNAVAILABLE', '缺少服务器目录与服务检查结果，需先准备 Python 3 再重新检查。')
        return {'facts': None, 'required_free_bytes': None, 'blockers': blockers, 'warnings': warnings}
    required = max(1024**3, (artifact or {}).get('unpacked_bytes', 0) * 3)
    if not facts['paths_safe'] or not facts['permissions_safe']:
        block('UNSAFE_DIRECTORY', '部署目录存在链接、类型、所有者或组/其他用户可写权限风险。')
    if facts['release_exists']:
        block('RELEASE_EXISTS', '目标版本目录已存在；请使用新的版本号，不覆盖已有版本。')
    if facts['free_bytes'] < required:
        block('DISK_SPACE_LOW', '可用空间不足，至少需 1 GiB 且不少于产物展开大小的三倍。')
    if not facts['config_private_readable']:
        block('CONFIG_NOT_READY', '服务器 shared/config.local.toml 缺失、不可读或权限不私密；未读取其内容。')
    if not facts['venv_available']:
        block('VENV_UNAVAILABLE', '服务器缺少 Python venv/ensurepip 模块，需安装并重新检查。')
    if not facts['python_supported']:
        block('PYTHON_VERSION_UNSUPPORTED', '项目需要 Python 3.11 或更新版本；不能直接使用 Ubuntu 22.04 默认的 Python 3.10。')
    if not facts['root_writable']:
        block('DIRECTORY_NOT_WRITABLE', '当前账号无法写入现有安装父目录，需先完成独立授权的目录准备。')
    if not facts['ports_known']:
        block('PORTS_UNKNOWN', '无法完整读取监听端口，不能判断端口冲突。')
    services = facts['services']
    if any(value['load'] == 'unknown' or value['active'] == 'unknown' for value in services.values()):
        block('SERVICES_UNKNOWN', '无法完整读取 systemd 服务状态。')
    if operation == 'install':
        if facts['current_exists']:
            block('INSTALL_DIRECTORY_EXISTS', '首次安装发现 current 入口；需先核对已有部署，不能自动覆盖。')
        elif facts['root_exists']:
            warnings.append('安装目录已存在；执行前仍需核对未识别文件与目录归属。')
        if any(services[name]['load'] != 'not-found' for name in SERVICES[:2]):
            block('APPLICATION_SERVICE_EXISTS', '已有同名应用服务，首次安装不能覆盖。')
        if facts['api_port_busy']:
            block('API_PORT_CONFLICT', '8000 端口已占用，首次安装不能自动接管。')
    else:
        if not facts['current_safe']:
            block('CURRENT_RELEASE_INVALID', '更新需要 current 指向 releases 下的有效版本目录。')
        if any(services[name]['load'] != 'loaded' for name in SERVICES[:2]):
            block('APPLICATION_SERVICE_MISSING', '更新需要已有 API 和 Worker 服务。')
        if any(services[name]['active'] != 'active' for name in SERVICES[:2]):
            block('APPLICATION_SERVICE_UNHEALTHY', '现有 API 或 Worker 未运行，需先核对故障与恢复方案。')
        if facts['api_port_busy']:
            warnings.append('8000 端口已监听，尚未核对所属进程；执行前需确认属于现有应用。')
    if facts['http_port_busy'] or facts['https_port_busy']:
        block('WEB_PORT_REVIEW_REQUIRED', '80/443 端口已占用，需核对站点归属与 Nginx 配置，不能自动覆盖。')
    warnings.extend(['sudo 权限未验证，未执行 sudo 或提权命令。',
                     '未读取运行配置内容、验证 OIDC/数据库配置或比对服务配置；这些仍是执行前必检项。'])
    return {'facts': facts, 'required_free_bytes': required, 'blockers': blockers, 'warnings': warnings}
