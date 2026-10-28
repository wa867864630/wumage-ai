import {useCallback, useEffect, useRef, useState} from 'react';
import {api, HTTPError, SessionExpired} from './api';
import {FormRuntime, formatFieldValue, type FormConfig} from './FormRuntime';

type Task = {id: string; applicant_id: string; approver_id: string; status: 'pending' | 'approved' | 'rejected' | 'withdrawn'; revision: number; updated_at: string; last_reason: string | null};
type Command = {recordId: string; body: {action: string; expected_revision: number; reason: string; idempotency_key: string}};
const labels = {pending: '待审批', approved: '已通过', rejected: '已拒绝', withdrawn: '已撤回'};
const actionLabels: Record<string, string> = {approved: '通过', rejected: '拒绝', withdrawn: '撤回', reassign_to_me: '由我接管'};

export function ApprovalWorkList({token, applicationId, admin = false, onExpired, onDirtyChange, version = 0, initialScope}: {
  token: string; applicationId: string; admin?: boolean; onExpired: () => void; onDirtyChange?: (dirty: boolean) => void; version?: number; initialScope?: string;
}) {
  const base = `/api/v1/applications/${applicationId}/approvals`;
  const [scope, setScope] = useState(initialScope ?? (admin ? 'assigned' : 'mine')); const [status, setStatus] = useState('all'); const [page, setPage] = useState(1);
  const [identity, setIdentity] = useState<{subject: string; tenant_id: string; roles: string[]} | null>(null);
  const [storageKey, setStorageKey] = useState(''); const [pending, setPending] = useState<Command | null>(null);
  const [items, setItems] = useState<Task[]>([]); const [total, setTotal] = useState(0); const [loading, setLoading] = useState(false); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [reasons, setReasons] = useState<Record<string, string>>({});
  const [detail, setDetail] = useState<{id: string; content: FormConfig; values: Record<string, unknown>; duration_days: number; workflow: Task; events: {action: string; reason: string; actor_id: string; created_at: string}[]} | null>(null);
  const alive = useRef(true); const epoch = useRef(0); const lock = useRef(false); const callbacks = useRef({onExpired, onDirtyChange}); callbacks.current = {onExpired, onDirtyChange};
  const report = useCallback((cause: unknown) => {if (!alive.current) return; if (cause instanceof SessionExpired) callbacks.current.onExpired(); else setError(cause instanceof Error ? cause.message : '审批操作失败');}, []);
  useEffect(() => {alive.current = true; return () => {alive.current = false; epoch.current++;};}, []);
  useEffect(() => {callbacks.current.onDirtyChange?.(busy || !!pending);}, [busy, pending]);
  useEffect(() => () => callbacks.current.onDirtyChange?.(false), []);
  useEffect(() => {
    const abort = new AbortController();
    api<NonNullable<typeof identity>>('/api/v1/me', token, undefined, abort.signal).then(current => {
      if (abort.signal.aborted) return; setIdentity(current);
      const key = `campus-approval-pending:${current.tenant_id}:${current.subject}:${applicationId}`; setStorageKey(key);
      try {const saved = JSON.parse(window.sessionStorage.getItem(key) ?? 'null'); if (saved && typeof saved.recordId === 'string' && saved.body?.idempotency_key && ['approved', 'rejected', 'withdrawn', 'reassign_to_me'].includes(saved.body.action)) setPending(saved);} catch {setError('无法读取待核对审批操作。');}
    }).catch(cause => {if (!abort.signal.aborted) report(cause);}); return () => abort.abort();
  }, [applicationId, token, report]);
  const load = useCallback(async (signal?: AbortSignal) => {
    const own = ++epoch.current; setLoading(true); setError('');
    try {
      const result = await api<{items: Task[]; total: number}>(`${base}?${new URLSearchParams({scope, status, page: String(page)})}`, token, undefined, signal);
      if (alive.current && !signal?.aborted && own === epoch.current) {setItems(result.items); setTotal(result.total); setDetail(null);}
    } finally {if (alive.current && !signal?.aborted && own === epoch.current) setLoading(false);}
  }, [base, token, scope, status, page]);
  useEffect(() => {const abort = new AbortController(); void load(abort.signal).catch(cause => {if (!abort.signal.aborted) report(cause);}); return () => {epoch.current++; abort.abort();};}, [load, report, version]);
  useEffect(() => {const guard = (event: BeforeUnloadEvent) => {if (busy || pending) {event.preventDefault(); event.returnValue = '';}}; window.addEventListener('beforeunload', guard); return () => window.removeEventListener('beforeunload', guard);}, [busy, pending]);
  async function open(id: string) {
    if (lock.current) return; const own = ++epoch.current;
    try {const result = await api<NonNullable<typeof detail>>(`${base}/${id}`, token); if (alive.current && own === epoch.current) setDetail(result);} catch (cause) {if (own === epoch.current) report(cause);}
  }
  async function execute(command: Command) {
    if (lock.current || !storageKey) return; lock.current = true; setBusy(true); setError(''); setNotice(''); let committed = false;
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify(command)); setPending(command);
      await api(`${base}/${command.recordId}/transition`, token, command.body); committed = true;
      await load(); window.sessionStorage.removeItem(storageKey); setPending(null); setNotice('原审批操作已确认，列表显示当前真实状态。');
    } catch (cause) {
      if (!committed && cause instanceof HTTPError && [403, 404, 409, 422].includes(cause.status)) {setPending(null); window.sessionStorage.removeItem(storageKey);}
      if (committed) setNotice('审批操作已成功，读取最新状态失败；请保留原请求核对。'); report(cause);
    } finally {lock.current = false; if (alive.current) setBusy(false);}
  }
  function act(task: Task, action: string) {
    if (busy || pending) return; const reason = (reasons[task.id] ?? '').trim();
    if (['rejected', 'reassign_to_me'].includes(action) && !reason) {setError('拒绝或接管时请填写原因。'); return;}
    if (!window.confirm(`对申请 ${task.id.slice(0, 8)} 执行“${actionLabels[action]}”？操作将保存并留痕。`)) return;
    void execute({recordId: task.id, body: {action, expected_revision: task.revision, reason, idempotency_key: crypto.randomUUID()}});
  }
  const canManage = admin && identity?.roles.includes('config_admin');
  return <section className="panel approval-work" aria-label="申请与审批"><h3>申请与审批</h3><p className="muted">结果保存在申请记录，刷新即可核对。申请人不能审批或接管本人申请。</p>
    <div className="admin-toolbar"><label>办理范围<select value={scope} disabled={busy || !!pending} onChange={event => {setScope(event.target.value); setPage(1);}}><option value="mine">我的申请</option>{canManage && <><option value="assigned">分配给我的审批</option><option value="all">本应用全部申请</option></>}</select></label>
      <label>状态<select value={status} disabled={busy} onChange={event => {setStatus(event.target.value); setPage(1);}}><option value="all">全部</option>{Object.entries(labels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><button disabled={busy} onClick={() => void load().catch(report)}>刷新审批状态</button></div>
    {loading && <p role="status">正在加载申请…</p>}{error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {pending && <div role="status">审批结果待核对，原对象与请求号已保留。<button disabled={busy} onClick={() => void execute(pending)}>核对并重试原审批</button></div>}
    {!loading && !error && !items.length && <p>当前范围暂无申请。</p>}
    {items.map(task => <article className="requirement-item" key={task.id}><strong>申请 {task.id.slice(0, 8)}</strong><span className="status-tag">{labels[task.status] ?? '状态待核对'}</span><p>申请人：{task.applicant_id} · 审批管理员：{task.approver_id} · 修订 {task.revision}</p>
      {task.last_reason && <p>处理说明：{task.last_reason}</p>}<button disabled={loading || busy || !!pending} onClick={() => void open(task.id)}>查看申请详情</button>
      {task.status === 'pending' && <div className="assistant-feature-actions"><label>处理说明<textarea maxLength={1000} disabled={busy || !!pending} value={reasons[task.id] ?? ''} onChange={event => setReasons(previous => ({...previous, [task.id]: event.target.value}))} placeholder="拒绝或接管时必填" /></label>
        {task.applicant_id === identity?.subject && <button disabled={busy || !!pending} onClick={() => act(task, 'withdrawn')}>撤回申请</button>}
        {canManage && identity && task.applicant_id !== identity.subject && (task.approver_id === identity.subject ? <><button disabled={busy || !!pending} onClick={() => act(task, 'approved')}>通过申请</button><button disabled={busy || !!pending} onClick={() => act(task, 'rejected')}>拒绝申请</button></> : <button disabled={busy || !!pending} onClick={() => act(task, 'reassign_to_me')}>由我接管审批</button>)}</div>}</article>)}
    <div className="admin-toolbar"><button disabled={loading || busy || page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页 · 共 {total} 条</span><button disabled={loading || busy || page * 20 >= total} onClick={() => setPage(page + 1)}>下一页</button></div>
    {detail && <div aria-label="申请详情"><h4>申请详情 · {labels[detail.workflow.status]}</h4><p>请假 {detail.duration_days} 个自然日（含首尾），按提交时版本展示。</p><dl>{detail.content.model.fields.map(field => <div key={field.id}><dt>{field.label}</dt><dd>{formatFieldValue(field, detail.values[field.key])}</dd></div>)}</dl><h4>最近100条处理记录</h4>{detail.events.map((event, index) => <p key={index}>{actionLabels[event.action] ?? event.action} · {event.actor_id} · {event.reason} · {new Date(event.created_at).toLocaleString()}</p>)}</div>}
  </section>;
}

export function ApprovalRuntime({token, applicationId, scope, admin, onExpired, onDirtyChange, mode = 'fill', draftId, onPauseReady}: {
  token: string; applicationId: string; scope: string; admin: boolean; onExpired: () => void; onDirtyChange: (dirty: boolean) => void; mode?: 'fill' | 'records';
  draftId?: string; onPauseReady?: (pause: (() => Promise<boolean>) | null) => void;
}) {
  const [formDirty, setFormDirty] = useState(false); const [taskDirty, setTaskDirty] = useState(false); const report = useRef(onDirtyChange); report.current = onDirtyChange;
  const formPause = useRef<(() => Promise<boolean>) | null>(null); const taskPending = useRef(taskDirty); taskPending.current = taskDirty;
  const registerFormPause = useCallback((pause: (() => Promise<boolean>) | null) => {formPause.current = pause;}, []);
  useEffect(() => {onPauseReady?.(async () => !taskPending.current && (await formPause.current?.() ?? true)); return () => onPauseReady?.(null);}, [onPauseReady]);
  useEffect(() => {report.current(formDirty || taskDirty);}, [formDirty, taskDirty]); useEffect(() => () => report.current(false), []);
  return <div><p className="management-notice">按自然日请假，开始/结束日期均包含在内；由本次发布时已认证的管理员审批。待审时可撤回，处理后保留记录。</p>{taskDirty && <p role="status">审批操作正在处理或结果待核对，请先核对原操作再切换任务。</p>}<FormRuntime token={token} applicationId={applicationId} scope={scope} noun="申请" mode={mode} onExpired={onExpired} onDirtyChange={setFormDirty} draftId={draftId} onPauseReady={registerFormPause}
    renderRecords={version => <ApprovalWorkList token={token} applicationId={applicationId} admin={admin} onExpired={onExpired} onDirtyChange={setTaskDirty} version={version} />} /></div>;
}
