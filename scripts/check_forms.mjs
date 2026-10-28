// Synthetic React DOM acceptance; no real model, credentials or business-data writes.
import assert from 'node:assert/strict';
import {readFileSync, existsSync} from 'node:fs';
import {resolve, dirname, extname} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..'); const web = resolve(repo, 'apps/web');
const requireWeb = createRequire(resolve(web, 'package.json'));
const {JSDOM} = requireWeb(process.env.WORKBENCH_JSDOM_PATH || 'jsdom');
const dom = new JSDOM('<!doctype html><div id="root"></div>', {url: 'http://localhost/'});
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLTextAreaElement', 'HTMLSelectElement', 'Event', 'MouseEvent'])
  Object.defineProperty(globalThis, key, {value: dom.window[key], configurable: true});
globalThis.IS_REACT_ACT_ENVIRONMENT = true; window.confirm = () => true;
const React = requireWeb('react'); const {act} = React; const {createRoot} = requireWeb('react-dom/client'); const ts = requireWeb('typescript');
const cache = new Map();
function load(path) {
  if (cache.has(path)) return cache.get(path).exports;
  const module = {exports: {}}; cache.set(path, module);
  const source = ts.transpileModule(readFileSync(path, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022}}).outputText;
  const local = name => {
    if (name.endsWith('.css')) return {};
    if (!name.startsWith('.')) return requireWeb(name);
    const base = resolve(dirname(path), name); return load(extname(base) ? base : ['.tsx', '.ts'].map(extension => base + extension).find(existsSync));
  };
  new Function('require', 'module', 'exports', source)(local, module, module.exports); return module.exports;
}
const clone = value => JSON.parse(JSON.stringify(value));
const response = (value, status = 200) => new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}});
let config = JSON.parse(readFileSync(resolve(repo, 'packages/contracts/application-types/v1/examples/form-config.json'), 'utf8'));
let revision = 0; let release = null; let published; const releases = []; const commands = new Map(); const records = [];
let loseSave = true; let losePublish = true; let loseSubmit = true; const writes = [];
const appId = '22222222-2222-4222-8222-222222222222'; const adminBase = `/api/v1/admin/applications/${appId}/form`; const publicBase = `/api/v1/applications/${appId}`;
globalThis.fetch = async (url, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : null;
  if (url === '/api/v1/me') return response({tenant_id: 'synthetic-tenant', subject: 'synthetic-admin'});
  if (url === adminBase) return response({content: clone(config), revision, current_release_id: release});
  if (url === adminBase + '/releases') return response(clone(releases));
  if (url === adminBase + '/draft') {
    writes.push({kind: 'save', key: body.idempotency_key});
    if (!commands.has(body.idempotency_key)) {
      assert.equal(body.expected_revision, revision); config = clone(body.content); revision++;
      commands.set(body.idempotency_key, {content: clone(config), revision, current_release_id: release});
    }
    if (loseSave) {loseSave = false; throw new TypeError('Synthetic save response loss');}
    return response(clone(commands.get(body.idempotency_key)));
  }
  if (url === adminBase + '/preview') {
    assert.ok(Object.values(body.values).includes(0), 'Trial preserves legal zero'); return response({valid: true});
  }
  if (url === adminBase + '/publish') {
    writes.push({kind: 'publish', key: body.idempotency_key});
    if (!commands.has(body.idempotency_key)) {
      assert.equal(body.expected_revision, revision); release = '33333333-3333-4333-8333-333333333333'; published = clone(config); revision++;
      releases.push({id: release, created_at: '2026-10-20T00:00:00Z', operation: 'publish'}); commands.set(body.idempotency_key, {release_id: release});
    }
    if (losePublish) {losePublish = false; throw new TypeError('Synthetic publication response loss');}
    return response(commands.get(body.idempotency_key));
  }
  if (url === publicBase + '/form') return response({content: clone(published), release_id: release, accepting_submissions: true});
  if (url === publicBase + '/records' && options.method === 'POST') {
    writes.push({kind: 'submit', key: body.idempotency_key});
    if (!commands.has(body.idempotency_key)) {
      assert.equal(body.release_id, release); assert.ok(Object.values(body.values).includes(0)); assert.ok(Object.values(body.values).includes(false));
      const row = {id: '44444444-4444-4444-8444-444444444444', release_id: release, owner_id: 'synthetic-student', created_at: '2026-10-20T00:00:00Z', values: clone(body.values), content: clone(published), command_key: body.idempotency_key};
      records.push(row); commands.set(body.idempotency_key, {record_id: row.id, release_id: release});
    }
    if (loseSubmit) {loseSubmit = false; throw new TypeError('Synthetic submission response loss');}
    return response(commands.get(body.idempotency_key));
  }
  if (url.startsWith(publicBase + '/records/by-command/')) {
    const result = commands.get(url.split('/').at(-1)); return response({found: !!result, record_id: result?.record_id});
  }
  if (url.startsWith(publicBase + '/records?') || url.startsWith(adminBase + '/records?')) return response({items: clone(records), total: records.length});
  if (url.startsWith(publicBase + '/records/') || url.startsWith(adminBase + '/records/')) return response(clone(records[0]));
  throw new Error('Unexpected form HTTP: ' + url);
};
const host = document.getElementById('root'); const root = createRoot(host);
const flush = async () => {await act(async () => {await new Promise(resolve => setTimeout(resolve, 0));});};
const button = (label, scope = host) => [...scope.querySelectorAll('button')].find(element => element.textContent === label);
async function click(element) {assert.ok(element, 'Missing clickable element'); assert.ok(!element.matches(':disabled')); await act(async () => element.dispatchEvent(new MouseEvent('click', {bubbles: true}))); await flush();}
async function input(element, value) {
  assert.ok(element, 'Missing editable field'); const prototype = element.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : element.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  await act(async () => {Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value); element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true}));}); await flush();
}
const labelled = (scope, text) => [...scope.querySelectorAll('label')].find(label => label.textContent.startsWith(text))?.querySelector('input,select,textarea');
try {
  const {FormEditor} = load(resolve(web, 'src/FormEditor.tsx'));
  let dirty = false;
  await act(async () => root.render(React.createElement(FormEditor, {initialToken: 'synthetic-admin', applicationId: appId, onDirtyChange(value) {dirty = value;}}))); await flush();
  await click(button('表单字段'));
  await click(button('添加字段'));
  let editor = host.querySelectorAll('.form-control-editor')[1];
  await input(labelled(editor, '名称'), '次数'); await input(labelled(editor, '组件'), 'number');
  await input(labelled(editor, '最小值'), '0'); await input(labelled(editor, '最大值'), '10');
  await click(button('添加字段')); editor = host.querySelectorAll('.form-control-editor')[2];
  await input(labelled(editor, '名称'), '是否参加'); await input(labelled(editor, '组件'), 'checkbox');
  await click(button('添加字段')); editor = host.querySelectorAll('.form-control-editor')[3];
  await input(labelled(editor, '名称'), '类别'); await input(labelled(editor, '组件'), 'select'); await input(labelled(editor, '选项'), 'a|类别甲\nb|类别乙');
  assert.equal(dirty, true);
  await click(button('暂存表单')); assert.ok(host.textContent.includes('结果待核对'));
  await click(button('核对并重试原操作')); assert.equal(revision, 1); assert.equal(dirty, false);
  const saves = writes.filter(write => write.kind === 'save'); assert.equal(saves[0].key, saves[1].key);
  assert.equal(config.model.fields[1].constraints.minimum, 0);
  await click(button('预览试填'));
  await input(host.querySelector('.form-fields textarea'), 'Synthetic preview'); await input(host.querySelector('.form-fields input[type="number"]'), '0');
  await click(button('校验试填')); assert.ok(host.textContent.includes('通过服务端表单校验')); assert.equal(records.length, 0);
  await click(button('发布表单')); assert.ok(host.textContent.includes('结果待核对'));
  await click(button('核对并重试原操作')); assert.equal(releases.length, 1);
  const publications = writes.filter(write => write.kind === 'publish'); assert.equal(publications[0].key, publications[1].key);
  const {FormRuntime, RecordBrowser} = load(resolve(web, 'src/FormRuntime.tsx'));
  await act(async () => root.render(React.createElement(FormRuntime, {key: 'runtime', token: 'synthetic-student', applicationId: appId, scope: 'synthetic-tenant:synthetic-student', onExpired() {}, onDirtyChange(value) {dirty = value;}}))); await flush();
  await input(host.querySelector('.form-fields textarea'), 'My immutable registration'); await input(host.querySelector('.form-fields input[type="number"]'), '0');
  await input(host.querySelector('.form-fields select'), 'a');
  await click(button('核对登记内容')); await click(button('确认提交登记')); assert.ok(host.textContent.includes('提交结果待核对'));
  await click(button('核对提交结果')); assert.equal(records.length, 1); assert.equal(writes.filter(write => write.kind === 'submit').length, 1); assert.equal(dirty, false);
  assert.ok(host.textContent.includes('登记已提交')); await click(button('查看登记详情'));
  const detail = host.querySelector('[aria-label="登记详情"]'); assert.ok(detail.textContent.includes('My immutable registration')); assert.ok(detail.textContent.includes('否')); assert.ok(detail.textContent.includes('次数0')); assert.ok(detail.textContent.includes('类别甲'));
  await act(async () => root.render(React.createElement(RecordBrowser, {token: 'synthetic-admin', applicationId: appId, admin: true, onExpired() {}}))); await flush();
  assert.ok(host.querySelector('[aria-label="应用登记记录"]')); assert.ok(host.textContent.includes('synthetic-student'));
  console.log('Form DOM checks passed: component/options editing, zero constraints, server preview, same-key save/publication recovery, submission reconciliation, immutable detail and administrator records.');
} finally {await act(async () => root.unmount()); dom.window.close();}
