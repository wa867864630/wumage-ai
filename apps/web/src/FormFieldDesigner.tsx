import {useId, useRef, useState} from 'react';
import type {FormConfig, DataField, Control} from './FormRuntime';
import {ApplicationConfigHeading} from './ApplicationEditorChrome';

const components: Record<Control['component'], string> = {text: '单行文本', textarea: '多行文本', number: '数字', checkbox: '布尔选择', select: '单选', multiselect: '多选', date: '日期'};

/** Controlled design surface; the host retains draft, locks and publication. */
export function FormFieldDesigner<T extends FormConfig>({config, disabled = false, protectedFields = [], onChange}: {
  config: T; disabled?: boolean; protectedFields?: readonly string[]; onChange: (update: (current: T) => T) => void;
}) {
  const blocked = disabled;
  const instanceId = useId();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const cards = useRef(new Map<string, HTMLFieldSetElement>());
  function locate(id: string) {
    setCollapsed(previous => {const next = new Set(previous); next.delete(id); return next;});
    const card = cards.current.get(id);
    card?.scrollIntoView?.({block: 'nearest'});
    card?.focus();
  }
  const controls = config.form.sections[0].fields;
  const byId = new Map(config.model.fields.map(field => [field.id, field]));
  function edit(update: (current: T) => T) {if (!disabled) onChange(update);}
  function fieldChange(id: string, patch: Partial<DataField>) {edit(next => {next.model.fields = next.model.fields.map(field => field.id === id ? {...field, ...patch} : field); return next;});}
  function controlChange(id: string, patch: Partial<Control>) {edit(next => {next.form.sections[0].fields = next.form.sections[0].fields.map(control => control.id === id ? {...control, ...patch} : control); return next;});}
  function addField() {
    edit(next => {const id = 'field_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12);
      next.model.fields.push({id, key: id, label: '新字段', data_type: 'string', nullable: true, constraints: {max_length: 1000}, options: []});
      next.form.sections[0].fields.push({id: id + '_control', field_id: id, component: 'text', label: '新字段', help: '', required: false, readonly: false, layout: {mobile: 12, tablet: 6, desktop: 6}}); return next;});
  }
  function removeField(id: string) {
    if (!window.confirm('从新表单移除此字段？历史记录仍按提交时版本保留。')) return;
    edit(next => {next.model.fields = next.model.fields.filter(field => field.id !== id); next.form.sections[0].fields = next.form.sections[0].fields.filter(control => control.field_id !== id); return next;});
  }
  function move(index: number, delta: number) {
    edit(next => {const controls = next.form.sections[0].fields; [controls[index], controls[index + delta]] = [controls[index + delta], controls[index]]; return next;});
  }
  return <div><ApplicationConfigHeading title="页面与表单" description="选择组件、调整顺序与宽度。字段标识固定，历史记录按提交时版本解释；首版最多30个字段、一个分组。" />
      <div className="field-designer-toolbar">
        <span>字段 {controls.length} / 30 · 必填 {controls.filter(control => control.required).length}</span>
        <div><button type="button" className="quiet-button" onClick={() => setCollapsed(new Set())}>展开全部字段</button>
          <button type="button" className="quiet-button" onClick={() => setCollapsed(new Set(controls.map(control => control.id)))}>收起全部字段</button></div>
      </div>
      <nav className="field-designer-outline" aria-label="字段快速定位">{controls.map((control, index) => <button type="button" className="quiet-button" key={control.id} onClick={() => locate(control.id)}>
        {index + 1}. {control.label}{control.required ? ' · 必填' : ''}
      </button>)}</nav>
      <button disabled={blocked || controls.length >= 30} onClick={addField}>添加字段</button>
      {controls.map((control, index) => {const field = byId.get(control.field_id)!; const panelId = `${instanceId}-${control.id}`; return <fieldset disabled={blocked} key={control.id} className="form-control-editor" tabIndex={-1} ref={node => {if (node) cards.current.set(control.id, node); else cards.current.delete(control.id);}}><legend>{index + 1}. {control.label}</legend>
        <div className="field-designer-toolbar"><span className="muted">{components[control.component]} · {control.required ? '必填' : '选填'}{protectedFields.includes(field.key) ? ' · 核心字段' : ''}</span>
          <button type="button" className="quiet-button" aria-expanded={!collapsed.has(control.id)} aria-controls={panelId} onClick={() => setCollapsed(previous => {const next = new Set(previous); if (next.has(control.id)) next.delete(control.id); else next.add(control.id); return next;})}>{collapsed.has(control.id) ? '展开配置' : '收起配置'}</button></div>
        <div id={panelId} className="field-designer-settings" hidden={collapsed.has(control.id)}>
        {protectedFields.includes(field.key) && <p className="muted">此为业务核心字段，组件、必填和删除受保护；可调整名称、提示、顺序与宽度。</p>}
        <div className="admin-toolbar"><button disabled={index === 0} onClick={() => move(index, -1)}>上移</button><button disabled={index === controls.length - 1} onClick={() => move(index, 1)}>下移</button><button disabled={controls.length <= 1 || protectedFields.includes(field.key)} onClick={() => removeField(field.id)}>移除字段</button></div>
        <label>名称<input maxLength={80} value={control.label} onChange={event => edit(next => {next.model.fields.find(item => item.id === field.id)!.label = event.target.value; next.form.sections[0].fields.find(item => item.id === control.id)!.label = event.target.value; return next;})} /></label>
        <label>组件<select disabled={protectedFields.includes(field.key)} value={control.component} onChange={event => edit(next => {const component = event.target.value as Control['component']; const target = next.model.fields.find(item => item.id === field.id)!;
          target.data_type = ({text: 'string', textarea: 'string', number: 'decimal', checkbox: 'boolean', date: 'date', select: 'enum', multiselect: 'enum_list'} as const)[component]; target.constraints = target.data_type === 'string' ? {max_length: 1000} : {}; target.options = ['select', 'multiselect'].includes(component) ? [{value: 'option_1', label: '选项一'}] : []; next.form.sections[0].fields.find(item => item.id === control.id)!.component = component; return next;})}>{Object.entries(components).map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select></label>
        <label><input type="checkbox" disabled={protectedFields.includes(field.key)} checked={control.required} onChange={event => edit(next => {next.model.fields.find(item => item.id === field.id)!.nullable = !event.target.checked; next.form.sections[0].fields.find(item => item.id === control.id)!.required = event.target.checked; return next;})} />必填</label>
        <label>填写提示<input maxLength={300} value={control.help} onChange={event => controlChange(control.id, {help: event.target.value})} /></label>
        <div className="admin-toolbar">{(['mobile', 'tablet', 'desktop'] as const).map((device, i) => <label key={device}>{['手机宽度', '平板宽度', '桌面宽度'][i]}<select value={control.layout[device]} onChange={event => controlChange(control.id, {layout: {...control.layout, [device]: Number(event.target.value)}})}>{[12, 6, 4, 3].map(width => <option key={width} value={width}>{width === 12 ? '整行' : `每行 ${12 / width} 份`}</option>)}</select></label>)}</div>
        {field.data_type === 'string' && <label>最多字符<input type="number" min={1} max={10000} value={field.constraints.max_length ?? 1000} onChange={event => fieldChange(field.id, {constraints: {...field.constraints, max_length: Number(event.target.value)}})} /></label>}
        {['integer', 'decimal'].includes(field.data_type) && <div className="admin-toolbar"><label>数字类型<select value={field.data_type} onChange={event => fieldChange(field.id, {data_type: event.target.value as 'integer' | 'decimal'})}><option value="decimal">小数</option><option value="integer">整数</option></select></label>{(['minimum', 'maximum', 'precision'] as const).map((bound, i) => <label key={bound}>{['最小值', '最大值', '小数位数'][i]}<input type="number" value={field.constraints[bound] ?? ''} onChange={event => fieldChange(field.id, {constraints: {...field.constraints, [bound]: event.target.value === '' ? undefined : Number(event.target.value)}})} /></label>)}</div>}
        {['enum', 'enum_list'].includes(field.data_type) && <label>选项（每行：稳定值|显示名称）<textarea value={field.options.map(option => `${option.value}|${option.label}`).join('\n')} onChange={event => fieldChange(field.id, {options: event.target.value.split('\n').map(line => {const [value, ...label] = line.split('|'); return {value, label: label.join('|') || value};})})} /></label>}
        </div>
      </fieldset>;})}</div>;
}
