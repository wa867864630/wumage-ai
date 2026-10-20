import type {ApplicationResolution} from './assistant';
import type {ScheduleResponse} from './generated/contracts';
import {ScheduleTable} from './ScheduleTable';

export function AssistantTurn({id, question, applicationName, response, resolution, elapsedMs, error, busy,
  onRetry, onChooseApplication, onNavigate, onSuggestion, onFeature}: {
  id: number; question: string; applicationName?: string; response?: ScheduleResponse; resolution?: ApplicationResolution;
  elapsedMs?: number; error?: string; busy: boolean; onRetry: () => void;
  onChooseApplication: (id: string) => void; onNavigate: () => void; onSuggestion: (text: string) => void;
  onFeature: (text: string) => void;
}) {
  const resolving = !response && (!resolution || resolution.status === 'query');
  return <section className="turn" id={`turn-${id}`}>
    <div className="user-message">{question}</div>
    <div className="assistant-message"><span className="assistant-avatar">✳</span><div className="answer">
      <div className="answer-label">{applicationName ?? '校园助手'} <span>{applicationName && applicationName !== '校园助手' ? '当前应用' : '应用与功能匹配'}</span></div>
      {error ? <div><p role="alert" className="error">{error}</p><button disabled={busy} onClick={onRetry}>重试此查询</button></div>
        : resolving ? <p role="status" className="muted">{resolution ? '已确定应用，正在匹配功能并查询…' : '正在匹配可访问的应用…'}</p>
          : response ? <>
            <p>{response.error?.message ?? response.routing?.message ?? '已为你查询课程安排。'}</p>
            {response.data && <><p className="answer-date">{response.data.start_date} — {response.data.end_date}</p>
              {response.data.courses.length ? <ScheduleTable columns={response.ui_hint.columns} rows={response.data.courses} />
                : <div className="no-courses">所选日期没有课程，合理安排你的时间。</div>}
              <p className="footnote">{response.data.source === 'sis' ? '教务接口' : '模拟课表 · 非真实教务安排'} · 配置 {response.ui_hint.release_id ?? '内置默认'}</p></>}
            {response.routing?.status !== 'resolved' && <div className="suggestions">{response.routing?.suggestions.map(item =>
              <button disabled={busy} key={item} onClick={() => onSuggestion(item)}>{item}</button>)}</div>}
            <p className="footnote">{response.ai_used ? 'AI 用时' : '查询用时'} {((response.ai_used ? response.ai_duration_ms : elapsedMs ?? 0) / 1000).toFixed(2)} 秒</p>
            <details className="request-detail"><summary>请求详情</summary><p>请求编号：{response.trace_id}</p>{response.routing?.rule_id && <p>规则：{response.routing.rule_id}</p>}</details>
          </> : resolution && <>
            <p>{resolution.message}</p>
            {!!resolution.candidates.length && <div className="assistant-application-choices" aria-label="匹配的应用">{resolution.candidates.map(item =>
              <button key={item.id} data-application-id={item.id} disabled={busy} onClick={() => onChooseApplication(item.id)}>
                <strong>{item.name}</strong><small>{item.category} · {item.id === 'schedule' ? '内置应用' : item.id.slice(0, 8)}</small>
                {item.description && <span>{item.description}</span>}</button>)}</div>}
            {resolution.status === 'selected' && !!resolution.features.length && <ul className="assistant-feature-list" aria-label="应用功能">{resolution.features.map(item => <li key={item}>{item}</li>)}</ul>}
            {resolution.status === 'selected' && !!resolution.feature_actions?.length && <div className="assistant-feature-actions" aria-label="可用功能操作">{resolution.feature_actions.map(item =>
              <button key={item.id} data-feature-id={item.id} disabled={busy} onClick={() => onFeature(item.text)}>{item.label}</button>)}</div>}
            {resolution.target === 'admin' && <button className="assistant-management-link" disabled={busy} onClick={onNavigate}>进入应用配置 ↗</button>}
            <p className="footnote">查询用时 {((elapsedMs ?? 0) / 1000).toFixed(2)} 秒</p>
          </>}
    </div></div>
  </section>;
}
