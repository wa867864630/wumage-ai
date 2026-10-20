"""Local-only artifact validation, SSH preflight and review plans; no remote writes."""
import base64
import hashlib
import importlib.util
import ipaddress
import secrets
import socket
import time
from threading import BoundedSemaphore
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.errors import APIError
from app.modules.config.routes import admin
from app.modules.identity.auth import auth_options
from app.settings import read_local_config
from app.modules.deployment.plans import build_plan
from app.modules.deployment.artifacts import ArtifactError, local_artifact
from app.modules.deployment.preflight import parse_probe, probe_command

router = APIRouter(prefix='/api/v1/admin/deployment', tags=['本地部署检查'])
_slots = BoundedSemaphore(2)
ORIGINS = {'http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:8000', 'http://localhost:8000'}


def local_operator(request: Request, principal=Depends(admin)):
    options = read_local_config().get('deployment', {})
    host = request.client.host if request.client else ''
    if (options.get('enabled') is not True or auth_options().get('mode', 'demo') != 'demo'
            or host not in ('127.0.0.1', '::1') or request.url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or request.headers.get('origin', '') not in ORIGINS | {''}):
        raise APIError(403, 'DEPLOYMENT_DISABLED', '部署助手仅允许在显式启用的本地开发环境使用。')
    # Application publishing alone never grants remote server access.
    operators = options.get('operator_subjects', [])
    if not isinstance(operators, list) or principal.subject not in operators:
        raise APIError(403, 'DEPLOYMENT_FORBIDDEN', '当前管理员未被单独授权操作服务器。')
    return options


class Connection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    host: str = Field(min_length=1, max_length=45)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(pattern=r'^[a-z_][a-z0-9_-]{0,31}$')
    password: SecretStr = Field(default=SecretStr(''), max_length=1024)
    fingerprint: str = Field(default='', pattern=r'^(?:SHA256:[A-Za-z0-9+/]{43})?$')

    @field_validator('host')
    @classmethod
    def target_ip(cls, value):
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise ValueError('首期只接受明确的服务器 IPv4/IPv6 地址，不接受 URL 或域名') from None
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified or '%' in value:
            raise ValueError('不允许此类型的目标地址')
        return str(address)


class PlanRequest(Connection):
    operation: Literal['install', 'update']
    release_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,63}$')
    artifact_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


def read_command(transport, command):
    """No caller-controlled shell fragments; bounded output and wall time."""
    channel = transport.open_session(timeout=5)
    try:
        channel.settimeout(5)
        channel.set_combine_stderr(True)
        channel.exec_command(command)
        output = bytearray()
        deadline = time.monotonic() + 5
        while True:
            if time.monotonic() >= deadline:
                raise APIError(504, 'SSH_TIMEOUT', '服务器检查超时。')
            if channel.recv_ready():
                output.extend(channel.recv(1024))
                if len(output) > 8192:
                    raise APIError(502, 'SSH_OUTPUT_LIMIT', '服务器检查输出超出限制。')
            elif channel.exit_status_ready():
                return channel.recv_exit_status(), output.decode('utf-8', errors='replace')
            else:
                time.sleep(.02)
    finally:
        channel.close()


def inspect_server(command, options):
    if command.host not in options.get('allowed_hosts', []):
        raise APIError(403, 'SSH_TARGET_DENIED', '目标地址尚未加入本地 deployment.allowed_hosts 白名单。')
    try:
        import paramiko
    except ImportError as exc:
        raise APIError(503, 'SSH_DEPENDENCY_MISSING', '请先安装 requirements-deployment.txt 中的可选 SSH 依赖。') from exc
    if not _slots.acquire(blocking=False):
        raise APIError(429, 'SSH_BUSY', '已有服务器检查进行中，请稍后重试。')
    transport = None
    sock = None
    try:
        sock = socket.create_connection((command.host, command.port), timeout=5)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = 5
        transport.auth_timeout = 5
        transport.start_client(timeout=5)
        fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(
            transport.get_remote_server_key().asbytes()).digest()).decode().rstrip('=')
        known = options.get('host_fingerprints', {}).get(f'{command.host}:{command.port}')
        expected = known or command.fingerprint
        if not expected:
            return {'status': 'fingerprint_confirmation_required', 'fingerprint': fingerprint,
                    'authenticated': False, 'checks': [], 'plan': []}
        if not secrets.compare_digest(fingerprint, expected):
            raise APIError(409, 'SSH_HOST_KEY_CHANGED', '服务器指纹不匹配，已停止，未发送登录密码。')
        if not command.password.get_secret_value():
            raise APIError(422, 'SSH_PASSWORD_REQUIRED', '核对主机指纹后，请输入本次连接密码。')
        transport.auth_password(command.username, command.password.get_secret_value(), fallback=False)
        if not transport.is_authenticated():
            raise APIError(422, 'SSH_AUTH_FAILED', '服务器账号认证失败；此错误不代表校园登录失效。')
        code, text = read_command(transport, 'cat /etc/os-release')
        fields = dict(line.split('=', 1) for line in text.splitlines() if '=' in line) if code == 0 else {}
        supported = fields.get('ID', '').strip('"') == 'ubuntu' and fields.get('VERSION_ID', '').strip('"') in ('22.04', '24.04')
        checks = [{'name': 'ubuntu_supported', 'ok': supported}]
        for name, fixed in [('python3', 'command -v python3'), ('systemd', 'command -v systemctl'),
                            ('nginx', 'command -v nginx'), ('sudo', 'command -v sudo')]:
            status, _ = read_command(transport, fixed)
            checks.append({'name': name, 'ok': status == 0})
        deployment_facts = None
        if isinstance(command, PlanRequest) and supported and any(item['name'] == 'python3' and item['ok'] for item in checks):
            deployment_facts = parse_probe(*read_command(transport, probe_command(command.release_id)))
        return {'status': 'checked' if supported else 'unsupported_os', 'fingerprint': fingerprint,
                'authenticated': True, 'checks': checks,
                'deployment_facts': deployment_facts,
                'plan': ['可填写版本号与产物摘要，重新只读检查并生成部署审阅计划。'], 'execution_available': False}
    except APIError:
        raise
    except paramiko.AuthenticationException as exc:
        raise APIError(422, 'SSH_AUTH_FAILED', '服务器账号认证失败，请核对用户名与密码。') from exc
    except Exception as exc:
        raise APIError(502, 'SSH_CHECK_FAILED', 'SSH 连接或检查失败，请检查地址、端口、网络及服务器 SSH 配置。') from exc
    finally:
        if transport is not None:
            transport.close()
        if sock is not None:
            sock.close()
        _slots.release()


@router.get('/capabilities')
def capabilities(options=Depends(local_operator)):
    return {'preflight_available': importlib.util.find_spec('paramiko') is not None,
            'planning_available': True, 'execution_available': False, 'template': 'Ubuntu 22.04/24.04 + systemd + Nginx',
            'allowed_hosts': options.get('allowed_hosts', [])}


@router.post('/inspect')
def inspect(command: Connection, options=Depends(local_operator)):
    return inspect_server(command, options)


@router.post('/plan')
def plan(command: PlanRequest, options=Depends(local_operator)):
    # Always recheck the host. A browser-provided inspection is never trusted.
    try:
        artifact = local_artifact(command.artifact_sha256)
    except (ArtifactError, OSError) as exc:
        raise APIError(409, 'DEPLOYMENT_ARTIFACT_INVALID', '本地产物校验失败，请重新打包并核对摘要。') from exc
    if artifact is None:
        raise APIError(409, 'DEPLOYMENT_ARTIFACT_REQUIRED', '请先运行 scripts/package_release.py 生成本地产物，并填写输出的 SHA256。')
    if artifact['release_id'] != command.release_id:
        raise APIError(409, 'DEPLOYMENT_RELEASE_MISMATCH', '版本号与本地产物清单不一致。')
    return build_plan(command, inspect_server(command, options), artifact)
