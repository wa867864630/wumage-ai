import {FormFieldDesigner} from './FormFieldDesigner';
import {ApplicationEditorHeader, ApplicationEditorActionBar, ApplicationConfigHeading} from './ApplicationEditorChrome';
import {useCallback, useEffect, useRef, useState, type ReactNode} from 'react';
import {api, HTTPError, SessionExpired} from './api';
import {ApplicationSettingsFields, type ApplicationSettingsValue} from './ApplicationSettingsFields';
import {ApplicationConfigTabs} from './ApplicationConfigTabs';
import {ApprovalWorkList} from './ApprovalRuntime';
import {SingleWorkflowSettings, type SingleWorkflow} from './SingleWorkflowSettings';
import {ManagementDialog} from './ManagementDialog';
import {FormChangeReview} from './ApplicationChangeReview';
type UnifiedConfig = FormConfig & {workflow?: SingleWorkflow};
import {FormFields, RecordBrowser, type FormConfig} from './FormRuntime';

type Draft = {revision: number; current_release_id: string | null; content: UnifiedConfig};
type Release = {id: string; created_at: string; operation: string};
type Pending = {path: string; body: Record<string, unknown>; method: string; label: string};
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));

export function UnifiedFormEditor({initialToken = '', applicationId = '', onSessionExpired, onDirtyChange, onUpdated, onUse, onNavigationLock, initialTab = 'basic', typeLabel = '简单登记', recordsLabel = '登记记录', protectedFields = [], renderRecords, renderWorkflow}: {
  initialToken?: string; applicationId?: string; embedded?: boolean; onSessionExpired?: () => void; onDirtyChange?: (value: boolean) => void;
  onUpdated?: () => void; onUse?: (id: string) => void; onNavigationLock?: (locked: boolean) => void; initialTab?: 'basic' | 'page' | 'questions' | 'ai' | 'history';
  typeLabel?: string; recordsLabel?: string; protectedFields?: string[]; renderRecords?: (version: number) => ReactNode; renderWorkflow?: () => ReactNode;
} = {}) {
  const base = `/api/v1/admin/applications/${applicationId}/form`;
  const [draft, setDraft] = useState<Draft | null>(null); const [content, setContent] = useState<UnifiedConfig | null>(null);
  const [releases, setReleases] = useState<Release[]>([]); const [tab, setTab] = useState(initialTab === 'page' ? 'fields' : initialTab === 'questions' ? 'records' : initialTab === 'history' ? 'history' : 'basic');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [changed, setChanged] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null); const [storageKey, setStorageKey] = useState(''); const [recordVersion, setRecordVersion] = useState(0);
  const [taskDirty, setTaskDirty] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [validated, setValidated] = useState(false);
  const [published, setPublished] = useState<UnifiedConfig | null>(null);
  const blocked = busy || !!pending || taskDirty;
  const [values, setValues] = useState<Record<string, unknown>>({}); const [preview, setPreview] = useState(''); const lock = useRef(false);
  const callbacks = useRef({onSessionExpired, onDirtyChange, onNavigationLock, onUpdated}); callbacks.current = {onSessionExpired, onDirtyChange, onNavigationLock, onUpdated};
  const alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const report = useCallback((cause: unknown) => {if (!alive.current) return; if (cause instanceof SessionExpired) callbacks.current.onSessionExpired?.(); else setError(cause instanceof Error ? cause.message : '操作失败');}, []);
  const dirty = changed || !!pending || busy || taskDirty;
  useEffect(() => {callbacks.current.onDirtyChange?.(dirty); callbacks.current.onNavigationLock?.(blocked || taskDirty);}, [dirty, busy, pending, taskDirty]);
  useEffect(() => () => {callbacks.current.onDirtyChange?.(false); callbacks.current.onNavigationLock?.(false);}, []);
  useEffect(() => {const guard = (event: BeforeUnloadEvent) => {if (dirty) {event.preventDefault(); event.returnValue = '';}}; window.addEventListener('beforeunload', guard); return () => window.removeEventListener('beforeunload', guard);}, [dirty]);
  const load = useCallback(async (signal?: AbortSignal) => {
    const [current, versions] = await Promise.all([api<Draft>(base, initialToken, undefined, signal), api<Release[]>(base + '/releases', initialToken, undefined, signal)]);
    const previous = current.current_release_id ? await api<{id: string; content: UnifiedConfig}>(`${base}/releases/${encodeURIComponent(current.current_release_id)}`, initialToken, undefined, signal) : null;
    if (previous && previous.id !== current.current_release_id) throw new Error('发布基线与当前应用不一致，请重新加载。');
    if (signal?.aborted || !alive.current) return;
    setPublished(previous?.content ?? null);
    setDraft(current); setContent(clone(current.content)); setReleases(versions); setChanged(false); setPreview(''); setValidated(false); setRecordVersion(value => value + 1);
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
  function edit(update: (current: UnifiedConfig) => UnifiedConfig) {if (!content || blocked) return; setContent(update(clone(content))); setChanged(true); setPreview(''); setValidated(false); setNotice('');}
  async function execute(command: Pending) {
    if (lock.current || taskDirty || !storageKey) return;
    lock.current = true; setBusy(true); setError(''); setNotice('');
    let committed = false;
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify(command)); setPending(command);
      await api(command.path, initialToken, command.body, undefined, command.method);
      committed = true; await load(); setPublishOpen(false);
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
  async function validateDesign() {
    if (!draft || !content || lock.current || changed || taskDirty) return;
    lock.current = true; setBusy(true); setError(''); setValidated(false);
    try {const result = await api<{valid: boolean}>(base + '/validate', initialToken, {expected_revision: draft.revision, content}); setValidated(result.valid === true);}
    catch (cause) {report(cause);} finally {lock.current = false; setBusy(false);}
  }
  if (!content || !draft) return <section className="panel"><p role="status">{busy ? '正在加载表单设计…' : '表单设计未加载。'}</p>{error && <p role="alert" className="error">{error}</p>}<button onClick={() => void load().catch(report)}>重新加载</button></section>;
  return <section className="application-workbench embedded-config application-detail unified-config-editor" aria-label="统一应用配置">
    <ApplicationEditorHeader name={content.application.name} typeLabel={typeLabel} publicationLabel={draft.current_release_id ? '已发布' : '未发布'}
      dirty={changed} unpublished={!published || JSON.stringify(draft.content) !== JSON.stringify(published)} actions={<>
      {draft.current_release_id && <button className="quiet-button" disabled={blocked || changed} onClick={() => onUse?.(applicationId)}>使用应用 ↗</button>}
      <button className="quiet-button" disabled={blocked} onClick={() => {if (!changed || window.confirm('放弃未暂存修改并重新加载？')) void load().catch(report);}}>重新加载</button></>} />
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {pending && <div role="status">上次操作结果待核对，保留原请求号重试不会重复写入。<button disabled={busy} onClick={() => void execute(pending)}>核对并重试原操作</button></div>}
    <ApplicationConfigTabs disabled={taskDirty} items={([['basic', '基础信息'], ['fields', '页面与表单'], ...(content.workflow ? [['workflow', '流程设计']] : []), ['preview', '预览与校验'], ['records', '业务记录'], ['history', '发布记录']] as [string, string][])} value={tab} onChange={setTab} />
    <div className="application-config-body" id={`config-panel-${tab}`} role="tabpanel" aria-labelledby={`config-tab-${tab}`}>
    {tab === 'basic' && <><ApplicationConfigHeading title="应用基本信息与访问范围" description="设置应用对外展示的信息，以及哪些角色可以使用。" />
      <ApplicationSettingsFields value={content.application as ApplicationSettingsValue} disabled={blocked} onChange={application => edit(next => {next.application = application; return next;})} />
      <p className="muted">普通用户仅查看本人记录；管理员按原应用权限查看业务记录。发布前不可办理。</p></>}
    {tab === 'fields' && <FormFieldDesigner config={content} disabled={blocked} protectedFields={protectedFields} onChange={edit} />}
    {tab === 'preview' && <form onSubmit={event => {event.preventDefault(); void trial();}}><ApplicationConfigHeading title="预览与校验" description="试填使用当前草稿，不提交业务记录。可先验证合法输入，再检查缺项与范围限制。" /><FormFields config={content} values={values} disabled={blocked} onChange={(key, value) => setValues(previous => ({...previous, [key]: value}))} /><button disabled={blocked}>校验试填</button>{preview && <p role="status">{preview}</p>}</form>}
    {tab === 'workflow' && content.workflow && <SingleWorkflowSettings value={content.workflow} disabled={blocked} onChange={workflow => edit(next => {next.workflow = workflow; return next;})} />}
    {tab === 'records' && (content.workflow ? <ApprovalWorkList token={initialToken} applicationId={applicationId} admin initialScope="all" version={recordVersion} onDirtyChange={setTaskDirty} onExpired={() => callbacks.current.onSessionExpired?.()} /> : renderRecords ? renderRecords(recordVersion) : <RecordBrowser token={initialToken} applicationId={applicationId} admin onExpired={() => callbacks.current.onSessionExpired?.()} version={recordVersion} />)}
    {tab === 'history' && <section className="management-releases"><ApplicationConfigHeading title="发布记录" description="查看当前版本与历史发布。恢复会创建新的发布记录，已有业务保留原提交版本。" />{!releases.length && <p>暂无发布记录。</p>}{releases.map(release => <article className="requirement-item" key={release.id}><p>{release.id} · {new Date(release.created_at).toLocaleString()}{release.id === draft.current_release_id ? ' · 当前发布' : ''}</p><button disabled={blocked || changed || release.id === draft.current_release_id} onClick={() => {if (window.confirm('恢复此历史表单为新的发布？现有登记记录保留原提交版本。')) void execute({path: base + '/rollback', method: 'POST', label: '恢复发布', body: {expected_revision: draft.revision, expected_release_id: draft.current_release_id, target_release_id: release.id, idempotency_key: crypto.randomUUID()}});}}>恢复此发布</button></article>)}</section>}
    </div>
    <ApplicationEditorActionBar dirty={changed} unpublished={!published || JSON.stringify(draft.content) !== JSON.stringify(published)} pending={!!pending} revision={draft.revision}
      section={({basic: '基础信息', fields: '页面与表单', workflow: '流程设计', preview: '预览与校验', records: '业务记录', history: '发布记录'} as Record<string, string>)[tab]}>
      <button className="quiet-button" disabled={blocked || !changed} onClick={() => void execute({path: base + '/draft', method: 'PUT', label: '暂存', body: {expected_revision: draft.revision, content, idempotency_key: crypto.randomUUID()}})}>暂存更改</button>
      <button disabled={blocked} onClick={() => setPublishOpen(true)}>试跑发布</button></ApplicationEditorActionBar>
    {publishOpen && <ManagementDialog title="试跑与发布" busy={busy || !!pending} onClose={() => setPublishOpen(false)}>
      <p>应用：{content.application.name} · 草稿修订 {draft.revision}</p>
      <FormChangeReview before={published} after={content} />
      <p>请先在“预览与校验”核对表单，确认字段、访问范围{content.workflow ? '和单级流程' : ''}。字段与流程保留历史版本；访问范围按本次发布生效。</p>
      {changed && <p role="status">当前有未暂存修改，请先返回暂存。</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {pending && <button disabled={busy} onClick={() => void execute(pending)}>核对本次发布结果</button>}
      <button disabled={blocked || changed} onClick={() => void validateDesign()}>校验当前草稿</button>
      {validated && <p role="status">当前设计通过服务端校验。确认发布后新办理将使用此版本。</p>}
      <button disabled={blocked || changed || !validated} onClick={() => void execute({path: base + '/publish', method: 'POST', label: '发布', body: {expected_revision: draft.revision, expected_release_id: draft.current_release_id, idempotency_key: crypto.randomUUID()}})}>确认发布</button>
      <button disabled={busy || !!pending} onClick={() => setPublishOpen(false)}>返回配置</button>
    </ManagementDialog>}
  </section>;
}

export function UnifiedApprovalEditor(props: Parameters<typeof UnifiedFormEditor>[0]) {
  return <UnifiedFormEditor {...props} typeLabel="单级请假审批" protectedFields={["start_date", "end_date", "reason"]} />;
}
