import {useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode} from 'react';
import {api, HTTPError, SessionExpired} from './api';

export type DataField = {id: string; key: string; label: string; data_type: 'string' | 'integer' | 'decimal' | 'boolean' | 'date' | 'enum' | 'enum_list'; nullable: boolean;
  constraints: {max_length?: number; minimum?: number; maximum?: number; precision?: number}; options: {value: string; label: string}[]};
export type Control = {id: string; field_id: string; component: 'text' | 'textarea' | 'number' | 'checkbox' | 'select' | 'multiselect' | 'date'; label: string; help: string; required: boolean;
  readonly: false; layout: {mobile: number; tablet: number; desktop: number}};
export type FormConfig = {schema_version: '1.0.0'; application: {name: string; description: string; category: string; visible_roles: string[]};
  model: {id: string; name: string; adapter_id: 'form.records'; fields: DataField[]};
  form: {id: string; name: string; model_id: string; submit_action_id: 'form.records.submit' | 'approval.apply'; sections: {id: string; title: string; fields: Control[]}[]}};

export function formatFieldValue(field: DataField, value: unknown): string {
  if (value === null || value === undefined || value === '') return '未填写';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (Array.isArray(value)) return value.map(item => field.options.find(option => option.value === item)?.label ?? String(item)).join('、');
  return field.options.find(option => option.value === value)?.label ?? String(value);
}

export function FormFields({config, values, onChange, disabled = false}: {config: FormConfig; values: Record<string, unknown>; onChange: (key: string, value: unknown) => void; disabled?: boolean}) {
  const fields = new Map(config.model.fields.map(field => [field.id, field]));
  return <div className="form-fields">{config.form.sections.map(section => <fieldset disabled={disabled} key={section.id}><legend>{section.title}</legend><div className="form-field-grid">{section.fields.map(control => {
    const field = fields.get(control.field_id); if (!field) return null;
    const value = values[field.key]; const label = control.label + (control.required ? ' *' : '');
    const style = {'--field-mobile': control.layout.mobile, '--field-tablet': control.layout.tablet, '--field-desktop': control.layout.desktop} as CSSProperties;
    return <label key={control.id} style={style} className="form-field"><span>{label}</span>
      {control.component === 'textarea' ? <textarea required={control.required} maxLength={field.constraints.max_length ?? 1000} value={String(value ?? '')} onChange={event => onChange(field.key, event.target.value)} />
        : control.component === 'checkbox' ? <input type="checkbox" checked={value === true} onChange={event => onChange(field.key, event.target.checked)} />
          : control.component === 'select' ? <select required={control.required} value={String(value ?? '')} onChange={event => onChange(field.key, event.target.value)}><option value="">请选择</option>{field.options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
            : control.component === 'multiselect' ? <select multiple required={control.required} value={Array.isArray(value) ? value as string[] : []} onChange={event => onChange(field.key, [...event.target.selectedOptions].map(option => option.value))}>{field.options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
              : <input type={control.component === 'number' ? 'number' : control.component === 'date' ? 'date' : 'text'} required={control.required}
                min={field.constraints.minimum} max={field.constraints.maximum} maxLength={field.constraints.max_length ?? 1000} step={field.data_type === 'integer' ? 1 : 'any'}
                value={String(value ?? '')} onChange={event => onChange(field.key, control.component === 'number' ? event.target.value === '' ? null : Number(event.target.value) : event.target.value)} />}
      {control.help && <small>{control.help}</small>}{control.component === 'multiselect' && <small>可多选，键盘使用 Ctrl / Command 选择多项。</small>}</label>;
  })}</div></fieldset>)}</div>;
}

export function RecordBrowser({token, applicationId, admin = false, onExpired, version = 0}: {token: string; applicationId: string; admin?: boolean; onExpired: () => void; version?: number}) {
  const [items, setItems] = useState<{id: string; release_id: string; owner_id: string; created_at: string}[]>([]);
  const [page, setPage] = useState(1); const [total, setTotal] = useState(0); const [error, setError] = useState(''); const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<{id: string; values: Record<string, unknown>; content: FormConfig; release_id: string} | null>(null);
  const [refresh, setRefresh] = useState(0); const request = useRef(0);
  const base = admin ? `/api/v1/admin/applications/${applicationId}/form/records` : `/api/v1/applications/${applicationId}/records`;
  const expired = useRef(onExpired); expired.current = onExpired;
  useEffect(() => {
    const own = ++request.current; const abort = new AbortController(); setLoading(true); setError(''); setDetail(null);
    api<{items: typeof items; total: number}>(`${base}?page=${page}`, token, undefined, abort.signal).then(result => {
      if (own === request.current) {setItems(result.items); setTotal(result.total);}
    }).catch(cause => {if (!abort.signal.aborted) {if (cause instanceof SessionExpired) expired.current(); else setError(cause.message);}})
      .finally(() => {if (!abort.signal.aborted && own === request.current) setLoading(false);});
    return () => {request.current++; abort.abort();};
  }, [base, token, page, version, refresh]);
  async function open(id: string) {
    const own = ++request.current;
    try {const result = await api<NonNullable<typeof detail>>(`${base}/${id}`, token); if (own === request.current) setDetail(result);}
    catch (cause) {if (own === request.current) {if (cause instanceof SessionExpired) expired.current(); else setError(cause instanceof Error ? cause.message : '读取失败');}}
  }
  return <section className="panel record-browser" aria-label={admin ? '应用登记记录' : '我的登记记录'}><h3>{admin ? '应用登记记录' : '我的登记记录'}</h3>
    <button onClick={() => setRefresh(refresh + 1)}>刷新记录</button>{loading && <p role="status">正在加载登记记录…</p>}{error && <p role="alert" className="error">{error}</p>}
    {!loading && !error && !items.length && <p>暂无登记记录。</p>}
    {items.map(item => <article className="requirement-item" key={item.id}><strong>登记 {item.id.slice(0, 8)}</strong><p>{new Date(item.created_at).toLocaleString()}{admin && ` · 提交者 ${item.owner_id}`}</p><button disabled={loading} onClick={() => void open(item.id)}>查看登记详情</button></article>)}
    <div className="admin-toolbar"><button disabled={loading || page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页 · 共 {total} 条</span><button disabled={loading || page * 20 >= total} onClick={() => setPage(page + 1)}>下一页</button></div>
    {detail && <div aria-label="登记详情"><h4>登记详情 · {detail.id.slice(0, 8)}</h4><p className="muted">按提交时的发布版本展示：{detail.release_id}</p><dl>{detail.content.model.fields.map(field => <div key={field.id}><dt>{field.label}</dt><dd>{detail.values[field.key] === null || detail.values[field.key] === undefined ? '未填写' : typeof detail.values[field.key] === 'boolean' ? detail.values[field.key] ? '是' : '否' : Array.isArray(detail.values[field.key]) ? (detail.values[field.key] as string[]).map(value => field.options.find(option => option.value === value)?.label ?? value).join('、') : field.options.find(option => option.value === detail.values[field.key])?.label ?? String(detail.values[field.key])}</dd></div>)}</dl></div>}
  </section>;
}

export function FormRuntime({token, applicationId, scope, onExpired, onDirtyChange, mode = 'fill', noun = '登记', renderRecords, draftId, onPauseReady}: {
  token: string; applicationId: string; scope: string; onExpired: () => void; onDirtyChange: (value: boolean) => void; mode?: 'fill' | 'records';
  noun?: string; renderRecords?: (version: number) => ReactNode;
  draftId?: string; onPauseReady?: (pause: (() => Promise<boolean>) | null) => void;
}) {
  type Command = {release_id: string; values: Record<string, unknown>; idempotency_key: string; draft_id?: string; draft_revision?: number};
  type DraftCommand = {draftId: string; body: {release_id: string; values: Record<string, unknown>; expected_revision: number; idempotency_key: string; cancel?: boolean}};
  const key = `campus-form-pending:${scope}:${applicationId}`;
  const [pending, setPending] = useState<Command | null>(() => {
    try {const value = JSON.parse(window.sessionStorage.getItem(key) ?? 'null'); return value && typeof value.release_id === 'string' && typeof value.idempotency_key === 'string' && value.values && typeof value.values === 'object' ? value : null;} catch {return null;}
  });
  const [definition, setDefinition] = useState<{content: FormConfig; release_id: string; accepting_submissions?: boolean} | null>(null);
  const draftCommandKey = `campus-form-draft-command:${scope}:${applicationId}`;
  const [draftPending, setDraftPending] = useState<DraftCommand | null>(() => {
    try {const value = JSON.parse(sessionStorage.getItem(draftCommandKey) ?? 'null'); return value && typeof value.draftId === 'string' && typeof value.body?.idempotency_key === 'string' && value.body.values ? value : null;} catch {return null;}
  });
  const [savedDraft, setSavedDraft] = useState<{id: string; revision: number; release_id: string} | null>(pending?.draft_id ? {id: pending.draft_id, revision: pending.draft_revision ?? 0, release_id: pending.release_id} : null);
  const savedDraftRef = useRef(savedDraft); savedDraftRef.current = savedDraft;
  const [loadingDefinition, setLoadingDefinition] = useState(true); const loadEpoch = useRef(0);
  const [draftNotice, setDraftNotice] = useState('');
  const [inputBackup, setInputBackup] = useState<Record<string, unknown> | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>(pending?.values ?? draftPending?.body.values ?? {}); const [changed, setChanged] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [receipt, setReceipt] = useState('');
  const [review, setReview] = useState(false); const [revision, setRevision] = useState(0); const [showRecords, setShowRecords] = useState(mode === 'records');
  const callbacks = useRef({onExpired, onDirtyChange}); callbacks.current = {onExpired, onDirtyChange}; const lock = useRef(false); const alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const dirty = changed || !!pending || !!draftPending || busy;
  useEffect(() => {callbacks.current.onDirtyChange(dirty);}, [dirty]);
  useEffect(() => () => callbacks.current.onDirtyChange(false), []);
  useEffect(() => {setShowRecords(mode === 'records');}, [mode]);
  const load = useCallback(async (restore = false) => {
    const own = ++loadEpoch.current; setLoadingDefinition(true);
    try {
      const next = await api<NonNullable<typeof definition>>(`/api/v1/applications/${applicationId}/form`, token);
      if (!alive.current || own !== loadEpoch.current) return;
      setError('');
      const restoreId = savedDraftRef.current?.id ?? draftId;
      if (restore && restoreId && !pending && !draftPending) {
        const draft = await api<{id: string; revision: number; status: string; release_id: string; content: FormConfig; values_json: Record<string, unknown>; record_id: string | null}>(`/api/v1/applications/${applicationId}/business-drafts/${restoreId}`, token);
        if (!alive.current || own !== loadEpoch.current) return;
        setDefinition(next);
        if (draft.status !== 'active') {setSavedDraft(null); setValues({}); setChanged(false); setReview(false); if (draft.record_id) setReceipt(draft.record_id); setDraftNotice(`此草稿已${draft.status === 'submitted' ? '提交，记录编号：' + draft.record_id : '取消'}，请从已有记录核对。`); setShowRecords(true); return;}
        setSavedDraft({id: draft.id, revision: draft.revision, release_id: draft.release_id}); setValues(draft.values_json); setChanged(false); setShowRecords(false);
        setDraftNotice(`已恢复服务端草稿 · 修订 ${draft.revision}。`);
        if (draft.release_id !== next.release_id) {
          setDefinition({...next, content: draft.content, release_id: draft.release_id, accepting_submissions: false});
          setDraftNotice('表单发布已更新，现按原版本保留草稿。请点击重新加载表单，核对字段及备份后再保存与提交。');
        }
        return;
      }
      setDefinition(next); if (next.accepting_submissions === false) setShowRecords(true);
      if (!restore && savedDraftRef.current) {setChanged(true); setDraftNotice('已加载当前表单，请核对字段及输入备份，先保存填写草稿，再确认提交。');}
      setValues(previous => {
        const keys = new Set(next.content.model.fields.map(field => field.key));
        if (Object.keys(previous).some(key => !keys.has(key))) {setInputBackup(previous); return Object.fromEntries(Object.entries(previous).filter(([key]) => keys.has(key)));}
        return previous;
      });
    } catch (cause) {if (alive.current && own === loadEpoch.current) {if (restore) setDefinition(null); if (cause instanceof SessionExpired) callbacks.current.onExpired(); else setError(cause instanceof Error ? cause.message : '表单加载失败');}}
    finally {if (alive.current && own === loadEpoch.current) setLoadingDefinition(false);}
  }, [applicationId, token, draftId]);
  useEffect(() => {void load(true);}, [load]);
  async function pause(override?: DraftCommand): Promise<boolean> {
    if (pending || lock.current || loadingDefinition) {setError('表单正在加载，或有写入结果待核对，请先完成核对再切换任务。'); return false;}
    if (!changed && !draftPending && !override) return true;
    if (!definition || definition.accepting_submissions === false && !draftPending && !override?.body.cancel) {setError('当前表单不可新保存，请先核对应用状态或重新加载表单；原输入仍保留。'); return false;}
    lock.current = true; setBusy(true); setError('');
    const command = draftPending ?? override ?? {draftId: savedDraft?.id ?? crypto.randomUUID(), body: {release_id: definition.release_id,
      expected_revision: savedDraft?.revision ?? 0, values, idempotency_key: crypto.randomUUID()}};
    try {
      sessionStorage.setItem(draftCommandKey, JSON.stringify(command)); setDraftPending(command);
      const result = await api<{draft_id: string; revision: number; values: Record<string, unknown>}>(`/api/v1/applications/${applicationId}/business-drafts/${command.draftId}`, token, command.body, undefined, 'PUT');
      sessionStorage.removeItem(draftCommandKey); setDraftPending(null);
      setSavedDraft(command.body.cancel ? null : {id: result.draft_id, revision: result.revision, release_id: command.body.release_id}); setValues(command.body.cancel ? {} : result.values); setChanged(false); setReview(false);
      setDraftNotice(command.body.cancel ? '填写草稿已取消，不会提交或启动审批。' : '填写草稿已保存，可查询其他应用，再从“我的工作”返回续填。'); return true;
    } catch (cause) {
      if (cause instanceof HTTPError && [403, 404, 409, 422].includes(cause.status)) {setDraftPending(null); sessionStorage.removeItem(draftCommandKey);}
      if (cause instanceof SessionExpired) callbacks.current.onExpired(); else setError(cause instanceof Error ? cause.message : '草稿保存失败');
      return false;
    } finally {lock.current = false; if (alive.current) setBusy(false);}
  }
  const pauseRef = useRef(pause); pauseRef.current = pause;
  useEffect(() => {onPauseReady?.(() => pauseRef.current()); return () => onPauseReady?.(null);}, [onPauseReady]);
  useEffect(() => {const guard = (event: BeforeUnloadEvent) => {if (dirty) {event.preventDefault(); event.returnValue = '';}}; window.addEventListener('beforeunload', guard); return () => window.removeEventListener('beforeunload', guard);}, [dirty]);
  function accepted(recordId: string) {setReceipt(recordId); setPending(null); setSavedDraft(null); window.sessionStorage.removeItem(key); setChanged(false); setReview(false); setValues({}); setRevision(value => value + 1); setShowRecords(true);}
  async function submit() {
    if (!definition || lock.current || loadingDefinition || draftPending || definition.accepting_submissions === false) return;
    if (!pending && savedDraft && savedDraft.release_id !== definition.release_id) {setError('请先保存新版填写草稿，再核对并提交；原版本草稿仍保留。'); return;}
    lock.current = true; setBusy(true); setError('');
    const booleans = Object.fromEntries(definition.content.model.fields.filter(field => field.data_type === 'boolean').map(field => [field.key, false]));
    const command = pending ?? {release_id: definition.release_id, values: {...booleans, ...values}, idempotency_key: crypto.randomUUID(),
      ...(savedDraft ? {draft_id: savedDraft.id, draft_revision: savedDraft.revision} : {})};
    try {
      window.sessionStorage.setItem(key, JSON.stringify(command)); setPending(command);
      const result = await api<{record_id: string}>(`/api/v1/applications/${applicationId}/records`, token, command); accepted(result.record_id);
    } catch (cause) {
      if (cause instanceof SessionExpired) {if (alive.current) callbacks.current.onExpired();}
      else {if (cause instanceof HTTPError && [403, 404, 409, 422].includes(cause.status)) {setPending(null); window.sessionStorage.removeItem(key);} setError(cause instanceof Error ? cause.message : '提交失败');}
    } finally {lock.current = false; setBusy(false);}
  }
  async function reconcile() {
    if (!pending || lock.current) return;
    lock.current = true; setBusy(true);
    try {const result = await api<{found: boolean; record_id?: string}>(`/api/v1/applications/${applicationId}/records/by-command/${pending.idempotency_key}`, token);
      if (result.found && result.record_id) accepted(result.record_id); else setError('尚未查到结果，请保留原请求重试或稍后核对。');
    } catch (cause) {if (alive.current) {if (cause instanceof SessionExpired) callbacks.current.onExpired(); else setError(cause instanceof Error ? cause.message : '核对失败');}}
    finally {lock.current = false; if (alive.current) setBusy(false);}
  }
  return <section className="panel form-runtime" aria-label={`${noun}应用`}><h2>{definition?.content.application.name ?? `${noun}应用`}</h2>{definition && <p>{definition.content.application.description}</p>}
    {loadingDefinition && <p role="status">正在加载表单与填写草稿…</p>}
    {error && <p role="alert" className="error">{error}</p>}{!definition && <button disabled={loadingDefinition} onClick={() => void load(true)}>重新加载表单</button>}
    {receipt && <p role="status">{noun}已提交。记录编号：{receipt}</p>}
    {draftNotice && <p role="status">{draftNotice}</p>}
    {savedDraft && definition && <button disabled={busy || !!pending || !!draftPending} onClick={() => {
      if (window.confirm('取消此填写草稿？不会撤回已提交申请。')) void pause({draftId: savedDraft.id, body: {release_id: definition.release_id,
        expected_revision: savedDraft.revision, values: {}, cancel: true, idempotency_key: crypto.randomUUID()}});
    }}>取消此填写草稿</button>}
    {savedDraft && <button disabled={loadingDefinition || busy || !!pending || !!draftPending} onClick={() => {
      if (!changed || window.confirm('重新载入服务端最新草稿？当前输入会保留在备份中，需自行核对合并。')) {setInputBackup(values); setReview(false); void load(true);}
    }}>重新载入已保存草稿</button>}
    {draftPending && <p role="status">草稿保存结果待核对，输入已锁定。<button disabled={busy} onClick={() => void pause()}>使用原请求核对草稿</button></p>}
    {definition?.accepting_submissions === false && <p role="status">当前{noun}已暂停新提交，已有记录仍可查看。</p>}
    {inputBackup && <details><summary>重新加载前的输入备份</summary><pre>{JSON.stringify(inputBackup, null, 2)}</pre></details>}
    <div className="assistant-feature-actions"><button disabled={loadingDefinition} onClick={() => setShowRecords(false)}>填写{noun}</button><button disabled={loadingDefinition} onClick={() => setShowRecords(true)}>查看我的记录</button><button disabled={loadingDefinition || busy || !!pending || !!draftPending} onClick={() => {if (!changed || window.confirm('重新加载可能改变字段，请先保留当前输入。继续？')) {setReview(false); void load();}}}>重新加载表单</button></div>
    {pending && <div role="status">提交结果待核对，已保留原请求。<button disabled={busy} onClick={() => void reconcile()}>核对提交结果</button><button disabled={busy || definition?.accepting_submissions === false} onClick={() => void submit()}>使用原请求重试提交</button><details><summary>原提交内容与版本</summary><p>{pending.release_id}</p><pre>{JSON.stringify(pending.values, null, 2)}</pre></details></div>}
    {!showRecords && definition && <form onSubmit={event => {event.preventDefault(); setReview(true);}}><FormFields config={definition.content} values={values} disabled={loadingDefinition || busy || !!pending || !!draftPending || definition.accepting_submissions === false} onChange={(field, value) => {setValues(previous => ({...previous, [field]: value})); setChanged(true); setReview(false); setReceipt('');}} />
      <button disabled={loadingDefinition || busy || !!pending || !!draftPending || definition.accepting_submissions === false}>核对{noun}内容</button>
      <button type="button" disabled={loadingDefinition || busy || !!pending || definition.accepting_submissions === false || !changed && !draftPending} onClick={() => void pause()}>保存填写草稿</button></form>}
    {review && definition && <div className="management-notice" aria-label={`${noun}提交确认`}><h3>确认提交{noun}</h3><dl>{definition.content.model.fields.map(field => <div key={field.id}><dt>{field.label}</dt><dd>{field.data_type === 'boolean' ? values[field.key] === true ? '是' : '否' : Array.isArray(values[field.key]) ? (values[field.key] as string[]).join('、') : String(values[field.key] ?? '未填写')}</dd></div>)}</dl><button disabled={busy || !!pending} onClick={() => void submit()}>确认提交{noun}</button><button disabled={busy || !!pending} onClick={() => setReview(false)}>继续修改</button></div>}
    {showRecords && (renderRecords ? renderRecords(revision) : <RecordBrowser token={token} applicationId={applicationId} onExpired={onExpired} version={revision} />)}
  </section>;
}
