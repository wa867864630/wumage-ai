import {useEffect, useState} from 'react';
import {api, SessionExpired} from './api';
import {ManagementDialog} from './ManagementDialog';

type Mode = 'today' | 'tomorrow' | 'day_after' | 'this_week' | 'next_week';
type Item = {id: string; text: string; date_mode: Mode; source: string; status: string; revision: number;
  occurrences: number; interpretations: Record<string, number>; reviewer: string | null;
  in_draft: boolean; published: boolean; updated_at: string};
type Library = {items: Item[]; draft_revision: number; limit: number};
const modes: Record<Mode, string> = {today: '今天', tomorrow: '明天', day_after: '后天', this_week: '本周', next_week: '下周'};
const statuses: Record<string, string> = {pending: '待审核', approved: '已审核', rejected: '已拒绝', disabled: '已停用'};
const sources: Record<string, string> = {manual: '管理员新增', ai_generated: 'AI 批量生成', learned: '查询学习'};

export function QuestionLibrary({basePath = '/api/v1/admin/question-library', token, disabled, draftRevision, onBusyChange, onEditingChange, onDraftChanged, onSessionExpired}: {
  basePath?: string; token: string; disabled: boolean; draftRevision: number; onBusyChange: (value: boolean) => void;
  onEditingChange: (value: boolean) => void; onDraftChanged: () => Promise<void>; onSessionExpired?: () => void;
}) {
  const [library, setLibrary] = useState<Library | null>(null);
  const [message, setMessage] = useState(''); const [failure, setFailure] = useState(false);
  const [loading, setLoading] = useState(false); const [working, setWorking] = useState(false);
  const [filter, setFilter] = useState('pending'); const [source, setSource] = useState('');
  const [sort, setSort] = useState('recent'); const [page, setPage] = useState(1);
  const [search, setSearch] = useState(''); const [count, setCount] = useState(10);
  const [text, setText] = useState(''); const [mode, setMode] = useState<Mode>('tomorrow');
  const [editing, setEditing] = useState<Item | null>(null); const [editorOpen, setEditorOpen] = useState(false);
  const editorDirty = editing ? text !== editing.text || mode !== editing.date_mode : !!text.trim() || mode !== 'tomorrow';
  useEffect(() => {onEditingChange(editorOpen);}, [editorOpen, onEditingChange]);
  useEffect(() => () => onEditingChange(false), [onEditingChange]);
  useEffect(() => {setPage(1);}, [filter, source, search, sort]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true);
    void api<Library>(basePath, token, undefined, AbortSignal.any([controller.signal, AbortSignal.timeout(15000)])).then(result => {
      if (!controller.signal.aborted) setLibrary(result);
    }).catch(cause => {if (!controller.signal.aborted) {
      if (cause instanceof SessionExpired) onSessionExpired?.();
      setFailure(true); setMessage(cause instanceof Error ? cause.message : '问法库加载失败');
    }}).finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
    // Callback identity must not restart reads while a parent renders progress.
  }, [token, draftRevision, basePath]);
  async function load() {setLibrary(await api<Library>(basePath, token));}
  async function work(task: () => Promise<void>) {
    setWorking(true); onBusyChange(true); setMessage(''); setFailure(false);
    try {await task();}
    catch (cause) {if (cause instanceof SessionExpired) onSessionExpired?.(); else {setFailure(true); setMessage(cause instanceof Error ? cause.message : '问法库操作失败');}}
    finally {setWorking(false); onBusyChange(false);}
  }
  function clear() {setText(''); setMode('tomorrow'); setEditing(null); setEditorOpen(false);}
  function closeEditor() {if (!working && (!editorDirty || window.confirm('放弃当前问法的未保存修改？'))) clear();}
  function openEditor(item: Item | null) {setEditing(item); setText(item?.text ?? ''); setMode(item?.date_mode ?? 'tomorrow'); setEditorOpen(true);}
  async function review(item: Item, action: 'approve' | 'reject' | 'disable' | 'delete') {
    if (!window.confirm(action === 'approve' ? '审核通过会将此问法加入草稿，发布后才生效。是否继续？'
      : '此操作会从草稿移除该问法的专属规则；已发布规则需再次发布才会停止。是否继续？')) return;
    await work(async () => {
      await api(`${basePath}/${item.id}/review`, token,
        {expected_revision: item.revision, expected_draft_revision: draftRevision, action});
      await onDraftChanged(); clear(); await load();
      setMessage(action === 'approve' ? '审核通过，已加入草稿。请使用底部“试跑发布”使其生效。' : '已处理；如影响草稿规则，请试跑并发布。');
    });
  }
  const visible = (library?.items.filter(item => (!filter || item.status === filter) && (!source || item.source === source) && item.text.includes(search)) ?? [])
    .sort((a, b) => sort === 'frequency' ? b.occurrences - a.occurrences : b.updated_at.localeCompare(a.updated_at));
  const pageCount = Math.max(1, Math.ceil(visible.length / 20)); const currentPage = Math.min(page, pageCount);
  const stale = !!library && library.draft_revision !== draftRevision;
  return <fieldset disabled={disabled || working} className="question-library management-fields"><legend className="sr-only">课表问法库 · 学习与审核</legend>
    <div className="library-toolbar"><div><strong>问法库</strong><span className="management-badge">待审核 {library?.items.filter(item => item.status === 'pending').length ?? 0}</span></div>
      <div><button className="quiet-button" disabled={loading} onClick={() => void work(load)}>刷新问法库</button><button onClick={() => openEditor(null)}>新增问法</button></div></div>
    <p className="hint">待审核问法不会参与直接查询。审核加入草稿，发布后生效。</p>
    <div className="library-generation"><label>生成数量<input type="number" min={1} max={20} value={count} onChange={event => setCount(Number(event.target.value))} /></label>
      <button className="quiet-button" disabled={editorOpen || !Number.isInteger(count) || count < 1 || count > 20} onClick={() => {
        if (!window.confirm(`调用已配置模型生成最多${count}条待审核问法，会产生模型用量。是否继续？`)) return;
        void work(async () => {
          const result = await api<{inserted: number; duplicates: number; ai_duration_ms: number}>(
            `${basePath}/generate`, token, {count, expected_draft_revision: draftRevision}, AbortSignal.timeout(65000));
          await load(); setMessage(`新增${result.inserted}条待审核问法，跳过${result.duplicates}条重复。AI 用时 ${(result.ai_duration_ms / 1000).toFixed(2)} 秒。`);
        });
      }}>AI 批量生成待审核问法</button></div>
    <div className="library-filters"><label>搜索问法<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="输入问法关键词" /></label>
      <label>审核状态<select value={filter} onChange={event => setFilter(event.target.value)}><option value="">全部状态</option>{Object.entries(statuses).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>来源<select value={source} onChange={event => setSource(event.target.value)}><option value="">全部来源</option>{Object.entries(sources).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>排序<select value={sort} onChange={event => setSort(event.target.value)}><option value="recent">最近更新</option><option value="frequency">观察次数优先</option></select></label></div>
    {stale && <p className="management-notice" role="alert">草稿版本已变化，请重新加载工作台和问法库后审核。</p>}
    {loading && !library ? <p role="status">正在加载问法库…</p> : <>
      <div className="table-scroll"><table className="question-table"><caption className="sr-only">课表问法及审核状态</caption><thead><tr><th scope="col">问法 / 日期</th><th scope="col">来源</th><th scope="col">状态与生效情况</th><th scope="col">观察次数</th><th scope="col">操作</th></tr></thead>
        <tbody>{visible.slice((currentPage - 1) * 20, currentPage * 20).map(item => <tr key={item.id}><td><strong>{item.text}</strong><small>{modes[item.date_mode]}</small></td>
          <td>{sources[item.source]}</td><td><span className={`management-badge ${item.status === 'pending' ? 'warning' : ''}`}>{statuses[item.status]}</span><small>{item.published ? '当前发布已包含' : item.in_draft ? '已入草稿，待发布' : '未加入草稿'}</small>
            {item.published && !item.in_draft && <small>草稿未包含，发布后将移除</small>}</td><td>{item.occurrences}</td>
          <td><button className="text-button" onClick={() => openEditor(item)}>查看 / 编辑</button></td></tr>)}</tbody></table></div>
      {!visible.length && <p className="management-empty">当前筛选下没有问法。可新增、AI生成，或等待成功的AI查询自动积累候选。</p>}
      <div className="library-pagination"><span>共 {visible.length} 条 · 第 {currentPage} / {pageCount} 页</span><div>
        <button className="quiet-button" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>上一页</button>
        <button className="quiet-button" disabled={currentPage >= pageCount} onClick={() => setPage(currentPage + 1)}>下一页</button></div></div>
    </>}
    {message && <p className={failure ? 'error management-notice' : 'management-notice'} role={failure ? 'alert' : 'status'}>{message}</p>}
    {editorOpen && <ManagementDialog title={editing ? '查看与编辑问法' : '新增待审核问法'} drawer busy={working} onClose={closeEditor}>
      <label>问法内容<input aria-label="问法内容" maxLength={80} value={text} disabled={disabled} onChange={event => setText(event.target.value)} placeholder="例如：帮我瞅瞅明天的课表" /></label>
      <label>日期含义<select value={mode} disabled={disabled} onChange={event => setMode(event.target.value as Mode)}>{Object.entries(modes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <p className="hint">只支持今天、明天、后天、本周、下周。修改后回到待审核，草稿旧规则会被移除。</p>
      {editing && <div className="question-review-info"><h3>审核与生效</h3><p>{sources[editing.source]} · {statuses[editing.status]}</p>
        <p>{editing.published ? '当前发布版本含此规则' : '当前发布版本未含此规则'}；{editing.in_draft ? '草稿已包含' : '草稿未包含'}。</p>
        <p>成功AI查询观察 {editing.occurrences} 次{editing.reviewer ? ` · 审核人 ${editing.reviewer}` : ''}</p>
        <p className="hint">观察次数不是独立用户数。{Object.entries(editing.interpretations).map(([key, value]) => `${modes[key as Mode] ?? key} ${value}次`).join('；')}</p>
        <div className="question-review-actions"><button disabled={disabled || editorDirty || stale || (editing.status === 'approved' && editing.in_draft)} onClick={() => void review(editing, 'approve')}>审核通过并加入草稿</button>
          <button className="quiet-button" disabled={disabled || editorDirty || stale || editing.status === 'rejected'} onClick={() => void review(editing, 'reject')}>拒绝</button>
          <button className="quiet-button" disabled={disabled || editorDirty || stale || editing.status === 'disabled'} onClick={() => void review(editing, 'disable')}>停用</button>
          <button className="quiet-button danger-text" disabled={disabled || editorDirty || stale} onClick={() => void review(editing, 'delete')}>删除</button></div>
        {editorDirty && <p className="hint">内容已修改，请先保存，再审核新内容。</p>}
      </div>}
      {failure && message && <p role="alert" className="error">{message}</p>}
      <div className="management-dialog-actions"><button className="quiet-button" disabled={working} onClick={closeEditor}>取消</button>
        <button disabled={disabled || !text.trim() || !editorDirty} onClick={() => void work(async () => {
          const result = await api<{inserted?: number}>(basePath + (editing ? `/${editing.id}` : ''), token,
            {text, date_mode: mode, ...(editing ? {expected_revision: editing.revision} : {})}, undefined, editing ? 'PUT' : 'POST');
          const wasEditing = !!editing; clear(); if (wasEditing) await onDraftChanged(); await load();
          setMessage(result.inserted === 0 ? '该问法已存在，未重复新增。' : '已保存为待审核，请查看并审核。');
        })}>{editing ? '保存修改并重新审核' : '新增待审核问法'}</button></div>
    </ManagementDialog>}
  </fieldset>;
}
