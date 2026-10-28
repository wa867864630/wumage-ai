"""Verified publisher resolves the first approver; arbitrary client-provided people are forbidden."""
import hashlib
import json
import os
from pathlib import Path
from datetime import date
from app.errors import APIError
from app.modules.config.store import Conflict, digest
from app.modules.approvals.schema import LeaveConfig, default_leave

TYPE_ID = 'platform.leave-approval'
TYPE_VERSION = '1.0.0'
ROOT = Path(__file__).resolve().parents[5]


def enabled():
    return os.environ.get('APPROVAL_APPLICATIONS_ENABLED', '1') == '1'


def approval_type():
    from app.modules.forms.adapter import form_type
    manifest = json.loads((ROOT / 'packages/contracts/application-types/v1/examples/leave-type.json').read_text(encoding='utf-8'))
    if (manifest['type_id'], manifest['type_version'], manifest['runtime_adapter_id']) != (TYPE_ID, TYPE_VERSION, 'leave-approval-v1'):
        raise APIError(503, 'TYPE_BUNDLE_INVALID', '审批类型清单与实现不一致。')
    paths = ('apps/api/app/modules/approvals/schema.py', 'apps/api/app/modules/approvals/adapter.py',
        'apps/api/app/modules/approvals/runtime.py', 'apps/api/app/modules/approvals/routes.py',
        'apps/web/src/ApprovalEditor.tsx', 'apps/web/src/ApprovalRuntime.tsx', 'packages/contracts/application-types/v1/leave-config.schema.json')
    sources = {name: hashlib.sha256((ROOT / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for name in paths}
    return {**manifest, 'type_digest': digest({'algorithm': 'reviewed-source-lf-v1', 'manifest': manifest, 'sources': sources,
        'form_dependency': form_type()['type_digest']}), 'installed': True}


class ApprovalAdapter:
    model = LeaveConfig
    defaults = staticmethod(default_leave)

    @staticmethod
    def validate_values(config, values):
        from app.modules.forms.schema import validate_values
        result = validate_values(config, values)
        if date.fromisoformat(result['end_date']) < date.fromisoformat(result['start_date']):
            raise APIError(422, 'LEAVE_DATE_RANGE', '结束日期不能早于开始日期。首版按自然日、含首尾计算。')
        return result

    @staticmethod
    def validate_publish(content):
        LeaveConfig.model_validate(content)

    @staticmethod
    def runtime_binding(application_id, actor_id):
        if not enabled() or not actor_id:
            raise Conflict('审批新增入口未启用或缺少已认证发布管理员。')
        return {'application_id': application_id, 'type_id': TYPE_ID, 'type_version': TYPE_VERSION,
            'type_digest': approval_type()['type_digest'], 'capability_contract': 'approval.single@1',
            'approver_id': actor_id, 'approver_source': 'authenticated-publisher'}

    @staticmethod
    def validate_runtime(application_id, binding):
        if not isinstance(binding, dict) or any(binding.get(key) != value for key, value in {'application_id': application_id,
                'type_id': TYPE_ID, 'type_version': TYPE_VERSION, 'type_digest': approval_type()['type_digest'],
                'capability_contract': 'approval.single@1', 'approver_source': 'authenticated-publisher'}.items()) or not binding.get('approver_id'):
            raise Conflict('审批发布的类型、审批人来源或运行能力绑定不可用。')
