import {useCallback, useEffect, useRef, useState, type FormEvent} from 'react';
import {api, HTTPError, SessionExpired} from './api';
import {ConfigAdmin} from './ConfigAdmin';
import {ManagementDialog} from './ManagementDialog';
import type {ManagementLocation} from './assistant';

type Application = {application_id: string; name: string; type_id: string; type_version: string; legacy: boolean; current_release_id: string | null};
type ApplicationType = {type_id: string; type_version: string; title: string; installed: boolean};
type Template = {id: string; type_id: string; name: string; description: string};
type CreateCommand = {type_id: string; type_version: string; template_id: string; name: string; idempotency_key: string};
const editors: Record<string, typeof ConfigAdmin | undefined> = {'campus.course-query': ConfigAdmin};

export function ApplicationWorkbench({token, onSessionExpired, onDirtyChange, onUse, location}: {
  token: string; onSessionExpired: () => void; onDirtyChange: (dirty: boolean) => void; onUse?: (id: string) => void; location?: ManagementLocation;
}) {
  const [items, setItems] = useState<Application[]>([]);
  const [types, setTypes] = useState<ApplicationType[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selected, setSelected] = useState('');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [childDirty, setChildDirty] = useState(false);
  const [locked, setLocked] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [template, setTemplate] = useState('course-standard');
  const [selectedType, setSelectedType] = useState('campus.course-query');
  const [creating, setCreating] = useState(false);
  const [pending, setPending] = useState<CreateCommand | null>(null);
  const [registryRevision, setRegistryRevision] = useState(0);
  const mounted = useRef(true);
  const callbacks = useRef({onSessionExpired, onDirtyChange});
  callbacks.current = {onSessionExpired, onDirtyChange};

  const report = useCallback((cause: unknown) => {
    if (cause instanceof SessionExpired) callbacks.current.onSessionExpired();
    else setError(cause instanceof Error ? cause.message : '应用列表加载失败，请重试。');
  }, []);
  const refresh = useCallback(async () => {
    const result = await api<{items: Application[]}>('/api/v1/admin/applications', token);
    if (mounted.current) setItems(result.items);
    return result.items;
  }, [token]);
  const updated = useCallback(() => {void refresh().catch(report);}, [refresh, report]);
  useEffect(() => {
    mounted.current = true;
    const abort = new AbortController();
    setLoading(true); setError('');
    void Promise.all([
      api<{items: Application[]}>('/api/v1/admin/applications', token, undefined, AbortSignal.any([abort.signal, AbortSignal.timeout(15000)])),
      api<{items: ApplicationType[]; templates: Template[]}>('/api/v1/admin/application-types', token, undefined, AbortSignal.any([abort.signal, AbortSignal.timeout(15000)])),
    ]).then(([apps, registry]) => {
      if (abort.signal.aborted) return;
      setItems(apps.items); setTypes(registry.items); setTemplates(registry.templates);
      setSelected(apps.items.some(item => item.application_id === location?.applicationId) ? location!.applicationId! : apps.items[0]?.application_id ?? '');
    }).catch(cause => {if (!abort.signal.aborted) report(cause);})
      .finally(() => {if (!abort.signal.aborted) setLoading(false);});
    return () => {mounted.current = false; abort.abort();};
  }, [token, report, registryRevision, location?.applicationId]);
  const dirty = childDirty || !!pending || creating || (createOpen && !!name.trim());
  useEffect(() => {callbacks.current.onDirtyChange(dirty);}, [dirty]);
  useEffect(() => () => callbacks.current.onDirtyChange(false), []);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {if (pending || creating || (createOpen && name.trim())) {event.preventDefault(); event.returnValue = '';}};
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [pending, creating, createOpen, name]);
  function select(id: string) {
    if (id === selected || locked || creating || pending) return;
    if (childDirty && !window.confirm('当前应用有未保存的修改，切换将丢失这些内容。是否继续？')) return;
    setChildDirty(false); setSelected(id); setError('');
  }
  function openCreate() {
    if (locked || childDirty || pending) return;
    setName(''); setCreateOpen(true); setError('');
  }
  function closeCreate() {
    if (creating || pending) return;
    if (name.trim() && !window.confirm('放弃本次新建应用？')) return;
    setName(''); setCreateOpen(false);
  }
  async function create(event?: FormEvent) {
    event?.preventDefault();
    const installed = types.find(item => item.type_id === templates.find(item => item.id === template)?.type_id && item.installed);
    if (!pending && (!installed || !name.trim())) return;
    const command = pending ?? {type_id: installed!.type_id, type_version: installed!.type_version,
      template_id: template, name: name.trim(), idempotency_key: crypto.randomUUID()};
    setPending(command); setCreating(true); setError('');
    let createdSuccessfully = false;
    try {
      const created = await api<{application_id: string}>('/api/v1/admin/applications', token, command);
      createdSuccessfully = true;
      await refresh();
      setSelected(created.application_id); setChildDirty(false); setCreateOpen(false); setName(''); setPending(null);
    } catch (cause) {
      if (!createdSuccessfully && cause instanceof HTTPError && cause.status >= 400 && cause.status < 500 && ![408, 429].includes(cause.status)) setPending(null);
      report(cause);
    }
    finally {setCreating(false);}
  }
  const current = items.find(item => item.application_id === selected);
  const Editor = current ? editors[current.type_id] : undefined;
  const visible = items.filter(item => item.name.includes(search.trim()));
  const sameName = (name: string) => items.filter(item => item.name.trim().toLocaleLowerCase() === name.trim().toLocaleLowerCase()).length > 1;
  return <div className="multi-application-workbench">
    <div className="workbench-intro"><div><h2>管理你的应用</h2><p>选择模板新建应用，配置后试跑发布，即可在微服务广场使用。</p></div>
      <button disabled={loading || locked || childDirty || creating || !!pending || !types.some(item => item.installed && editors[item.type_id])} onClick={openCreate}>＋ 新建应用</button></div>
    {childDirty && !locked && <p className="hint">当前有未保存内容，暂存后即可新建其他应用。</p>}
    {error && !createOpen && <div role="alert" className="management-notice error">{error}<button className="quiet-button" onClick={() => {
      if (!types.length) setRegistryRevision(value => value + 1); else updated();
    }}>刷新应用列表</button></div>}
    <div className="application-workbench multi-app-layout">
      <aside className="application-list" aria-label="可配置应用">
        <div className="application-list-heading"><h2>应用列表</h2><span>{items.length}</span></div>
        <label><span className="sr-only">搜索应用</span><input type="search" placeholder="搜索应用名称" value={search} onChange={event => setSearch(event.target.value)} /></label>
        {visible.map(item => <button key={item.application_id} title={item.application_id} disabled={locked || creating || !!pending}
          className={`application-list-item ${item.application_id === selected ? 'selected' : ''}`}
          aria-current={item.application_id === selected ? 'true' : undefined} onClick={() => select(item.application_id)}>
          <span className="mini-icon">▦</span><span><strong>{item.name}</strong><small>{item.current_release_id ? '已发布' : item.legacy ? '内置应用' : '草稿 · 未发布'}{sameName(item.name) && ` · ${item.application_id.slice(0, 8)}`}</small></span></button>)}
        {!visible.length && <p className="hint">{loading ? '正在加载…' : items.length ? '没有匹配的应用。' : '还没有应用，请点击上方新建。'}</p>}
        <p className="application-list-note">每个应用独立保存配置与发布记录。</p>
      </aside>
      {current ? <div className="selected-application-editor">
        <div className="application-type-label">应用类型：{types.find(item => item.type_id === current.type_id)?.title ?? current.type_id} · {current.type_version}
          {!current.legacy && <span> · 课程查询模板，共用当前教务数据服务</span>}</div>
        {Editor ? <Editor key={selected} initialToken={token} applicationId={selected} initialTab={selected === location?.applicationId ? location.tab : undefined} embedded
          onDirtyChange={setChildDirty} onNavigationLock={setLocked} onUpdated={updated} onUse={onUse} onSessionExpired={onSessionExpired} />
          : <p role="alert" className="management-notice error">尚未安装此应用类型的管理界面，暂不能编辑。</p>}
      </div> : <div className="management-empty" role="status">{loading ? '正在加载应用工作台…' : '选择左侧应用，或新建一个应用开始配置。'}</div>}
    </div>
    {createOpen && <ManagementDialog title="从模板新建应用" busy={creating || !!pending} onClose={closeCreate}>
      <form onSubmit={event => void create(event)}>
        <p className="hint">选择已安装的应用类型和模板。创建后先保存为独立草稿，发布后用户才可使用。</p>
        <fieldset disabled={creating || !!pending} className="management-fields">
          <label>应用类型<select aria-label="应用类型" value={selectedType} onChange={event => {
            setSelectedType(event.target.value); setTemplate(templates.find(item => item.type_id === event.target.value)?.id ?? '');
          }}>
            {types.filter(item => item.installed && editors[item.type_id]).map(item => <option key={item.type_id} value={item.type_id}>{item.title}</option>)}</select></label>
          <div className="application-template-grid">{templates.filter(item => item.type_id === selectedType).map(item => <label key={item.id} className={`application-template ${template === item.id ? 'selected' : ''}`}>
            <input type="radio" name="application-template" value={item.id} checked={template === item.id} onChange={() => setTemplate(item.id)} />
            <strong>{item.name}</strong><span>{item.description}</span></label>)}</div>
          <label>应用名称<input aria-label="新应用名称" autoFocus required maxLength={30} value={name} onChange={event => setName(event.target.value)} placeholder="例如：我的课程查询" /></label>
          <p className="hint">当前课程模板查询登录用户本人的课程，使用现有教务数据源；表单、审批等类型待安装后开放。</p>
        </fieldset>
        {error && <p role="alert" className="error">{error}</p>}
        {pending && !creating && <p role="alert">创建结果待确认。点击下方按钮使用同一请求号核对，避免重复创建。</p>}
        <div className="management-dialog-actions"><button type="button" className="quiet-button" disabled={creating || !!pending} onClick={closeCreate}>取消</button>
          <button type="submit" disabled={creating || (!pending && !name.trim())}>{creating ? '正在创建…' : pending ? '确认上次创建结果' : '创建并开始配置'}</button></div>
      </form>
    </ManagementDialog>}
  </div>;
}
