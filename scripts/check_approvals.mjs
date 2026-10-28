// Synthetic DOM approval acceptance; the real database state machine is tested separately.
import assert from 'node:assert/strict';
import {readFileSync, existsSync} from 'node:fs';
import {resolve, dirname, extname} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..'); const web = resolve(repo, 'apps/web'); const requireWeb = createRequire(resolve(web, 'package.json'));
const {JSDOM} = requireWeb(process.env.WORKBENCH_JSDOM_PATH || 'jsdom'); const dom = new JSDOM('<!doctype html><div id="root"></div>', {url: 'http://localhost/'});
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLTextAreaElement', 'HTMLSelectElement', 'Event', 'MouseEvent']) Object.defineProperty(globalThis, key, {value: dom.window[key], configurable: true});
globalThis.IS_REACT_ACT_ENVIRONMENT = true; window.confirm = () => true;
const React = requireWeb('react'); const {act} = React; const {createRoot} = requireWeb('react-dom/client'); const ts = requireWeb('typescript'); const cache = new Map();
function load(path) {
  if (cache.has(path)) return cache.get(path).exports; const module = {exports: {}}; cache.set(path, module);
  const source = ts.transpileModule(readFileSync(path, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022}}).outputText;
  const local = name => {if (!name.startsWith('.')) return requireWeb(name); const base = resolve(dirname(path), name); return load(extname(base) ? base : ['.tsx', '.ts'].map(extension => base + extension).find(existsSync));};
  new Function('require', 'module', 'exports', source)(local, module, module.exports); return module.exports;
}
const clone = value => JSON.parse(JSON.stringify(value)); const response = (value, status = 200) => new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}});
const config = JSON.parse(readFileSync(resolve(repo, 'packages/contracts/application-types/v1/examples/leave-config.json'), 'utf8'));
const appId = '55555555-5555-4555-8555-555555555555'; const release = '66666666-6666-4666-8666-666666666666'; const base = `/api/v1/applications/${appId}`; const tasks = []; const commands = new Map(); const actions = []; let loseResponse = true;
function add(approver = 'local-admin') {
  const id = '77777777-7777-4777-8777-' + String(tasks.length + 1).padStart(12, '0');
  const task = {id, applicant_id: 'student-one', approver_id: approver, status: 'pending', revision: 1, last_reason: null, updated_at: '2026-10-20T00:00:00Z',
    values: {start_date: '2026-10-20', end_date: '2026-10-21', reason: 'Synthetic leave'}, events: []}; tasks.push(task); return task;
}
globalThis.fetch = async (url, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : null; const admin = options.headers?.Authorization === 'Bearer synthetic-admin';
  if (url === '/api/v1/me') return response({tenant_id: 'synthetic-tenant', subject: admin ? 'local-admin' : 'student-one', roles: [admin ? 'config_admin' : 'student']});
  if (url === `/api/v1/admin/applications/${appId}/form`) return response({content: clone(config), revision: 0, current_release_id: release});
  if (url.endsWith('/form/releases')) return response([{id: release, operation: 'publish', created_at: '2026-10-20T00:00:00Z'}]);
  if (url === base + '/form') return response({content: clone(config), release_id: release, accepting_submissions: true});
  if (url === base + '/records' && options.method === 'POST') {const task = add(); task.values = body.values; return response({record_id: task.id});}
  if (url.startsWith(base + '/approvals?')) {
    const params = new URL(url, 'http://localhost').searchParams; const who = admin ? 'local-admin' : 'student-one';
    const rows = tasks.filter(task => (params.get('scope') === 'all' || params.get('scope') === 'assigned' ? task.approver_id === who || params.get('scope') === 'all' : task.applicant_id === who) && (params.get('status') === 'all' || task.status === params.get('status')));
    return response({items: clone(rows), total: rows.length});
  }
  if (url.startsWith(base + '/approvals/') && url.endsWith('/transition')) {
    const task = tasks.find(item => item.id === url.split('/').at(-2)); actions.push(clone(body));
    if (!commands.has(body.idempotency_key)) {
      assert.equal(body.expected_revision, task.revision); assert.equal(task.status, 'pending'); task.revision++;
      if (body.action === 'reassign_to_me') task.approver_id = 'local-admin'; else task.status = body.action;
      task.last_reason = body.reason; task.events.push({action: body.action, actor_id: admin ? 'local-admin' : 'student-one', reason: body.reason, created_at: '2026-10-20T00:00:00Z'});
      commands.set(body.idempotency_key, {id: task.id, status: task.status, revision: task.revision});
    }
    if (loseResponse) {loseResponse = false; throw new TypeError('Synthetic decision response loss');}
    return response(commands.get(body.idempotency_key));
  }
  if (url.startsWith(base + '/approvals/')) {const task = tasks.find(item => item.id === url.split('/').at(-1)); return response({id: task.id, content: clone(config), values: task.values, duration_days: 2, workflow: clone(task), events: task.events});}
  throw new Error('Unexpected approval HTTP: ' + url);
};
const host = document.getElementById('root'); const root = createRoot(host); const flush = async () => {await act(async () => {await new Promise(resolve => setTimeout(resolve, 0));});};
const button = (label, scope = host) => [...scope.querySelectorAll('button')].find(element => element.textContent === label);
async function click(element) {assert.ok(element, 'Missing clickable element'); assert.ok(!element.matches(':disabled')); await act(async () => element.dispatchEvent(new MouseEvent('click', {bubbles: true}))); await flush();}
async function input(element, value) {assert.ok(element); const prototype = element.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; await act(async () => {Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value); element.dispatchEvent(new Event('input', {bubbles: true}));}); await flush();}
try {
  const {ApprovalEditor} = load(resolve(web, 'src/ApprovalEditor.tsx')); const {ApprovalRuntime, ApprovalWorkList} = load(resolve(web, 'src/ApprovalRuntime.tsx'));
  await act(async () => root.render(React.createElement(ApprovalEditor, {initialToken: 'synthetic-admin', applicationId: appId}))); await flush();
  await click(button('审批流程')); assert.ok(host.querySelector('[aria-label="单级审批流程"]')); assert.ok(host.textContent.includes('历史申请固定原发布'));
  await click(button('表单字段')); for (const element of [...host.querySelectorAll('button')].filter(element => element.textContent === '移除字段')) assert.ok(element.matches(':disabled'), 'Core leave fields cannot be removed');
  let dirty = false;
  await act(async () => root.render(React.createElement(ApprovalRuntime, {token: 'synthetic-student', applicationId: appId, scope: 'synthetic:student', admin: false, onExpired() {}, onDirtyChange(value) {dirty = value;}}))); await flush();
  const dates = host.querySelectorAll('.form-fields input[type="date"]'); await input(dates[0], '2026-10-20'); await input(dates[1], '2026-10-21'); await input(host.querySelector('.form-fields textarea'), 'Synthetic leave');
  await click(button('核对申请内容')); await click(button('确认提交申请')); assert.ok(host.textContent.includes('申请已提交')); assert.ok(host.textContent.includes('待审批')); assert.equal(tasks.length, 1);
  await click(button('查看申请详情')); assert.ok(host.querySelector('[aria-label="申请详情"]').textContent.includes('2 个自然日')); assert.equal(dirty, false);
  await act(async () => root.render(React.createElement(ApprovalWorkList, {key: 'admin', token: 'synthetic-admin', applicationId: appId, admin: true, onExpired() {}}))); await flush();
  await click(button('通过申请')); assert.ok(host.textContent.includes('审批结果待核对')); await click(button('核对并重试原审批'));
  assert.equal(actions[0].idempotency_key, actions[1].idempotency_key); assert.equal(tasks[0].events.length, 1); assert.ok(host.textContent.includes('已通过'));
  add(); await click(button('刷新审批状态')); const count = actions.length; await click(button('拒绝申请')); assert.equal(actions.length, count, 'Missing rejection reason is not submitted');
  await input(host.querySelector('.approval-work textarea'), '材料不完整'); await click(button('拒绝申请')); assert.equal(tasks[1].status, 'rejected');
  add(); await act(async () => root.render(React.createElement(ApprovalWorkList, {key: 'student', token: 'synthetic-student', applicationId: appId, onExpired() {}}))); await flush();
  await click(button('撤回申请')); assert.equal(tasks[2].status, 'withdrawn'); assert.ok(!button('通过申请'), 'Ordinary applicants have no approval action');
  add('other-admin'); await act(async () => root.render(React.createElement(ApprovalWorkList, {key: 'takeover', token: 'synthetic-admin', applicationId: appId, admin: true, initialScope: 'all', onExpired() {}}))); await flush();
  await input(host.querySelector('.approval-work textarea'), '原审批人暂不可用，由我接管'); await click(button('由我接管审批')); assert.equal(tasks[3].approver_id, 'local-admin'); await click(button('通过申请')); assert.equal(tasks[3].status, 'approved');
  console.log('Approval DOM checks passed: fixed workflow/core fields, applicant submit/progress, assigned approval, lost-response same-key retry, rejection reason, withdrawal and explicit takeover.');
} finally {await act(async () => root.unmount()); dom.window.close();}
