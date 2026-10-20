import {useEffect, useState} from 'react';
import {api, JSONRequest, SessionExpired} from './api';
import {ScheduleTable} from './ScheduleTable';
import type {DisplayConfig} from './generated/contracts';

type Preview = {candidate: DisplayConfig; changes: string[]; can_accept: boolean; review: {can_publish: boolean; violations: string[]}};
type Context = {context: {base_revision: number; base_content_hash: string}; example_proposal: unknown};

export function CourseProposalPanel({token, revision, resetVersion, disabled, onBusyChange, onPendingChange, onAccepted, onSessionExpired, onEditingChange}: {
  token: string; revision: number; disabled: boolean; onBusyChange: (busy: boolean) => void;
  resetVersion: number; onPendingChange: (pending: boolean) => void; onAccepted: () => Promise<void>; onSessionExpired?: () => void;
  onEditingChange?: (editing: boolean) => void;
}) {
  const [text, setText] = useState(''); const [preview, setPreview] = useState<Preview | null>(null);
  const [baseline, setBaseline] = useState<number | null>(null); const [message, setMessage] = useState('');
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const [requestText, setRequestText] = useState('');
  useEffect(() => {setText(''); setRequestText(''); setPreview(null); setBaseline(null); setPendingKey(null); setMessage(''); onPendingChange(false);}, [token, revision, resetVersion, onPendingChange]);
  const hasEdit = !!text.trim() || !!requestText.trim();
  useEffect(() => {onEditingChange?.(hasEdit);}, [hasEdit, onEditingChange]);
  useEffect(() => () => onEditingChange?.(false), [onEditingChange]);
  async function work(task: () => Promise<void>) {
    onBusyChange(true); setMessage('');
    try {await task();}
    catch (cause) {if (cause instanceof SessionExpired) onSessionExpired?.(); else setMessage(cause instanceof Error ? cause.message : '提案操作失败');}
    finally {onBusyChange(false);}
  }
  async function accept() {
    if (!preview?.can_accept) return;
    if (!pendingKey && !window.confirm('接受提案会暂存新草稿，不会发布。是否继续？')) return;
    const key = pendingKey ?? crypto.randomUUID(); setPendingKey(key); onPendingChange(true);
    await work(async () => {
      const result = await api<{replayed: boolean}>('/api/v1/admin/course-display/designer/accept', token,
        new JSONRequest(`{"proposal":${text},"idempotency_key":${JSON.stringify(key)}}`));
      await onAccepted();
      setPendingKey(null); onPendingChange(false); setPreview(null); setText(''); setRequestText(''); setBaseline(null);
      setMessage(result.replayed ? '已确认上次接受结果，并重新加载当前草稿。' : '提案已暂存；学生继续使用原发布版本，请按原流程试跑并发布。');
    });
  }
  return <fieldset disabled={disabled}><legend>课表配置提案 · AI 辅助设计</legend>
    <p className="hint">先在应用管理配置模型。AI 只生成提案，仍需校验、接受并按原流程发布；也可继续使用示例或手工 JSON。不修改使用角色，不发送学生课表记录。</p>
    {hasEdit && <button className="quiet-button" disabled={!!pendingKey} onClick={() => {
      if (!window.confirm('清空当前需求与提案？未接受的内容将丢失。')) return;
      setText(''); setRequestText(''); setPreview(null); setBaseline(null); setMessage('');
    }}>清空当前需求与提案</button>}
    <label>描述要修改的设计<textarea aria-label="AI 设计需求" rows={3} maxLength={4000} value={requestText} disabled={disabled || !!pendingKey}
      placeholder="例如：把地点改成上课地点，隐藏教师，其他设置不变。" onChange={event => {setRequestText(event.target.value); setPreview(null); setMessage(text ? '需求已修改；下方保留上一份提案，新提案生成成功后替换。' : '');}} /></label>
    <button disabled={!requestText.trim() || !!pendingKey} onClick={() => void work(async () => {
      setPreview(null);
      const result = await api<{proposal: unknown & {status: string; questions: {text: string}[]}; preview: Preview | null; usage: unknown}>(
        '/api/v1/admin/course-display/designer/generate', token, {text: requestText.trim(), expected_revision: revision}, AbortSignal.timeout(65000));
      setText(JSON.stringify(result.proposal, null, 2)); setBaseline(revision); setPreview(result.preview);
      setMessage(result.proposal.status === 'needs_clarification' ? '请补充需求后重新生成：' + result.proposal.questions.map(item => item.text).join('；')
        : 'AI 提案已生成并校验，请核对差异后决定是否接受。用量：' + JSON.stringify(result.usage));
    })}>使用 AI 生成提案</button>
    <button disabled={!!pendingKey} onClick={() => void work(async () => {
      const result = await api<Context>('/api/v1/admin/course-display/designer/context', token);
      setBaseline(result.context.base_revision); setText(JSON.stringify(result.example_proposal, null, 2)); setPreview(null);
      setMessage('已读取最新服务端草稿与示例；修改 JSON 后先预览。');
    })}>载入当前基线与示例提案</button>
    {baseline !== null && <p className="hint">提案基于草稿修订 {baseline}；页面修订 {revision}。请先暂存页面上的手工编辑。</p>}
    <label>提案 JSON<textarea aria-label="课表配置提案 JSON" rows={14} maxLength={131072} value={text} disabled={disabled || !!pendingKey}
      onChange={event => {setText(event.target.value); setPreview(null); setMessage('');}} /></label>
    <button disabled={!text.trim() || !!pendingKey} onClick={() => void work(async () => {
      setPreview(null);
      const result = await api<Preview>('/api/v1/admin/course-display/designer/preview', token, new JSONRequest(text));
      setPreview(result); setMessage(result.can_accept ? '校验通过，可接受为草稿。' : '提案无实际变化或回归未通过，不能接受。');
    })}>校验并预览提案</button>
    {preview && <div aria-live="polite"><h3>服务端核对的变更</h3><ul>{preview.changes.map((change, index) => <li key={index}>{change}</li>)}</ul>
      {!!preview.review.violations.length && <p className="error">规则回归阻止项：{preview.review.violations.join('、')}</p>}
      <ScheduleTable columns={preview.candidate.columns} rows={[{id: 'proposal-preview', name: '合成预览课程', date: '2026-10-19', start_time: '09:00', end_time: '10:35', location: '教学楼 A-201', teacher: '模拟教师'}]} />
      <button disabled={!preview.can_accept} onClick={() => void accept()}>{pendingKey ? '使用相同请求号确认接受结果' : '接受提案并暂存'}</button></div>}
    {pendingKey && <p role="alert">接受结果待核对，请用相同请求号重试。也可重新加载并核对草稿；不要另发新提案。</p>}
    {message && <p role="status">{message}</p>}
  </fieldset>;
}
