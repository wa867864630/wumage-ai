import {useCallback, useEffect, useRef, useState, type FormEvent} from 'react';
import {createRoot} from 'react-dom/client';
import {ApplicationManagement} from './ApplicationManagement';
import {AssistantTurn} from './AssistantTurn';
import {Login} from './Login';
import {RequirementPanel} from './RequirementPanel';
import {FormRuntime} from './FormRuntime';
import {ApprovalRuntime} from './ApprovalRuntime';
import {MyWork, type WorkTarget} from './MyWork';
import {api, SessionExpired, type Session} from './api';
import type {ScheduleResponse} from './generated/contracts';
import type {ApplicationResolution, ManagementLocation} from './assistant';
import './style.css';

const plannedServices = [
  {id: 'schedule', icon: '▦', name: '课表助手', category: '学习服务', desc: '一句话查询课程，随时掌握每天的学习安排。', ready: true},
  {id: 'leave', icon: '↗', name: '请假申请', category: '校园事务', desc: '提交请假申请、查看审批进度。', ready: false},
  {id: 'purchase', icon: '▣', name: '采购申请', category: '校园事务', desc: '采购需求与审批流程的统一入口。', ready: false},
  {id: 'visit', icon: '⌘', name: '访问申请', category: '校园事务', desc: '访客预约、入校访问与审核。', ready: false},
  {id: 'venue', icon: '◈', name: '场地预约', category: '校园生活', desc: '查找场地与可用时段，安排校园活动。', ready: false},
  {id: 'events', icon: '✧', name: '活动管理', category: '校园生活', desc: '活动发布、报名与管理。', ready: false},
];
type Service = typeof plannedServices[number] & {application_id?: string; type_id?: string};
type ScheduleQuery = ({kind: 'text'; text: string} | {kind: 'dates'; start: string; end: string}) & {applicationId?: string | null; selectedApplicationId?: string};
type Turn = {id: number; question: string; request: ScheduleQuery; response?: ScheduleResponse; resolution?: ApplicationResolution; applicationName?: string; elapsedMs?: number; error?: string};
const today = () => new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());

export function Workspace({session, onLogout, initialPage}: {session: Session; onLogout: (notice?: string) => void; initialPage?: string}) {
  const [services, setServices] = useState<Service[]>(plannedServices.map(service => ({...service, ready: false})));
  const [activeApplication, setActiveApplication] = useState<string | null>(null);
  const [constructionSubject, setConstructionSubject] = useState<string | null>(null);
  const [requirementsOpen, setRequirementsOpen] = useState(false);
  const [requirementDirty, setRequirementDirty] = useState(false);
  const [runtimeDirty, setRuntimeDirty] = useState(false);
  const [runtimeMode, setRuntimeMode] = useState<'fill' | 'records'>('fill');
  const [workOpen, setWorkOpen] = useState(false); const [workTarget, setWorkTarget] = useState<WorkTarget | null>(null);
  const runtimePause = useRef<(() => Promise<boolean>) | null>(null);
  const registerPause = useCallback((pause: (() => Promise<boolean>) | null) => {runtimePause.current = pause;}, []);
  const [managementLocation, setManagementLocation] = useState<ManagementLocation>(() => {
    if (typeof window === 'undefined') return {};
    const params = new URLSearchParams(window.location.hash.split('?')[1] ?? '');
    const valid = (value: string | null) => value && /^[a-f0-9-]{36}$/.test(value) ? value : undefined;
    return {applicationId: valid(params.get('application')), taskId: valid(params.get('task'))};
  });
  const [catalogError, setCatalogError] = useState('');
  const [editorDirty, setEditorDirty] = useState(false);
  const [page, setPage] = useState(() => {
    const selected = initialPage ?? (typeof window !== 'undefined' && /^#workbench(?:\?|$)/.test(window.location.hash) ? 'admin' : 'chat');
    return ['admin', 'deployment', 'ai-model'].includes(selected) && !session.identity.roles.includes('config_admin') ? 'chat' : selected;
  });
  const [sidebar, setSidebar] = useState(false);
  const [text, setText] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [category, setCategory] = useState('全部');
  const [search, setSearch] = useState('');
  const [start, setStart] = useState(today);
  const [end, setEnd] = useState(today);
  const [dates, setDates] = useState(false);
  const [logoutError, setLogoutError] = useState('');
  const [loggingOut, setLoggingOut] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const queryInFlight = useRef(false);
  const bottom = useRef<HTMLDivElement>(null);
  const admin = session.identity.roles.includes('config_admin');
  useEffect(() => {
    if (page === 'admin' || page === 'deployment' || page === 'ai-model') return;
    const abort = new AbortController();
    api<{applications: typeof services}>('/api/v1/applications', session.token, undefined, abort.signal).then(body => {
      setServices(body.applications); setCatalogError('');
    }).catch(cause => {if (!abort.signal.aborted) {
      if (cause instanceof SessionExpired) onLogout('登录已过期，请重新登录。');
      else {setServices([]); setCatalogError(cause instanceof Error ? cause.message : '应用目录暂不可用');}
    }});
    return () => abort.abort();
  }, [page, session.token, onLogout]);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    let active = true;
    async function verify() {
      try {await api('/api/v1/me', session.token);}
      catch (cause) {if (active && cause instanceof SessionExpired) onLogout('登录已过期，请重新登录。');}
    }
    const timer = window.setInterval(() => void verify(), 60000);
    const focus = () => void verify(); window.addEventListener('focus', focus);
    return () => {active = false; window.clearInterval(timer); window.removeEventListener('focus', focus);};
  }, [session.token, onLogout]);
  useEffect(() => {bottom.current?.scrollIntoView({behavior: 'smooth', block: 'end'});}, [turns, busy]);
  function navigate(next: string) {
    if ((next !== page || next === 'chat') && (editorDirty || requirementDirty || runtimeDirty) && !window.confirm('当前有未保存或待核对的操作，是否离开？已保存内容可以恢复，待核对操作请保留原请求。')) return false;
    if (next === 'admin' && next !== page) setManagementLocation({});
    setPage(next); setSidebar(false);
    window.history.replaceState(null, '', window.location.pathname + window.location.search + (next === 'admin' ? '#workbench' : ''));
    return true;
  }
  function newChat() {if (!busy && navigate('chat')) {setTurns([]); setText(''); setActiveApplication(null); setConstructionSubject(null); setDates(false);}}
  async function logout() {
    if ((editorDirty || requirementDirty || runtimeDirty) && !window.confirm('当前有未保存或待核对操作，确定退出？')) return;
    setLoggingOut(true); setLogoutError(''); controller.current?.abort();
    try {if (session.token.startsWith('demo_')) await api('/api/v1/auth/demo/logout', session.token, {}); onLogout();}
    catch (cause) {setLogoutError(cause instanceof Error ? cause.message : '退出失败，请重试');}
    finally {setLoggingOut(false);}
  }
  async function query(event: FormEvent, byDate = false) {
    event.preventDefault();
    const request: ScheduleQuery = byDate ? {kind: 'dates', start, end} : {kind: 'text', text: text.trim()};
    if (request.kind === 'text' && !request.text) return;
    await runQuery({...request, applicationId: activeApplication});
  }
  async function runQuery(request: ScheduleQuery, retryId?: number) {
    if (busy || loggingOut || queryInFlight.current) return;
    queryInFlight.current = true;
    if (runtimeDirty) {
      const paused = await runtimePause.current?.();
      if (!paused) {queryInFlight.current = false; return;}
      setRuntimeDirty(false);
    }
    if (request.kind === 'text' && /^(?:打开|查看|返回|继续)?(?:我的工作|我的待办|刚才的任务|刚才|未完成任务|续填)[。！! ]*$/.test(request.text.trim())) {
      setWorkOpen(true); setText(''); queryInFlight.current = false; return;
    }
    const question = request.kind === 'dates' ? `${request.start} 至 ${request.end}的课表` : request.text;
    const id = retryId ?? Date.now(); const abort = new AbortController(); controller.current = abort;
    const timer = window.setTimeout(() => abort.abort(), 16000);
    setBusy(true); if (retryId === undefined) setText(''); setPage('chat');
    setTurns(current => retryId === undefined ? [...current, {id, question, request}].slice(-30) : current.map(turn => turn.id === id ? {id, question, request} : turn));
    const queryStarted = performance.now();
    try {
      let applicationId = request.applicationId;
      let queryText = request.kind === 'text' ? request.text : '';
      if (request.kind === 'text') {
        const resolution = await api<ApplicationResolution>('/api/v1/assistant/resolve', session.token, {
          text: request.text, current_application_id: request.applicationId ?? null,
          selected_application_id: request.selectedApplicationId ?? null,
          construction_subject: constructionSubject,
        }, abort.signal);
        if (resolution.construction_subject) setConstructionSubject(resolution.construction_subject);
        if (resolution.runtime_action) {setRuntimeMode(resolution.runtime_action); setDates(false);}
        setTurns(current => current.map(turn => turn.id === id ? {...turn, resolution,
          applicationName: resolution.application_name ?? '校园助手', elapsedMs: performance.now() - queryStarted} : turn));
        if (['selected', 'query'].includes(resolution.status) && resolution.application_id) {setActiveApplication(resolution.application_id); setConstructionSubject(null);}
        else if (['reset', 'clarification'].includes(resolution.status)) {setActiveApplication(null); setDates(false);}
        if (resolution.status !== 'query' || !resolution.application_id || !resolution.query_text) return;
        applicationId = resolution.application_id; queryText = resolution.query_text;
        setTurns(current => current.map(turn => turn.id === id ? {...turn, request: {...request, applicationId}} : turn));
      }
      if (!applicationId) throw new Error('请先选择应用，再使用日期查询。');
      const base = applicationId === 'schedule' ? '/api/v1/course-schedule' : `/api/v1/applications/${applicationId}`;
      const path = request.kind === 'dates' ? `${base}${applicationId === 'schedule' ? '' : '/schedule'}?${new URLSearchParams({start_date: request.start, end_date: request.end})}` : `${base}/query`;
      const result = await api<ScheduleResponse>(path, session.token, request.kind === 'dates' ? undefined : {text: queryText}, abort.signal);
      setTurns(current => current.map(turn => turn.id === id ? {...turn, response: result,
        applicationName: turn.applicationName ?? services.find(service => service.id === applicationId)?.name ?? '校园助手', elapsedMs: performance.now() - queryStarted} : turn));
    } catch (cause) {
      if (cause instanceof SessionExpired) {onLogout('登录已过期，请重新登录。'); return;}
      setTurns(current => current.map(turn => turn.id === id ? {...turn, error: cause instanceof Error ? cause.message : '请求失败'} : turn));
    } finally {window.clearTimeout(timer); queryInFlight.current = false; setBusy(false);}
  }
  function useApplication(id: string) {
    if (busy || !navigate('chat')) return;
    setActiveApplication(id === '00000000-0000-4000-8000-000000000001' ? 'schedule' : id);
    setTurns([]); setText(''); setDates(false); setRuntimeMode('fill'); setWorkTarget(null);
  }
  async function resumeWork(target: WorkTarget) {
    if (busy || loggingOut || queryInFlight.current) return;
    if (runtimeDirty && !await runtimePause.current?.()) return;
    setRuntimeDirty(false); setWorkTarget(target); setActiveApplication(target.applicationId); setRuntimeMode(target.mode);
    setDates(false); setWorkOpen(false); setPage('chat');
  }
  async function releaseApplication() {
    if (busy || runtimeDirty && !await runtimePause.current?.()) return;
    setRuntimeDirty(false); setActiveApplication(null); setDates(false);
  }
  function openConstruction() {
    if (!navigate('chat')) return;
    setConstructionSubject(null); setRequirementsOpen(true);
  }
  function openManagement(resolution: ApplicationResolution) {
    if (!admin || !navigate('admin')) return;
    setManagementLocation({applicationId: resolution.application_id === 'schedule'
      ? '00000000-0000-4000-8000-000000000001' : resolution.application_id ?? undefined, tab: resolution.tab});
  }
  const management = admin ? [
    {id: 'application-design', icon: '⚙', name: '应用工作台', category: '应用管理', desc: '设计暂存、预览、发布即生效及版本回滚。', ready: true},
    {id: 'server-deployment', icon: '↥', name: '服务器部署', category: '应用管理', desc: '本地 SSH 指纹核对、只读检查及安装/更新计划审阅。', ready: true},
    {id: 'ai-model-settings', icon: '✦', name: 'AI 模型配置', category: '应用管理', desc: '配置模型接口、密钥及调用限制，测试设计模型连接。', ready: true},
  ] : [];
  const visible = [...services, ...management].filter(service => (category === '全部' || service.category === category) && `${service.name}${service.desc}`.includes(search));
  const scheduleApp = services.find(service => service.id === activeApplication && service.ready);
  const activeName = [...turns].reverse().find(turn => turn.resolution?.application_id === activeApplication &&
    ['query', 'selected'].includes(turn.resolution.status))?.applicationName ?? scheduleApp?.name;
  const activeType = workTarget?.applicationId === activeApplication ? workTarget.typeId : [...turns].reverse().find(turn => turn.resolution?.application_id === activeApplication && turn.resolution.type_id)?.resolution?.type_id ?? scheduleApp?.type_id;
  const starterPrompts = activeApplication ? activeType === 'platform.leave-approval' ? ['填写请假申请', '查看我的申请进度', '这个应用有哪些功能'] : activeType === 'platform.form' ? ['填写登记表', '查看我的登记记录', '这个应用有哪些功能'] : ['今天有什么课', '明天课表', '这个应用有哪些功能'] : ['有哪些应用', '今天有什么课', ...(admin ? ['打开应用工作台'] : ['选择一个应用'])];
  return <div className="workspace">
    {sidebar && <button className="sidebar-backdrop" aria-label="关闭导航" onClick={() => setSidebar(false)} />}
    <aside className={`sidebar ${sidebar ? 'open' : ''}`}><a className="wordmark" href="#" onClick={event => {event.preventDefault(); newChat();}}>✳ <span>校园智能工作台</span></a><div className="workspace-label">CAMPUS WORKSPACE <span>开发版</span></div>
      <button className="new-chat" disabled={busy} onClick={newChat}>＋ 开始新对话</button><nav aria-label="主导航"><button disabled={busy} className={page === 'chat' ? 'active' : ''} onClick={() => {if (navigate('chat')) {setActiveApplication(null); setDates(false);}}}><span>◉</span>校园助手</button><button className={page === 'services' || page === 'deployment' || page === 'ai-model' ? 'active' : ''} onClick={() => navigate('services')}><span>▦</span>微服务广场 <small>{services.length}</small></button>{admin && <button className={page === 'admin' ? 'active' : ''} onClick={() => navigate('admin')}><span>⚙</span>应用工作台</button>}</nav>
      <div className="sidebar-section">已开通微服务</div>{services.filter(service => service.ready).map(service => <button key={service.id} disabled={busy} className="service-shortcut" onClick={() => useApplication(service.id)}><span className="mini-icon">▦</span><span>{service.name}<small>{service.category} · 已开通</small></span><span className="online-dot" /></button>)}
      <div className="sidebar-section">本次查询 <span>{turns.length}</span></div><div className="query-history">{turns.length ? [...turns].reverse().map(turn => <button key={turn.id} onClick={() => {navigate('chat'); window.setTimeout(() => document.getElementById(`turn-${turn.id}`)?.scrollIntoView({behavior: 'smooth'}), 0);}}>{turn.question}</button>) : <p>还没有查询记录<br />从一个问题开始吧</p>}</div>
      <div className="profile"><div className="avatar">{session.name.slice(0, 1)}</div><div><strong>{session.name}</strong><small>{admin ? '配置管理员' : '学生'} · {session.identity.mode === 'demo' ? '模拟身份' : '校园认证'}</small></div><button aria-label="退出登录" title="退出登录" disabled={loggingOut} onClick={() => void logout()}>⇥</button></div>{logoutError && <div role="alert" className="error logout-error">{logoutError}<button onClick={() => onLogout('本地已退出，服务端演示会话将在一小时内到期。')}>仅退出本页</button></div>}
    </aside>
    <main className="workspace-main"><header className="topbar"><div><button className="menu-toggle" aria-label="打开导航" aria-expanded={sidebar} onClick={() => setSidebar(true)}>☰</button><span>{page === 'services' ? '微服务广场' : page === 'admin' ? '应用工作台' : page === 'ai-model' ? 'AI 模型配置' : page === 'deployment' ? '服务器部署' : '校园助手'} <span className="topbar-slash">/</span> <small>{page === 'chat' ? activeName ?? '全局应用匹配' : '校园智能工作台'}</small></span></div><span className="environment"><i />{session.identity.mode === 'demo' ? '演示环境' : '校园认证'}</span></header>
      {catalogError && <p role="alert" className="error">{catalogError}</p>}
      {page === 'admin' && admin && managementLocation.taskId && <section className="panel requirement-context" aria-label="当前建设任务"><p>正在配置建设需求关联的应用。完成配置、试跑与发布后，可返回需求核对当前进度。</p><button onClick={() => {if (navigate('chat')) {setRequirementsOpen(true); setConstructionSubject(null);}}}>返回建设需求</button></section>}
      {page === 'services' ? <section className="service-page"><div className="overline">CAMPUS AGENTS</div><h1>每一件校园事，都有专属助手。</h1><p className="muted">校园微服务，以智能体的方式连接你与校园。</p><div className="service-tools"><div className="category-tabs">{['全部', '学习服务', '校园事务', '校园生活', ...(admin ? ['应用管理'] : [])].map(item => <button aria-pressed={category === item} key={item} onClick={() => setCategory(item)}>{item}</button>)}</div><label className="service-search"><span className="sr-only">搜索微服务</span><input placeholder="搜索微服务…" value={search} onChange={event => setSearch(event.target.value)} /></label></div><div className="service-grid">{visible.map(service => <article className={`service-card ${service.ready ? 'ready' : ''}`} key={service.id}><div className="service-card-top"><span className={`agent-icon ${service.id}`}>{service.icon}</span><span className={`status-tag ${service.ready ? 'available' : ''}`}>{service.category === '应用管理' ? '管理入口' : service.ready ? '已开通' : '待接入'}</span></div><h2>{service.name}</h2><span className="muted service-category">{service.category}</span><p>{service.desc}</p><button disabled={!service.ready || busy} onClick={() => {if (service.id === 'application-design') navigate('admin'); else if (service.id === 'ai-model-settings') navigate('ai-model'); else if (service.id === 'server-deployment') navigate('deployment'); else useApplication(service.id);}}>{service.category === '应用管理' ? '进入管理 ↗' : service.ready ? '使用微服务 ↗' : '暂未开放'}</button></article>)}</div>{!visible.length && <p className="empty-state">没有匹配的微服务，请更换关键词。</p>}<p className="footnote">“微服务”为校园应用名称。待接入项目仅展示规划，不提供申请、审批或预约功能。</p></section>
      : (page === 'admin' || page === 'deployment' || page === 'ai-model') && admin ? <div className={`admin-page ${page === 'admin' ? 'workbench-page' : ''}`}><div className="page-heading"><div><div className="overline" hidden={page === 'admin'}>APPLICATION MANAGEMENT</div><h1>{page === 'ai-model' ? '配置模型，辅助应用设计。' : page === 'deployment' ? '连接服务器，先检查再操作。' : '应用工作台'}</h1><p className="muted">{page === 'admin' ? '选择应用，配置、试跑并发布。' : '应用发布立即生效；服务器部署是独立运维能力。'}</p></div><button onClick={() => navigate('services')}>返回微服务广场</button></div><ApplicationManagement location={managementLocation} onUse={useApplication} onOpenRequirements={openConstruction} key={page} initialToken={session.token} deployment={page === 'deployment'} aiModel={page === 'ai-model'} onDirtyChange={setEditorDirty} onSessionExpired={() => onLogout('登录已过期，请重新登录。')} /></div>
      : <div className={`chat-page ${turns.length ? 'has-turns' : ''}`}>
        {!turns.length && <section className="welcome"><div className="welcome-mark">✳</div><div className="overline">YOUR CAMPUS COMPANION</div><h1>{session.name}，今天想办理什么？</h1><p>先找到应用，再使用功能。可以说“有哪些应用”或“打开某个应用”。</p></section>}
        <div className="assistant-context" aria-label="当前应用"><span>{activeApplication ? `已锁定：${activeName ?? activeApplication}` : '校园助手 · 自动匹配应用'}</span>
            <button className="quiet-button" disabled={busy} onClick={() => setWorkOpen(true)}>我的工作</button>
          {admin && <button className="quiet-button" onClick={() => setRequirementsOpen(true)}>我的建设需求</button>}
          {activeApplication && <button className="quiet-button" disabled={busy} onClick={() => void runQuery({kind: 'text', text: '这个应用有哪些功能',
            applicationId: activeApplication, selectedApplicationId: activeApplication})}>查看功能</button>}
          {activeApplication && <button className="quiet-button" disabled={busy} onClick={() => {setActiveApplication(null); setDates(false);}}>切换应用</button>}</div>
        {!!turns.length && <div className="conversation" aria-live="polite">{turns.map(turn => <AssistantTurn key={turn.id} {...turn}
          busy={busy || loggingOut} onRetry={() => void runQuery(turn.request, turn.id)} onSuggestion={setText}
          onChooseApplication={id => void runQuery({...turn.request, selectedApplicationId: id}, turn.id)}
          onFeature={text => {if (turn.resolution?.application_id) void runQuery({kind: 'text', text,
            applicationId: turn.resolution.application_id, selectedApplicationId: turn.resolution.application_id});}}
          onConstruction={admin ? subject => {setConstructionSubject(subject); setRequirementsOpen(true);} : undefined}
          onNavigate={() => {if (turn.resolution) openManagement(turn.resolution);}} />)}<div ref={bottom} /></div>}
        {workOpen && <MyWork token={session.token} onResume={target => void resumeWork(target)} onClose={() => setWorkOpen(false)} onExpired={() => onLogout('登录已过期，请重新登录。')} />}
        {activeApplication && activeType === 'platform.form' && <FormRuntime key={`${activeApplication}:${workTarget?.applicationId === activeApplication ? workTarget.draftId ?? '' : ''}`} token={session.token} applicationId={activeApplication}
          scope={`${session.identity.tenant_id}:${session.identity.subject}`} mode={runtimeMode} draftId={workTarget?.applicationId === activeApplication ? workTarget.draftId : undefined} onPauseReady={registerPause} onDirtyChange={setRuntimeDirty} onExpired={() => onLogout('登录已过期，请重新登录。')} />}
        {activeApplication && activeType === 'platform.leave-approval' && <ApprovalRuntime key={`${activeApplication}:${workTarget?.applicationId === activeApplication ? workTarget.draftId ?? '' : ''}`} token={session.token} applicationId={activeApplication}
          scope={`${session.identity.tenant_id}:${session.identity.subject}`} admin={admin} mode={runtimeMode} draftId={workTarget?.applicationId === activeApplication ? workTarget.draftId : undefined} onPauseReady={registerPause} onDirtyChange={setRuntimeDirty} onExpired={() => onLogout('登录已过期，请重新登录。')} />}
        {admin && <RequirementPanel token={session.token} scope={`${session.identity.tenant_id}:${session.identity.subject}`} open={requirementsOpen} subject={constructionSubject}
          onDirtyChange={setRequirementDirty} onUse={useApplication} onConfigure={(id, taskId) => {
            if (!navigate('admin')) return; setConstructionSubject(null); setManagementLocation({applicationId: id, tab: 'basic', taskId});
            window.history.replaceState(null, '', window.location.pathname + window.location.search + '#workbench?' + new URLSearchParams({application: id, task: taskId}));
          }}
          onClose={() => setRequirementsOpen(false)} onExpired={() => onLogout('登录已过期，请重新登录。')} />}

        <div className="composer-area"><form className="composer" onSubmit={event => void query(event)}><label className="sr-only" htmlFor="question">向校园助手提问</label><textarea id="question" placeholder={activeApplication ? '描述要使用的功能，也可以说“打开其他应用”…' : '例如：有哪些应用、打开课程查询、配置某个应用…'} maxLength={200} value={text} disabled={busy || loggingOut} onChange={event => setText(event.target.value)} onKeyDown={event => {if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {event.preventDefault(); event.currentTarget.form?.requestSubmit();}}} /><div className="composer-toolbar"><div><span className="composer-agent">▦ {activeName ?? '自动匹配应用'}</span><button type="button" className="date-toggle" disabled={!activeApplication || busy || ['platform.form', 'platform.leave-approval'].includes(activeType ?? '')} title={!activeApplication ? '请先选择应用' : undefined} aria-expanded={dates} onClick={() => setDates(!dates)}>▤ 日期查询</button></div><button className="send-button" aria-label="发送查询" disabled={busy || loggingOut || !text.trim()}>{busy ? '…' : '↑'}</button></div></form>
          {dates && <form className="date-picker" onSubmit={event => void query(event, true)}><label>开始日期<input type="date" required value={start} onChange={event => setStart(event.target.value)} /></label><label>结束日期<input type="date" required min={start} value={end} onChange={event => setEnd(event.target.value)} /></label><button disabled={busy || loggingOut}>查询</button><small>最多 31 天 · 上海时区</small></form>}
          {!turns.length && <div className="starter-prompts"><span>从这里开始</span>{starterPrompts.map((item, i) => <button key={item} disabled={busy} onClick={() => setText(item)}><span>{['◷', '▦', '▤'][i]}</span>{item}<span>↗</span></button>)}</div>}<p className="composer-note">先匹配应用与功能 · 规则优先 · 必要时 AI 辅助查询 · {session.identity.mode === 'demo' ? '演示数据仅供体验' : '按校园身份授权查询'} · 点击 ↑ 发送</p>
        </div>
      </div>}
    </main></div>;
}
function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [notice, setNotice] = useState('');
  return session ? <Workspace session={session} onLogout={message => {setSession(null); setNotice(message ?? '');}} /> : <Login notice={notice} onLogin={next => {setNotice(''); setSession(next);}} />;
}
createRoot(document.getElementById('root')!).render(<App />);
