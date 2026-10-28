import {useCallback, useEffect, useRef, useState} from 'react';
import {api, HTTPError} from './api';
import {ConstructionPreview} from './ConstructionPreview';

type Result = {generation_status: string; summary?: string; message?: string; questions?: string[]; content?: unknown;
  item?: {id: string; revision: number}; evidence?: {line: number; quote: string}[]; ai_duration_ms?: number};

export function ConstructionOptions({token, scope, task, disabled, onChange, initialGenerationKey}: {
  token: string; scope: string; task: {id: string; revision: number; business_kind?: string}; disabled: boolean;
  onChange: (value: Record<string, string> | null) => void;
  initialGenerationKey?: string;
}) {
  const [mode, setMode] = useState(initialGenerationKey ? 'ai' : 'template');
  const [sources, setSources] = useState<{application_id: string; name: string}[]>([]);
  const [source, setSource] = useState(''); const [text, setText] = useState('');
  const [doc, setDoc] = useState<{name: string; text: string} | null>(null);
  const [result, setResult] = useState<Result | null>(null); const [error, setError] = useState('');
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false); const active = useRef(true); const lock = useRef(false);
  const fileRead = useRef(0);
  const keyName = `campus-construction-ai:${scope}:${task.id}:${task.revision}`;
  const [key, setKey] = useState(() => {if (initialGenerationKey) return initialGenerationKey; try {return sessionStorage.getItem(keyName) ?? '';} catch {return '';}});
  const callback = useRef(onChange); callback.current = onChange;
  useEffect(() => {
    if (initialGenerationKey) {try {sessionStorage.setItem(keyName, initialGenerationKey);} catch { /* History can still restore the command. */ }}
  }, [initialGenerationKey, keyName]);
  const [aiAllowed, setAIAllowed] = useState(false);
  const [aiStatus, setAIStatus] = useState('正在检查 AI 配置…'); const [aiLoading, setAILoading] = useState(false);
  const [sourcesLoading, setSourcesLoading] = useState(false); const [sourcesError, setSourcesError] = useState('');
  const sourceRead = useRef(0); const aiRead = useRef(0); const modeRef = useRef(mode); modeRef.current = mode;
  const loadSources = useCallback(async () => {
    const own = ++sourceRead.current; setSourcesLoading(true); setSourcesError(''); setSource(''); setSources([]);
    if (modeRef.current === 'copy') callback.current(null);
    try {
      const value = await api<{items: {application_id: string; name: string; type_id: string; available?: boolean; lifecycle?: {status: string}}[]}>('/api/v1/admin/applications', token);
      if (active.current && own === sourceRead.current) setSources(value.items.filter(row => row.available !== false
        && (!row.lifecycle || row.lifecycle.status === 'active')
        && row.type_id === ({course_query: 'campus.course-query', registration: 'platform.form', leave: 'platform.leave-approval'}[task.business_kind ?? ''])));
    } catch {if (active.current && own === sourceRead.current) setSourcesError('来源应用加载失败，请重试，或切换为模板创建。');}
    finally {if (active.current && own === sourceRead.current) setSourcesLoading(false);}
  }, [token, task.business_kind]);
  const loadAI = useCallback(async () => {
    const own = ++aiRead.current; setAILoading(true);
    try {
      const value = await api<{enabled: boolean}>('/api/v1/admin/ai-model', token);
      if (active.current && own === aiRead.current) {
        setAIAllowed(value.enabled === true);
        setAIStatus(value.enabled === true ? 'AI 已启用，点击生成才会调用模型。' : 'AI 尚未启用，请在应用管理的 AI 模型配置中保存并启用，再重新检查。');
      }
    } catch (cause) {if (active.current && own === aiRead.current) {
      setAIAllowed(false); setAIStatus(cause instanceof HTTPError && cause.status === 403
        ? '当前身份或环境不允许使用 AI 创建，仅本地开发管理员可用。仍可选择模板或复制设计。'
        : 'AI 配置检查失败，可重新检查；仍可选择模板或复制设计。');
    }} finally {if (active.current && own === aiRead.current) setAILoading(false);}
  }, [token]);
  useEffect(() => {
    active.current = true;
    void loadSources(); void loadAI();
    return () => {active.current = false; fileRead.current++; sourceRead.current++; aiRead.current++;};
  }, [loadSources, loadAI, task.id]);
  function change(next: string) {
    fileRead.current++;
    setMode(next); setError(''); setResult(null); setAccepted(false);
    callback.current(next === 'template' ? {} : next === 'copy' && source ? {source_application_id: source} : null);
  }
  function revise() {
    if (disabled || lock.current || !result) return;
    const message = result.generation_status === 'interrupted'
      ? '原生成结果未确定，另开请求可能再次产生 AI 费用。确认新建请求？'
      : result.generation_status === 'proposed'
        ? '调整后将重新生成提案，并产生新的 AI 请求。当前接受状态将清除，确认继续？'
        : null;
    if (message && !window.confirm(message)) return;
    setKey(''); setResult(null); setAccepted(false); setError(''); callback.current(null);
    try {sessionStorage.removeItem(keyName);} catch { /* The original command remains available on the server. */ }
  }
  function review(response: Result) {
    if ((response.item && (response.item.id !== task.id || response.item.revision !== task.revision))
      || (response.generation_status === 'proposed' && !response.item)) {
      setResult(null); setAccepted(false); callback.current(null);
      setError('原 AI 结果与当前需求或修订不一致，不能接受此提案。请从原需求的历史记录核对，或重新打开最新需求。'); return;
    }
    setError(''); setResult(response);
  }
  async function check() {
    if (!key || lock.current) return;
    lock.current = true; setBusy(true); setAccepted(false); callback.current(null);
    try {
      const response = await api<{status: string; result?: Result | null}>(`/api/v1/assistant/requirements/commands/${encodeURIComponent(key)}`, token);
      if (active.current) {review(response.result ?? {generation_status: response.status === 'expired' || response.status === 'not_found' ? 'interrupted' : 'processing', message: '尚未取得完成结果，可稍后核对；另开请求可能产生新的 AI 费用。'}); callback.current(null);}
    } catch (cause) {if (active.current) setError(cause instanceof Error ? cause.message : '核对失败');}
    finally {lock.current = false; if (active.current) setBusy(false);}
  }
  async function generate() {
    if (disabled || lock.current || !aiAllowed || aiLoading || !text.trim() || key) return;
    const requestKey = crypto.randomUUID(); setKey(requestKey);
    try {sessionStorage.setItem(keyName, requestKey);} catch { /* memory still preserves the command */ }
    lock.current = true; setBusy(true); setError(''); callback.current(null);
    try {
      const response = await api<Result>(`/api/v1/assistant/requirements/${task.id}/generate`, token, {
        expected_revision: task.revision, text: text.trim(), idempotency_key: requestKey,
        ...(mode === 'document' && doc ? {document_name: doc.name, document_text: doc.text} : {})}, AbortSignal.timeout(95000));
      if (active.current) review(response);
    } catch (cause) {if (active.current) {
      if (cause instanceof HTTPError && [403, 404, 409, 422].includes(cause.status)) {setKey(''); try {sessionStorage.removeItem(keyName);} catch {}}
      setError((cause instanceof Error ? cause.message : '生成失败') + '；请先核对原请求。');
    }}
    finally {lock.current = false; if (active.current) setBusy(false);}
  }
  return <div className="construction-options">
    <label>创建方式<select disabled={disabled || busy} value={mode} onChange={event => change(event.target.value)}>
      <option value="template">选择模板</option><option value="copy">复制已有应用设计</option>
      <option value="ai" disabled={!aiAllowed && !key}>对话描述，由 AI 生成</option><option value="document" disabled={!aiAllowed && !key}>从文本说明生成</option>
    </select></label>
    <p className="muted" role="status">{aiStatus}</p><button type="button" disabled={disabled || busy || aiLoading} onClick={() => void loadAI()}>重新检查 AI 配置</button>
    {key && !['ai', 'document'].includes(mode) && <p className="muted">已有 AI 请求，可切换到 AI 创建方式核对原结果，不会重新调用模型。</p>}
    {mode === 'copy' && <><label>来源应用<select disabled={disabled || busy || sourcesLoading} value={source} onChange={event => {setSource(event.target.value); callback.current(event.target.value ? {source_application_id: event.target.value} : null);}}>
      <option value="">请选择相同类型应用</option>{sources.map(row => <option key={row.application_id} value={row.application_id}>{row.name} · {row.application_id.slice(0, 8)}</option>)}
    </select></label>
      {sourcesLoading ? <p role="status">正在加载来源应用…</p> : sourcesError ? <p role="alert" className="error">{sourcesError}</p> : !sources.length && <p className="muted">暂无可复制的同类型启用应用，请先选择模板创建。</p>}
      <button type="button" disabled={disabled || busy || sourcesLoading} onClick={() => void loadSources()}>刷新复制来源</button>
      <p className="muted">复制当前草稿设计和访问范围，来源修订会在确认时再次核对。业务记录、审批人和发布历史独立。刷新来源后需重新选择。</p></>}
    {['ai', 'document'].includes(mode) && <>
      {mode === 'document' && <label>需求说明文件（UTF-8 .txt/.md，64 KiB、500行）<input type="file" accept=".txt,.md" disabled={disabled || busy || !!key} onChange={async event => {
        const read = ++fileRead.current;
        const file = event.target.files?.[0]; setDoc(null); setError(''); callback.current(null); if (!file) return;
        try {if (!/\.(txt|md)$/i.test(file.name) || file.size > 65536) throw new Error('请选择64 KiB以内的 .txt/.md。');
          const content = new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer());
          if (!content.trim() || content.split(/\r?\n/).length > 500 || /[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(content)) throw new Error('文本为空、超过500行或含不支持的控制字符。');
          if (active.current && read === fileRead.current) setDoc({name: file.name, text: content});
        } catch (cause) {if (active.current && read === fileRead.current) setError(cause instanceof Error ? cause.message : '文件读取失败');}
      }} /></label>}
      <label>描述需要的字段、顺序、选项和屏幕宽度<textarea maxLength={4000} disabled={disabled || busy || !!key} value={text} onChange={event => {setText(event.target.value); callback.current(null);}} /></label>
      <p className="muted">重新生成时，请把完整要求写在描述中；每次按当前描述和所选文档生成，不自动继承上一份提案。调整按钮不会立即调用 AI。</p>
      <p className="muted">AI 仅生成当前类型的配置提案；请假流程限单级审批。文档原文仅用于本次请求，保留文件摘要和有限引用。请先移除个人隐私和凭据。</p>
      {!key && <button type="button" disabled={disabled || busy || aiLoading || !aiAllowed || !text.trim() || mode === 'document' && !doc} onClick={() => void generate()}>生成设计提案</button>}
      {key && <button type="button" disabled={disabled || busy} onClick={() => void check()}>核对原 AI 请求</button>}
      {busy && <p role="status">正在生成或核对，请保留此页面…</p>}
      {result && <div role="status"><p>{result.summary ?? result.message}</p>{result.ai_duration_ms !== undefined && <p>AI 用时：{(result.ai_duration_ms / 1000).toFixed(1)} 秒</p>}
        {result.questions?.map((question, index) => <p key={index}>{question}</p>)}
        {result.evidence?.map((entry, index) => <p key={index}>第 {entry.line} 行：{entry.quote}</p>)}
        {result.generation_status === 'proposed' && <><ConstructionPreview content={result.content} businessKind={task.business_kind} /><details><summary>查看完整配置提案</summary><pre>{JSON.stringify(result.content, null, 2)}</pre></details>
          <label><input type="checkbox" checked={accepted} disabled={disabled || busy} onChange={event => {setAccepted(event.target.checked); callback.current(event.target.checked ? {generation_key: key} : null);}} />我已核对提案，将其用于创建草稿</label></>}
        {['proposed', 'failed', 'needs_clarification', 'interrupted'].includes(result.generation_status) && <button type="button" disabled={disabled || busy} onClick={revise}>
          {result.generation_status === 'proposed' ? '调整需求重新生成' : '补充需求后新建生成请求'}</button>}
      </div>}
    </>}
    {error && <p role="alert" className="error">{error}</p>}
  </div>;
}
