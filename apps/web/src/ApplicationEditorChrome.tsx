import type {ReactNode} from 'react';

type Status = {dirty: boolean; unpublished: boolean};

export function ApplicationConfigHeading({title, description}: {title: string; description: string}) {
  return <div className="config-section-heading"><h3>{title}</h3><p>{description}</p></div>;
}

/** Presentation only: callers retain authorization, revision and command ownership. */
export function ApplicationEditorHeader({name, typeLabel, publicationLabel, dirty, unpublished, actions}: Status & {
  name: string; typeLabel: string; publicationLabel: string; actions: ReactNode;
}) {
  return <header className="application-detail-heading">
    <div><div className="overline">应用配置 · {typeLabel}</div><h2>{name}</h2>
      <div className="application-statuses"><span className="management-badge">{publicationLabel}</span>
        {dirty && <span className="management-badge warning">有未保存修改</span>}
        {unpublished && <span className="management-badge warning">有待发布变更</span>}
      </div>
    </div>
    <div className="application-editor-actions">{actions}</div>
  </header>;
}

export function ApplicationEditorActionBar({dirty, unpublished, revision, section, pending = false, children}: Status & {
  revision: number; section: string; pending?: boolean; children: ReactNode;
}) {
  return <footer className="management-action-bar">
    <div><strong>{pending ? '上次操作结果待核对' : dirty ? '有未保存修改' : unpublished ? '草稿已保存，尚未发布' : '草稿已保存'}</strong>
      <small>草稿修订 {revision} · {section}</small></div>
    <div>{children}</div>
  </footer>;
}
