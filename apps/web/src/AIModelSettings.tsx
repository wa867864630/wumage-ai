import {useEffect, useState} from 'react';
import {api, SessionExpired} from './api';

type Settings = {revision: number; enabled: boolean; base_url: string; model: string; timeout_seconds: number;
  max_tokens: number; token_parameter: string; json_mode: boolean; calls_per_hour: number; api_key_configured: boolean};

export function AIModelSettings({token, onSessionExpired, onDirtyChange}: {token: string; onSessionExpired: () => void; onDirtyChange: (dirty: boolean) => void}) {
  const [value, setValue] = useState<Settings | null>(null); const [key, setKey] = useState('');
  const [clearKey, setClearKey] = useState(false); const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState('');
  useEffect(() => {onDirtyChange(dirty);}, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  useEffect(() => {const warn = (event: BeforeUnloadEvent) => {if (dirty) {event.preventDefault(); event.returnValue = '';}};
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);}, [dirty]);
  async function work(task: () => Promise<void>) {
    setBusy(true); setMessage('');
    try {await task();}
    catch (cause) {if (cause instanceof SessionExpired) onSessionExpired(); else setMessage(cause instanceof Error ? cause.message : '模型配置操作失败');}
    finally {setBusy(false); setKey('');}
  }
  function edit(changes: Partial<Settings>) {if (value) {setValue({...value, ...changes}); setDirty(true);}}
  return <section className="panel"><h2>AI 模型配置</h2>
    <p className="hint">用于管理端设计提案及本地演示租户的课表查询兜底。查询先匹配规则，未命中且无日期或操作歧义时才调用 AI；关闭模型即可停用兜底。配置由本地开发管理员维护，密钥加密保存在本机，不返回页面。</p>
    <button disabled={busy} onClick={() => {
      if (dirty && !window.confirm('重新加载会丢弃尚未保存的模型配置，是否继续？')) return;
      void work(async () => {setValue(await api<Settings>('/api/v1/admin/ai-model', token)); setDirty(false); setClearKey(false);});
    }}>加载模型配置</button>
    {value && <form onSubmit={event => {event.preventDefault(); void work(async () => {
      const {revision, api_key_configured: _configured, ...options} = value;
      const result = await api<Settings>('/api/v1/admin/ai-model', token, {...options, expected_revision: revision, api_key: key, clear_api_key: clearKey}, undefined, 'PUT');
      setValue(result); setDirty(false); setClearKey(false); setMessage('模型配置已保存，立即生效。');
    });}}><fieldset disabled={busy}><legend>Chat Completions 兼容接口</legend><div className="rule-editor">
      <label>启用管理端 AI<input type="checkbox" checked={value.enabled} onChange={event => edit({enabled: event.target.checked})} /></label>
      <label>API 基础地址<input value={value.base_url} maxLength={300} placeholder="https://api.deepseek.com/v1" onChange={event => edit({base_url: event.target.value})} /></label>
      <label>模型名称<input value={value.model} maxLength={100} placeholder="deepseek-chat" onChange={event => edit({model: event.target.value})} /></label>
      <label>API Key（留空保留已有密钥）<input type="password" autoComplete="new-password" maxLength={2048} value={key} onChange={event => {setKey(event.target.value); setDirty(true); setClearKey(false);}} /></label>
      <label>清除已存密钥<input type="checkbox" checked={clearKey} onChange={event => {setClearKey(event.target.checked); setDirty(true); setKey('');}} /></label>
      <label>超时秒数<input type="number" min={5} max={60} value={value.timeout_seconds} onChange={event => edit({timeout_seconds: Number(event.target.value)})} /></label>
      <label>最大生成 tokens<input type="number" min={128} max={8192} value={value.max_tokens} onChange={event => edit({max_tokens: Number(event.target.value)})} /></label>
      <label>长度参数<select value={value.token_parameter} onChange={event => edit({token_parameter: event.target.value})}><option value="max_tokens">max_tokens（DeepSeek 等）</option><option value="max_completion_tokens">max_completion_tokens</option></select></label>
      <label>JSON 模式<input type="checkbox" checked={value.json_mode} onChange={event => edit({json_mode: event.target.checked})} /></label>
      <label>每人每小时调用上限<input type="number" min={1} max={100} value={value.calls_per_hour} onChange={event => edit({calls_per_hour: Number(event.target.value)})} /></label>
    </div><p className="hint">{value.api_key_configured ? '已保存密钥，页面不回显。' : '尚未保存密钥。'} 更换 API 地址时需重新输入密钥。调用上限为单进程限制，重启后重置，不是金额限额。</p><button>保存模型配置</button></fieldset></form>}
    <button disabled={busy || dirty || !value?.enabled} onClick={() => void work(async () => {
      const result = await api<{status: string; usage: unknown}>('/api/v1/admin/ai-model/test', token, {}, AbortSignal.timeout(65000));
      setMessage(`连接测试通过。用量：${JSON.stringify(result.usage)}`);
    })}>{busy ? '处理中…' : '测试已保存的模型（会调用模型）'}</button>
    {message && <p role="status">{message}</p>}
  </section>;
}
