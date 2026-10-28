export type ApplicationSettingsValue = {
  name: string; description: string; category: '学习服务' | '校园事务' | '校园生活';
  visible_roles: ('student' | 'teacher' | 'config_admin')[];
};

/** Shared metadata/access editor. The host owns revisions, saving and authorization. */
export function ApplicationSettingsFields({value, disabled = false, onChange}: {
  value: ApplicationSettingsValue; disabled?: boolean; onChange: (value: ApplicationSettingsValue) => void;
}) {
  return <fieldset disabled={disabled} className="management-fields"><legend className="sr-only">应用基本信息</legend>
    <div className="management-form-grid"><label>应用名称<input required maxLength={30} value={value.name} onChange={event => onChange({...value, name: event.target.value})} /></label>
      <label>应用分类<select value={value.category} onChange={event => onChange({...value, category: event.target.value as ApplicationSettingsValue['category']})}>{['学习服务', '校园事务', '校园生活'].map(category => <option key={category}>{category}</option>)}</select></label>
      <label className="full-width">应用描述<textarea rows={4} maxLength={160} value={value.description} onChange={event => onChange({...value, description: event.target.value})} /></label>
    </div>
    <div className="role-setting"><h4>可见及可使用角色</h4><p className="hint">至少选择一个角色。发布后由服务端校验访问权限。</p><div className="role-options">{([['student', '学生'], ['teacher', '教师'], ['config_admin', '配置管理员']] as const).map(([role, title]) => <label key={role}>
      <input type="checkbox" checked={value.visible_roles.includes(role)} onChange={event => onChange({...value, visible_roles: event.target.checked ? [...value.visible_roles, role] : value.visible_roles.filter(item => item !== role)})} />{title}
    </label>)}</div></div>
  </fieldset>;
}
