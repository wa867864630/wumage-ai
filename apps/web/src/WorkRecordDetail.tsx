import {useEffect, useRef, useState} from 'react';
import {api, SessionExpired} from './api';
import {formatFieldValue, type FormConfig} from './FormRuntime';

export type WorkRecordTarget = {id: string; application_id: string; type_id: string};
type Detail = {id: string; release_id: string; content: FormConfig; values: Record<string, unknown>;
  duration_days?: number; workflow?: {status: string; last_reason: string | null}};

export function WorkRecordDetail({token, target, onClose, onExpired}: {
  token: string; target: WorkRecordTarget; onClose: () => void; onExpired: () => void;
}) {
  const [snapshot, setSnapshot] = useState<{token: string; value: Detail} | null>(null);
  const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [refresh, setRefresh] = useState(0);
  const expired = useRef(onExpired); expired.current = onExpired;
  const supported = ['platform.form', 'platform.leave-approval'].includes(target.type_id);
  useEffect(() => {
    const abort = new AbortController(); setSnapshot(null); setLoading(true); setError('');
    if (!supported) {setError('此类型暂不支持在我的工作查看详情，请进入原应用。'); setLoading(false); return;}
    const resource = target.type_id === 'platform.leave-approval' ? 'approvals' : 'records';
    void api<Detail>(`/api/v1/applications/${encodeURIComponent(target.application_id)}/${resource}/${encodeURIComponent(target.id)}`, token, undefined, abort.signal)
      .then(value => {if (!abort.signal.aborted) {
        if (value.id !== target.id) throw new Error('返回记录与所选记录不一致，请重新核对。');
        setSnapshot({token, value});
      }})
      .catch(cause => {if (!abort.signal.aborted) {if (cause instanceof SessionExpired) expired.current(); else setError(cause instanceof Error ? cause.message : '记录详情读取失败');}})
      .finally(() => {if (!abort.signal.aborted) setLoading(false);});
    return () => abort.abort();
  }, [token, target.application_id, target.id, target.type_id, supported, refresh]);
  const detail = snapshot?.token === token ? snapshot.value : null;
  return <section className="management-notice" aria-label="我的工作记录详情"><div className="admin-toolbar"><h3>所选记录详情</h3>
    <button onClick={onClose}>关闭记录详情</button><button disabled={loading || !supported} onClick={() => setRefresh(value => value + 1)}>刷新此记录</button></div>
    <p>应用编号：{target.application_id} · 记录编号：{target.id}</p>
    {loading && <p role="status">正在读取所选记录…</p>}{error && <p role="alert" className="error">{error}</p>}
    {detail && <><p>{detail.content.application.name} · 按提交时版本展示：{detail.release_id}</p>
      <dl>{detail.content.model.fields.map(field => <div key={field.id}><dt>{field.label}</dt><dd>{formatFieldValue(field, detail.values[field.key])}</dd></div>)}</dl>
      {detail.workflow && <p>请假状态：{({pending: '待审批', approved: '已通过', rejected: '已拒绝', withdrawn: '已撤回'} as Record<string, string>)[detail.workflow.status] ?? '状态待核对'}{detail.duration_days !== undefined && ` · ${detail.duration_days} 天`}</p>}
      {detail.workflow?.last_reason && <p>处理说明：{detail.workflow.last_reason}</p>}
      <p className="muted">这里只读取原记录，不会再次提交或办理审批；办理请进入原应用。</p>
    </>}
  </section>;
}
