"""Single-level leave workflow, a strict executable subset of application-design/v1."""
from typing import Literal
from pydantic import Field, model_validator
from app.modules.forms.schema import Strict, Identifier, Form, FormConfig


class PublisherActor(Strict):
    kind: Literal['relation']
    resolver_id: Literal['identity.application-publisher']
    selection: Literal['exactly_one']


class Start(Strict):
    id: Identifier
    type: Literal['start']
    next: Identifier


class ApprovalNode(Strict):
    id: Identifier
    type: Literal['approval']
    name: str = Field(min_length=1, max_length=80)
    actor: PublisherActor
    allow_self_approval: Literal[False]
    on_missing_actor: Literal['block']
    on_approve: Identifier
    on_reject: Identifier


class End(Strict):
    id: Identifier
    type: Literal['end']
    outcome: Literal['approved', 'rejected']


class Withdrawal(Strict):
    allowed_at_node_ids: list[Identifier] = Field(min_length=1, max_length=1)
    action_id: Literal['approval.withdraw']


class Workflow(Strict):
    id: Identifier
    name: str = Field(min_length=1, max_length=80)
    model_id: Identifier
    form_id: Identifier
    executor_id: Literal['approval.single']
    entry_node_id: Identifier
    nodes: list[Start | ApprovalNode | End] = Field(min_length=4, max_length=4)
    withdrawal: Withdrawal

    @model_validator(mode='after')
    def single_level(self):
        starts = [node for node in self.nodes if isinstance(node, Start)]
        approvals = [node for node in self.nodes if isinstance(node, ApprovalNode)]
        ends = [node for node in self.nodes if isinstance(node, End)]
        if len({node.id for node in self.nodes}) != 4 or len(starts) != 1 or len(approvals) != 1 or {node.outcome for node in ends} != {'approved', 'rejected'}:
            raise ValueError('首版仅支持开始、单级审批、通过与拒绝两个结束节点')
        by_outcome = {node.outcome: node.id for node in ends}; start, approval = starts[0], approvals[0]
        if (self.entry_node_id != start.id or start.next != approval.id or approval.on_approve != by_outcome['approved']
                or approval.on_reject != by_outcome['rejected'] or self.withdrawal.allowed_at_node_ids != [approval.id]):
            raise ValueError('流程连线或撤回阶段不符合单级审批规则')
        return self


class ApprovalForm(Form):
    submit_action_id: Literal['approval.apply']


class LeaveConfig(FormConfig):
    form: ApprovalForm
    workflow: Workflow

    @model_validator(mode='after')
    def leave_fields(self):
        if self.workflow.model_id != self.model.id or self.workflow.form_id != self.form.id:
            raise ValueError('流程的表单与模型引用不一致')
        fields = {field.key: field for field in self.model.fields}
        if any(key not in fields or fields[key].data_type != kind or fields[key].nullable
               for key, kind in {'start_date': 'date', 'end_date': 'date', 'reason': 'string'}.items()):
            raise ValueError('请假必须保留必填的起止日期和请假原因，不得改变核心字段类型')
        return self


def default_leave():
    fields = [
        {'id': 'start_date', 'key': 'start_date', 'label': '开始日期', 'data_type': 'date', 'nullable': False, 'constraints': {}, 'options': []},
        {'id': 'end_date', 'key': 'end_date', 'label': '结束日期', 'data_type': 'date', 'nullable': False, 'constraints': {}, 'options': []},
        {'id': 'reason', 'key': 'reason', 'label': '请假原因', 'data_type': 'string', 'nullable': False, 'constraints': {'max_length': 1000}, 'options': []}]
    controls = [{'id': field['id'] + '_control', 'field_id': field['id'], 'label': field['label'], 'help': '', 'component': 'textarea' if field['key'] == 'reason' else 'date',
        'required': True, 'readonly': False, 'layout': {'mobile': 12, 'tablet': 12 if field['key'] == 'reason' else 6, 'desktop': 12 if field['key'] == 'reason' else 6}} for field in fields]
    return LeaveConfig.model_validate({'application': {'name': '请假申请', 'description': '按自然日提交请假，由发布管理员审批，可查询进度或在待审时撤回。', 'category': '校园事务'},
        'model': {'id': 'leave_application', 'name': '请假申请', 'adapter_id': 'form.records', 'fields': fields},
        'form': {'id': 'leave_form', 'name': '请假申请表', 'model_id': 'leave_application', 'submit_action_id': 'approval.apply', 'sections': [{'id': 'leave_details', 'title': '请假信息', 'fields': controls}]},
        'workflow': {'id': 'leave_process', 'name': '单级请假审批', 'model_id': 'leave_application', 'form_id': 'leave_form', 'executor_id': 'approval.single', 'entry_node_id': 'start',
            'nodes': [{'id': 'start', 'type': 'start', 'next': 'review'}, {'id': 'review', 'type': 'approval', 'name': '管理员审批',
                'actor': {'kind': 'relation', 'resolver_id': 'identity.application-publisher', 'selection': 'exactly_one'},
                'allow_self_approval': False, 'on_missing_actor': 'block', 'on_approve': 'approved', 'on_reject': 'rejected'},
                {'id': 'approved', 'type': 'end', 'outcome': 'approved'}, {'id': 'rejected', 'type': 'end', 'outcome': 'rejected'}],
            'withdrawal': {'allowed_at_node_ids': ['review'], 'action_id': 'approval.withdraw'}}})
