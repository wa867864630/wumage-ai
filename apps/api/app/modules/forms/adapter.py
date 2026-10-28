"""Local form type adapter; publication continues to use ConfigStore's transaction and history."""
import hashlib
import json
import os
from pathlib import Path

from app.errors import APIError
from app.modules.config.store import Conflict, digest
from app.modules.forms.schema import FormConfig, default_form, validate_values

TYPE_ID = 'platform.form'
TYPE_VERSION = '1.0.0'
ROOT = Path(__file__).resolve().parents[5]


def enabled():
    return os.environ.get('FORM_APPLICATIONS_ENABLED', '1') == '1'


def form_type():
    manifest = json.loads((ROOT / 'packages/contracts/application-types/v1/examples/form-type.json').read_text(encoding='utf-8'))
    if (manifest['type_id'], manifest['type_version'], manifest['runtime_adapter_id']) != (TYPE_ID, TYPE_VERSION, 'form-records-v1'):
        raise APIError(503, 'TYPE_BUNDLE_INVALID', '表单类型清单与已安装实现不一致。')
    paths = ('apps/api/app/modules/forms/schema.py', 'apps/api/app/modules/forms/adapter.py',
        'apps/api/app/modules/forms/runtime.py', 'apps/api/app/modules/forms/routes.py', 'apps/api/app/modules/forms/drafts.py',
        'apps/web/src/FormEditor.tsx', 'apps/web/src/FormRuntime.tsx',
        'packages/contracts/application-types/v1/form-config.schema.json')
    implementation = {name: hashlib.sha256((ROOT / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for name in paths}
    return {**manifest, 'type_digest': digest({'algorithm': 'reviewed-source-lf-v1', 'manifest': manifest, 'implementation': implementation}), 'installed': True}


class FormAdapter:
    model = FormConfig
    defaults = staticmethod(default_form)
    validate_values = staticmethod(validate_values)

    @staticmethod
    def validate_publish(content):
        FormConfig.model_validate(content)

    @staticmethod
    def runtime_binding(application_id, actor_id):
        installed = form_type()
        if not enabled():
            raise Conflict('表单应用尚未启用。')
        return {'application_id': application_id, 'type_id': TYPE_ID, 'type_version': TYPE_VERSION,
            'type_digest': installed['type_digest'], 'capability_contract': 'form.records@1'}

    @staticmethod
    def validate_runtime(application_id, binding):
        installed = form_type()
        if not isinstance(binding, dict) or any(binding.get(key) != value for key, value in {
            'application_id': application_id, 'type_id': TYPE_ID, 'type_version': TYPE_VERSION,
            'type_digest': installed['type_digest'], 'capability_contract': 'form.records@1'}.items()):
            raise Conflict('表单发布的类型或运行能力绑定不可用。')
