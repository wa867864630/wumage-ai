import {useEffect, useId, useRef, type ReactNode} from 'react';

export function ManagementDialog({title, children, onClose, busy = false, drawer = false}: {
  title: string; children: ReactNode; onClose: () => void; busy?: boolean; drawer?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    const previous = document.activeElement as HTMLElement | null;
    dialog?.showModal();
    return () => {dialog?.close(); previous?.focus();};
  }, []);
  return <dialog ref={ref} className={`management-dialog ${drawer ? 'management-drawer' : ''}`} aria-labelledby={titleId}
    onCancel={event => {event.preventDefault(); if (!busy) onClose();}}>
    <header className="management-dialog-header"><h2 id={titleId}>{title}</h2>
      <button className="quiet-button" disabled={busy} onClick={onClose} aria-label={`关闭${title}`}>关闭 ×</button></header>
    <div className="management-dialog-body">{children}</div>
  </dialog>;
}
