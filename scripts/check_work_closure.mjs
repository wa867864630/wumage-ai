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
dom.window.HTMLDialogElement.prototype.showModal = function () {this.setAttribute('open', '');};
dom.window.HTMLDialogElement.prototype.close = function () {this.removeAttribute('open');};
const React = requireWeb('react'); const {act} = React; const {createRoot} = requireWeb('react-dom/client'); const ts = requireWeb('typescript');
const cache = new Map();
function load(path) {
  if (cache.has(path)) return cache.get(path).exports;
  const module = {exports: {}}; cache.set(path, module);
  const source = ts.transpileModule(readFileSync(path, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022}}).outputText;
  const local = name => {
    if (name.endsWith('.css')) return {};
    if (name === 'react-dom/client') return {createRoot: () => ({render() {}})};
    if (!name.startsWith('.')) return requireWeb(name);
    const base = resolve(dirname(path), name); return load(extname(base) ? base : ['.tsx', '.ts'].map(extension => base + extension).find(existsSync));
  };
  new Function('require', 'module', 'exports', source)(local, module, module.exports); return module.exports;
}
globalThis.sessionStorage = window.sessionStorage;
const clone = value => JSON.parse(JSON.stringify(value));
const response = (value, status = 200) => new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}});
const config = JSON.parse(readFileSync(resolve(repo, 'packages/contracts/application-types/v1/examples/form-config.json'), 'utf8'));
const appId = '22222222-2222-4222-8222-222222222222'; const release = '33333333-3333-4333-8333-333333333333';
const base = `/api/v1/applications/${appId}`; const commands = new Map(); const draftRows = new Map(); const writes = [];
let loseDraft = true; let loseLifecycle = true; let loseAI = true; let lifecycle = {status: 'active', revision: 0}; let submitted;
let definitionRelease = release;
let sourceMode = 'normal'; let aiMode = 'enabled';
globalThis.fetch = async (url, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : null;
  if (url === base + '/form') return response({content: config, release_id: definitionRelease, accepting_submissions: true});
  if (url === '/api/v1/applications') return response({applications: [
    {id: appId, type_id: 'platform.form', name: 'Synthetic registration', category: '校园事务', desc: 'Synthetic form', ready: true},
    {id: 'schedule', type_id: 'campus.course-query', name: 'Synthetic course', category: '学习服务', desc: 'Synthetic course', ready: true}]});
  if (url === '/api/v1/me') return response({tenant_id: 'tenant', subject: 'student', roles: ['student'], student_id: 'synthetic-student', mode: 'demo'});
  if (url === '/api/v1/assistant/resolve') {
    assert.equal(body.text, '今天课表'); return response({status: 'query', application_id: 'schedule', application_name: 'Synthetic course', query_text: '今天课表', type_id: 'campus.course-query'});
  }
  if (url === '/api/v1/course-schedule/query') {
    writes.push({kind: 'query'}); return response({data: null, ui_hint: {release_id: 'course-release', columns: []}, routing: {status: 'resolved', message: 'Synthetic course response', suggestions: []}, ai_used: false, ai_duration_ms: 0, trace_id: 'synthetic-query'});
  }
  if (url.startsWith(base + '/business-drafts/')) {
    const id = url.split('/').at(-1);
    if (options.method === 'PUT') {
      writes.push({kind: 'draft', key: body.idempotency_key});
      if (!commands.has(body.idempotency_key)) {
        const previous = draftRows.get(id);
        if (previous && body.expected_revision !== previous.revision) return response({error: {message: '填写草稿已变化，请核对最新状态。'}}, 409);
        const value = {draft_id: id, revision: body.expected_revision + 1, status: 'active', release_id: body.release_id, values: clone(body.values)};
        commands.set(body.idempotency_key, value); draftRows.set(id, {...value, id, values_json: value.values, content: config, record_id: null});
      }
      if (loseDraft) {loseDraft = false; throw new TypeError('Synthetic draft response loss');}
      return response(commands.get(body.idempotency_key));
    }
    return response(draftRows.get(id));
  }
  if (url === base + '/records' && options.method === 'POST') {submitted = body; writes.push({kind: 'submit', key: body.idempotency_key}); return response({record_id: '44444444-4444-4444-8444-444444444444'});}
  if (url.startsWith(base + '/records?')) return response({items: [], total: 0});
  if (url.startsWith('/api/v1/my-work?')) return response({items: [...draftRows.values()].map(row => ({...row, application_id: appId, type_id: 'platform.form', updated_at: '2026-10-20T00:00:00Z'})), total: draftRows.size, assigned: [], submitted: []});
  if (url.endsWith('/lifecycle')) {
    writes.push({kind: 'lifecycle', key: body.idempotency_key});
    if (!commands.has(body.idempotency_key)) {assert.equal(body.expected_revision, lifecycle.revision); lifecycle = {status: body.status, revision: lifecycle.revision + 1}; commands.set(body.idempotency_key, clone(lifecycle));}
    if (loseLifecycle) {loseLifecycle = false; throw new TypeError('Synthetic lifecycle response loss');}
    return response(commands.get(body.idempotency_key));
  }
  if (url === '/api/v1/admin/applications') {
    if (sourceMode === 'failed') throw new TypeError('Synthetic source failure');
    return response({items: sourceMode === 'empty' ? [] : [
      {application_id: appId, type_id: 'platform.form', name: 'Synthetic source'},
      {application_id: 'disabled-source', type_id: 'platform.form', name: 'Disabled source', lifecycle: {status: 'disabled'}},
      {application_id: 'archived-source', type_id: 'platform.form', name: 'Archived source', lifecycle: {status: 'archived'}},
      {application_id: 'other-type', type_id: 'campus.course-query', name: 'Other type'}]});
  }
  if (url === '/api/v1/admin/ai-model') return aiMode === 'forbidden' ? response({error: {message: 'Synthetic forbidden'}}, 403) : response({enabled: aiMode === 'enabled'});
  if (url.endsWith('/generate')) {
    assert.ok(['登记姓名', '登记姓名，增加联系电话'].includes(body.text)); assert.ok(body.idempotency_key); assert.equal(body.expected_revision, 1);
    writes.push({kind: 'ai', key: body.idempotency_key});
    commands.set(body.idempotency_key, {generation_status: 'proposed', item: {id: 'synthetic-task', revision: 1}, content: config, summary: 'Synthetic proposal', ai_duration_ms: 1250});
    if (loseAI) {loseAI = false; throw new TypeError('Synthetic AI response loss');}
    return response(commands.get(body.idempotency_key));
  }
  if (url.startsWith('/api/v1/assistant/requirements/commands/')) return response({status: 'succeeded', result: commands.get(url.split('/').at(-1))});
  throw new Error('Unexpected work HTTP: ' + url);
};
const host = document.getElementById('root'); const root = createRoot(host);
const flush = async () => {await act(async () => {await new Promise(resolve => setTimeout(resolve, 0));});};
const button = label => [...host.querySelectorAll('button')].find(element => element.textContent === label);
async function click(element) {assert.ok(element, 'Missing clickable element'); assert.ok(!element.matches(':disabled')); await act(async () => element.dispatchEvent(new MouseEvent('click', {bubbles: true}))); await flush();}
async function input(element, value) {
  assert.ok(element); const prototype = element.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : element.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  await act(async () => {Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value); element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true}));}); await flush();
}
try {
  const {FormRuntime} = load(resolve(web, 'src/FormRuntime.tsx')); let pause; let dirty = false;
  const props = {token: 'synthetic', applicationId: appId, scope: 'tenant:student', onExpired() {}, onDirtyChange(value) {dirty = value;}, onPauseReady(value) {pause = value;}};
  await act(async () => root.render(React.createElement(FormRuntime, props))); await flush();
  await input(host.querySelector('.form-fields textarea'), 'Half completed input'); assert.equal(dirty, true);
  let allowed; await act(async () => {allowed = await pause();}); await flush(); assert.equal(allowed, false); assert.ok(host.textContent.includes('草稿保存结果待核对'));
  await click(button('使用原请求核对草稿')); assert.equal(dirty, false); assert.equal(draftRows.size, 1);
  const draftWrites = writes.filter(row => row.kind === 'draft'); assert.equal(draftWrites[0].key, draftWrites[1].key);
  assert.equal(writes.filter(row => row.kind === 'submit').length, 0, 'Pausing is not submission');
  const {MyWork} = load(resolve(web, 'src/MyWork.tsx')); let target;
  const workFetch = globalThis.fetch; let workMode = 'normal'; let failWork; let detailMode = 'normal'; let resolveOldDetail; let workReads = 0;
  const historicalRecord = {id: 'synthetic-receipt', release_id: release, content: config, values: {content: 'Historical submitted value'}};
  globalThis.fetch = async (url, options) => {
    if (url === base + '/records/synthetic-receipt') {
      if (detailMode === 'pending') return new Promise(resolve => {resolveOldDetail = resolve;});
      if (detailMode === 'failed') return response({error: {message: 'Synthetic record unavailable'}}, 404);
      return response(historicalRecord);
    }
    if (url === base + '/approvals/synthetic-assignment') return response({...historicalRecord, id: 'synthetic-assignment', duration_days: 2, workflow: {status: 'pending', last_reason: null}});
    if (!url.startsWith('/api/v1/my-work?')) return workFetch(url, options);
    workReads++;
    if (workMode === 'pending') return new Promise((_, reject) => {failWork = reject;});
    const value = await (await workFetch(url, options)).json();
    value.assigned = [{id: 'synthetic-assignment', application_id: appId}];
    value.submitted = [{id: 'synthetic-receipt', application_id: appId, type_id: 'platform.form'}];
    if (workMode === 'many') value.total = 21;
    if (workMode === 'shrunk' && url.endsWith('page=2')) value.items = [];
    return response(value);
  };
  await act(async () => root.render(React.createElement(MyWork, {token: 'synthetic', onResume(value) {target = value;}, onClose() {}, onExpired() {}}))); await flush();
  assert.ok(button('进入原应用办理审批')); assert.ok(button('核对原应用记录'));
  const readsBeforeFilter = workReads;
  const workFilter = host.querySelector('input[type="search"]');
  await input(workFilter, '  SYNTHETIC-RECEIPT  ');
  assert.ok(button('查看此记录详情'));
  assert.equal(button('查看此申请详情'), undefined);
  assert.equal(button('返回续填此草稿'), undefined);
  assert.ok(host.textContent.includes('当前页草稿没有匹配项'));
  assert.equal(host.textContent.includes('暂无已保存的填写草稿'), false, 'No match must not claim that no drafts exist');
  await input(workFilter, 'no-matching-work');
  assert.equal(button('查看此记录详情'), undefined);
  await input(workFilter, appId);
  assert.ok(button('查看此记录详情')); assert.ok(button('查看此申请详情')); assert.ok(button('返回续填此草稿'));
  assert.equal(workReads, readsBeforeFilter, 'Filtering loaded work makes no new list request');
  await click(button('清除工作筛选'));
  const writesBeforeDetails = writes.length;
  await click(button('查看此记录详情'));
  let recordDetail = host.querySelector('[aria-label="我的工作记录详情"]');
  assert.ok(recordDetail.textContent.includes('Historical submitted value'));
  assert.ok(recordDetail.textContent.includes(release), 'Detail is pinned to submission release');
  detailMode = 'failed'; await click(button('刷新此记录'));
  assert.ok(recordDetail.textContent.includes('Synthetic record unavailable'));
  assert.equal(recordDetail.textContent.includes('Historical submitted value'), false, 'Failed detail refresh hides the previous record');
  detailMode = 'normal'; await click(button('刷新此记录'));
  assert.ok(recordDetail.textContent.includes('Historical submitted value'));
  await click(button('关闭记录详情'));
  detailMode = 'pending'; await click(button('查看此记录详情'));
  await click(button('查看此申请详情'));
  await act(async () => resolveOldDetail(response(historicalRecord))); await flush();
  recordDetail = host.querySelector('[aria-label="我的工作记录详情"]');
  assert.ok(recordDetail.textContent.includes('记录编号：synthetic-assignment'));
  assert.ok(recordDetail.textContent.includes('请假状态：待审批 · 2 天'));
  assert.equal(writes.length, writesBeforeDetails, 'Reading details never submits or transitions business records');
  assert.equal(target, undefined);
  workMode = 'pending'; await click(button('刷新工作'));
  assert.equal(button('返回续填此草稿'), undefined);
  assert.equal(button('进入原应用办理审批'), undefined);
  assert.equal(button('核对原应用记录'), undefined, 'All previous work targets disappear while refreshing');
  await act(async () => failWork(new TypeError('Synthetic work list unavailable'))); await flush();
  assert.ok(host.textContent.includes('请点击“刷新工作”重试'));
  assert.equal(button('返回续填此草稿'), undefined, 'Failed refresh cannot expose stale targets');
  assert.equal(target, undefined);
  workMode = 'many'; await click(button('刷新工作'));
  await click(button('下一页草稿')); assert.ok(host.textContent.includes('第 2 页'));
  workMode = 'shrunk'; await click(button('刷新工作'));
  assert.ok(host.textContent.includes('草稿数量已变化'));
  assert.ok(host.textContent.includes('第 1 页'));
  await click(button('返回续填此草稿')); assert.ok(target.draftId); assert.equal(target.applicationId, appId);
  globalThis.fetch = workFetch;
  await act(async () => root.render(React.createElement(FormRuntime, {...props, key: 'resume', draftId: target.draftId}))); await flush();
  assert.equal(host.querySelector('.form-fields textarea').value, 'Half completed input');
  const changedElsewhere = draftRows.get(target.draftId); changedElsewhere.revision++;
  changedElsewhere.values_json = changedElsewhere.values = {content: 'Saved in another tab'};
  await input(host.querySelector('.form-fields textarea'), 'Unsaved current tab'); await click(button('保存填写草稿'));
  assert.ok(host.textContent.includes('填写草稿已变化'));
  await click(button('重新载入已保存草稿')); assert.equal(host.querySelector('.form-fields textarea').value, 'Saved in another tab');
  assert.ok(host.textContent.includes('Unsaved current tab'), 'Conflict reload preserves local input in a visible backup');
  definitionRelease = '55555555-5555-4555-8555-555555555555';
  await act(async () => root.render(React.createElement(FormRuntime, {...props, key: 'old-version', draftId: target.draftId}))); await flush();
  assert.ok(host.textContent.includes('按原版本保留草稿'));
  await click(button('重新加载表单')); assert.ok(host.textContent.includes('先保存填写草稿'));
  await click(button('核对登记内容')); await click(button('确认提交登记')); assert.equal(submitted, undefined, 'Old draft version cannot silently submit under a new release');
  await click(button('保存填写草稿')); await click(button('核对登记内容')); await click(button('确认提交登记')); assert.equal(submitted.draft_id, target.draftId); assert.equal(submitted.draft_revision, 3);
  const {ApplicationLifecycle} = load(resolve(web, 'src/ApplicationLifecycle.tsx'));
  await act(async () => root.render(React.createElement(ApplicationLifecycle, {token: 'synthetic', applicationId: appId, value: clone(lifecycle), disabled: false, onDirtyChange() {}, onLock() {}, onUpdated() {}, onExpired() {}}))); await flush();
  await input(host.querySelector('input'), 'Synthetic stop reason'); await click(button('停用')); assert.ok(host.textContent.includes('结果待核对'));
  await act(async () => root.render(React.createElement(ApplicationLifecycle, {key: 'lifecycle-reload', token: 'synthetic', applicationId: appId, value: clone(lifecycle), disabled: false, onDirtyChange() {}, onLock() {}, onUpdated() {}, onExpired() {}}))); await flush();
  assert.ok(host.textContent.includes('结果待核对'), 'Lifecycle reload restores the original principal-scoped command');
  await click(button('使用原请求核对')); assert.equal(lifecycle.status, 'disabled'); assert.equal(lifecycle.revision, 1);
  const lifecycleWrites = writes.filter(row => row.kind === 'lifecycle'); assert.equal(lifecycleWrites[0].key, lifecycleWrites[1].key);
  const {ConstructionOptions} = load(resolve(web, 'src/ConstructionOptions.tsx')); let creation;
  await act(async () => root.render(React.createElement(ConstructionOptions, {token: 'synthetic', scope: 'tenant:admin', task: {id: 'synthetic-task', revision: 1, business_kind: 'registration'}, disabled: false, onChange(value) {creation = value;}}))); await flush();
  await input(host.querySelector('select'), 'copy'); await input(host.querySelectorAll('select')[1], appId); assert.equal(creation.source_application_id, appId);
  assert.equal(host.querySelectorAll('select')[1].options.length, 2, 'Only active same-type sources are offered');
  sourceMode = 'failed'; await click(button('刷新复制来源'));
  assert.equal(creation, null, 'Refreshing invalidates the selected source');
  assert.ok(host.textContent.includes('来源应用加载失败'));
  sourceMode = 'empty'; await click(button('刷新复制来源'));
  assert.ok(host.textContent.includes('暂无可复制的同类型启用应用'));
  sourceMode = 'normal'; await click(button('刷新复制来源'));
  await input(host.querySelectorAll('select')[1], appId); assert.equal(creation.source_application_id, appId);
  aiMode = 'disabled'; await click(button('重新检查 AI 配置'));
  assert.ok(host.textContent.includes('AI 尚未启用'));
  assert.equal(host.querySelector('option[value="ai"]').disabled, true);
  aiMode = 'forbidden'; await click(button('重新检查 AI 配置'));
  assert.ok(host.textContent.includes('当前身份或环境不允许'));
  assert.equal(creation.source_application_id, appId, 'AI capability checks preserve copy selection');
  aiMode = 'enabled'; await click(button('重新检查 AI 配置'));
  await input(host.querySelector('select'), 'ai'); await input(host.querySelector('textarea'), '登记姓名'); await click(button('生成设计提案'));
  assert.equal(creation, null);
  aiMode = 'disabled'; await click(button('重新检查 AI 配置'));
  assert.equal(button('核对原 AI 请求').disabled, false, 'Disabled model does not block original-command reconciliation');
  await click(button('核对原 AI 请求')); assert.ok(host.textContent.includes('AI 用时：1.3 秒'));
  aiMode = 'enabled'; await click(button('重新检查 AI 配置'));
  const review = host.querySelector('[aria-label="设计提案核对"]');
  assert.ok(review.textContent.includes('宽度（12 等份）'));
  assert.deepEqual([...review.querySelectorAll('li strong')].map(node => node.textContent), config.form.sections.flatMap(section => section.fields.map(field => field.label)));
  assert.ok(review.textContent.includes('仍需检查配置、试用并单独发布'));
  assert.equal(creation, null, 'Reading the proposal never accepts or creates it');
  await click(host.querySelector('input[type="checkbox"]')); assert.ok(creation.generation_key); assert.equal(writes.filter(row => row.kind === 'ai').length, 1);
  await click(button('核对原 AI 请求'));
  assert.equal(host.querySelector('input[type="checkbox"]').checked, false);
  assert.equal(creation, null, 'Reconciled proposal requires explicit review again');
  await click(host.querySelector('input[type="checkbox"]'));
  const originalGeneration = creation.generation_key;
  window.confirm = () => false;
  await click(button('调整需求重新生成'));
  assert.equal(creation.generation_key, originalGeneration, 'Cancelling revision keeps the accepted proposal');
  assert.equal(host.querySelector('textarea').disabled, true);
  window.confirm = () => true;
  await click(button('调整需求重新生成'));
  assert.equal(creation, null);
  assert.equal(host.querySelector('[aria-label="设计提案核对"]'), null);
  assert.equal(host.querySelector('textarea').value, '登记姓名');
  assert.equal(host.querySelector('textarea').disabled, false);
  assert.equal(writes.filter(row => row.kind === 'ai').length, 1, 'Revision itself never calls the model');
  await input(host.querySelector('textarea'), '登记姓名，增加联系电话');
  await click(button('生成设计提案'));
  assert.equal(creation, null, 'New proposal is not automatically accepted');
  await click(host.querySelector('input[type="checkbox"]'));
  assert.notEqual(creation.generation_key, originalGeneration);
  assert.equal(writes.filter(row => row.kind === 'ai').length, 2);
  assert.ok(commands.has(originalGeneration), 'Original command remains available for reconciliation');
  const currentGeneration = creation.generation_key;
  const validResult = clone(commands.get(currentGeneration));
  for (const item of [undefined, {id: 'other-task', revision: 1}, {id: 'synthetic-task', revision: 2}]) {
    commands.set(currentGeneration, {...validResult, item});
    await click(button('核对原 AI 请求'));
    assert.equal(creation, null);
    assert.equal(host.querySelector('input[type="checkbox"]'), null, 'Unbound or mismatched proposal cannot be accepted');
    assert.ok(host.textContent.includes('原 AI 结果与当前需求或修订不一致'));
  }
  commands.set(currentGeneration, validResult);
  await click(button('核对原 AI 请求'));
  assert.equal(host.querySelector('[role="alert"]'), null, 'Successful reconciliation clears the stale error');
  assert.equal(writes.filter(row => row.kind === 'ai').length, 2, 'Binding checks never regenerate');
  const {ConstructionPreview} = load(resolve(web, 'src/ConstructionPreview.tsx'));
  const leaveProposal = JSON.parse(readFileSync(resolve(repo, 'packages/contracts/application-types/v1/examples/leave-config.json'), 'utf8'));
  leaveProposal.application.description = '<img src=x onerror=alert(1)>';
  await act(async () => root.render(React.createElement(ConstructionPreview, {content: leaveProposal, businessKind: 'leave'})));
  assert.ok(host.textContent.includes('发布管理员单级审批'));
  assert.ok(host.textContent.includes('不能自审'));
  assert.ok(host.textContent.includes('<img src=x onerror=alert(1)>'));
  assert.equal(host.querySelector('img'), null, 'Model text is displayed as text, never HTML');
  const {UnifiedFormEditor, UnifiedApprovalEditor} = load(resolve(web, 'src/UnifiedFormEditor.tsx'));
  const editorFetch = globalThis.fetch; let editorContent = clone(config); let editorRevision = 0; let editorRelease = null; let editorPublished = null; const editorWrites = []; const editorPublishKeys = []; let loseEditorPublish = true;
  const editorBase = `/api/v1/admin/applications/${appId}/form`;
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    if (url === editorBase) return response({content: editorContent, revision: editorRevision, current_release_id: editorRelease});
    if (url === editorBase + '/releases') return response([]);
    if (url === editorBase + '/releases/' + release) return response({id: release, content: editorPublished});
    if (url === editorBase + '/draft') {assert.equal(body.expected_revision, editorRevision); editorContent = clone(body.content); editorRevision++; editorWrites.push('save'); return response({});}
    if (url === editorBase + '/validate') {assert.equal(body.expected_revision, editorRevision); editorWrites.push('validate'); return response({valid: true});}
    if (url === editorBase + '/publish') {assert.equal(body.expected_revision, editorRevision); editorRelease = release; editorPublished = clone(editorContent); editorWrites.push('publish'); editorPublishKeys.push(body.idempotency_key); if (loseEditorPublish) {loseEditorPublish = false; throw new TypeError('Synthetic unified publish response loss');} return response({release_id: release});}
    return editorFetch(url, options);
  };
  const editorProps = {initialToken: 'synthetic', applicationId: appId, onDirtyChange() {}, onNavigationLock() {}, onSessionExpired() {throw new Error('Unexpected expiry');}};
  await act(async () => root.render(React.createElement(UnifiedFormEditor, editorProps))); await flush();
  assert.ok(host.querySelector('.application-detail-heading .application-statuses'), 'Registration shows common publication status');
  assert.equal(host.querySelector('.management-action-bar').parentElement, host.querySelector('.unified-config-editor'), 'Common actions stay outside the padded tab panel');
  assert.match(host.querySelector('.management-action-bar small').textContent, /基础信息/);
  const commonTabs = [...host.querySelectorAll('[role="tab"]')].map(node => node.textContent);
  assert.ok(commonTabs.includes('基础信息') && commonTabs.includes('发布记录'));
  const commonBasic = host.querySelector('.management-form-grid'); assert.ok(commonBasic);
  await input(commonBasic.querySelector('input'), 'Unified registration name');
  await click(button('暂存更改')); assert.equal(editorContent.application.name, 'Unified registration name');
  await click(button('页面与表单'));
  const originalFieldCount = editorContent.model.fields.length;
  await click(button('添加字段'));
  let addedField = [...host.querySelectorAll('.form-control-editor')].at(-1);
  await input(addedField.querySelector('input'), 'Contact preference');
  await input(addedField.querySelector('select'), 'select');
  await input(addedField.querySelector('textarea'), 'email|Email\nphone|Phone');
  await input(addedField.querySelectorAll('select')[3], '4');
  const writesBeforeOutline = editorWrites.length;
  await click(button('收起全部字段'));
  assert.ok([...host.querySelectorAll('.field-designer-settings')].every(node => node.hidden));
  const locateAdded = [...host.querySelectorAll('[aria-label="字段快速定位"] button')].find(node => node.textContent.includes('Contact preference'));
  await click(locateAdded);
  addedField = [...host.querySelectorAll('.form-control-editor')].at(-1);
  assert.equal(addedField.querySelector('.field-designer-settings').hidden, false);
  assert.equal(document.activeElement, addedField, 'Quick navigation focuses the selected field');
  assert.equal(addedField.querySelector('textarea').value, 'email|Email\nphone|Phone', 'Collapse retains unsaved edits');
  assert.equal(editorWrites.length, writesBeforeOutline, 'Outline navigation never writes configuration');
  await click(button('展开全部字段'));
  assert.ok([...host.querySelectorAll('.field-designer-settings')].every(node => !node.hidden));
  await click(button('暂存更改'));
  assert.equal(editorContent.model.fields.length, originalFieldCount + 1);
  assert.deepEqual(editorContent.model.fields.at(-1).options, [{value: 'email', label: 'Email'}, {value: 'phone', label: 'Phone'}]);
  assert.equal(editorContent.form.sections[0].fields.at(-1).layout.desktop, 4);
  assert.equal(editorContent.form.sections[0].fields.at(-1).label, 'Contact preference');
  editorContent = clone(leaveProposal); editorContent.application.description = 'Synthetic leave'; editorRevision = 0; editorRelease = null;
  await act(async () => root.render(React.createElement(UnifiedApprovalEditor, {...editorProps, key: 'unified-approval'}))); await flush();
  assert.ok(host.querySelector('.application-detail-heading .application-statuses'), 'Approval shows the same publication status');
  assert.match(host.querySelector('.management-action-bar small').textContent, /基础信息/);
  const leaveTabs = [...host.querySelectorAll('[role="tab"]')].map(node => node.textContent);
  assert.deepEqual(leaveTabs.filter(label => label !== '流程设计'), commonTabs, 'Both types share the same public configuration tabs');
  await click(button('页面与表单'));
  const protectedControl = host.querySelector('.form-control-editor');
  assert.ok(protectedControl.querySelector('select').disabled, 'Leave core component remains protected');
  assert.ok([...protectedControl.querySelectorAll('button')].find(node => node.textContent === '移除字段').disabled);
  assert.ok(protectedControl.querySelector('input[type="checkbox"]').disabled, 'Leave core required status remains protected');
  await click(button('流程设计'));
  const workflowFields = host.querySelector('[aria-label="单级流程设计"]');
  assert.ok(workflowFields.querySelector('.config-section-heading'), 'Workflow follows the same section heading layout');
  await input(workflowFields.querySelectorAll('input')[0], 'My leave workflow');
  await input(workflowFields.querySelectorAll('input')[1], 'My review node');
  assert.ok(host.querySelector('.workflow-diagram').textContent.includes('My review node'));
  await click(button('暂存更改')); assert.equal(editorContent.workflow.name, 'My leave workflow');
  assert.equal(editorContent.workflow.nodes.find(node => node.type === 'approval').name, 'My review node');
  await click(button('试跑发布')); assert.equal(button('确认发布').disabled, true);
  await click(button('校验当前草稿')); await click(button('确认发布'));
  assert.ok(button('核对本次发布结果'), 'Unknown publication can be reconciled inside the locked dialog');
  await click(button('核对本次发布结果'));
  assert.equal(editorPublishKeys[0], editorPublishKeys[1]);
  assert.deepEqual(editorWrites, ['save', 'save', 'save', 'validate', 'publish', 'publish']);
  await input(host.querySelector('[aria-label="单级流程设计"] input'), 'Revised workflow');
  await click(button('暂存更改')); await click(button('试跑发布'));
  const designReview = host.querySelector('[aria-label="设计发布变更核对"]');
  assert.ok(designReview.textContent.includes('流程名称：My leave workflow → Revised workflow'));
  assert.equal(button('确认发布').disabled, true, 'Changed design requires a new validation');
  assert.equal(editorPublished.workflow.name, 'My leave workflow', 'Draft edits never change the published baseline');
  await click(button('返回配置'));
  const {ApplicationChangeReview} = load(resolve(web, 'src/ApplicationChangeReview.tsx'));
  const oldAccess = {application: {...config.application, visible_roles: ['student', 'teacher']}};
  await act(async () => root.render(React.createElement(ApplicationChangeReview, {before: oldAccess, after: {application: {...oldAccess.application, visible_roles: ['teacher', 'student']}}})));
  assert.ok(host.textContent.includes('基础信息与访问范围无变化'), 'Role order alone is not an access change');
  await act(async () => root.render(React.createElement(ApplicationChangeReview, {before: oldAccess, after: {application: {...oldAccess.application, visible_roles: ['config_admin']}}})));
  assert.ok(host.textContent.includes('可使用角色：学生、教师 → 配置管理员'));
  assert.ok(host.textContent.includes('可能影响已有记录的访问入口'));
  globalThis.fetch = editorFetch;
  draftRows.clear(); definitionRelease = release;
  window.HTMLElement.prototype.scrollIntoView = function () {};
  const {Workspace} = load(resolve(web, 'src/main.tsx'));
  await act(async () => root.render(React.createElement(Workspace, {session: {token: 'synthetic', name: 'Synthetic user', identity: {tenant_id: 'tenant', subject: 'student', roles: ['student'], student_id: 'synthetic-student', mode: 'demo'}}, onLogout() {throw new Error('Unexpected logout');}}))); await flush();
  await click([...host.querySelectorAll('.service-shortcut')].find(element => element.textContent.includes('Synthetic registration')));
  await input(host.querySelector('.form-fields textarea'), 'Cross app draft'); await input(host.querySelector('#question'), '今天课表');
  const before = writes.length;
  await act(async () => host.querySelector('.composer').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}))); await flush();
  assert.equal(draftRows.size, 1); assert.deepEqual(writes.slice(before).map(row => row.kind), ['draft', 'query']);
  assert.equal(host.querySelector('.form-runtime'), null, 'Course query switches away from form');
  await input(host.querySelector('#question'), '返回刚才');
  await act(async () => host.querySelector('.composer').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}))); await flush();
  await click(button('返回续填此草稿')); assert.equal(host.querySelector('.form-fields textarea').value, 'Cross app draft');
  assert.equal(writes.filter(row => row.kind === 'submit').length, 1, 'Returning never replays a business submission');
  console.log('Work closure DOM passed: draft replay, old-release migration, lifecycle replay, copy/AI reconciliation, and Workspace save-before-course-query then explicit task resume.');
} finally {await act(async () => root.unmount()); dom.window.close();}
