import {useEffect, useState, type FormEvent} from 'react';
import {api, type Identity, type Session} from './api';
type Student = {id: string; name: string; student_no: string};
export function Login({onLogin, notice}: {onLogin: (session: Session) => void; notice: string}) {
  const [mode, setMode] = useState('');
  const [students, setStudents] = useState<Student[]>([]);
  const [student, setStudent] = useState('');
  const [method, setMethod] = useState('student');
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setError(''); setMode('');
    api<{authentication: string}>('/api/health', '', undefined, controller.signal).then(async health => {
      setMode(health.authentication);
      if (health.authentication === 'demo') {
        const data = await api<{students: Student[]}>('/api/v1/auth/demo/students', '', undefined, controller.signal);
        setStudents(data.students); setStudent(data.students[0]?.id ?? '');
      }
    }).catch(cause => {if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '连接失败');});
    return () => controller.abort();
  }, [retry]);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); let issued = '';
    try {
      let credential = token.trim(); let name = '校园用户';
      if (mode === 'demo' && method === 'student') {
        const data = await api<{access_token: string; student: {name: string}}>('/api/v1/auth/demo/login', '', {student_id: student});
        credential = data.access_token; issued = credential; name = data.student.name;
      }
      const identity = await api<Identity>('/api/v1/me', credential);
      if (identity.roles.includes('config_admin')) name = '配置管理员'; else if (!issued) name = identity.subject;
      setToken(''); onLogin({token: credential, name, identity});
    } catch (cause) {
      if (issued) void api('/api/v1/auth/demo/logout', issued, {}).catch(() => {});
      setError(cause instanceof Error ? cause.message : '登录失败');
    } finally {setBusy(false);}
  }
  const demo = mode === 'demo' && method === 'student';
  return <div className="login-layout"><section className="login-story"><a className="wordmark" href="#">✳ <span>校园 · 智能工作台</span></a><div><span className="overline">ONE CAMPUS. ONE WORKSPACE.</span><h1>校园里的事，<br />从一句话开始。</h1><p>连接校园微服务，让学习与工作更简单。<br />一个入口，找到你的专属智能体。</p><div className="story-tags"><span>课表查询</span><span>校园微服务</span><span>统一工作台</span></div></div><small>以服务连接校园 · CAMPUS WORKSPACE</small></section>
    <section className="login-card"><span className="login-symbol">✳</span><h2>欢迎来到校园工作台</h2><p className="muted">登录后，开始你的校园服务之旅</p>{mode === 'demo' && <div className="segmented"><button aria-pressed={method === 'student'} onClick={() => setMethod('student')}>模拟学生</button><button aria-pressed={method === 'token'} onClick={() => setMethod('token')}>管理令牌</button></div>}
      <form className="login-form" onSubmit={event => void submit(event)}>{demo ? <label>选择模拟账号<select required disabled={busy} value={student} onChange={event => setStudent(event.target.value)}><option value="" disabled>请选择学生</option>{students.map(item => <option key={item.id} value={item.id}>{item.name} · {item.student_no}</option>)}</select></label> : <label>{mode === 'oidc' ? '校园访问令牌' : '本地管理令牌'}<input required type="password" autoComplete="off" disabled={busy} value={token} placeholder="输入已配置或签发的令牌" onChange={event => setToken(event.target.value)} /></label>}<button className="primary" disabled={!mode || busy || (demo ? !student : !token.trim())}>{busy ? '正在验证身份…' : '登录并进入工作台 →'}</button></form>
      {(error || notice) && <p role="alert" className="error">{error || notice}</p>}{error && <button className="text-button" onClick={() => setRetry(value => value + 1)}>重新连接服务</button>}<div className="login-note">{mode === 'demo' ? '开发演示环境：模拟账号无密码。管理入口使用本地管理令牌，不是学校账号密码。' : '使用校园系统签发的访问令牌，当前尚未接入自动 SSO 跳转。'}<br />令牌仅保存在本页内存，刷新需重新登录。</div>
    </section></div>;
}
