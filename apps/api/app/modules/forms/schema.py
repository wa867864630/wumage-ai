"""Executable subset of application-design/v1 model/form definitions, without scripts or dynamic DDL."""
from datetime import date
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer, field_validator
from app.errors import APIError
from app.modules.config.schema import ApplicationInfo

Identifier = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_-]{0,63}$')]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, strict=True)

    @field_validator('*', mode='before')
    @classmethod
    def no_null_character(cls, value, info):
        if info.field_name in ('readonly', 'allow_self_approval') and type(value) is not bool:
            raise ValueError('布尔策略必须使用JSON布尔值，不能用数字替代')
        if isinstance(value, str) and '\x00' in value:
            raise ValueError('文本不能包含NUL控制字符')
        return value


class Option(Strict):
    value: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)


def absent_constraint_schema(schema):
    # Omitted means unset; explicit JSON null is rejected by the runtime validator below.
    schema.pop('default', None)
    choices = [item for item in schema.get('anyOf', []) if item.get('type') != 'null']
    if len(choices) == 1:
        schema.pop('anyOf', None)
        schema.update(choices[0])


class Constraints(Strict):
    max_length: int | None = Field(default=None, ge=1, le=10000, json_schema_extra=absent_constraint_schema)
    minimum: float | None = Field(default=None, ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False, json_schema_extra=absent_constraint_schema)
    maximum: float | None = Field(default=None, ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False, json_schema_extra=absent_constraint_schema)
    precision: int | None = Field(default=None, ge=0, le=6, json_schema_extra=absent_constraint_schema)

    @model_validator(mode='before')
    @classmethod
    def omit_unset_constraints(cls, value):
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError('未设置的约束应省略，不能写null')
        return value

    @model_serializer(mode='wrap')
    def serialize(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class DataField(Strict):
    id: Identifier
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    label: str = Field(min_length=1, max_length=80)
    data_type: Literal['string', 'integer', 'decimal', 'boolean', 'date', 'enum', 'enum_list']
    nullable: bool
    constraints: Constraints
    options: list[Option] = Field(max_length=20)

    @model_validator(mode='after')
    def coherent(self):
        if self.data_type in ('enum', 'enum_list'):
            if not self.options or len({option.value for option in self.options}) != len(self.options):
                raise ValueError('选择字段需要不重复的选项')
        elif self.options:
            raise ValueError('非选择字段不能带选项')
        if self.constraints.minimum is not None and self.constraints.maximum is not None and self.constraints.minimum > self.constraints.maximum:
            raise ValueError('最小值不能大于最大值')
        if self.data_type not in ('integer', 'decimal') and any(value is not None for value in (self.constraints.minimum, self.constraints.maximum, self.constraints.precision)):
            raise ValueError('数值约束只能用于数字字段')
        if self.data_type != 'string' and self.constraints.max_length is not None:
            raise ValueError('长度约束只能用于文本字段')
        return self


class DataModel(Strict):
    id: Identifier
    name: str = Field(min_length=1, max_length=80)
    adapter_id: Literal['form.records']
    fields: list[DataField] = Field(min_length=1, max_length=30)


class Layout(Strict):
    mobile: int = Field(ge=1, le=12)
    tablet: int = Field(ge=1, le=12)
    desktop: int = Field(ge=1, le=12)


class Control(Strict):
    id: Identifier
    field_id: Identifier
    component: Literal['text', 'textarea', 'number', 'checkbox', 'select', 'multiselect', 'date']
    label: str = Field(min_length=1, max_length=80)
    help: str = Field(max_length=300)
    required: bool
    readonly: Literal[False] = False
    layout: Layout


class Section(Strict):
    id: Identifier
    title: str = Field(min_length=1, max_length=80)
    fields: list[Control] = Field(min_length=1, max_length=30)


class Form(Strict):
    id: Identifier
    name: str = Field(min_length=1, max_length=80)
    model_id: Identifier
    sections: list[Section] = Field(min_length=1, max_length=1)
    submit_action_id: Literal['form.records.submit']


class FormConfig(Strict):
    schema_version: Literal['1.0.0'] = '1.0.0'
    application: ApplicationInfo
    model: DataModel
    form: Form

    @model_validator(mode='after')
    def references(self):
        fields = self.model.fields
        controls = [control for section in self.form.sections for control in section.fields]
        if self.form.model_id != self.model.id or len({field.id for field in fields}) != len(fields) or len({field.key for field in fields}) != len(fields):
            raise ValueError('数据模型标识或字段引用不一致')
        if {control.field_id for control in controls} != {field.id for field in fields} or len(controls) != len(fields) or len({control.id for control in controls}) != len(controls):
            raise ValueError('每个数据字段必须有一个唯一表单控件')
        allowed = {'string': {'text', 'textarea'}, 'integer': {'number'}, 'decimal': {'number'}, 'boolean': {'checkbox'}, 'date': {'date'}, 'enum': {'select'}, 'enum_list': {'multiselect'}}
        by_id = {field.id: field for field in fields}
        for control in controls:
            field = by_id[control.field_id]
            if control.component not in allowed[field.data_type] or control.required != (not field.nullable):
                raise ValueError('控件类型或必填约束与数据字段不一致')
        return self


def default_form():
    return FormConfig.model_validate({'application': {'name': '登记应用', 'description': '填写登记表并查询本人记录。', 'category': '校园事务'},
        'model': {'id': 'registration', 'name': '登记记录', 'adapter_id': 'form.records', 'fields': [
            {'id': 'content', 'key': 'content', 'label': '登记内容', 'data_type': 'string', 'nullable': False, 'constraints': {'max_length': 1000}, 'options': []}]},
        'form': {'id': 'registration_form', 'name': '登记表', 'model_id': 'registration', 'submit_action_id': 'form.records.submit', 'sections': [
            {'id': 'basic', 'title': '登记信息', 'fields': [{'id': 'content_control', 'field_id': 'content', 'component': 'textarea', 'label': '登记内容',
                'help': '', 'required': True, 'readonly': False, 'layout': {'mobile': 12, 'tablet': 12, 'desktop': 12}}]}]}})


def validate_values(config: FormConfig, values):
    if not isinstance(values, dict) or set(values) - {field.key for field in config.model.fields}:
        raise APIError(422, 'FORM_VALUES_INVALID', '提交包含未声明字段。')
    result = {}
    for field in config.model.fields:
        value = values.get(field.key)
        if value is None or value == '' or value == []:
            if not field.nullable:
                raise APIError(422, 'FORM_FIELD_REQUIRED', f'请填写“{field.label}”。')
            result[field.key] = None
            continue
        valid = True
        if field.data_type == 'string':
            valid = isinstance(value, str) and '\x00' not in value and bool(value.strip()) and len(value) <= (field.constraints.max_length or 1000)
        elif field.data_type in ('integer', 'decimal'):
            valid = type(value) in (int, float) and abs(value) <= 9007199254740991 and math.isfinite(value) and (field.data_type != 'integer' or value == int(value))
            if valid:
                valid = (field.constraints.minimum is None or value >= field.constraints.minimum) and (field.constraints.maximum is None or value <= field.constraints.maximum)
                if field.constraints.precision is not None:
                    valid = valid and abs(value - round(value, field.constraints.precision)) <= 1e-9
        elif field.data_type == 'boolean':
            valid = type(value) is bool
        elif field.data_type == 'date':
            try:
                valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
            except (ValueError, TypeError):
                valid = False
        elif field.data_type == 'enum':
            valid = isinstance(value, str) and value in {option.value for option in field.options}
        elif field.data_type == 'enum_list':
            valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
            if valid:
                valid = len(set(value)) == len(value) and set(value) <= {option.value for option in field.options}
        if not valid:
            raise APIError(422, 'FORM_VALUE_INVALID', f'“{field.label}”不符合类型、选项或范围要求。')
        result[field.key] = value
    return result


class SaveForm(Strict):
    expected_revision: int = Field(ge=0)
    content: FormConfig
    idempotency_key: str = Field(min_length=16, max_length=80)


class PreviewForm(Strict):
    expected_revision: int = Field(ge=0)
    content: FormConfig
    values: dict = Field(max_length=30)
