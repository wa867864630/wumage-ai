import {useCallback, useEffect, useRef, useState} from 'react';
import {api, HTTPError, SessionExpired} from './api';
import {ConstructionOptions} from './ConstructionOptions';
import {ConstructionPreview} from './ConstructionPreview';

type Business = 'unknown' | 'course_query' | 'registration' | 'leave' | 'purchase' | 'venue' | 'visit' | 'event';
const labels: Record<Business, string> = {unknown: '待明确', course_query: '课程查询', registration: '简单登记', leave: '请假', purchase: '采购', venue: '场地预约', visit: '访问申请', event: '活动报名'};
type Requirement = {id: string; title: string; description: string; business_kind?: Business; status: 'pending_capability' | 'converted' | 'cancelled'; revision: number;
  target_application_id?: string | null; allowed_actions?: string[]; blocked_reason?: string | null;
  progress?: {draft_revision: number; release_id: string | null; unpublished_changes: boolean} | null};
type Confirmation = {command_id: string; command_key: string; summary: {requirement_title: string; type_id: string; type_version: string; type_digest: string;
  template_id: string; visible_roles: string[]; payload: {name: string; task_id?: string; expected_revision?: number; template_id?: string; source_application_id?: string; generation_key?: string}; source?: {application_id: string; revision: number; content: unknown}; generated?: {generation_key: string; content: unknown}}; expires_at: string; result?: {item: Requirement; application_id: string} | null};
type Pending = {path: string; method: string; body: Record<string, unknown>; key: string};
type Operation = {id: string; command_key?: string; action: string; status: string; result: {item?: Requirement; application_id?: string;
  generation_status?: string; summary?: string; message?: string; content?: unknown; questions?: string[]; ai_duration_ms?: number} | null; confirmation?: Confirmation | null};

export function RequirementPanel({token, scope, open, subject, onClose, onExpired, onDirtyChange, onConfigure, onUse}: {
  token: string; scope: string; open: boolean; subject: string | null; onClose: () => void; onExpired: () => void;
  onDirtyChange?: (dirty: boolean) => void; onConfigure?: (id: string, taskId: string) => void; onUse?: (id: string) => void;
}) {
  const storageKey = `campus-requirement-pending:${scope}`;
  const [restored] = useState<Pending | null>(() => {
    try {
      const value = JSON.parse(window.sessionStorage.getItem(storageKey) ?? 'null');
      if (typeof value?.path === 'string' && /^\/api\/v1\/assistant\/requirements(?:\/[a-zA-Z0-9/-]+)?$/.test(value.path)
          && ['POST', 'PUT'].includes(value.method) && typeof value.key === 'string' && value.body && typeof value.body === 'object' && !Array.isArray(value.body)) return value;
      if (value && typeof value.title === 'string' && typeof value.description === 'string' && typeof value.idempotency_key === 'string')
        return {path: '/api/v1/assistant/requirements', method: 'POST', body: value, key: value.idempotency_key};
    } catch { /* Browser state is not authoritative. */ }
    return null;
  });
  const [pending, setPending] = useState<Pending | null>(restored);
  const [items, setItems] = useState<Requirement[]>([]); const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1); const [filter, setFilter] = useState('all');
  const [loading, setLoading] = useState(false); const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [completedCreation, setCompletedCreation] = useState<Requirement | null>(null);
  const [busy, setBusy] = useState(false); const lock = useRef(false); const generation = useRef(0);
  const [editing, setEditing] = useState<Requirement | null>(null); const [formOpen, setFormOpen] = useState(false);
  const [title, setTitle] = useState(String(restored?.body.title ?? '')); const [description, setDescription] = useState(String(restored?.body.description ?? ''));
  const [business, setBusiness] = useState<Business>((restored?.body.business_kind as Business) ?? 'unknown');
  const [changed, setChanged] = useState(false); const [creating, setCreating] = useState<Requirement | null>(null);
  const [name, setName] = useState(''); const [template, setTemplate] = useState('course-standard');
  const [creationOptions, setCreationOptions] = useState<Record<string, string> | null>({});
  const [restoredGeneration, setRestoredGeneration] = useState('');
  const [recoverySession, setRecoverySession] = useState(0);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null); const [operations, setOperations] = useState<Operation[] | null>(null);
  const [audit, setAudit] = useState<{action: string; at: string}[] | null>(null);
  const callbacks = useRef({onExpired, onDirtyChange}); callbacks.current = {onExpired, onDirtyChange};
  const report = useCallback((cause: unknown) => {
    if (cause instanceof SessionExpired) callbacks.current.onExpired();
    else setError(cause instanceof Error ? cause.message : '操作失败，请重试。');
  }, []);
  const dirty = changed || !!pending || busy;
  useEffect(() => {callbacks.current.onDirtyChange?.(dirty);}, [dirty]);
  useEffect(() => () => callbacks.current.onDirtyChange?.(false), []);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {if (dirty) {event.preventDefault(); event.returnValue = '';}};
    window.addEventListener('beforeunload', guard); return () => window.removeEventListener('beforeunload', guard);
  }, [dirty]);
  const reload = useCallback(async () => {
    const own = ++generation.current; setLoading(true); setError('');
    try {
      const suffix = page === 1 && filter === 'all' ? '' : `?${new URLSearchParams({page: String(page), status: filter})}`;
      const result = await api<{items: Requirement[]; total?: number}>('/api/v1/assistant/requirements' + suffix, token);
      if (own === generation.current) {setItems(result.items); setTotal(result.total ?? result.items.length); setLoaded(true);}
    } catch (cause) {if (own === generation.current) report(cause);}
    finally {if (own === generation.current) setLoading(false);}
  }, [page, filter, token, report]);
  useEffect(() => {if (open) void reload(); return () => {generation.current++;};}, [open, reload]);
  useEffect(() => {
    if (!open || !subject || pending || changed || editing) return;
    setTitle(`${subject}应用建设需求`); setDescription(`希望建设${subject}应用。`);
    setBusiness((Object.entries(labels).find(([, label]) => label === subject)?.[0] as Business) ?? 'unknown'); setFormOpen(true);
  }, [open, subject]);
  function safeChange() {return !pending && !busy && (!changed || window.confirm('当前内容尚未保存，放弃修改并继续？'));}
  function newForm() {
    if (!safeChange()) return;
    setCompletedCreation(null);
    setEditing(null); setTitle(''); setDescription(''); setBusiness('unknown'); setFormOpen(true);
    setChanged(false); setConfirmation(null); setCreating(null); setAudit(null); setNotice('');
  }
  async function detail(item: Requirement) {
    if (!safeChange() || lock.current) return;
    lock.current = true; setBusy(true); const own = ++generation.current; setLoading(false); setError('');
    try {
      const result = await api<{item: Requirement; audit: {action: string; at: string}[]}>(`/api/v1/assistant/requirements/${item.id}`, token);
      if (own !== generation.current) return;
      setCompletedCreation(null);
      setEditing(result.item); setTitle(result.item.title); setDescription(result.item.description); setBusiness(result.item.business_kind ?? 'unknown');
      setAudit(result.audit); setChanged(false); setFormOpen(true); setCreating(null); setConfirmation(null);
    } catch (cause) {if (own === generation.current) report(cause);}
    finally {lock.current = false; setBusy(false);}
  }
  function accept(item: Requirement) {
    generation.current++; setLoading(false);
    setItems(previous => {
      if (filter !== 'all' && filter !== item.status) return previous.filter(row => row.id !== item.id);
      return previous.some(row => row.id === item.id) ? previous.map(row => row.id === item.id ? item : row) : [item, ...previous].slice(0, 20);
    }); setLoaded(true);
  }
  function clearPending() {window.sessionStorage.removeItem(storageKey); setPending(null);}
  async function run(command: Pending) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setNotice(''); setCompletedCreation(null); generation.current++; setLoading(false);
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify(command)); setPending(command);
      const result = await api<{item?: Requirement; command_id?: string; summary?: Confirmation['summary']}>(command.path, token, command.body, undefined, command.method);
      clearPending();
      if (result.command_id && result.summary) {setConfirmation(result as Confirmation); setChanged(false); setNotice('请核对创建摘要，确认后才创建应用。');}
      else if (result.item) {
        if (result.item.status === 'converted' && result.item.target_application_id) setCompletedCreation(result.item);
        accept(result.item); setEditing(null); setFormOpen(false); setTitle(''); setDescription(''); setChanged(false);
        setNotice(result.item.status === 'converted' ? '应用已创建。请继续配置；页面加载失败时可从原记录继续。' : result.item.status === 'cancelled' ? '需求已取消，已创建的应用仍保留。' : '建设需求已保存。');
        setConfirmation(null); setCreating(null);
      } void reload();
    } catch (cause) {
      if (cause instanceof HTTPError && [403, 404, 409, 422].includes(cause.status)) {
        clearPending(); if (cause.status === 409 && !command.path.includes('/confirmations/')) setConfirmation(null);
      } report(cause);
    } finally {lock.current = false; setBusy(false);}
  }
  async function save() {
    if (pending) {await run(pending); return;}
    const key = crypto.randomUUID();
    await run({key, path: '/api/v1/assistant/requirements' + (editing ? `/${editing.id}` : ''), method: editing ? 'PUT' : 'POST',
      body: {title: title.trim(), description: description.trim(), business_kind: business, idempotency_key: key,
        ...(editing ? {expected_revision: editing.revision} : {})}});
  }
  async function history() {
    try {const result = await api<{items: Operation[]}>('/api/v1/assistant/requirements/command-history', token); setOperations(result.items);}
    catch (cause) {report(cause);}
  }
  async function renewConfirmation() {
    if (!confirmation || pending || lock.current) return;
    const original = confirmation.summary.payload;
    if (!original.task_id || original.expected_revision === undefined) {
      setError('原摘要缺少需求绑定，请从需求列表重新选择创建方式。'); return;
    }
    lock.current = true; setBusy(true); setError('');
    const own = ++generation.current; setLoading(false); let next: Pending | null = null;
    try {
      const response = await api<{item: Requirement}>(`/api/v1/assistant/requirements/${original.task_id}`, token);
      if (own !== generation.current) return;
      if (response.item.revision !== original.expected_revision || !response.item.allowed_actions?.includes('prepare_creation')) {
        setError('原需求已变化或不再允许创建，请从最新需求继续。'); return;
      }
      const key = crypto.randomUUID();
      next = {key, path: `/api/v1/assistant/requirements/${original.task_id}/prepare`, method: 'POST', body: {
        name: original.name, expected_revision: original.expected_revision, template_id: confirmation.summary.template_id,
        idempotency_key: key,
        ...(original.source_application_id ? {source_application_id: original.source_application_id} : {}),
        ...(original.generation_key ? {generation_key: original.generation_key} : {})}};
    } catch (cause) {report(cause);}
    finally {lock.current = false; setBusy(false);}
    if (next) await run(next);
  }
  async function resumeGeneration(operation: Operation) {
    const original = operation.result?.item;
    if (!original || !operation.command_key || !safeChange() || lock.current) return;
    lock.current = true; setBusy(true); setError('');
    const own = ++generation.current; setLoading(false);
    try {
      const response = await api<{item: Requirement}>(`/api/v1/assistant/requirements/${original.id}`, token);
      if (own !== generation.current) return;
      const current = response.item;
      if (current.revision !== original.revision || !current.allowed_actions?.includes('prepare_creation')) {
        setNotice('原需求已变化或不再允许创建。历史提案仅供查看，请从最新需求选择创建方式。'); return;
      }
      setCreating(current); setRestoredGeneration(operation.command_key); setRecoverySession(value => value + 1); setCreationOptions(null);
      setCompletedCreation(null);
      setName(current.title.slice(0, 30)); setTemplate(current.business_kind === 'leave' ? 'leave-basic' : current.business_kind === 'registration' ? 'form-registration' : 'course-standard');
      setFormOpen(false); setConfirmation(null); setChanged(true);
      setNotice('已恢复原 AI 请求，请核对原结果并重新接受；不会重新调用模型。');
    } catch (cause) {report(cause);}
    finally {lock.current = false; setBusy(false);}
  }
  async function reconcile() {
    if (!pending || lock.current) return;
    try {
      const result = await api<{found: boolean; result?: {item: Requirement} | null; confirmation?: Confirmation | null}>(`/api/v1/assistant/requirements/commands/${encodeURIComponent(pending.key)}`, token);
      if (!result.found) {setNotice('暂未查到已提交结果；请保留原请求重试或稍后核对。'); return;}
      if (result.result?.item) {accept(result.result.item); setCompletedCreation(result.result.item.status === 'converted' && result.result.item.target_application_id ? result.result.item : null); clearPending(); setChanged(false); setFormOpen(false); setConfirmation(null); setCreating(null); setNotice('已核对原操作结果。'); void reload();}
      else if (result.confirmation) {setConfirmation(result.confirmation); clearPending(); setChanged(false); setNotice('创建摘要已找到，请核对后确认。');}
    } catch (cause) {report(cause);}
  }
  if (!open) return null;
  const readOnly = !!editing && editing.status !== 'pending_capability';
  return <section className="panel requirement-panel" aria-label="我的建设需求">
    <div className="admin-toolbar"><h2>我的建设需求</h2><button disabled={busy || !!pending} onClick={() => {if (safeChange()) {setChanged(false); setFormOpen(false); onClose();}}}>收起</button>
      <button disabled={busy} onClick={() => void reload()}>刷新需求</button><button disabled={busy || !!pending} onClick={newForm}>新建需求</button><button disabled={busy} onClick={() => void history()}>核对最近操作</button></div>
    <p className="muted">先新建并保存建设需求，再选择模板、复制已有设计、AI 描述或文本说明创建。需求仅本人可见；未安装类型先保存，等待相应能力接入。</p>
    {pending && <div className="management-notice" role="status">上次结果待核对，原参数已锁定。<button disabled={busy} onClick={() => void reconcile()}>核对原操作</button><button disabled={busy} onClick={() => void run(pending)}>使用原请求重试保存</button></div>}
    {(formOpen || (pending && 'title' in pending.body)) && <form onSubmit={event => {event.preventDefault(); void save();}}>
      <h3>{editing ? `需求详情 · 修订 ${editing.revision}` : '完善建设需求'}</h3>
      <fieldset disabled={busy || !!pending || readOnly} className="management-fields">
        <label>需求名称<input required maxLength={80} value={title} onChange={event => {setTitle(event.target.value); setChanged(true);}} /></label>
        <label>业务目标<select value={business} onChange={event => {setBusiness(event.target.value as Business); setChanged(true);}}>{Object.entries(labels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        <label>希望实现什么<textarea required maxLength={1000} value={description} onChange={event => {setDescription(event.target.value); setChanged(true);}} /></label>
      </fieldset>
      {!readOnly && !pending && <button disabled={busy || !title.trim() || !description.trim()}>{editing ? '保存需求修改' : '确认保存建设需求'}</button>}
      {audit && <details><summary>变更记录</summary>{audit.map((entry, index) => <p key={index}>{entry.action} · {entry.at}</p>)}</details>}
      {editing && <p className="muted">修改冲突时保留当前输入；可放弃修改后重新打开详情查看最新修订。</p>}
    </form>}
    {creating && !confirmation && <form onSubmit={event => {
      event.preventDefault(); const key = crypto.randomUUID(); void run({key, path: `/api/v1/assistant/requirements/${creating.id}/prepare`, method: 'POST',
        body: {name: name.trim(), template_id: template, expected_revision: creating.revision, idempotency_key: key, ...creationOptions}});
    }}><h3>选择创建方式 · {creating.title}</h3><p className="muted">{['registration', 'leave'].includes(creating.business_kind ?? '') ? '申请与登记记录按应用和用户隔离。' : '共用当前教务数据服务。'}新实例先形成草稿，发布前用户不可使用。</p>
      <fieldset disabled={busy || !!pending} className="management-fields"><label>应用名称<input required maxLength={30} value={name} onChange={event => {setName(event.target.value); setChanged(true);}} /></label>
        <label>模板<select value={template} onChange={event => {setTemplate(event.target.value); setChanged(true);}}>{creating.business_kind === 'leave' ? <option value="leave-basic">单级请假申请</option> : creating.business_kind === 'registration' ? <option value="form-registration">简单登记表</option> : <><option value="course-standard">标准课程查询</option><option value="course-compact">简洁课程查询</option></>}</select></label>
        <ConstructionOptions key={`${creating.id}:${creating.revision}:${restoredGeneration}:${recoverySession}`} initialGenerationKey={restoredGeneration || undefined} token={token} scope={scope} task={creating} disabled={busy || !!pending}
          onChange={value => {setCreationOptions(value); setChanged(true);}} /></fieldset><button disabled={busy || !!pending || !name.trim() || creationOptions === null}>生成创建确认</button></form>}
    {confirmation && <div className="management-notice" aria-label="创建确认摘要"><h3>确认创建应用</h3><p>建设需求：{confirmation.summary.requirement_title}</p><p>应用名称：{confirmation.summary.payload.name}</p>
      <p>类型：{confirmation.summary.type_id} · {confirmation.summary.type_version}</p><p>模板：{confirmation.summary.template_id === 'leave-basic' ? '单级请假申请' : confirmation.summary.template_id === 'form-registration' ? '简单登记表' : confirmation.summary.template_id === 'course-compact' ? '简洁课程查询' : '标准课程查询'}</p>
      <p>默认使用角色：{confirmation.summary.visible_roles.join('、')}。发布前可在配置页调整。</p><p className="muted">确认有效期至 {new Date(confirmation.expires_at).toLocaleString()}。仅创建草稿，不自动发布。</p>
      {(confirmation.summary.source || confirmation.summary.generated) && <>
        <p>创建来源：{confirmation.summary.source ? `复制应用 ${confirmation.summary.source.application_id} · 修订 ${confirmation.summary.source.revision}` : '已校验 AI 提案'}</p>
        <ConstructionPreview content={(confirmation.summary.source ?? confirmation.summary.generated)?.content} applicationName={confirmation.summary.payload.name}
          businessKind={confirmation.summary.type_id === 'platform.leave-approval' ? 'leave' : confirmation.summary.type_id === 'platform.form' ? 'registration' : 'course_query'} />
        <details><summary>查看确认使用的完整设计配置</summary><pre>{JSON.stringify((confirmation.summary.source ?? confirmation.summary.generated)?.content, null, 2)}</pre></details>
      </>}
      {Date.parse(confirmation.expires_at) <= Date.now() && <p role="status">创建确认已过期，请重新生成摘要并核对。原 AI 设计不会重新生成。</p>}
      <button disabled={busy || !!pending || Date.parse(confirmation.expires_at) <= Date.now()} onClick={() => void run({key: confirmation.command_key, path: `/api/v1/assistant/requirements/confirmations/${confirmation.command_id}/execute`, method: 'POST', body: {}})}>确认创建应用草稿</button>
      <button disabled={busy || !!pending} onClick={() => void renewConfirmation()}>重新生成创建确认</button>
      <button disabled={busy || !!pending} onClick={() => {setConfirmation(null); setCreating(null);}}>暂不创建</button></div>}
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {completedCreation && <section className="management-notice" aria-label="应用创建结果">
      <h3>应用已创建，下一步配置并发布</h3><p>原需求：{completedCreation.title}</p>
      <p>应用编号：{completedCreation.target_application_id}</p>
      <p className="muted">创建不会自动发布。请进入关联应用，核对基础信息和访问范围、完成设计与预览，再明确发布。</p>
      {onConfigure && completedCreation.allowed_actions?.includes('configure') && <button disabled={busy || !!pending} onClick={() => {
        if (safeChange()) {setChanged(false); onConfigure(completedCreation.target_application_id!, completedCreation.id);}
      }}>继续配置并发布</button>}
    </section>}
    <label>筛选需求<select value={filter} disabled={busy} onChange={event => {setFilter(event.target.value); setPage(1);}}><option value="all">全部</option><option value="pending_capability">待处理</option><option value="converted">已关联应用</option><option value="cancelled">已取消</option></select></label>
    {loading && <p role="status">正在加载建设需求…</p>}{!loading && loaded && !error && !items.length && <p className="muted">暂无已保存需求。可以先说“我要请假”或新建需求。</p>}
    {items.map(item => <article key={item.id} className="requirement-item"><strong>{item.title}</strong><span className="status-tag">{item.status === 'cancelled' ? '已取消' : item.target_application_id ? item.progress?.release_id ? '已发布' : '待配置发布' : item.allowed_actions?.includes('prepare_creation') ? '可创建应用' : '等待能力接入'}</span>
      <p>{item.description}</p><small>需求 {item.id.slice(0, 8)} · 修订 {item.revision} · {labels[item.business_kind ?? 'unknown']}</small>
      {item.blocked_reason && <p className="muted">{item.blocked_reason}</p>}{item.progress?.release_id && <p>发布版本 {item.progress.release_id}{item.progress.unpublished_changes ? ' · 仍有未发布修改' : ' · 当前草稿与发布一致'}</p>}
      <div className="assistant-feature-actions"><button disabled={busy || !!pending} onClick={() => void detail(item)}>{item.status === 'pending_capability' ? '查看 / 编辑需求' : '查看需求详情'}</button>
        {item.allowed_actions?.includes('prepare_creation') && <button disabled={busy || !!pending} onClick={() => {if (!safeChange()) return; setCompletedCreation(null); setCreating(item); setRestoredGeneration(''); setCreationOptions({}); setName(item.title.slice(0, 30)); setTemplate(item.business_kind === 'leave' ? 'leave-basic' : item.business_kind === 'registration' ? 'form-registration' : 'course-standard'); setFormOpen(false); setConfirmation(null); setChanged(false);}}>选择方式创建</button>}
        {item.target_application_id && item.allowed_actions?.includes('configure') && <button disabled={busy || !!pending} onClick={() => {if (safeChange()) {setChanged(false); onConfigure?.(item.target_application_id!, item.id);}}}>继续配置应用</button>}
        {item.progress?.release_id && item.target_application_id && <button disabled={busy || !!pending} onClick={() => {if (safeChange()) {setChanged(false); onUse?.(item.target_application_id!);}}}>返回应用使用</button>}
        {item.status !== 'cancelled' && <button disabled={busy || !!pending} onClick={() => {if (!safeChange() || !window.confirm(`取消“${item.title}”？记录及已创建应用均保留。`)) return;
          void run({key: `cancel-${item.id}-${item.revision}`, path: `/api/v1/assistant/requirements/${item.id}/cancel`, method: 'POST', body: {expected_revision: item.revision}});
        }}>取消需求</button>}</div></article>)}
    <div className="admin-toolbar"><button disabled={loading || busy || page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页 · 共 {total} 条</span><button disabled={loading || busy || page * 20 >= total} onClick={() => setPage(page + 1)}>下一页</button></div>
    {operations && <details open><summary>最近50次操作 · 仅本人</summary>{operations.length ? operations.map(operation => <div className="requirement-item" key={operation.id}><p>{{generate: 'AI 生成设计', create: '创建应用', edit: '修改需求', cancel: '取消需求', save: '保存需求'}[operation.action] ?? '操作'} · {({succeeded: '已成功', expired: '已过期', needs_clarification: '需要补充需求', failed: '已失败', unknown: '结果待核对', awaiting_confirmation: operation.action === 'generate' ? '生成结果待核对' : '等待确认'} as Record<string, string>)[operation.status] ?? '结果待核对'} · {operation.result?.item?.title ?? operation.confirmation?.summary.requirement_title}</p>
      {operation.action === 'generate' && operation.result && <details><summary>查看历史生成结果</summary><p>{operation.result.summary ?? operation.result.message}</p>
        {operation.result.ai_duration_ms !== undefined && <p>AI 用时：{(operation.result.ai_duration_ms / 1000).toFixed(1)} 秒</p>}
        {operation.result.questions?.map((question, index) => <p key={index}>{question}</p>)}
        {operation.result.content != null && <><ConstructionPreview content={operation.result.content} businessKind={operation.result.item?.business_kind} /><pre>{JSON.stringify(operation.result.content, null, 2)}</pre></>}
        <p className="muted">历史结果只供核对，不代表已创建或发布。原描述和文档原文不在此恢复。</p></details>}
      {operation.action === 'generate' && operation.command_key && operation.result?.item && <button disabled={busy || !!pending} onClick={() => void resumeGeneration(operation)}>继续核对此 AI 请求</button>}
      {operation.result?.item && <button disabled={busy || !!pending} onClick={() => void detail(operation.result!.item!)}>查看当前结果</button>}{!operation.result && operation.confirmation && <button disabled={busy || !!pending} onClick={() => {if (safeChange()) {setConfirmation(operation.confirmation!); setCreating(null); setFormOpen(false);}}}>查看原创建摘要</button>}</div>) : <p>暂无持久操作记录。旧需求仍可从列表查看。</p>}</details>}
  </section>;
}
