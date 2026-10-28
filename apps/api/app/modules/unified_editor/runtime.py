"""Version 1.1 UI integration; records and transitions reuse the reviewed v1 executors."""
import hashlib
from pathlib import Path
from app.errors import APIError
from app.modules.config.store import ConfigStore, digest, Conflict
from app.modules.config.application_store import ApplicationStore
from app.modules.forms.adapter import FormAdapter, form_type, enabled as form_enabled
from app.modules.approvals.adapter import ApprovalAdapter, approval_type, enabled as approval_enabled
from app.modules.forms.runtime import FormRecords, form_store as legacy_store
from app.modules.approvals.runtime import ApprovalRecords

VERSION = '1.1.0'
ROOT = Path(__file__).resolve().parents[5]


def unified_type(type_id):
    original = form_type() if type_id == 'platform.form' else approval_type() if type_id == 'platform.leave-approval' else None
    if original is None:
        raise APIError(422, 'APPLICATION_TYPE_UNAVAILABLE', '该类型没有统一编辑器版本。')
    # The versioned runtime is immutable; shared UI/routing belongs to the platform
    # editor protocol, so a platform UI fix does not invalidate application releases.
    paths = ('apps/api/app/modules/unified_editor/runtime.py',)
    sources = {name: hashlib.sha256((ROOT / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for name in paths}
    return {**original, 'type_version': VERSION, 'type_digest': digest({'legacy_dependency': original['type_digest'],
        'version': VERSION, 'editor_protocol': 'platform-editor@1', 'sources': sources}), 'editor_protocol': 'platform-editor@1', 'installed': True}


class UnifiedFormAdapter(FormAdapter):
    type_id = 'platform.form'

    def runtime_binding(self, application_id, actor_id):
        if not form_enabled():
            raise Conflict('表单新增办理已暂停。')
        return {'application_id': application_id, 'type_id': self.type_id, 'type_version': VERSION,
            'type_digest': unified_type(self.type_id)['type_digest'], 'capability_contract': 'form.records@1'}

    def validate_runtime(self, application_id, binding):
        expected = {'application_id': application_id, 'type_id': self.type_id, 'type_version': VERSION,
            'type_digest': unified_type(self.type_id)['type_digest'], 'capability_contract': 'form.records@1'}
        if not isinstance(binding, dict) or any(binding.get(key) != value for key, value in expected.items()):
            raise Conflict('表单发布的精确版本或实现摘要不一致。')


class UnifiedApprovalAdapter(ApprovalAdapter):
    type_id = 'platform.leave-approval'

    def runtime_binding(self, application_id, actor_id):
        if not approval_enabled() or not actor_id:
            raise Conflict('审批新增入口未启用或缺少已认证发布管理员。')
        return {'application_id': application_id, 'type_id': self.type_id, 'type_version': VERSION,
            'type_digest': unified_type(self.type_id)['type_digest'], 'capability_contract': 'approval.single@1',
            'approver_id': actor_id, 'approver_source': 'authenticated-publisher'}

    def validate_runtime(self, application_id, binding):
        expected = {'application_id': application_id, 'type_id': self.type_id, 'type_version': VERSION,
            'type_digest': unified_type(self.type_id)['type_digest'], 'capability_contract': 'approval.single@1',
            'approver_source': 'authenticated-publisher'}
        if not isinstance(binding, dict) or any(binding.get(key) != value for key, value in expected.items()) or not binding.get('approver_id'):
            raise Conflict('审批发布的精确版本、审批人来源或实现摘要不一致。')


def adapter_for(info):
    if info['type_version'] != VERSION:
        raise APIError(422, 'APPLICATION_TYPE_UNAVAILABLE', '未安装此运行适配器版本。')
    if info['type_id'] == 'platform.form':
        return UnifiedFormAdapter()
    if info['type_id'] == 'platform.leave-approval':
        return UnifiedApprovalAdapter()
    raise APIError(422, 'APPLICATION_TYPE_MISMATCH', '应用不是表单或审批类型。')


def form_store(store, application_id, expected_type=None):
    info = ApplicationStore(store).get(application_id)
    if info['type_version'] == '1.0.0':
        return legacy_store(store, application_id, expected_type)
    if expected_type and info['type_id'] != expected_type:
        raise APIError(422, 'APPLICATION_TYPE_MISMATCH', '接口与应用类型不一致。')
    return ConfigStore(store.engine, store.tenant_id, store.actor_id, application_id, adapter=adapter_for(info))


class UnifiedFormRecords(FormRecords):
    def __init__(self, store, principal, application_id):
        self.store = form_store(store, application_id, self.type_id)
        self.principal = principal


class UnifiedApprovalRecords(ApprovalRecords):
    def __init__(self, store, principal, application_id):
        self.store = form_store(store, application_id, self.type_id)
        self.principal = principal


def record_service(store, principal, application_id):
    info = ApplicationStore(store).get(application_id)
    if info['type_version'] == '1.0.0':
        from app.modules.forms.routes import record_service as legacy_service
        return legacy_service(store, principal, application_id)
    cls = UnifiedApprovalRecords if info['type_id'] == 'platform.leave-approval' else UnifiedFormRecords
    return cls(store, principal, application_id)
