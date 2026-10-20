"""Course-only design proposals. No model calls or publication side effects."""
import json
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.errors import APIError
from app.modules.config.schema import Column, DisplayConfig, FieldName, IntentRule
from app.modules.config.store import digest, Conflict
from app.modules.config.review import review

OPERATIONS = ['set_column', 'reorder_columns', 'replace_rules', 'set_application_metadata']
CATALOG_HASH = digest(OPERATIONS)
MAX_BYTES = 128 * 1024
Identifier = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_-]{0,63}$')]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class SetColumn(StrictModel):
    operation: Literal['set_column']
    value: Column


class ReorderColumns(StrictModel):
    operation: Literal['reorder_columns']
    fields: list[FieldName] = Field(min_length=1, max_length=6)


class ReplaceRules(StrictModel):
    operation: Literal['replace_rules']
    rules: list[IntentRule] = Field(max_length=50)


class CourseMetadata(StrictModel):
    name: str = Field(min_length=1, max_length=30)
    description: str = Field(min_length=1, max_length=160)
    category: Literal['学习服务', '校园事务', '校园生活']


class SetMetadata(StrictModel):
    operation: Literal['set_application_metadata']
    value: CourseMetadata


Operation = Annotated[SetColumn | ReorderColumns | ReplaceRules | SetMetadata, Field(discriminator='operation')]


class Question(StrictModel):
    id: Identifier
    text: str = Field(min_length=1, max_length=500)
    blocking: Literal[True]
    suggested_answers: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(max_length=5)


class CourseProposal(StrictModel):
    protocol_version: Literal['1.0.0']
    profile: Literal['course_query']
    request_id: Identifier
    application_id: Literal['schedule']
    base_revision: int = Field(ge=0)
    base_content_hash: Hash
    catalog_hash: Hash
    status: Literal['proposed', 'needs_clarification']
    summary: str = Field(min_length=1, max_length=1000)
    operations: list[Operation] = Field(max_length=20)
    questions: list[Question] = Field(max_length=10)


class AcceptProposal(StrictModel):
    proposal: CourseProposal
    idempotency_key: str = Field(min_length=16, max_length=80)


async def read_command(request, model):
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_BYTES:
            raise APIError(413, 'PROPOSAL_TOO_LARGE', '课表提案不得超过 128 KiB。')
    request._body = bytes(payload)  # Preserve the bounded body for FastAPI's typed body/OpenAPI handling.
    return parse_command(payload, model)


def parse_command(payload, model):
    if len(payload) > MAX_BYTES:
        raise APIError(413, 'PROPOSAL_TOO_LARGE', '课表提案不得超过 128 KiB。')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    def invalid_constant(_):
        raise ValueError('non-finite number')
    try:
        value = json.loads(payload, object_pairs_hook=pairs, parse_constant=invalid_constant)
        return model.model_validate(value, strict=True)
    except ValidationError as exc:
        tags = set(OPERATIONS)
        details = [{'path': '/' + '/'.join(str(part).replace('~', '~0').replace('/', '~1')[:200]
                     for part in item['loc'] if part not in tags), 'message': item['msg'][:250]}
                   for item in exc.errors(include_input=False)[:20]]
        raise APIError(422, 'PROPOSAL_INVALID', '提案格式或规则不合法，请检查标出的字段。', details) from None
    except (ValueError, TypeError, RecursionError, UnicodeDecodeError):
        # Never echo an arbitrary model response or its input values into errors/logs.
        raise APIError(422, 'PROPOSAL_INVALID', '提案格式不合法：请检查字段类型、未知属性、重复键及课表规则限制。') from None


def context(draft):
    request_id = 'request_' + uuid4().hex
    value = {'protocol_version': '1.0.0', 'profile': 'course_query', 'request_id': request_id,
        'application_id': 'schedule', 'base_revision': draft['revision'],
        'base_content_hash': digest(draft['content']), 'catalog_hash': CATALOG_HASH,
        'draft': draft['content'], 'user_request': '调整课表展示。',
        'selection': {'kind': 'application', 'object_id': 'schedule'},
        'allowed_operations': OPERATIONS, 'confirmed_decisions': ['角色和学生查询规则保持不变。'], 'validation_issues': []}
    column = next((item for item in draft['content']['columns'] if item['field'] == 'location'), draft['content']['columns'][0])
    label = '上课地点' if column['field'] == 'location' else '课程展示'
    changed = {**column, 'label': label if column['label'] != label else '地点' if column['field'] == 'location' else '课程名称'}
    proposal = {key: value[key] for key in ('protocol_version', 'profile', 'request_id', 'application_id',
        'base_revision', 'base_content_hash', 'catalog_hash')}
    proposal.update(status='proposed', summary='手工示例：调整一个现有列的展示名称，不修改角色或发布配置。',
        operations=[{'operation': 'set_column', 'value': changed}], questions=[])
    return {'context': value, 'example_proposal': proposal}


def evaluate(proposal: CourseProposal, draft):
    if (proposal.base_revision != draft['revision'] or proposal.base_content_hash != digest(draft['content'])
            or proposal.catalog_hash != CATALOG_HASH):
        raise Conflict('草稿或能力目录已变化，请重新载入提案基线。')
    if proposal.status != 'proposed' or proposal.questions or not proposal.operations:
        raise APIError(422, 'PROPOSAL_NEEDS_CLARIFICATION', '需先解决澄清问题；只有包含有效操作的提案可以接受。')
    content = DisplayConfig.model_validate(draft['content']).model_dump()
    before = DisplayConfig.model_validate(content)
    changes = []
    targets = set()
    for operation in proposal.operations:
        target = operation.operation + (':' + operation.value.field if isinstance(operation, SetColumn) else '')
        if target in targets:
            raise APIError(422, 'OPERATION_CONFLICT', '同一提案不能重复修改同一列、顺序、规则或元数据。')
        targets.add(target)
        if isinstance(operation, SetColumn):
            index = next((i for i, item in enumerate(content['columns']) if item['field'] == operation.value.field), None)
            if index is None:
                raise APIError(422, 'COLUMN_SET_CHANGED', '只能修改当前草稿中已有的课表列。')
            old = content['columns'][index]
            new = operation.value.model_dump()
            if old['label'] != new['label']:
                changes.append(f"{old['field']} 展示名称：{old['label']} → {new['label']}")
            if old['visible'] != new['visible']:
                changes.append(f"{old['field']} 显隐：{'显示' if old['visible'] else '隐藏'} → {'显示' if new['visible'] else '隐藏'}")
            content['columns'][index] = new
        elif isinstance(operation, ReorderColumns):
            fields = [item['field'] for item in content['columns']]
            if len(operation.fields) != len(set(operation.fields)) or set(fields) != set(operation.fields):
                raise APIError(422, 'COLUMN_SET_CHANGED', '重排必须包含原有全部列，不能增删或重复。')
            if fields != operation.fields:
                changes.append('列顺序：' + '、'.join(fields) + ' → ' + '、'.join(operation.fields))
            lookup = {item['field']: item for item in content['columns']}
            content['columns'] = [lookup[field] for field in operation.fields]
        elif isinstance(operation, ReplaceRules):
            rules = [rule.model_dump() for rule in operation.rules]
            if content['rules'] != rules:
                changes.append(f"意图规则从 {len(content['rules'])} 条调整为 {len(rules)} 条；详细影响见回归结果。")
            content['rules'] = rules
        else:
            for key, value in operation.value.model_dump().items():
                if content['application'][key] != value:
                    changes.append(f"应用 {key}：{content['application'][key]} → {value}")
                content['application'][key] = value
    try:
        candidate = DisplayConfig.model_validate(content)
    except ValidationError:
        raise APIError(422, 'PROPOSAL_BUSINESS_INVALID', '候选配置无效：至少保留一列可见、规则 ID 唯一且名称不能为空。') from None
    result = review(before, candidate)
    return {'candidate': candidate.model_dump(), 'changes': changes, 'review': result,
            'can_accept': result['can_publish'] and bool(changes)}
