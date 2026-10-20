"""Compiled-in application types. Catalog metadata never loads executable code."""
import json
from copy import deepcopy
from pathlib import Path

from app.errors import APIError
from app.modules.config.store import digest

CONTRACTS = Path(__file__).resolve().parents[5] / 'packages/contracts/application-types/v1'
TYPE_ID = 'campus.course-query'
TYPE_VERSION = '1.0.0'
BINDING_ID = '00000000-0000-4000-8000-000000000002'


def course_type():
    # Only this reviewed bundle is installed. Never resolve a client-supplied path.
    manifest = json.loads((CONTRACTS / 'examples/course-type.json').read_text(encoding='utf-8'))
    if (manifest['type_id'], manifest['type_version'], manifest['runtime_adapter_id']) != (
            TYPE_ID, TYPE_VERSION, 'course-query-v1'):
        raise APIError(503, 'TYPE_BUNDLE_INVALID', '课程类型清单与已安装实现不一致。')
    editors = {'display': 'course-columns-v1', 'questions': 'course-questions-v1'}
    if {item['id']: item['editor_id'] for item in manifest['editor_sections']} != editors or len(manifest['editor_sections']) != 2:
        raise APIError(503, 'TYPE_BUNDLE_INVALID', '课程配置编辑器注册不完整。')
    schemas = {name: json.loads((CONTRACTS / name).read_text(encoding='utf-8')) for name in (
        'type-manifest.schema.json', 'application-envelope.schema.json',
        'course-config.schema.json', 'course-application.schema.json')}
    return {**deepcopy(manifest), 'type_digest': digest({'manifest': manifest, 'schemas': schemas,
            'implementation_version': 'course-query-v1.0.0'}), 'installed': True}


def require_type(type_id, type_version, expected_digest=None):
    if (type_id, type_version) != (TYPE_ID, TYPE_VERSION):
        raise APIError(422, 'APPLICATION_TYPE_UNAVAILABLE', '应用类型或精确版本尚未安装。')
    result = course_type()
    if expected_digest and result['type_digest'] != expected_digest:
        raise APIError(409, 'APPLICATION_TYPE_CHANGED', '应用类型包已变化，请重新校验。')
    return result
