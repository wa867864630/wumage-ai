"""Compiled-in application types. Catalog metadata never loads executable code."""
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable

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


@dataclass(frozen=True)
class TypeBundle:
    type_id: str
    type_version: str
    load: Callable[[], dict]


class ExactTypeRegistry:
    """Only reviewed, compiled providers; no latest-version fallback or dynamic paths."""

    def __init__(self, bundles):
        providers = {}
        for bundle in bundles:
            key = (bundle.type_id, bundle.type_version)
            if key in providers:
                raise APIError(503, 'TYPE_REGISTRY_DUPLICATE', '同一类型精确版本不能重复注册。')
            providers[key] = bundle
        self._providers = MappingProxyType(providers)

    def require(self, type_id, type_version, expected_digest=None):
        bundle = self._providers.get((type_id, type_version))
        if bundle is None:
            raise APIError(422, 'APPLICATION_TYPE_UNAVAILABLE', '应用类型或精确版本尚未安装。')
        result = deepcopy(bundle.load())
        if (result.get('type_id'), result.get('type_version')) != (type_id, type_version) or result.get('installed') is not True:
            raise APIError(503, 'TYPE_BUNDLE_INVALID', '类型提供器与注册的精确版本不一致。')
        fingerprint = result.get('type_digest')
        if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(char not in '0123456789abcdef' for char in fingerprint):
            raise APIError(503, 'TYPE_BUNDLE_INVALID', '类型提供器缺少有效实现摘要。')
        if expected_digest and fingerprint != expected_digest:
            raise APIError(409, 'APPLICATION_TYPE_CHANGED', '应用类型包已变化，不能静默执行旧绑定。')
        return result


def _form_bundle():
    from app.modules.forms.adapter import form_type
    return form_type()


def _approval_bundle():
    from app.modules.approvals.adapter import approval_type
    return approval_type()


def _unified_form():
    from app.modules.unified_editor.runtime import unified_type
    return unified_type('platform.form')


def _unified_approval():
    from app.modules.unified_editor.runtime import unified_type
    return unified_type('platform.leave-approval')


_registry = ExactTypeRegistry((TypeBundle(TYPE_ID, TYPE_VERSION, course_type),
    TypeBundle('platform.form', '1.0.0', _form_bundle), TypeBundle('platform.leave-approval', '1.0.0', _approval_bundle),
    TypeBundle('platform.form', '1.1.0', _unified_form), TypeBundle('platform.leave-approval', '1.1.0', _unified_approval)))


def require_type(type_id, type_version, expected_digest=None):
    return _registry.require(type_id, type_version, expected_digest)
