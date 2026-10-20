import { useEffect, useState } from 'react';

type Student = { id: string; student_no: string; name: string };

export function DemoIdentity({ token, disabled, onChange, onBusy }: {
  token: string; disabled: boolean; onChange: (token: string) => void; onBusy: (busy: boolean) => void;
}) {
  const [students, setStudents] = useState<Student[]>([]);
  const [selected, setSelected] = useState('');
  const [active, setActive] = useState('默认模拟学生一');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/v1/auth/demo/students', { signal: controller.signal }).then(async response => {
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message ?? '模拟学生加载失败');
      setStudents(body.students); setSelected(body.students[0]?.id ?? '');
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '模拟学生加载失败'); });
    return () => controller.abort();
  }, []);

  async function change(logout = false) {
    setBusy(true); onBusy(true); setError('');
    try {
      const response = await fetch(`/api/v1/auth/demo/${logout ? 'logout' : 'login'}`, {
        method: 'POST', signal: AbortSignal.timeout(10000),
        headers: { 'Content-Type': 'application/json', ...(logout && token ? { Authorization: `Bearer ${token}` } : {}) },
        ...(!logout ? { body: JSON.stringify({ student_id: selected }) } : {}),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message ?? '身份切换失败');
      const oldToken = token;
      onChange(logout ? '' : body.access_token);
      setActive(logout ? '默认模拟学生一' : `${body.student.name} / ${body.student.student_no}`);
      if (!logout && oldToken) void fetch('/api/v1/auth/demo/logout', {
        method: 'POST', headers: { Authorization: `Bearer ${oldToken}` }, signal: AbortSignal.timeout(5000),
      }).catch(() => {});
    } catch (cause) { setError(cause instanceof Error ? cause.message : '身份服务暂不可用'); }
    finally { setBusy(false); onBusy(false); }
  }
  return <section className="panel"><h2>模拟学生身份</h2>
    <p className="hint">仅用于开发验收，不是校园登录。会话一小时有效，刷新或服务重启后需重新选择；不会授予管理权限。</p>
    <div className="admin-toolbar"><label>选择已入库的学生<select value={selected} disabled={disabled || busy} onChange={event => setSelected(event.target.value)}>
      {students.map(student => <option key={student.id} value={student.id}>{student.name} / {student.student_no}</option>)}
    </select></label><button disabled={disabled || busy || !selected} onClick={() => void change()}>切换学生</button>
      <button disabled={disabled || busy || !token} onClick={() => void change(true)}>退出模拟会话</button></div>
    <p role="status">当前：{active}{!token && '（未登录，使用演示默认身份）'}</p>
    {error && <p className="error" role="alert">{error}</p>}
  </section>;
}
