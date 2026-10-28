import {ConfigAdmin} from './ConfigAdmin';
import {FormEditor} from './FormEditor';
import {ApprovalEditor} from './ApprovalEditor';
import {UnifiedFormEditor, UnifiedApprovalEditor} from './UnifiedFormEditor';

type Editor = typeof ConfigAdmin;
/** A reviewed editor is bound to one precise version. Unknown versions never fall back. */
const bundles: ReadonlyArray<readonly [string, string, Editor]> = [
  ['campus.course-query', '1.0.0', ConfigAdmin],
  ['platform.form', '1.0.0', FormEditor],
  ['platform.leave-approval', '1.0.0', ApprovalEditor],
  ['platform.form', '1.1.0', UnifiedFormEditor],
  ['platform.leave-approval', '1.1.0', UnifiedApprovalEditor],
];
const editors = new Map<string, Editor>();
for (const [typeId, version, editor] of bundles) {
  const key = JSON.stringify([typeId, version]);
  if (editors.has(key)) throw new Error('Duplicate application editor version');
  editors.set(key, editor);
}
export function applicationEditor(typeId: string, version: string): Editor | undefined {
  return editors.get(JSON.stringify([typeId, version]));
}
