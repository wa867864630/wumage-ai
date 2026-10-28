import type {FormConfig} from './FormRuntime';
import type {SingleWorkflow} from './SingleWorkflowSettings';

type Metadata = {name: string; description: string; category: string; visible_roles: string[]};
type ApplicationConfig = {application: Metadata};
type FormDesign = FormConfig & {workflow?: SingleWorkflow};
const roleNames: Record<string, string> = {student: '学生', teacher: '教师', config_admin: '配置管理员'};
const roles = (items: string[]) => items.map(item => roleNames[item] ?? item).join('、');
function normalized(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(normalized);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, normalized(item)]));
  return value;
}
const same = (a: unknown, b: unknown) => JSON.stringify(normalized(a)) === JSON.stringify(normalized(b));

/** Shared, read-only review. Publication remains an authorized server operation. */
export function ApplicationChangeReview({before, after}: {before: ApplicationConfig | null; after: ApplicationConfig}) {
  const old = before?.application; const current = after.application; const changes: string[] = [];
  const accessChanged = !!old && !same([...old.visible_roles].sort(), [...current.visible_roles].sort());
  for (const [key, label] of [['name', '应用名称'], ['description', '应用说明'], ['category', '应用分类']] as const) {
    if (old && old[key] !== current[key]) changes.push(`${label}：${old[key]} → ${current[key]}`);
  }
  if (old && accessChanged) changes.push(`可使用角色：${roles(old.visible_roles)} → ${roles(current.visible_roles)}`);
  return <section aria-label="基础配置发布核对"><h4>{old ? '基础配置变更' : '首次发布核对'}</h4>
    {old ? changes.length ? <ul>{changes.map((change, index) => <li key={index}>{change}</li>)}</ul> : <p>基础信息与访问范围无变化。</p>
      : <><p>应用：{current.name}</p><p>可使用角色：{roles(current.visible_roles)}</p></>}
    {accessChanged && <p>访问范围发布后生效，可能影响已有记录的访问入口；数据不会因此删除。</p>}
  </section>;
}

export function FormChangeReview({before, after}: {before: FormDesign | null; after: FormDesign}) {
  const changes: string[] = [];
  if (before) {
    const oldFields = new Map(before.model.fields.map(field => [field.id, field]));
    const oldControls = before.form.sections.flatMap(section => section.fields);
    const controls = after.form.sections.flatMap(section => section.fields);
    for (const field of after.model.fields) {
      const old = oldFields.get(field.id);
      if (!old) changes.push(`新增字段：${field.label}`);
      else if (!same(old, field) || !same(oldControls.find(control => control.field_id === field.id), controls.find(control => control.field_id === field.id))) changes.push(`调整字段：${old.label}${old.label === field.label ? '' : ` → ${field.label}`}（组件、必填、选项、限制或布局）`);
    }
    for (const field of before.model.fields) if (!after.model.fields.some(item => item.id === field.id)) changes.push(`移除字段：${field.label}（历史记录保留原字段）`);
    if (!same(oldControls.map(control => control.field_id), controls.map(control => control.field_id))) changes.push('字段顺序或字段集合已变化。');
    if (!changes.length && (!same(before.model, after.model) || !same(before.form, after.form))) changes.push('表单模型或分组设置已调整，请核对设计。');
    if (before.workflow?.name !== after.workflow?.name) changes.push(`流程名称：${before.workflow?.name ?? '未配置'} → ${after.workflow?.name ?? '未配置'}`);
    const nodeName = (value: FormDesign) => value.workflow?.nodes.find(node => node.type === 'approval')?.name;
    if (nodeName(before) !== nodeName(after)) changes.push(`审批节点名称：${nodeName(before) ?? '未配置'} → ${nodeName(after) ?? '未配置'}`);
  }
  return <section aria-label="设计发布变更核对"><ApplicationChangeReview before={before} after={after} />
    <h4>表单与流程核对</h4>
    {before ? changes.length ? <ul>{changes.map((change, index) => <li key={index}>{change}</li>)}</ul> : <p>表单与流程设计无变化。</p>
      : <p>首次开放 {after.model.fields.length} 个字段：{after.form.sections.flatMap(section => section.fields).map(control => control.label).join('、')}</p>}
    {after.workflow && <p>本次流程：{after.workflow.name} · {after.workflow.nodes.find(node => node.type === 'approval')?.name} · 发布管理员单级审批</p>}
    <p className="muted">字段与流程变化用于新办理；已有记录和在途申请保留提交时版本。访问范围按当前发布和服务端权限执行。</p>
  </section>;
}
