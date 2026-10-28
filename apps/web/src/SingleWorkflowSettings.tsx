import {ApplicationConfigHeading} from './ApplicationEditorChrome';

export type SingleWorkflow = {id: string; name: string; nodes: Array<{id: string; type: string; name?: string}>};

export function SingleWorkflowSettings({value, disabled, onChange}: {value: SingleWorkflow; disabled: boolean; onChange: (value: SingleWorkflow) => void}) {
  return <section aria-label="单级流程设计">
    <ApplicationConfigHeading title="流程设计" description="修改流程和节点名称后，暂存并明确发布。新发布只影响新申请，历史申请保留原版本。" />
    <fieldset className="management-fields" disabled={disabled}>
      <label>流程名称<input maxLength={80} required value={value.name} onChange={event => onChange({...value, name: event.target.value})} /></label>
      {value.nodes.filter(node => node.type === 'approval').map(node => <label key={node.id}>审批节点名称<input required maxLength={80} value={node.name ?? ''}
        onChange={event => onChange({...value, nodes: value.nodes.map(item => item.id === node.id ? {...item, name: event.target.value} : item)})} /></label>)}
    </fieldset>
    <div className="workflow-diagram"><span>填写申请</span><span aria-hidden="true">→</span><span>{value.nodes.find(node => node.type === 'approval')?.name || '审批节点'}</span><span aria-hidden="true">→</span><span>通过 / 拒绝</span></div>
    <p>审批人来源：本次发布的已认证管理员。禁止自审，缺少审批人时阻止发布；待审可撤回。</p>
    <p className="muted">当前仅支持单级审批，不支持新增多级、条件分支或自由输入人员ID。</p>
  </section>;
}
