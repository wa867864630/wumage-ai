type Row = Record<string, unknown>;
const object = (value: unknown): Row => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
const list = (value: unknown): Row[] => Array.isArray(value) ? value.map(object) : [];
const label = (value: unknown) => typeof value === 'string' || typeof value === 'number' ? String(value) : '未提供';
const components: Record<string, string> = {text: '单行文本', textarea: '多行文本', number: '数字', checkbox: '勾选', select: '单选', multiselect: '多选', date: '日期'};
const roles: Record<string, string> = {student: '学生', teacher: '教师', config_admin: '配置管理员'};

/** Read-only review of a server-validated proposal; never executes model content. */
export function ConstructionPreview({content, businessKind, applicationName}: {content: unknown; businessKind?: string; applicationName?: string}) {
  const config = object(content); const application = object(config.application);
  const sections = list(object(config.form).sections); const fields = list(object(config.model).fields);
  return <section aria-label="设计提案核对">
    <h4>设计提案核对</h4>
    <p>应用：{applicationName ?? label(application.name)}</p>
    {typeof application.description === 'string' && <p>{application.description}</p>}
    <p>可使用角色：{Array.isArray(application.visible_roles) ? application.visible_roles.map(role => roles[String(role)] ?? label(role)).join('、') : '请查看完整配置'}</p>
    {sections.map((section, sectionIndex) => <div key={sectionIndex}>
      <h4>{label(section.title)}</h4>
      <ol>{list(section.fields).map((control, index) => {
        const field = fields.find(item => item.id === control.field_id) ?? {};
        const layout = object(control.layout); const constraints = object(field.constraints);
        return <li key={index}>
          <strong>{label(control.label)}</strong> · {components[String(control.component)] ?? label(control.component)} · {control.required === true ? '必填' : '选填'}
          <p>宽度（12 等份）：手机 {label(layout.mobile)}/12 · 平板 {label(layout.tablet)}/12 · 桌面 {label(layout.desktop)}/12</p>
          {typeof control.help === 'string' && control.help && <p>填写提示：{control.help}</p>}
          {list(field.options).length > 0 && <p>选项：{list(field.options).map(option => label(option.label)).join('、')}</p>}
          {Object.keys(constraints).length > 0 && <p>填写限制：{Object.entries(constraints).map(([key, value]) => `${({max_length: '最大字数', minimum: '最小值', maximum: '最大值', precision: '小数位数'} as Record<string, string>)[key] ?? key} ${label(value)}`).join('；')}</p>}
        </li>;
      })}</ol>
    </div>)}
    {businessKind === 'leave' && <p>审批：提交申请 → 发布管理员单级审批 → 通过 / 拒绝。不能自审；创建草稿不会立即确定发布审批人。</p>}
    {Array.isArray(config.columns) && <div><h4>课程展示顺序</h4><ol>{list(config.columns).map((column, index) => <li key={index}>
      {label(column.label)} · {column.visible === true ? '显示' : '隐藏'}
    </li>)}</ol><h4>自定义查询规则</h4>{list(config.rules).length ? <ol>{list(config.rules).map((rule, index) => <li key={index}>
      {rule.enabled === true ? '启用' : '停用'} · {rule.match_type === 'exact' ? '完整匹配' : '关键词匹配'} · {Array.isArray(rule.terms) ? rule.terms.map(label).join('、') : '未提供'}
      <p>查询范围：{({today: '今天', tomorrow: '明天', day_after: '后天', this_week: '本周', next_week: '下周'} as Record<string, string>)[String(rule.date_mode)] ?? label(rule.date_mode)} · 优先级 {label(rule.priority)}</p>
    </li>)}</ol> : <p>无自定义规则，继续使用系统内置查询规则。</p>}</div>}
    {!sections.length && !Array.isArray(config.columns) && <p>此类型的详细展示与规则请在下方完整配置中核对。</p>}
    <p className="muted">当前仅为设计提案。确认后创建草稿，仍需检查配置、试用并单独发布。</p>
  </section>;
}
