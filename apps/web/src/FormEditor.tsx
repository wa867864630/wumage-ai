import {useCallback, useEffect, useRef, useState, type ReactNode} from 'react';
import {api, HTTPError, SessionExpired} from './api';
import {FormFields, RecordBrowser, type FormConfig, type DataField, type Control} from './FormRuntime';

type Draft = {revision: number; current_release_id: string | null; content: FormConfig};
type Release = {id: string; created_at: string; operation: string};
type Pending = {path: string; body: Record<string, unknown>; method: string; label: string};
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
const components: Record<Control['component'], string> = {text: '单行文本', textarea: '多行文本', number: '数字', checkbox: '布尔选择', select: '单选', multiselect: '多选', date: '日期'};

export function FormEditor({initialToken = '', applicationId = '', onSessionExpired, onDirtyChange, onUpdated, onUse, onNavigationLock, initialTab = 'basic', typeLabel = '简单登记', recordsLabel = '登记记录', protectedFields = [], renderRecords, renderWorkflow}: {
  initialToken?: string; applicationId?: string; embedded?: boolean; onSessionExpired?: () => void; onDirtyChange?: (value: boolean) => void;
  onUpdated?: () => void; onUse?: (id: string) => void; onNavigationLock?: (locked: boolean) => void; initialTab?: 'basic' | 'page' | 'questions' | 'ai' | 'history';
  typeLabel?: string; recordsLabel?: string; protectedFields?: string[]; renderRecords?: (version: number) => ReactNode; renderWorkflow?: () => ReactNode;
} = {}) {
  const base = `/api/v1/admin/applications/${applicationId}/form`;
  const [draft, setDraft] = useState<Draft | null>(null); const [content, setContent] = useState<FormConfig | null>(null);
  const [releases, setReleases] = useState<Release[]>([]); const [tab, setTab] = useState(initialTab === 'page' ? 'fields' : initialTab === 'questions' ? 'records' : initialTab === 'history' ? 'history' : 'basic');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [changed, setChanged] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null); const [storageKey, setStorageKey] = useState(''); const [recordVersion, setRecordVersion] = useState(0);
  const [values, setValues] = useState<Record<string, unknown>>({}); const [preview, setPreview] = useState(''); const lock = useRef(false);
  const callbacks = useRef({onSessionExpired, onDirtyChange, onNavigationLock, onUpdated}); callbacks.current = {onSessionExpired, onDirtyChange, onNavigationLock, onUpdated};
  const alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const report = useCallback((cause: unknown) => {if (!alive.current) return; if (cause instanceof SessionExpired) callbacks.current.onSessionExpired?.(); else setError(cause instanceof Error ? cause.message : '操作失败');}, []);
  const dirty = changed || !!pending || busy;
  useEffect(() => {callbacks.current.onDirtyChange?.(dirty); callbacks.current.onNavigationLock?.(busy || !!pending);}, [dirty, busy, pending]);
  useEffect(() => () => {callbacks.current.onDirtyChange?.(false); callbacks.current.onNavigationLock?.(false);}, []);
  useEffect(() => {const guard = (event: BeforeUnloadEvent) => {if (dirty) {event.preventDefault(); event.returnValue = '';}}; window.addEventListener('beforeunload', guard); return () => window.removeEventListener('beforeunload', guard);}, [dirty]);
  const load = useCallback(async (signal?: AbortSignal) => {
    const [current, versions] = await Promise.all([api<Draft>(base, initialToken, undefined, signal), api<Release[]>(base + '/releases', initialToken, undefined, signal)]);
    if (signal?.aborted || !alive.current) return;
    setDraft(current); setContent(clone(current.content)); setReleases(versions); setChanged(false); setPreview(''); setRecordVersion(value => value + 1);
  }, [base, initialToken]);
  useEffect(() => {
    const abort = new AbortController(); setBusy(true); setError('');
    void Promise.all([api<{subject: string; tenant_id: string}>('/api/v1/me', initialToken, undefined, abort.signal), load(abort.signal)]).then(([identity]) => {
      if (abort.signal.aborted) return;
      const key = `campus-form-editor:${identity.tenant_id}:${identity.subject}:${applicationId}`; setStorageKey(key);
      try {
        const saved = JSON.parse(window.sessionStorage.getItem(key) ?? 'null');
        if (saved && typeof saved.path === 'string' && saved.path.startsWith(base + '/') && ['POST', 'PUT'].includes(saved.method) && saved.body && typeof saved.body === 'object') setPending(saved);
      } catch {setError('无法读取本标签页重试记录。');}
    }).catch(cause => {if (!abort.signal.aborted) report(cause);}).finally(() => {if (!abort.signal.aborted) setBusy(false);});
    return () => abort.abort();
  }, [applicationId, base, initialToken, load, report]);
  function edit(update: (current: FormConfig) => FormConfig) {if (!content || busy || pending) return; setContent(update(clone(content))); setChanged(true); setPreview(''); setNotice('');}
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
  async function execute(command: Pending) {
    if (lock.current || !storageKey) return;
    lock.current = true; setBusy(true); setError(''); setNotice('');
    let committed = false;
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify(command)); setPending(command);
      await api(command.path, initialToken, command.body, undefined, command.method);
      committed = true; await load();
      window.sessionStorage.removeItem(storageKey); setPending(null);
      setNotice(`${command.label}已确认，已重新读取最新状态。`); callbacks.current.onUpdated?.();
    } catch (cause) {
      if (!committed && cause instanceof HTTPError && [403, 404, 409, 422].includes(cause.status)) {setPending(null); window.sessionStorage.removeItem(storageKey);}
      if (committed) setNotice(`${command.label}已成功，但读取最新状态失败；请保留原操作核对，避免重复提交。`);
      report(cause);
    } finally {lock.current = false; setBusy(false);}
  }
  async function trial() {
    if (!draft || !content || lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {
      const booleans = Object.fromEntries(content.model.fields.filter(field => field.data_type === 'boolean').map(field => [field.key, false]));
      await api(base + '/preview', initialToken, {expected_revision: draft.revision, content, values: {...booleans, ...values}}); setPreview('当前输入通过服务端表单校验。试填不会写登记记录。');
    } catch (cause) {setPreview(''); report(cause);} finally {lock.current = false; setBusy(false);}
  }
  if (!content || !draft) return <section className="panel"><p role="status">{busy ? '正在加载表单设计…' : '表单设计未加载。'}</p>{error && <p role="alert" className="error">{error}</p>}<button onClick={() => void load().catch(report)}>重新加载</button></section>;
  const controls = content.form.sections[0].fields; const byId = new Map(content.model.fields.map(field => [field.id, field]));
  return <section className="application-workbench embedded-config form-editor" aria-label="表单应用工作台">
    <header className="application-detail-heading"><div><h2>{content.application.name}</h2><p>{typeLabel} · 草稿修订 {draft.revision} · {draft.current_release_id ? '已有发布' : '未发布'}</p></div><div className="assistant-feature-actions">
      <button disabled={busy || !!pending || !changed} onClick={() => void execute({path: base + '/draft', method: 'PUT', label: '暂存', body: {expected_revision: draft.revision, content, idempotency_key: crypto.randomUUID()}})}>暂存表单</button>
      <button disabled={busy || !!pending || changed} onClick={() => {if (window.confirm(`发布“${content.application.name}”？将按当前访问范围开放表单。`)) void execute({path: base + '/publish', method: 'POST', label: '发布', body: {expected_revision: draft.revision, expected_release_id: draft.current_release_id, idempotency_key: crypto.randomUUID()}});}}>发布表单</button>
      {draft.current_release_id && <button disabled={busy || !!pending || changed} onClick={() => onUse?.(applicationId)}>使用应用 ↗</button>}
      <button disabled={busy || !!pending} onClick={() => {if (!changed || window.confirm('放弃未暂存修改并重新加载？')) void load().catch(report);}}>重新加载</button></div></header>
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {pending && <div role="status">上次操作结果待核对，保留原请求号重试不会重复写入。<button disabled={busy} onClick={() => void execute(pending)}>核对并重试原操作</button></div>}
    <div className="admin-toolbar" role="tablist" aria-label="表单配置">{[['basic', '基础与访问'], ['fields', '表单字段'], ...(renderWorkflow ? [['workflow', '审批流程']] : []), ['preview', '预览试填'], ['records', recordsLabel], ['history', '发布记录']].map(([id, label]) => <button role="tab" aria-selected={tab === id} key={id} onClick={() => setTab(id)}>{label}</button>)}</div>
    {tab === 'basic' && <fieldset disabled={busy || !!pending} className="management-fields"><legend>基础信息与访问范围</legend>
      <label>应用名称<input required maxLength={30} value={content.application.name} onChange={event => edit(next => {next.application.name = event.target.value; return next;})} /></label>
      <label>应用说明<textarea maxLength={160} value={content.application.description} onChange={event => edit(next => {next.application.description = event.target.value; return next;})} /></label>
      <label>分类<select value={content.application.category} onChange={event => edit(next => {next.application.category = event.target.value; return next;})}>{['学习服务', '校园事务', '校园生活'].map(category => <option key={category}>{category}</option>)}</select></label>
      <div>允许使用角色{[['student', '学生'], ['teacher', '教师'], ['config_admin', '配置管理员']].map(([id, label]) => <label key={id}><input type="checkbox" checked={content.application.visible_roles.includes(id)} onChange={event => edit(next => {next.application.visible_roles = event.target.checked ? [...next.application.visible_roles, id] : next.application.visible_roles.filter(role => role !== id); return next;})} />{label}</label>)}</div>
      <p className="muted">普通用户仅查看本人记录；本租户管理员可查看当前应用全部记录。发布前目录中不可使用。</p></fieldset>}
    {tab === 'fields' && <div><p className="muted">选择组件、调整顺序与宽度。字段标识固定，历史记录按提交时版本解释；首版最多30个字段、一个分组。</p>
      <button disabled={busy || !!pending || controls.length >= 30} onClick={addField}>添加字段</button>
      {controls.map((control, index) => {const field = byId.get(control.field_id)!; return <fieldset disabled={busy || !!pending} key={control.id} className="form-control-editor"><legend>{index + 1}. {control.label}</legend>
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
      </fieldset>;})}</div>}
    {tab === 'preview' && <form onSubmit={event => {event.preventDefault(); void trial();}}><p className="muted">试填使用当前草稿，不提交业务记录。可先验证合法输入，再检查缺项与范围限制。</p><FormFields config={content} values={values} disabled={busy || !!pending} onChange={(key, value) => setValues(previous => ({...previous, [key]: value}))} /><button disabled={busy || !!pending}>校验试填</button>{preview && <p role="status">{preview}</p>}</form>}
    {tab === 'workflow' && renderWorkflow?.()}
    {tab === 'records' && (renderRecords ? renderRecords(recordVersion) : <RecordBrowser token={initialToken} applicationId={applicationId} admin onExpired={() => callbacks.current.onSessionExpired?.()} version={recordVersion} />)}
    {tab === 'history' && <section className="panel"><h3>发布记录</h3>{!releases.length && <p>暂无发布记录。</p>}{releases.map(release => <article className="requirement-item" key={release.id}><p>{release.id} · {new Date(release.created_at).toLocaleString()}{release.id === draft.current_release_id ? ' · 当前发布' : ''}</p><button disabled={busy || !!pending || changed || release.id === draft.current_release_id} onClick={() => {if (window.confirm('恢复此历史表单为新的发布？现有登记记录保留原提交版本。')) void execute({path: base + '/rollback', method: 'POST', label: '恢复发布', body: {expected_revision: draft.revision, expected_release_id: draft.current_release_id, target_release_id: release.id, idempotency_key: crypto.randomUUID()}});}}>恢复此发布</button></article>)}</section>}
  </section>;
}
