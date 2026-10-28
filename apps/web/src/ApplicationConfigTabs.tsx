import {useRef, type KeyboardEvent} from 'react';

/** Hosts provide capability-specific tabs; keyboard and selected state stay consistent. */
export function ApplicationConfigTabs<T extends string>({items, value, onChange, idPrefix = 'config', label = '应用功能配置', disabled = false}: {
  items: readonly (readonly [T, string])[]; value: T; onChange: (value: T) => void; idPrefix?: string; label?: string; disabled?: boolean;
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  function navigate(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (disabled) return;
    const next = event.key === 'ArrowRight' ? (index + 1) % items.length
      : event.key === 'ArrowLeft' ? (index + items.length - 1) % items.length
      : event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : -1;
    if (next < 0 || !items[next]) return;
    event.preventDefault(); onChange(items[next][0]); buttons.current[next]?.focus();
  }
  return <div className="management-tabs" role="tablist" aria-label={label}>{items.map(([id, title], index) => <button key={id} ref={element => {buttons.current[index] = element;}}
    id={`${idPrefix}-tab-${id}`} disabled={disabled} role="tab" aria-selected={value === id} aria-controls={`${idPrefix}-panel-${id}`} tabIndex={value === id ? 0 : -1}
    onKeyDown={event => navigate(event, index)} onClick={() => onChange(id)}>{title}</button>)}</div>;
}
