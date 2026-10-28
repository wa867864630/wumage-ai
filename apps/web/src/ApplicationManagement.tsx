import {useState, type FormEvent} from 'react';
import {ApplicationWorkbench} from './ApplicationWorkbench';
import {api, SessionExpired} from './api';
import {AIModelSettings} from './AIModelSettings';
import type {ManagementLocation} from './assistant';

type Inspection = {status: string; fingerprint: string; authenticated: boolean; checks: {name: string; ok: boolean}[]; plan: string[]};
type DeploymentPlan = {digest: string; operation: 'install' | 'update'; target: {host: string; port: number; username: string; fingerprint: string}; release_directory: string; artifact_sha256: string; artifact: {file_count: number; archive_bytes: number; web_build: string}; preflight: {facts: {free_bytes: number} | null; required_free_bytes: number | null; blockers: {code: string; message: string}[]; warnings: string[]}; steps: {id: string; description: string; requires_sudo: boolean}[]; limitations: string[]};
export function ApplicationManagement({initialToken, onSessionExpired, onDirtyChange, deployment = false, aiModel = false, onUse, location, onOpenRequirements}: {
  initialToken: string; onSessionExpired: () => void; onDirtyChange: (dirty: boolean) => void; deployment?: boolean; aiModel?: boolean; onUse?: (id: string) => void; location?: ManagementLocation;
  onOpenRequirements?: () => void;
}) {
  if (aiModel) return <AIModelSettings token={initialToken} onSessionExpired={onSessionExpired} onDirtyChange={onDirtyChange} />;
  return deployment ? <DeploymentPanel token={initialToken} onSessionExpired={onSessionExpired} /> : <ApplicationWorkbench token={initialToken} onSessionExpired={onSessionExpired} onDirtyChange={onDirtyChange} onUse={onUse} location={location} onOpenRequirements={onOpenRequirements} />;
}
export function DeploymentPanel({token, onSessionExpired}: {token: string; onSessionExpired: () => void}) {
  const [host, setHost] = useState(''); const [port, setPort] = useState(22);
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('');
  const [fingerprint, setFingerprint] = useState(''); const [result, setResult] = useState<Inspection | null>(null);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const [capability, setCapability] = useState('尚未检查本地权限及可选依赖');
  const [operation, setOperation] = useState<'install' | 'update'>('install');
  const [releaseId, setReleaseId] = useState(''); const [artifactSha, setArtifactSha] = useState('');
  const [plan, setPlan] = useState<DeploymentPlan | null>(null);
  async function capabilities() {
    setBusy(true); setError('');
    try {
      const body = await api<{preflight_available: boolean; allowed_hosts: string[]}>('/api/v1/admin/deployment/capabilities', token);
      setCapability(body.preflight_available ? `只读检查可用；已配置 ${body.allowed_hosts.length} 个目标地址。` : '权限已通过，但缺少可选 SSH 依赖 requirements-deployment.txt。');
    } catch (cause) {if (cause instanceof SessionExpired) onSessionExpired(); else setError(cause instanceof Error ? cause.message : '检查失败');}
    finally {setBusy(false);}
  }
  async function inspect(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setResult(null); setPlan(null);
    try {
      const body = await api<Inspection>('/api/v1/admin/deployment/inspect', token,
        {host, port, username, password, fingerprint}, AbortSignal.timeout(45000));
      setResult(body);
    } catch (cause) {if (cause instanceof SessionExpired) onSessionExpired(); else setError(cause instanceof Error ? cause.message : '检查失败');}
    finally {setBusy(false); setPassword('');}
  }
  async function createPlan(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setPlan(null); setResult(null);
    try {
      setPlan(await api<DeploymentPlan>('/api/v1/admin/deployment/plan', token,
        {host, port, username, password, fingerprint, operation, release_id: releaseId, artifact_sha256: artifactSha}, AbortSignal.timeout(45000)));
    } catch (cause) {if (cause instanceof SessionExpired) onSessionExpired(); else setError(cause instanceof Error ? cause.message : '生成计划失败');}
    finally {setBusy(false); setPassword('');}
  }
  return <section className="panel" onChange={() => setPlan(null)}><h2>服务器部署 · 检查与计划</h2>
    <p className="hint">核对主机指纹、只读检查后，可生成安装或更新的审阅计划。实际安装与版本更新尚未开放。</p>
    <p className="hint">默认关闭。需在本地配置 deployment.enabled、operator_subjects、allowed_hosts，重启 API；部署授权独立于应用发布权限。</p>
    <button disabled={busy} onClick={() => void capabilities()}>检查本地部署权限</button><p role="status" className="hint">{capability}</p>
    <form className="deployment-form" onSubmit={event => void inspect(event)}><fieldset disabled={busy}><legend>本次 SSH 连接</legend><div className="rule-editor">
      <label>服务器 IP<input required maxLength={45} value={host} onChange={event => {setHost(event.target.value); setFingerprint(''); setResult(null);}} placeholder="明确的 IPv4 / IPv6 地址" /></label>
      <label>SSH 端口<input type="number" min={1} max={65535} required value={port} onChange={event => {setPort(Number(event.target.value)); setFingerprint(''); setResult(null);}} /></label>
      <label>Linux 用户名<input required pattern="[a-z_][a-z0-9_-]{0,31}" maxLength={32} value={username} autoComplete="off" onChange={event => {setUsername(event.target.value); setResult(null);}} /></label>
      <label>密码（仅本次请求，不保存）<input type="password" maxLength={1024} value={password} autoComplete="new-password" onChange={event => setPassword(event.target.value)} /></label>
      <label>经独立核实的 SHA256 主机指纹<input value={fingerprint} maxLength={50} onChange={event => {setFingerprint(event.target.value); setResult(null);}} placeholder="首次留空，仅获取指纹，不发送密码" /></label>
    </div><p className="hint">请通过服务器控制台等独立渠道核对指纹，再手动填写。不要只复制网络返回值就认定可信；建议将核实后的指纹写入本地 host_fingerprints，下次变化将被拒绝。</p><button>{busy ? '检查中…' : fingerprint ? '验证指纹并执行只读检查' : '仅获取主机指纹'}</button></fieldset></form>
    {error && <p role="alert" className="error">{error}</p>}{result && <div aria-live="polite"><h3>{result.authenticated ? '只读检查完成' : '主机指纹待独立确认'}</h3><p className="trace">{result.fingerprint}</p>{result.checks.map(check => <p key={check.name}>{check.ok ? '✓' : '○'} {check.name}：{check.ok ? '满足' : '缺失或不支持'}</p>)}{result.plan.map(step => <p key={step} className="hint">{step}</p>)}</div>}
    <form className="deployment-form" onSubmit={event => void createPlan(event)}><fieldset disabled={busy}><legend>部署审阅计划</legend><div className="rule-editor">
      <label>操作<select value={operation} onChange={event => setOperation(event.target.value as 'install' | 'update')}><option value="install">首次安装</option><option value="update">更新版本</option></select></label>
      <label>版本号<input required pattern="[a-z0-9][a-z0-9-]{0,63}" maxLength={64} value={releaseId} onChange={event => setReleaseId(event.target.value)} placeholder="例如 release-20261019" /></label>
      <label>构建产物 SHA256<input required pattern="[a-f0-9]{64}" maxLength={64} value={artifactSha} onChange={event => setArtifactSha(event.target.value)} placeholder="64 位小写十六进制摘要" /></label>
    </div><p className="hint">先在本机生成发布包，填写输出的版本号与 SHA256。生成计划会验证本地产物清单与每个文件的摘要，再核验服务器；密码每次请求后清空，请重新输入。版本固定放在 /opt/campus-ai/releases 下。</p><button>校验产物、重新检查并生成计划</button></fieldset></form>
    {plan && <div aria-live="polite"><h3>{plan.operation === 'install' ? '首次安装' : '版本更新'}计划 · 仅供审阅</h3><p>目标：{plan.target.username}@{plan.target.host}:{plan.target.port}</p><p className="trace">主机指纹：{plan.target.fingerprint}</p><p>版本目录：{plan.release_directory}</p><p className="trace">产物 SHA256：{plan.artifact_sha256}</p><p>本地产物校验通过：{plan.artifact.file_count} 个文件，{plan.artifact.archive_bytes} 字节；{plan.artifact.web_build === 'built' ? '打包前完整构建通过' : '使用已有前端文件，构建尚未验证'}。</p>
      <h4>服务器执行前检查</h4>{plan.preflight.facts && <p>可用空间：{(plan.preflight.facts.free_bytes / 1024 ** 3).toFixed(2)} GiB；本次至少需要：{((plan.preflight.required_free_bytes ?? 0) / 1024 ** 3).toFixed(2)} GiB。</p>}
      {plan.preflight.blockers.length ? <><p className="error">有 {plan.preflight.blockers.length} 项条件阻止执行：</p><ul>{plan.preflight.blockers.map(item => <li key={item.code}>{item.message}</li>)}</ul></> : <p>本轮检查未发现阻止项；执行确认与安装更新功能仍未开放。</p>}{plan.preflight.warnings.map(item => <p className="hint" key={item}>{item}</p>)}
      <ol>{plan.steps.map(step => <li key={step.id}>{step.description}{step.requires_sudo && <strong>（需单独确认提权）</strong>}</li>)}</ol>{plan.limitations.map(item => <p className="hint" key={item}>{item}</p>)}<p className="trace">计划摘要：{plan.digest}</p></div>}
  </section>;
}
