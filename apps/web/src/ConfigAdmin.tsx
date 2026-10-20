import {useCallback, useEffect, useRef, useState, type KeyboardEvent} from 'react';
import {ScheduleTable} from './ScheduleTable';
import {api as request, SessionExpired} from './api';
import {CourseProposalPanel} from './CourseProposalPanel';
import {QuestionLibrary} from './QuestionLibrary';
import {ManagementDialog} from './ManagementDialog';
import type {Column as ContractColumn, DisplayConfig, IntentRule} from './generated/contracts';

export type Column = ContractColumn;
type Content = DisplayConfig;
type Draft = {revision: number; current_release_id: string | null; content: Content};
type Release = {id: string; created_at: string; operation: string; source_release_id: string | null};
type Preview = {routing: {status: string; message: string; start_date?: string; end_date?: string}; review: {
  changes: string[]; can_publish: boolean; violations: string[];
  regressions: {text: string; changed: boolean; before: {status: string}; after: {status: string; start_date?: string; end_date?: string}}[];
}};
type Audit = {id: string; actor_id: string; action: string; trace_id: string; created_at: string; before_json: unknown; after_json: unknown};
type Monitoring = {counters: Record<string, number>; outbox: {counts: Record<string, number>; failed: {id: string; attempts: number; last_error: string}[]}};
const tabs = [['basic', '基础信息'], ['page', '页面配置'], ['questions', '问法与规则'], ['ai', 'AI 设计'], ['history', '发布记录']] as const;
type Tab = typeof tabs[number][0];
const tabNames = Object.fromEntries(tabs);
const dates = [['today', '今天'], ['tomorrow', '明天'], ['day_after', '后天'], ['this_week', '本周'], ['next_week', '下周']];
const same = (left: unknown, right: unknown): boolean => JSON.stringify(left) === JSON.stringify(right);

export function ConfigAdmin({initialToken = '', onSessionExpired, onDirtyChange, applicationId, embedded = false, onUpdated, onUse, onNavigationLock, initialTab = 'basic'}: {
  initialToken?: string; onSessionExpired?: () => void; onDirtyChange?: (dirty: boolean) => void;
  applicationId?: string; embedded?: boolean; onUpdated?: () => void; onUse?: (id: string) => void; onNavigationLock?: (locked: boolean) => void;
  initialTab?: 'basic' | 'page' | 'questions' | 'history';
} = {}) {
  const scoped = !!applicationId && applicationId !== '00000000-0000-4000-8000-000000000001';
  const apiBase = scoped ? `/api/v1/admin/applications/${applicationId}/configuration` : '/api/v1/admin/course-display';
  const availableTabs = scoped ? tabs.filter(([id]) => id !== 'ai') : tabs;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saved, setSaved] = useState<Content | null>(null);
  const [published, setPublished] = useState<Content | null>(null);
  const [history, setHistory] = useState<Release[]>([]);
  const [audits, setAudits] = useState<Audit[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [failure, setFailure] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [question, setQuestion] = useState('明天课表');
  const [monitoring, setMonitoring] = useState<Monitoring | null>(null);
  const [retryCommand, setRetryCommand] = useState<{path: string; body: unknown} | null>(null);
  const [proposalPending, setProposalPending] = useState(false);
  const [proposalEditing, setProposalEditing] = useState(false);
  const [questionEditing, setQuestionEditing] = useState(false);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [visited, setVisited] = useState<Set<Tab>>(new Set(['basic', initialTab]));
  const [ruleView, setRuleView] = useState('library');
  const [search, setSearch] = useState('');
  const [publishOpen, setPublishOpen] = useState(false);
  const sessionExpired = useRef(onSessionExpired);
  sessionExpired.current = onSessionExpired;
  const dirty = !!draft && !!saved && !same(draft.content, saved);
  const localWork = dirty || proposalEditing || questionEditing;
  const uncertain = proposalPending || !!retryCommand;
  const writeBlocked = busy || proposalPending || questionEditing || proposalEditing || !!retryCommand;
  const savedUnpublished = !!saved && ((scoped && !!draft && !draft.current_release_id) || (!!published && !same(saved, published)));

  const api = useCallback(async <T,>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> => {
    try {return await request<T>(`${apiBase}${path}`, initialToken, body, signal ?? AbortSignal.timeout(15000), method);}
    catch (cause) {if (cause instanceof SessionExpired) sessionExpired.current?.(); throw cause;}
  }, [initialToken, apiBase]);
  const load = useCallback(async (signal?: AbortSignal) => {
    const requestSignal = signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000);
    const [next, releases, events, live] = await Promise.all([
      api<Draft>('', 'GET', undefined, requestSignal), api<Release[]>('/releases', 'GET', undefined, requestSignal),
      api<Audit[]>('/audit', 'GET', undefined, requestSignal),
      request<{content: Content | null}>(scoped ? `${apiBase}/current` : '/api/v1/config/course-display', initialToken, undefined, requestSignal),
    ]);
    return {next, releases, events, live};
  }, [api, initialToken, apiBase, scoped]);
  function applyLoaded(data: Awaited<ReturnType<typeof load>>) {
    setDraft(data.next); setSaved(data.next.content); setPublished(data.live.content);
    setHistory(data.releases); setAudits(data.events); setPreview(null); setRetryCommand(null);
    setRefreshVersion(value => value + 1);
  }
  async function refresh() {applyLoaded(await load()); onUpdated?.();}
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setMessage(''); setFailure(false); setDraft(null); setSaved(null); setPublished(null);
    void load(controller.signal).then(data => {if (!controller.signal.aborted) applyLoaded(data);}).catch(cause => {
      if (!controller.signal.aborted) {
        if (cause instanceof SessionExpired) sessionExpired.current?.();
        setFailure(true); setMessage(cause instanceof Error ? cause.message : '应用加载失败');
      }
    }).finally(() => {if (!controller.signal.aborted) setBusy(false);});
    return () => controller.abort();
  }, [load]);
  useEffect(() => {onNavigationLock?.(busy || uncertain);}, [busy, uncertain, onNavigationLock]);
  useEffect(() => () => onNavigationLock?.(false), [onNavigationLock]);
  useEffect(() => {onDirtyChange?.(localWork || uncertain || busy);}, [localWork, uncertain, busy, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {if (localWork || uncertain || busy) {event.preventDefault(); event.returnValue = '';}};
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, [localWork, uncertain, busy]);
  async function action(work: () => Promise<void>, success: string) {
    setBusy(true); setMessage(''); setFailure(false);
    try {await work(); setMessage(success);}
    catch (cause) {setFailure(true); setMessage(cause instanceof Error ? cause.message : '操作失败');}
    finally {setBusy(false);}
  }
  function selectTab(next: Tab) {setTab(next); setVisited(previous => new Set([...previous, next]));}
  function tabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % availableTabs.length;
    else if (event.key === 'ArrowLeft') next = (index + availableTabs.length - 1) % availableTabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = availableTabs.length - 1;
    else return;
    event.preventDefault(); selectTab(availableTabs[next][0]); document.getElementById(`config-tab-${availableTabs[next][0]}`)?.focus();
  }
  function change(content: Content) {if (draft) {setDraft({...draft, content}); setPreview(null);}}
  function changeRules(rules: IntentRule[]) {if (draft) change({...draft.content, rules});}
  function updateRule(index: number, patch: Partial<IntentRule>) {
    if (draft) changeRules(draft.content.rules.map((rule, i) => i === index ? {...rule, ...patch} : rule));
  }
  async function save() {
    if (!draft) return;
    const next = await api<Draft>('/draft', 'PUT', {expected_revision: draft.revision, content: draft.content});
    setDraft(next); setSaved(next.content); setPreview(null); setRefreshVersion(value => value + 1); onUpdated?.();
  }
  async function writeRelease(path: string, body: unknown) {
    setRetryCommand({path, body}); await api(path, 'POST', body); await refresh();
  }
  function reload() {
    if ((localWork || uncertain) && !window.confirm('重新加载会清除未保存的编辑及提案。若上次写入结果未确认，请加载后核对草稿、发布记录和审计。是否继续？')) return;
    void action(refresh, '已重新加载当前草稿与发布状态。');
  }
  const appName = saved?.application.name ?? '课表助手';
  const matches = `${appName}${saved?.application.description ?? ''}`.includes(search.trim());
  return <section className={`application-workbench ${embedded ? 'embedded-config' : ''}`} aria-label="应用工作台">
    {!embedded && <aside className="application-list" aria-label="可配置应用">
      <div className="application-list-heading"><h2>应用列表</h2><span>1</span></div>
      <label><span className="sr-only">搜索应用</span><input type="search" placeholder="搜索应用" value={search} onChange={event => setSearch(event.target.value)} /></label>
      {matches ? <button className="application-list-item selected" aria-current="true" onClick={() => selectTab('basic')}>
        <span className="mini-icon">▦</span><span><strong>{appName}</strong><small>课表查询与应用配置</small></span></button>
        : <p className="hint application-list-empty">没有匹配的应用。<button className="text-button" onClick={() => setSearch('')}>清除搜索</button></p>}
      <p className="application-list-note">选择应用后，在右侧管理各项配置。</p>
    </aside>}
    <div className="application-detail">
      <header className="application-detail-heading"><div><div className="overline">APPLICATION SETTINGS</div><h2>{draft?.content.application.name || appName}</h2>
        <div className="application-statuses"><span className="management-badge">{draft?.current_release_id ? '已发布' : scoped ? '未发布' : '使用内置配置'}</span>
          {dirty && <span className="management-badge warning">有未保存修改</span>}{(savedUnpublished || (scoped && draft && !draft.current_release_id)) && <span className="management-badge warning">有待发布变更</span>}</div></div>
        {onUse && draft?.current_release_id && <button className="quiet-button" disabled={busy || localWork || uncertain} onClick={() => onUse(applicationId ?? 'schedule')}>使用应用 ↗</button>}<button className="quiet-button" disabled={busy} onClick={reload}>重新加载</button></header>
      <div className="management-tabs" role="tablist" aria-label="应用功能配置">{availableTabs.map(([id, label], index) => <button key={id} id={`config-tab-${id}`} role="tab"
        aria-selected={tab === id} aria-controls={`config-panel-${id}`} tabIndex={tab === id ? 0 : -1}
        onKeyDown={event => tabKey(event, index)} onClick={() => selectTab(id)}>{label}</button>)}</div>
      {message && <div className={`management-notice ${failure ? 'error' : ''}`} role={failure ? 'alert' : 'status'}>{message}</div>}
      {retryCommand && <div className="management-notice warning" role="alert">上次发布结果待核对。
        <button disabled={busy} onClick={() => void action(async () => {await writeRelease(retryCommand.path, retryCommand.body);}, '已确认上次请求结果。')}>使用相同请求号重试</button>
        <button className="quiet-button" disabled={busy} onClick={() => selectTab('history')}>查看发布记录</button></div>}
      {draft ? <>
        {(questionEditing || proposalEditing || proposalPending) && <div className="management-notice">
          {questionEditing ? '问法尚在编辑，请保存或取消后进行其他写入。' : proposalPending ? 'AI 提案接受结果待确认，请使用相同请求号核对。' : 'AI 需求或提案尚未处理，请接受或清空后进行其他写入。'}
          <button className="text-button" onClick={() => selectTab(questionEditing ? 'questions' : 'ai')}>前往处理</button></div>}
        <div className="application-config-body">
          <section id="config-panel-basic" role="tabpanel" aria-labelledby="config-tab-basic" hidden={tab !== 'basic'}>
            <div className="config-section-heading"><h3>应用基本信息与访问范围</h3><p>设置应用对外展示的信息，以及哪些角色可以使用。</p></div>
            <fieldset disabled={writeBlocked} className="management-fields"><legend className="sr-only">应用基本信息</legend>
              <div className="management-form-grid"><label>应用名称<input required maxLength={30} value={draft.content.application.name} onChange={event => change({...draft.content, application: {...draft.content.application, name: event.target.value}})} /></label>
                <label>应用分类<select value={draft.content.application.category} onChange={event => change({...draft.content, application: {...draft.content.application, category: event.target.value as Content['application']['category']}})}>{['学习服务', '校园事务', '校园生活'].map(value => <option key={value}>{value}</option>)}</select></label>
                <label className="full-width">应用描述<textarea rows={4} maxLength={160} value={draft.content.application.description} onChange={event => change({...draft.content, application: {...draft.content.application, description: event.target.value}})} /></label>
              </div>
              <div className="role-setting"><h4>可见及可使用角色</h4><p className="hint">至少选择一个角色。发布后由服务端校验访问权限。</p><div className="role-options">{[['student', '学生'], ['teacher', '教师'], ['config_admin', '配置管理员']].map(([value, label]) => <label key={value}><input type="checkbox" checked={draft.content.application.visible_roles.includes(value as Content['application']['visible_roles'][number])} onChange={event => {
                const role = value as Content['application']['visible_roles'][number];
                change({...draft.content, application: {...draft.content.application, visible_roles: event.target.checked ? [...draft.content.application.visible_roles, role] : draft.content.application.visible_roles.filter(item => item !== role)}});
              }} />{label}</label>)}</div></div>
            </fieldset>
          </section>
          <section id="config-panel-page" role="tabpanel" aria-labelledby="config-tab-page" hidden={tab !== 'page'}>
            <div className="config-section-heading"><h3>页面配置</h3><p>调整课表字段名称、顺序与显隐，下方预览会同步更新。</p></div>
            <fieldset disabled={writeBlocked} className="management-fields"><legend className="sr-only">课表展示字段</legend>
              {draft.content.columns.map((column, index) => <div className="column-editor" key={column.field}>
                <label><input type="checkbox" checked={column.visible} onChange={event => change({...draft.content, columns: draft.content.columns.map((item, i) => i === index ? {...item, visible: event.target.checked} : item)})} />{column.field}</label>
                <input aria-label={`${column.field} 展示名称`} value={column.label} maxLength={30} onChange={event => change({...draft.content, columns: draft.content.columns.map((item, i) => i === index ? {...item, label: event.target.value} : item)})} />
                {[-1, 1].map(offset => <button key={offset} className="quiet-button" disabled={index + offset < 0 || index + offset >= draft.content.columns.length} aria-label={`${column.field} ${offset < 0 ? '上移' : '下移'}`} onClick={() => {
                  const columns = [...draft.content.columns]; [columns[index], columns[index + offset]] = [columns[index + offset], columns[index]]; change({...draft.content, columns});
                }}>{offset < 0 ? '↑' : '↓'}</button>)}
              </div>)}
            </fieldset>
            <div className="config-preview"><h4>课表效果预览</h4><p className="hint">使用合成数据展示，字段显隐仅控制页面展示。</p>
              <ScheduleTable columns={draft.content.columns} rows={[{id: 'preview', name: '程序设计基础（预览）', date: '2026-10-19', start_time: '09:00', end_time: '10:35', location: '教学楼 A-201', teacher: '模拟教师'}]} /></div>
          </section>
          <section id="config-panel-questions" role="tabpanel" aria-labelledby="config-tab-questions" hidden={tab !== 'questions'}>
            <div className="config-section-heading"><h3>问法与规则</h3><p>维护常用表达、审核学习结果，或调整匹配规则。</p></div>
            <div className="management-subtabs" aria-label="问法配置视图">{[['library', '问法库'], ['rules', '匹配规则']].map(([key, label]) => <button key={key} aria-pressed={ruleView === key} onClick={() => setRuleView(key)}>{label}</button>)}</div>
            <div hidden={ruleView !== 'library'}>{visited.has('questions') && <QuestionLibrary basePath={scoped ? `/api/v1/admin/applications/${applicationId}/questions` : undefined} token={initialToken} draftRevision={draft.revision}
              disabled={busy || dirty || proposalEditing || proposalPending || !!retryCommand} onBusyChange={setBusy} onEditingChange={setQuestionEditing}
              onDraftChanged={refresh} onSessionExpired={onSessionExpired} />}</div>
            <fieldset hidden={ruleView !== 'rules'} disabled={writeBlocked} className="management-fields"><legend className="sr-only">匹配规则</legend>
              <p className="hint">精确问法为完整表达；关键词模式要求全部词出现且不含额外指令。同优先级日期冲突时要求澄清。</p>
              {draft.content.rules.map((rule, index) => <details className="management-rule" key={rule.id}><summary>{rule.terms[0] || '未命名规则'}<span>{rule.enabled ? '启用' : '停用'} · {dates.find(([value]) => value === rule.date_mode)?.[1]}</span></summary>
                <div className="rule-editor"><label>启用<input type="checkbox" checked={rule.enabled} onChange={event => updateRule(index, {enabled: event.target.checked})} /></label>
                  <label>匹配方式<select value={rule.match_type} onChange={event => updateRule(index, {match_type: event.target.value as IntentRule['match_type']})}><option value="exact">精确问法</option><option value="keywords">全部关键词</option></select></label>
                  <label>词条（每行一个）<textarea value={rule.terms.join('\n')} onChange={event => updateRule(index, {terms: event.target.value.split('\n')})} /></label>
                  <label>查询日期<select value={rule.date_mode} onChange={event => updateRule(index, {date_mode: event.target.value as IntentRule['date_mode']})}>{dates.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                  <label>优先级<input type="number" min={1} max={1000} value={rule.priority} onChange={event => updateRule(index, {priority: Number(event.target.value)})} /></label>
                  <button className="quiet-button" onClick={() => changeRules(draft.content.rules.filter((_, i) => i !== index))}>删除规则</button></div>
              </details>)}
              {!draft.content.rules.length && <p className="management-empty">暂无自定义规则，内置日期语法仍可使用。</p>}
              <button disabled={draft.content.rules.length >= 50} onClick={() => changeRules([...draft.content.rules, {id: `rule-${crypto.randomUUID().slice(0, 12)}`, enabled: true, priority: 100, match_type: 'exact', terms: ['明日课程'], date_mode: 'tomorrow'}])}>新增匹配规则</button>
            </fieldset>
          </section>
          <section id="config-panel-ai" role="tabpanel" aria-labelledby="config-tab-ai" hidden={tab !== 'ai'}>
            <div className="config-section-heading"><h3>AI 设计</h3><p>描述修改目标，核对差异后接受为草稿；发布仍需人工确认。</p></div>
            {dirty && <p className="management-notice">请先暂存基础信息或页面修改，再生成 AI 提案。</p>}
            {visited.has('ai') && <CourseProposalPanel token={initialToken} revision={draft.revision} resetVersion={refreshVersion}
              disabled={busy || dirty || questionEditing || !!retryCommand} onBusyChange={setBusy} onPendingChange={setProposalPending}
              onEditingChange={setProposalEditing} onAccepted={refresh} onSessionExpired={onSessionExpired} />}
          </section>
          <section id="config-panel-history" role="tabpanel" aria-labelledby="config-tab-history" hidden={tab !== 'history'}>
            <div className="config-section-heading"><h3>发布记录</h3><p>查看当前版本、历史发布及操作审计。回滚会创建新的发布记录。</p></div>
            <p className="hint">当前版本：{draft.current_release_id ?? (scoped ? '尚未发布' : '内置默认配置')}</p>
            <ul className="management-releases">{history.map(release => <li key={release.id} className="release-row"><div><strong>{release.operation === 'rollback' ? '回滚发布' : '应用发布'}</strong> {release.id === draft.current_release_id && <span className="management-badge">当前生效</span>}
              <p>{new Date(release.created_at).toLocaleString()}</p><small>{release.id}</small>{release.source_release_id && <p className="hint">恢复自 {release.source_release_id}</p>}</div>
              <button className="quiet-button" disabled={writeBlocked || dirty || release.id === draft.current_release_id} onClick={() => {
                if (!window.confirm('将创建新发布记录，恢复所选版本。草稿内容保持不变，是否继续？')) return;
                void action(async () => {await writeRelease('/rollback', {expected_revision: draft.revision, expected_release_id: draft.current_release_id, target_release_id: release.id, idempotency_key: crypto.randomUUID()});}, '已回滚；当前草稿保持不变。');
              }}>回滚到此版本</button></li>)}</ul>
            {!history.length && <p className="management-empty">还没有发布记录。</p>}
            <details className="management-audit"><summary>操作审计（最近50条）</summary><ul>{audits.map(event => <li key={event.id}><div>{new Date(event.created_at).toLocaleString()} · {event.actor_id} · {event.action}<br /><small>请求编号：{event.trace_id}</small>
              <details><summary>查看变更前后</summary><pre>{JSON.stringify({before: event.before_json, after: event.after_json}, null, 2)}</pre></details></div></li>)}</ul></details>
            <details className="management-audit"><summary>运行诊断</summary><button disabled={busy} onClick={() => void action(async () => {
              try {setMonitoring(await request<Monitoring>(scoped ? `${apiBase}/monitoring` : '/api/v1/admin/monitoring', initialToken));}
              catch (cause) {if (cause instanceof SessionExpired) sessionExpired.current?.(); throw cause;}
            }, '已加载运行指标。')}>刷新运行指标</button>
              {monitoring && <><p className="hint">单进程指标，查询与设计模型调用分别统计。</p><pre>{JSON.stringify({counters: monitoring.counters, outbox: monitoring.outbox.counts}, null, 2)}</pre>
                {monitoring.outbox.failed.map(event => <p key={event.id}>{event.id} · 重试{event.attempts}次 · {event.last_error}<button disabled={busy} onClick={() => void action(async () => {await api(`/outbox/${event.id}/retry`, 'POST');}, '已加入投递重试队列。')}>重新投递</button></p>)}</>}
            </details>
          </section>
        </div>
        <footer className="management-action-bar"><div><strong>{dirty ? '有未保存修改' : savedUnpublished ? '草稿已保存，尚未发布' : '草稿已保存'}</strong><small>草稿修订 {draft.revision} · {tabNames[tab]}</small></div>
          <div><button className="quiet-button" disabled={writeBlocked || !dirty} onClick={() => void action(save, '更改已暂存，当前发布版本不受影响。')}>暂存更改</button>
            <button disabled={writeBlocked} onClick={() => {setPreview(null); setPublishOpen(true);}}>试跑发布</button></div></footer>
      </> : <div className="management-empty" role="status">{busy ? '正在加载应用配置…' : '应用尚未加载，请点击重新加载重试。'}</div>}
      {publishOpen && draft && <ManagementDialog title="试跑与发布" busy={busy} onClose={() => setPublishOpen(false)}>
        <p className="hint">先核对当前草稿的查询行为与变更，再确认发布。{dirty ? '当前存在未保存修改，请先暂存。' : `草稿修订 ${draft.revision}。`}</p>
        {dirty && <button disabled={writeBlocked} onClick={() => void action(save, '已暂存，请继续试跑。')}>暂存当前更改</button>}
        <label>试跑问句<input maxLength={200} value={question} disabled={busy} onChange={event => {setQuestion(event.target.value); setPreview(null);}} /></label>
        <button disabled={busy || !question.trim() || dirty} onClick={() => void action(async () => {
          setPreview(null); setPreview(await api<Preview>('/preview', 'POST', {content: draft.content, text: question}));
        }, '草稿试跑与固定回归已完成。')}>校验并试跑草稿</button>
        {preview && <div className="publish-review"><h3>问句试跑结果</h3><p>{preview.routing.message}</p><p>{preview.routing.start_date} {preview.routing.end_date}</p>
          <h3>发布变更</h3><p>{preview.review.changes.join('；') || '与当前发布内容相同'}</p>
          <p className={preview.review.can_publish ? 'hint' : 'error'}>{preview.review.can_publish ? '固定回归通过，可以发布。' : `回归未通过：${preview.review.violations.join('、')}`}</p>
          <details><summary>查看问句回归对比</summary><ul>{preview.review.regressions.map(row => <li key={row.text}>{row.text}：{row.before.status} → {row.after.status} {row.after.start_date} {row.changed ? '（变化）' : ''}</li>)}</ul></details>
        </div>}
        {failure && message && <p role="alert" className="error">{message}</p>}
        {retryCommand && <div className="management-notice" role="alert">发布结果待核对，请保持原请求号重试，或返回配置后重新加载核对版本。
          <button disabled={busy} onClick={() => void action(async () => {
            await writeRelease(retryCommand.path, retryCommand.body); setPublishOpen(false);
          }, '已确认上次发布结果。')}>使用原请求确认发布结果</button></div>}
        <div className="management-dialog-actions"><button className="quiet-button" disabled={busy} onClick={() => setPublishOpen(false)}>返回配置</button>
          <button disabled={writeBlocked || dirty || !preview?.review.can_publish} onClick={() => {
            if (!window.confirm('发布后，新查询立即使用此配置。是否确认发布？')) return;
            void action(async () => {await writeRelease('/publish', {expected_revision: draft.revision, expected_release_id: draft.current_release_id, idempotency_key: crypto.randomUUID()}); setPublishOpen(false);}, '应用已发布，新查询将使用当前版本。');
          }}>确认发布</button></div>
      </ManagementDialog>}
    </div>
  </section>;
}
