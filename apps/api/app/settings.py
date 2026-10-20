"""Local TOML configuration; secrets never appear in error messages."""
import os
from functools import lru_cache
from pathlib import Path
import tomllib

CONFIG_PATH = Path(__file__).resolve().parents[3] / 'config.local.toml'


class SettingsError(Exception):
    pass


@lru_cache
def read_local_config():
    if not CONFIG_PATH.exists():
        return {}
    try:
        with CONFIG_PATH.open('rb') as source:
            result = tomllib.load(source)
        if any(not isinstance(result.get(section, {}), dict)
               for section in ('database', 'local_admin', 'auth', 'sis', 'redis', 'telemetry', 'deployment')):
            raise SettingsError('配置中的 database、local_admin、auth、sis、redis 和 telemetry 必须是 TOML 配置节。')
        return result
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SettingsError('config.local.toml 无法读取或格式错误，请检查文件。') from exc


def database_is_configured():
    if 'DATABASE_URL' in os.environ:
        return bool(os.environ['DATABASE_URL'])
    return bool(read_local_config().get('database'))


def database_options():
    config = read_local_config().get('database', {})
    required = ('host', 'name', 'user', 'password')
    if any(not isinstance(config.get(key), str) or not config[key].strip() for key in required):
        raise SettingsError('请在 config.local.toml 的 database 节填写 host、name、user 和 password。')
    port = config.get('port', 5432)
    if type(port) is not int or not 1 <= port <= 65535:
        raise SettingsError('数据库 port 必须是 1～65535 的整数。')
    sslmode = config.get('sslmode', 'prefer')
    if sslmode not in ('disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'):
        raise SettingsError('数据库 sslmode 配置无效。')
    backend = config.get('backend', 'postgresql')
    if backend not in ('postgresql', 'opengauss'):
        raise SettingsError('数据库 backend 必须为 postgresql 或 opengauss。')
    return {**config, 'port': port, 'sslmode': sslmode, 'backend': backend}


def admin_token():
    value = os.environ.get('LOCAL_ADMIN_TOKEN')
    if value is None:
        value = read_local_config().get('local_admin', {}).get('token', '')
    if not isinstance(value, str):
        raise SettingsError('本地管理 token 必须是字符串。')
    return value
