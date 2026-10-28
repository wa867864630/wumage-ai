import {useEffect, useRef, useState} from 'react';
import {FormEditor} from './FormEditor';
import {ApprovalWorkList} from './ApprovalRuntime';

export function ApprovalEditor(props: NonNullable<Parameters<typeof FormEditor>[0]> = {}) {
  const [formDirty, setFormDirty] = useState(false); const [taskDirty, setTaskDirty] = useState(false); const [editorLock, setEditorLock] = useState(false);
  const callbacks = useRef(props); callbacks.current = props;
  useEffect(() => {callbacks.current.onDirtyChange?.(formDirty || taskDirty); callbacks.current.onNavigationLock?.(editorLock || taskDirty);}, [formDirty, taskDirty, editorLock]);
  useEffect(() => () => {callbacks.current.onDirtyChange?.(false); callbacks.current.onNavigationLock?.(false);}, []);
  return <FormEditor {...props} typeLabel="单级请假审批" recordsLabel="申请与审批" protectedFields={['start_date', 'end_date', 'reason']}
    onDirtyChange={setFormDirty} onNavigationLock={setEditorLock}
    renderWorkflow={() => <section className="panel" aria-label="单级审批流程"><h3>单级请假流程</h3><div className="workflow-diagram"><span>填写申请</span><span aria-hidden="true">→</span><span>发布管理员审批</span><span aria-hidden="true">→</span><span>通过 / 拒绝</span></div>
      <label>审批人来源<select value="publisher" disabled><option value="publisher">本次发布管理员（已认证）</option></select></label>
      <p>发布会将当前管理员绑定为新申请的审批人，禁止自审，缺少有效身份时阻止发布。历史申请固定原发布和办理人，不随新发布自动改派。</p>
      <p>首版支持单级审批与待审撤回；暂未接入组织人员目录、多级条件流或任意指定人员。授权管理员可在待审申请上说明原因后接管，并留存记录。</p></section>}
    renderRecords={version => <ApprovalWorkList token={props.initialToken ?? ''} applicationId={props.applicationId ?? ''} admin initialScope="all" version={version}
      onDirtyChange={setTaskDirty} onExpired={() => callbacks.current.onSessionExpired?.()} />} />;
}
