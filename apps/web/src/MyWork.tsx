import {useEffect, useRef, useState} from 'react';
import {api, SessionExpired} from './api';
import {WorkRecordDetail, type WorkRecordTarget} from './WorkRecordDetail';

export type WorkTarget = {applicationId: string; typeId: string; draftId?: string; mode: 'fill' | 'records'};
type Draft = {id: string; application_id: string; application_name?: string; type_id: string; revision: number; updated_at: string};
type Receipt = {id: string; application_id: string; application_name?: string; type_id?: string; created_at?: string};
type WorkList = {items: Draft[]; total: number; assigned: Receipt[]; submitted: Receipt[]};
export function MyWork({token, onResume, onClose, onExpired}: {token: string; onResume: (target: WorkTarget) => void; onClose: () => void; onExpired: () => void}) {
  const [snapshot, setSnapshot] = useState<{token: string; page: number; refresh: number; value: WorkList} | null>(null);
  const [page, setPage] = useState(1); const [refresh, setRefresh] = useState(0); const [loading, setLoading] = useState(false); const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [selectedRecord, setSelectedRecord] = useState<WorkRecordTarget | null>(null);
  const [query, setQuery] = useState('');
  const result = snapshot?.token === token && snapshot.page === page && snapshot.refresh === refresh ? snapshot.value : null;
  const needle = query.trim().toLocaleLowerCase();
  const matches = (item: {id: string; application_id: string; application_name?: string}, fallback: string) => !needle
    || [item.id, item.application_id, item.application_name ?? fallback].some(value => value.toLocaleLowerCase().includes(needle));
  const drafts = result?.items.filter(item => matches(item, item.type_id === 'platform.leave-approval' ? '请假' : '登记')) ?? [];
  const assigned = result?.assigned.filter(item => matches(item, '请假应用')) ?? [];
  const submitted = result?.submitted.filter(item => matches(item, '登记应用')) ?? [];
  const callbacks = useRef({onExpired}); callbacks.current = {onExpired};
  useEffect(() => {const abort = new AbortController(); setLoading(true); setError(''); setSelectedRecord(null);
    void api<WorkList>(`/api/v1/my-work?page=${page}`, token, undefined, abort.signal).then(value => {
      if (abort.signal.aborted) return;
      const lastPage = Math.max(1, Math.ceil(value.total / 20));
      if (page > lastPage) {setNotice('草稿数量已变化，已返回当前有效页。'); setPage(lastPage); return;}
      setSnapshot({token, page, refresh, value});
    })
      .catch(cause => {if (!abort.signal.aborted) {if (cause instanceof SessionExpired) callbacks.current.onExpired(); else setError(cause.message);}})
      .finally(() => {if (!abort.signal.aborted) setLoading(false);});
    return () => abort.abort();
  }, [token, page, refresh]);
  return <section className="panel" aria-label="我的工作"><div className="admin-toolbar"><h2>我的工作</h2><button onClick={onClose}>收起我的工作</button><button disabled={loading} onClick={() => {setNotice(''); setRefresh(value => value + 1);}}>刷新工作</button></div>
    <p className="muted">选择具体草稿或应用继续，进入后会重新检查权限和发布版本；已有记录不会再次提交。</p>
    <label>筛选已加载工作<input type="search" maxLength={120} value={query} placeholder="应用名称、应用编号或记录编号" onChange={event => {setQuery(event.target.value); setSelectedRecord(null);}} /></label>
    {query && <button onClick={() => {setQuery(''); setSelectedRecord(null);}}>清除工作筛选</button>}
    <p className="muted">筛选范围：当前页草稿、最近100项审批和最近100项本人提交。查找其他草稿请翻页；筛选不调用 AI。</p>
    {loading && <p role="status">正在加载我的工作…</p>}{notice && <p role="status">{notice}</p>}{error && <p role="alert" className="error">{error} 请点击“刷新工作”重试。</p>}
    {result && selectedRecord && <WorkRecordDetail key={`${selectedRecord.application_id}:${selectedRecord.id}:${selectedRecord.type_id}`} token={token} target={selectedRecord} onExpired={onExpired} onClose={() => setSelectedRecord(null)} />}
    {result && <><h3>未完成填写 · {result.total}</h3>{needle && <p role="status">本页匹配 {drafts.length} / {result.items.length} 项</p>}{!result.items.length ? <p>暂无已保存的填写草稿。</p> : !drafts.length && <p>当前页草稿没有匹配项，可清除筛选或翻页查找。</p>}
      {drafts.map(item => <article className="requirement-item" key={item.id}><strong>{item.application_name ?? (item.type_id === 'platform.leave-approval' ? '请假' : '登记')} · 草稿 {item.id.slice(0, 8)}</strong><p>应用 {item.application_id.slice(0, 8)} · 修订 {item.revision} · {new Date(item.updated_at).toLocaleString()}</p>
        <button disabled={loading} onClick={() => onResume({applicationId: item.application_id, typeId: item.type_id, draftId: item.id, mode: 'fill'})}>返回续填此草稿</button></article>)}
      <div className="admin-toolbar"><button disabled={loading || page <= 1} onClick={() => setPage(value => value - 1)}>上一页草稿</button><span>第 {page} 页</span><button disabled={loading || page * 20 >= result.total} onClick={() => setPage(value => value + 1)}>下一页草稿</button></div>
      <h3>分配给我的审批 · 最近100项</h3>{needle && <p role="status">匹配 {assigned.length} / {result.assigned.length} 项</p>}{!result.assigned.length ? <p>暂无待审批任务。</p> : !assigned.length && <p>已加载审批没有匹配项，可清除筛选。</p>}{assigned.map(item => <p key={item.id}>{item.application_name ?? '请假应用'} · 申请 {item.id.slice(0, 8)} <button onClick={() => setSelectedRecord({id: item.id, application_id: item.application_id, type_id: 'platform.leave-approval'})}>查看此申请详情</button> <button onClick={() => onResume({applicationId: item.application_id, typeId: 'platform.leave-approval', mode: 'records'})}>进入原应用办理审批</button></p>)}
      <h3>本人已提交 · 最近100项</h3>{needle && <p role="status">匹配 {submitted.length} / {result.submitted.length} 项</p>}{!result.submitted.length ? <p>暂无已提交记录。</p> : !submitted.length && <p>已加载提交记录没有匹配项，可清除筛选。</p>}{submitted.map(item => <p key={item.id}>{item.application_name ?? '登记应用'} · 记录 {item.id.slice(0, 8)} <button onClick={() => setSelectedRecord({id: item.id, application_id: item.application_id, type_id: item.type_id ?? 'platform.form'})}>查看此记录详情</button> <button onClick={() => onResume({applicationId: item.application_id, typeId: item.type_id ?? 'platform.form', mode: 'records'})}>核对原应用记录</button></p>)}</>}
  </section>;
}
