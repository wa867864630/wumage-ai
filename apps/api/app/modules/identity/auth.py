"""Server-side identities. Demo mode is explicit; OIDC never falls back to demo."""
import secrets
from functools import lru_cache
from urllib.parse import urlparse

from fastapi import Request
from pydantic import BaseModel, Field

from app.errors import APIError
from app.settings import admin_token, read_local_config


class Principal(BaseModel):
    subject: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    roles: list[str]
    student_id: str | None = None
    mode: str = 'demo'


def auth_options():
    options = read_local_config().get('auth', {})
    if options.get('mode', 'demo') not in ('demo', 'oidc'):
        raise APIError(503, 'AUTH_CONFIGURATION', 'auth.mode 必须是 demo 或 oidc。')
    return options


@lru_cache(maxsize=4)
def jwks_client(url):
    import jwt
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=300, timeout=3)


def verify_oidc(token, options):
    issuer, audience, jwks = (options.get(key, '') for key in ('issuer', 'audience', 'jwks_url'))
    if (not all(isinstance(value, str) and value for value in (issuer, audience, jwks)) or
            any(urlparse(value).scheme != 'https' or not urlparse(value).hostname or
                urlparse(value).username or urlparse(value).password for value in (issuer, jwks))):
        raise APIError(503, 'AUTH_CONFIGURATION', '请配置 HTTPS issuer、jwks_url 与 audience。')
    try:
        import jwt
    except ImportError as exc:
        raise APIError(503, 'AUTH_CONFIGURATION', '请安装 PyJWT[crypto] 以启用 OIDC。') from exc
    try:
        key = jwks_client(jwks).get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=['RS256'], audience=audience, issuer=issuer,
                            options={'require': ['exp', 'iss', 'aud', 'sub']})
        roles = claims.get(options.get('roles_claim', 'roles'), [])
        tenant = claims.get(options.get('tenant_claim', 'tenant_id'))
        student = claims.get(options.get('student_claim', 'student_id'))
        if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
            raise ValueError('Invalid roles')
        if not isinstance(tenant, str) or not tenant or (student is not None and not isinstance(student, str)):
            raise ValueError('Invalid identity scope')
        return Principal(subject=claims['sub'], tenant_id=tenant, roles=roles, student_id=student, mode='oidc')
    except jwt.PyJWKClientConnectionError as exc:
        raise APIError(503, 'IDENTITY_UNAVAILABLE', '身份服务暂时不可用，请稍后重试。') from exc
    except Exception as exc:
        raise APIError(401, 'INVALID_TOKEN', '令牌无效、已过期或不包含有效身份范围。') from exc


def current_user(request: Request):
    options = auth_options()
    authorization = request.headers.get('authorization', '')
    if len(authorization) > 16384:
        raise APIError(401, 'INVALID_TOKEN', '访问令牌长度无效。')
    if options.get('mode', 'demo') == 'oidc':
        if not authorization.startswith('Bearer '):
            raise APIError(401, 'AUTH_REQUIRED', '请使用校园身份令牌登录。')
        principal = verify_oidc(authorization[7:], options)
    else:
        # Demo student never accepts an arbitrary identity from a browser parameter.
        configured = admin_token() if authorization else ''
        if authorization and len(configured) >= 24 and secrets.compare_digest(
                authorization.encode(), ('Bearer ' + configured).encode()):
            principal = Principal(subject='local-admin', tenant_id='local-demo',
                                  roles=['config_admin'], student_id='local-demo-student')
        elif authorization.startswith('Bearer demo_'):
            from app.modules.identity.demo import resolve_session
            principal = resolve_session(authorization[7:])
        elif authorization:
            raise APIError(401, 'INVALID_TOKEN', '本地令牌无效。')
        else:
            raise APIError(401, 'AUTH_REQUIRED', '请先登录校园工作台。')
    request.state.principal = principal
    return principal


def require_schedule(request: Request):
    principal = current_user(request)
    if not set(principal.roles) & {'student', 'teacher', 'config_admin'}:
        raise APIError(403, 'SCHEDULE_FORBIDDEN', '当前角色没有课表查询权限。')
    if not principal.student_id:
        raise APIError(409, 'IDENTITY_NOT_LINKED', '校园身份尚未关联教务账号，请联系管理员。')
    return principal
