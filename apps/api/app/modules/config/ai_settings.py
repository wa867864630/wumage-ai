"""Local operator-owned model settings, encrypted API key, never part of releases."""
import json
import os
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.errors import APIError
from app.settings import CONFIG_PATH

SETTINGS_PATH = CONFIG_PATH.with_name('config.ai-design.secret')
KEY_PATH = CONFIG_PATH.with_name('config.ai-key.secret')
_lock = RLock()


class ModelOptions(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool = False
    base_url: str = Field(default='', max_length=300)
    model: str = Field(default='', max_length=100, pattern=r'^[a-zA-Z0-9_./:-]*$')
    timeout_seconds: int = Field(default=60, ge=5, le=60)
    max_tokens: int = Field(default=4096, ge=128, le=8192)
    token_parameter: str = Field(default='max_tokens', pattern=r'^(max_tokens|max_completion_tokens)$')
    json_mode: bool = True
    calls_per_hour: int = Field(default=20, ge=1, le=100)

    @field_validator('base_url')
    @classmethod
    def validate_url(cls, value):
        value = value.strip().rstrip('/')
        if not value:
            return value
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise ValueError('模型 API 地址格式无效') from None
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.scheme not in ('https', 'http')
                or (parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'))
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError('远程 API 必须使用 HTTPS，不能包含账号、查询参数或片段；HTTP 仅允许本机模型')
        if parsed.path.endswith('/chat/completions'):
            raise ValueError('请填写 API 基础地址，例如 https://api.deepseek.com/v1，不含 /chat/completions')
        return value


class SaveModel(ModelOptions):
    expected_revision: int = Field(ge=0)
    api_key: SecretStr = Field(default=SecretStr(''), max_length=2048)
    clear_api_key: bool = False


def _read():
    if not SETTINGS_PATH.exists():
        return {**ModelOptions().model_dump(), 'revision': 0, 'key_cipher': ''}
    try:
        if SETTINGS_PATH.is_symlink():
            raise ValueError()
        data = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError()
        options = ModelOptions.model_validate({key: data[key] for key in ModelOptions.model_fields}, strict=True)
        if type(data['revision']) is not int or data['revision'] < 0 or not isinstance(data['key_cipher'], str):
            raise ValueError()
        return options.model_dump() | {'revision': data['revision'], 'key_cipher': data['key_cipher']}
    except (OSError, ValueError, KeyError):
        raise APIError(503, 'AI_SETTINGS_UNAVAILABLE', '模型配置文件无法读取或校验失败。') from None


def _fernet(create=False):
    from cryptography.fernet import Fernet
    if KEY_PATH.is_symlink():
        raise OSError('invalid key path')
    if not KEY_PATH.exists() and create:
        with KEY_PATH.open('xb') as output:
            os.chmod(KEY_PATH, 0o600)
            output.write(Fernet.generate_key())
    return Fernet(KEY_PATH.read_bytes())


def _public(data):
    return {**{key: data[key] for key in ModelOptions.model_fields},
            'revision': data['revision'], 'api_key_configured': bool(data['key_cipher'])}


def model_settings():
    with _lock:
        return _public(_read())


def save_model(command: SaveModel):
    with _lock:
        data = _read()
        if data['revision'] != command.expected_revision:
            raise APIError(409, 'AI_CONFIG_CONFLICT', '模型配置已变化，请重新加载后保存。')
        if command.clear_api_key and command.api_key.get_secret_value():
            raise APIError(422, 'AI_KEY_CONFLICT', '清除密钥和设置新密钥不能同时进行。')
        cipher = data['key_cipher'] if data['base_url'] == command.base_url else ''
        temporary = SETTINGS_PATH.with_name(SETTINGS_PATH.name + '.' + uuid4().hex + '.secret')
        try:
            if command.clear_api_key:
                cipher = ''
            if command.api_key.get_secret_value():
                secret = command.api_key.get_secret_value().strip()
                if not secret or any(ord(char) < 33 or ord(char) > 126 for char in secret):
                    raise APIError(422, 'AI_KEY_INVALID', 'API Key 包含无效字符。')
                cipher = _fernet(create=True).encrypt(secret.encode()).decode()
            if command.enabled and (not command.base_url or not command.model or not cipher):
                raise APIError(422, 'AI_CONFIG_INCOMPLETE', '启用模型需要 API 基础地址、模型名和密钥；更换 API 地址时请重新填写密钥。')
            result = {key: getattr(command, key) for key in ModelOptions.model_fields}
            result.update(revision=data['revision'] + 1, key_cipher=cipher)
            if SETTINGS_PATH.is_symlink():
                raise OSError('invalid settings path')
            with temporary.open('x', encoding='utf-8') as output:
                os.chmod(temporary, 0o600)
                json.dump(result, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, SETTINGS_PATH)
            return _public(result)
        except APIError:
            raise
        except (OSError, ValueError):
            raise APIError(503, 'AI_SETTINGS_WRITE_FAILED', '模型配置保存失败，请检查本地密钥文件和写入权限。') from None
        finally:
            if temporary.exists():
                temporary.unlink()


def active_model():
    from cryptography.fernet import InvalidToken
    with _lock:
        data = _read()
        if not data['enabled']:
            raise APIError(409, 'AI_DISABLED', '请先在应用管理的 AI 模型配置中保存并启用模型。')
        try:
            secret = _fernet().decrypt(data['key_cipher'].encode()).decode()
        except (OSError, ValueError, InvalidToken):
            raise APIError(503, 'AI_KEY_UNAVAILABLE', '模型密钥无法解密，请在配置页重新设置。') from None
        return _public(data), SecretStr(secret)
