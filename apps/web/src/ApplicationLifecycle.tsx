import {useEffect, useRef, useState} from 'react';
import {api, HTTPError, SessionExpired} from './api';
import {RecordBrowser} from './FormRuntime';

export type Lifecycle = {status: 'active' | 'disabled' | 'archived'; revision: number};
const labels = {active: '启用', disabled: '停用', archived: '归档'};
type Command = {status: string; expected_revision: number; reason: string; idempotency_key: string};

export function ApplicationLifecycle({token, applicationId, value, disabled, onUpdated, onDirtyChange, onLock, onExpired}: {
  token: string; applicationId: string; value?: Lifecycle; disabled: boolean; onUpdated: () => void;
  onDirtyChange: (value: boolean) => void; onLock: (value: boolean) => void; onExpired: () => void;
}) {
  const current = value ?? {status: 'active', revision: 0};
  const [reason, setReason] = useState(''); const [pending, setPending] = useState<Command | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [storageKey, setStorageKey] = useState('');
  const lock = useRef(false); const callbacks = useRef({onUpdated, onDirtyChange, onLock, onExpired}); callbacks.current = {onUpdated, onDirtyChange, onLock, onExpired};
  useEffect(() => {
    const abort = new AbortController();
    void api<{tenant_id: string; subject: string}>('/api/v1/me', token, undefined, abort.signal).then(identity => {
      if (abort.signal.aborted || !identity.tenant_id || !identity.subject) return;
      const key = `campus-lifecycle-pending:${identity.tenant_id}:${identity.subject}:${applicationId}`; setStorageKey(key);
      try {
        const saved = JSON.parse(window.sessionStorage.getItem(key) ?? 'null');
        if (saved && ['active', 'disabled', 'archived'].includes(saved.status) && typeof saved.reason === 'string'
            && Number.isInteger(saved.expected_revision) && typeof saved.idempotency_key === 'string') {setPending(saved); setReason(saved.reason);}
      } catch { /* Local state never grants authority. */ }
    }).catch(cause => {if (!abort.signal.aborted) {if (cause instanceof SessionExpired) callbacks.current.onExpired(); else setError(cause.message);}});
    return () => abort.abort();
  }, [token, applicationId]);
  useEffect(() => {callbacks.current.onDirtyChange(!!reason.trim() || !!pending || busy); callbacks.current.onLock(!!pending || busy);}, [reason, pending, busy]);
  useEffect(() => () => {callbacks.current.onDirtyChange(false); callbacks.current.onLock(false);}, []);
  async function run(command: Command) {
    if (lock.current || !storageKey) return; lock.current = true; setBusy(true); setPending(command); setError('');
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify(command));
      const result = await api<Lifecycle>(`/api/v1/admin/applications/${applicationId}/lifecycle`, token, command);
      window.sessionStorage.removeItem(storageKey);
      setNotice(`应用已${labels[result.status]}。`); setPending(null); setReason(''); callbacks.current.onUpdated();
    } catch (cause) {
      if (cause instanceof SessionExpired) callbacks.current.onExpired();
      else {
        if (cause instanceof HTTPError && [403, 404, 409, 422].includes(cause.status)) {setPending(null); window.sessionStorage.removeItem(storageKey); callbacks.current.onUpdated();}
        setError(cause instanceof Error ? cause.message : '状态变更失败');
      }
    } finally {lock.current = false; setBusy(false);}
  }
  function change(status: string) {
    if (!reason.trim() || !window.confirm(`确认${labels[status as keyof typeof labels]}此应用？停用会停止新业务；归档后设计只读，已有记录保留。`)) return;
    void run({status, expected_revision: current.revision, reason: reason.trim(), idempotency_key: crypto.randomUUID()});
  }
  return <section className="management-notice" aria-label="应用状态管理"><strong>应用状态：{labels[current.status]} · 修订 {current.revision}</strong>
    <details><summary>启用、停用与归档</summary><p>停用后不接受新查询和提交，已有记录可查看，待审批申请继续办理。归档前必须处理完待审批申请；恢复后先处于停用状态。</p>
      <label>变更说明<input maxLength={500} value={reason} disabled={disabled || busy || !!pending} onChange={event => setReason(event.target.value)} /></label>
      <div className="assistant-feature-actions">{(current.status === 'archived' ? ['disabled'] : ['active', 'disabled', 'archived']).filter(status => status !== current.status).map(status =>
        <button key={status} disabled={disabled || busy || !!pending || !storageKey || !reason.trim()} onClick={() => change(status)}>{current.status === 'archived' ? '恢复为停用' : labels[status as keyof typeof labels]}</button>)}</div>
    </details>
    {pending && <p role="status">状态变更结果待核对。<button disabled={busy} onClick={() => void run(pending)}>使用原请求核对</button></p>}
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>;
}

export function ArchivedApplication({token, applicationId, typeId, onExpired}: {token: string; applicationId: string; typeId: string; onExpired: () => void}) {
  const [value, setValue] = useState<{draft: unknown; releases: unknown; audit: unknown} | null>(null);
  const [error, setError] = useState(''); const [refresh, setRefresh] = useState(0);
  const forms = ['platform.form', 'platform.leave-approval'].includes(typeId);
  const callbacks = useRef(onExpired); callbacks.current = onExpired;
  useEffect(() => {
    const abort = new AbortController(); const base = `/api/v1/admin/applications/${applicationId}/${forms ? 'form' : 'configuration'}`;
    void Promise.all([api(base, token, undefined, abort.signal), api(`${base}/releases`, token, undefined, abort.signal), api(`${base}/audit`, token, undefined, abort.signal)])
      .then(([draft, releases, audit]) => {if (!abort.signal.aborted) {setValue({draft, releases, audit}); setError('');}})
      .catch(cause => {if (!abort.signal.aborted) {if (cause instanceof SessionExpired) callbacks.current(); else setError(cause.message);}});
    return () => abort.abort();
  }, [token, applicationId, forms, refresh]);
  return <section className="panel"><h3>归档应用 · 只读</h3><p>保留设计、发布、审计和业务记录。需要修改时先恢复为停用状态。</p><button onClick={() => setRefresh(value => value + 1)}>刷新归档资料</button>
    {error && <p role="alert" className="error">{error}</p>}{value && Object.entries(value).map(([name, content]) => <details key={name}><summary>{{draft: '查看设计草稿', releases: '查看发布历史', audit: '查看审计记录'}[name]}</summary><pre>{JSON.stringify(content, null, 2)}</pre></details>)}
    {forms && <RecordBrowser token={token} applicationId={applicationId} admin onExpired={onExpired} />}
  </section>;
}
