from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FieldName = Literal['name', 'date', 'start_time', 'end_time', 'location', 'teacher']


class IntentRule(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    enabled: bool = True
    priority: int = Field(default=100, ge=1, le=1000)
    match_type: Literal['exact', 'keywords'] = 'exact'
    terms: list[str] = Field(min_length=1, max_length=10)
    date_mode: Literal['today', 'tomorrow', 'day_after', 'this_week', 'next_week'] = 'today'

    @model_validator(mode='after')
    def safe_terms(self):
        from app.modules.intent.router import normalize
        forbidden = ('不', '别', '取消', '请假', '采购', '预约', '修改', '删除', '成绩', '学号', '权限')
        if any(not normalize(term) or len(term) > 80 or any(word in term for word in forbidden)
               for term in self.terms):
            raise ValueError('规则词必须为 1～80 字符，不得包含否定或其他业务操作')
        self.terms = list(dict.fromkeys(normalize(term) for term in self.terms))
        return self


class Column(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    field: FieldName
    label: str = Field(min_length=1, max_length=30)
    visible: bool = True


class ApplicationInfo(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(default='课表助手', min_length=1, max_length=30)
    description: str = Field(default='一句话查询课程，随时掌握每天的学习安排。', min_length=1, max_length=160)
    category: Literal['学习服务', '校园事务', '校园生活'] = '学习服务'
    visible_roles: list[Literal['student', 'teacher', 'config_admin']] = Field(
        default_factory=lambda: ['student', 'teacher', 'config_admin'], min_length=1, max_length=3)

    @model_validator(mode='after')
    def unique_roles(self):
        if len(set(self.visible_roles)) != len(self.visible_roles):
            raise ValueError('可见角色不能重复')
        return self


class DisplayConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1'] = '1'
    columns: list[Column] = Field(min_length=1, max_length=6)
    rules: list[IntentRule] = Field(default_factory=list, max_length=50)
    application: ApplicationInfo = Field(default_factory=ApplicationInfo)

    @model_validator(mode='after')
    def validate_columns(self):
        fields = [column.field for column in self.columns]
        if len(fields) != len(set(fields)):
            raise ValueError('展示字段不能重复')
        if not any(column.visible for column in self.columns):
            raise ValueError('至少保留一个可见字段')
        if any(not column.label.strip() for column in self.columns):
            raise ValueError('字段名称不能为空白')
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError('规则 ID 不能重复')
        return self


def default_config() -> DisplayConfig:
    return DisplayConfig(columns=[
        Column(field=field, label=label)
        for field, label in [('name', '课程'), ('date', '日期'), ('start_time', '开始时间'),
                             ('end_time', '结束时间'), ('location', '地点'), ('teacher', '教师')]
    ])


class SaveDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    content: DisplayConfig


class PublishDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    expected_release_id: str | None
    idempotency_key: str = Field(min_length=16, max_length=80)


class Rollback(PublishDraft):
    target_release_id: str = Field(min_length=1, max_length=36)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: DisplayConfig
    text: str = Field(default='明天课表', min_length=1, max_length=200)
