// React DOM state regression. Pass an installed jsdom module using WORKBENCH_JSDOM_PATH.
// All HTTP/model responses are synthetic; this never reads local credentials.
import assert from 'node:assert/strict';
import {readFileSync, existsSync} from 'node:fs';
import {resolve, dirname, extname} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const rootPath = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const web = resolve(rootPath, 'apps/web');
const requireWeb = createRequire(resolve(web, 'package.json'));
const {JSDOM} = requireWeb(process.env.WORKBENCH_JSDOM_PATH || 'jsdom');
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {url: 'http://localhost/'});
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLTextAreaElement', 'HTMLSelectElement', 'Event', 'MouseEvent', 'KeyboardEvent']) {
  Object.defineProperty(globalThis, key, {value: dom.window[key], configurable: true});
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.sessionStorage = window.sessionStorage;
dom.window.HTMLDialogElement.prototype.showModal = function () {this.setAttribute('open', '');};
dom.window.HTMLDialogElement.prototype.close = function () {this.removeAttribute('open');};
dom.window.HTMLElement.prototype.scrollIntoView = function () {};
let confirms = 0;
window.confirm = () => {confirms++; return true;};
const React = requireWeb('react'); const {act} = React;
const {createRoot} = requireWeb('react-dom/client'); const ts = requireWeb('typescript');
const cache = new Map();
function load(path) {
  if (cache.has(path)) return cache.get(path).exports;
  const module = {exports: {}}; cache.set(path, module);
  const source = ts.transpileModule(readFileSync(path, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
  }}).outputText;
  const localRequire = name => {
    if (name.endsWith('.css')) return {};
    if (name === 'react-dom/client') return {createRoot: () => ({render() {}})};
    if (!name.startsWith('.')) return requireWeb(name);
    const base = resolve(dirname(path), name);
    return load(extname(base) ? base : ['.tsx', '.ts'].map(extension => base + extension).find(existsSync));
  };
  new Function('require', 'module', 'exports', source)(localRequire, module, module.exports);
  return module.exports;
}
const clone = value => JSON.parse(JSON.stringify(value));
let config = {schema_version: '1', application: {name: '课表助手', description: '合成服务说明', category: '学习服务', visible_roles: ['student', 'teacher', 'config_admin']},
  columns: [{field: 'name', label: '课程', visible: true}, {field: 'teacher', label: '教师', visible: true}], rules: []};
let revision = 0; let live = clone(config); let releaseId = null;
let questions = [{id: 'synthetic-question', text: '帮我瞅瞅明天课表', date_mode: 'tomorrow', source: 'learned', status: 'pending', revision: 1,
  occurrences: 3, interpretations: {tomorrow: 3}, reviewer: null, in_draft: false, published: false, updated_at: '2026-10-20T00:00:00Z'}];
let publicationFails = true; let regressionPasses = true; const requests = [];
const response = (value, status = 200) => new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}});
globalThis.fetch = async (url, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : null;
  requests.push({url, method: options.method, body});
  if (url === '/api/v1/admin/course-display') return response({revision, current_release_id: releaseId, content: clone(config)});
  if (url === '/api/v1/config/course-display') return response({release_id: releaseId, content: clone(live)});
  if (url.endsWith('/releases') || url.endsWith('/audit')) return response([]);
  if (url.endsWith('/draft')) {assert.equal(body.expected_revision, revision); config = clone(body.content); revision++; return response({revision, current_release_id: releaseId, content: config});}
  if (url.endsWith('/preview')) return response({routing: {status: 'resolved', message: '合成试跑结果'}, review: {
    changes: ['合成变更'], can_publish: regressionPasses, violations: regressionPasses ? [] : ['合成阻止项'], regressions: [],
  }});
  if (url.endsWith('/publish')) {
    if (publicationFails) {publicationFails = false; return response({error: {message: '合成网络结果待确认'}}, 503);}
    live = clone(config); releaseId = 'synthetic-release'; return response({release_id: releaseId});
  }
  if (url === '/api/v1/admin/question-library' && options.method === 'GET') return response({items: clone(questions), draft_revision: revision, limit: 500});
  if (url === '/api/v1/admin/question-library' && options.method === 'POST') {
    questions.push({...questions[0], id: 'new-synthetic-question', text: body.text, date_mode: body.date_mode, source: 'manual', occurrences: 0});
    return response({inserted: 1});
  }
  throw new Error('Unexpected synthetic request: ' + url);
};
const {ConfigAdmin} = load(resolve(web, 'src/ConfigAdmin.tsx'));
const host = document.getElementById('root'); const root = createRoot(host); let dirty = false;
async function flush() {await act(async () => {await new Promise(resolve => setTimeout(resolve, 0));});}
async function click(element) {
  assert.ok(element, 'Missing clickable element'); assert.equal(element.matches(':disabled'), false, 'Element must be enabled');
  await act(async () => element.dispatchEvent(new MouseEvent('click', {bubbles: true}))); await flush();
}
const button = (text, scope = host) => [...scope.querySelectorAll('button')].find(element => element.textContent === text);
const tab = id => document.getElementById('config-tab-' + id);
async function input(element, value) {
  assert.ok(element, 'Missing input');
  const proto = element.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : element.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(element, value);
    element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true}));
  }); await flush();
}
try {
  await act(async () => root.render(React.createElement(ConfigAdmin, {initialToken: 'synthetic-token', onDirtyChange(value) {dirty = value;}})));
  await flush();
  assert.equal(tab('basic').getAttribute('aria-selected'), 'true');
  assert.equal(host.querySelector('#config-panel-basic').hidden, false);
  assert.equal(host.querySelector('#config-panel-page').hidden, true);
  assert.equal(host.querySelectorAll('input[type=password]').length, 0);
  assert.equal(requests.filter(request => request.method !== 'GET').length, 0, 'Opening workbench must only read');
  const name = host.querySelector('#config-panel-basic label input');
  await input(name, '合成修改名称'); assert.equal(dirty, true);
  const confirmationsBeforeTabs = confirms;
  await click(tab('page')); await click(tab('basic'));
  assert.equal(name.value, '合成修改名称'); assert.equal(confirms, confirmationsBeforeTabs, 'Switching tabs must not discard or confirm');
  const leave = new Event('beforeunload', {cancelable: true}); window.dispatchEvent(leave); assert.equal(leave.defaultPrevented, true);
  await click(button('暂存更改')); assert.equal(dirty, false);
  assert.equal(config.application.name, '合成修改名称'); assert.equal(live.application.name, '课表助手');
  await click(tab('ai'));
  await input(host.querySelector('[aria-label="AI 设计需求"]'), '合成设计需求，不调用真实模型');
  await click(tab('basic')); await click(tab('ai'));
  assert.equal(host.querySelector('[aria-label="AI 设计需求"]').value, '合成设计需求，不调用真实模型');
  assert.equal(button('试跑发布').disabled, true, 'Unaccepted AI editing blocks competing writes');
  await click(button('清空当前需求与提案')); assert.equal(dirty, false);
  await click(tab('questions')); await flush();
  assert.ok(host.querySelector('.question-table tbody tr'));
  await click(button('新增问法'));
  assert.ok(host.querySelector('dialog[open]')); assert.equal(dirty, true);
  await input(host.querySelector('[aria-label="问法内容"]'), '合成新增的明天课表');
  await click(button('新增待审核问法'));
  assert.equal(host.querySelector('dialog[open]'), null); assert.equal(dirty, false);
  assert.equal(config.rules.length, 0, 'Adding a pending question must not alter rules');
  assert.equal(host.querySelectorAll('.question-table tbody tr').length, 2);
  await click(tab('page')); await click(tab('questions'));
  assert.equal(host.querySelectorAll('.question-table tbody tr').length, 2);
  await click(button('试跑发布'));
  assert.equal(button('确认发布').disabled, true, 'Publishing requires a successful review');
  regressionPasses = false; await click(button('校验并试跑草稿'));
  assert.equal(button('确认发布').disabled, true, 'Regression failures block publishing');
  regressionPasses = true; await click(button('校验并试跑草稿')); await click(button('确认发布'));
  assert.ok(button('使用相同请求号重试'));
  await click(button('返回配置'));
  await click(button('使用相同请求号重试'));
  const publications = requests.filter(request => request.url.endsWith('/publish'));
  assert.equal(publications.length, 2); assert.deepEqual(publications[0].body, publications[1].body);
  assert.equal(dirty, false); assert.equal(releaseId, 'synthetic-release');
  assert.equal(requests.some(request => request.url.includes('/generate')), false, 'No real or synthetic model invocation is needed');
  const {Workspace} = load(resolve(web, 'src/main.tsx'));
  const {applicationEditor} = load(resolve(web, 'src/applicationEditors.ts'));
  for (const typeId of ['campus.course-query', 'platform.form', 'platform.leave-approval']) {
    assert.equal(typeof applicationEditor(typeId, '1.0.0'), 'function');
    for (const version of ['latest', '1', '2.0.0']) assert.equal(applicationEditor(typeId, version), undefined, 'Unknown versions must never reuse the old editor');
  }
  assert.equal(applicationEditor('campus.course-query', '1.1.0'), undefined);
  assert.equal(typeof applicationEditor('platform.form', '1.1.0'), 'function');
  assert.equal(typeof applicationEditor('platform.leave-approval', '1.1.0'), 'function');
  const originalFetch = globalThis.fetch;
  const legacyId = '00000000-0000-4000-8000-000000000001';
  const createdId = '11111111-1111-4111-8111-111111111111';
  const applicationItems = [{application_id: legacyId, name: config.application.name, type_id: 'campus.course-query', type_version: '1.0.0', legacy: true, current_release_id: releaseId, lifecycle: {status: 'disabled', revision: 1}}];
  let createdConfig = null; let createdLive = null; let createdRevision = 0; let createdRelease = null;
  let loseCreateResponse = true; let rejectFirstRefresh = true; const createCommands = [];
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    if (url === '/api/v1/admin/application-types') return response({items: [{type_id: 'campus.course-query', type_version: '1.0.0', title: '课程查询', installed: true}], templates: [
      {id: 'course-standard', type_id: 'campus.course-query', name: '标准课程查询', description: '标准合成模板'},
      {id: 'course-compact', type_id: 'campus.course-query', name: '简洁课程查询', description: '简洁合成模板'},
    ]});
    if (url === '/api/v1/admin/applications') {
      requests.push({url, method: options.method, body});
      if (options.method === 'POST') {
        createCommands.push(clone(body));
        if (!createdConfig) {
          createdConfig = clone(config); createdConfig.application.name = body.name;
          createdConfig.columns.find(column => column.field === 'teacher').visible = body.template_id !== 'course-compact';
          applicationItems.push({application_id: createdId, name: body.name, type_id: body.type_id, type_version: body.type_version, legacy: false, current_release_id: null});
        }
        if (loseCreateResponse) {loseCreateResponse = false; throw new Error('Synthetic lost creation response');}
        return response({application_id: createdId, replayed: createCommands.length > 1});
      }
      if (createCommands.length >= 2 && rejectFirstRefresh) {
        rejectFirstRefresh = false;
        return response({error: {message: 'Synthetic rejected list refresh after successful creation'}}, 403);
      }
      return response({items: clone(applicationItems)});
    }
    if (url.startsWith(`/api/v1/admin/applications/${createdId}/configuration`)) {
      requests.push({url, method: options.method, body});
      const suffix = url.slice(`/api/v1/admin/applications/${createdId}/configuration`.length);
      if (suffix === '') return response({revision: createdRevision, current_release_id: createdRelease, content: createdConfig});
      if (suffix === '/current') return response({release_id: createdRelease, content: createdLive});
      if (suffix === '/releases' || suffix === '/audit') return response([]);
      if (suffix === '/draft') {
        assert.equal(body.expected_revision, createdRevision); createdConfig = clone(body.content); createdRevision++;
        applicationItems[1].name = createdConfig.application.name;
        return response({revision: createdRevision, current_release_id: createdRelease, content: createdConfig});
      }
      if (suffix === '/preview') return response({routing: {status: 'resolved', message: '新应用合成试跑通过'}, review: {changes: ['独立应用配置'], can_publish: true, violations: [], regressions: []}});
      if (suffix === '/publish') {
        createdLive = clone(createdConfig); createdRelease = 'created-synthetic-release'; createdRevision++;
        applicationItems[1].current_release_id = createdRelease;
        return response({release_id: createdRelease});
      }
    }
    if (url === '/api/v1/applications') return response({applications: [
      {id: 'schedule', icon: '▦', name: config.application.name, category: '学习服务', desc: '原应用', ready: true},
      ...(createdRelease ? [{id: createdId, icon: '▦', name: createdLive.application.name, category: '学习服务', desc: '新应用', ready: true}] : []),
    ]});
    if (url === `/api/v1/admin/applications/${createdId}/questions`) return response({items: [], draft_revision: createdRevision, limit: 500});
    if (url === '/api/v1/assistant/resolve') {
      requests.push({url, method: options.method, body});
      const result = {status: 'query', message: '已确定应用', application_id: null, application_name: null,
        query_text: body.text === '明天呢' ? '明天课表' : body.text, candidates: [], features: [], feature_actions: [], target: null, tab: 'basic'};
      const choices = [{id: 'schedule', name: config.application.name, category: '学习服务', description: '原应用'},
        {id: createdId, name: createdLive.application.name, category: '学习服务', description: '新应用'}];
      if (!body.selected_application_id && body.text === '有哪些应用') return response({...result, status: 'reset', message: '请选择应用', candidates: choices, query_text: null});
      if (!body.selected_application_id && body.text === '搜索应用独立') return response({...result, status: 'reset', message: '找到以下应用，请选择后使用。', candidates: [choices[1]], query_text: null});
      const named = choices.find(item => body.text.includes(item.name));
      const selected = named?.id ?? body.selected_application_id ?? body.current_application_id;
      if (!selected) return response({...result, status: 'clarification', message: '请先确定使用哪个应用，再匹配应用功能。', candidates: choices, query_text: null});
      result.application_id = selected; result.application_name = choices.find(item => item.id === selected).name;
      if (body.text.includes('问法')) return response({...result, status: 'navigation', message: '已定位新应用的问法配置', target: 'admin', tab: 'questions', query_text: null});
      if (body.text.startsWith('打开') || ['有哪些应用', '搜索应用独立', '这个应用有哪些功能'].includes(body.text)) return response({...result, status: 'selected', message: '已锁定“' + result.application_name + '”', query_text: null, features: ['查询本人课表', '按日期查询'], feature_actions: [
        {id: 'query-today', label: '查询今天课表', text: '今天课表'}, {id: 'configure-questions', label: '问法与规则', text: '管理问法与规则'},
      ]});
      return response(result);
    }
    if (url === '/api/v1/course-schedule/query') {
      requests.push({url, method: options.method, body});
      return response({data: null, ui_hint: {release_id: releaseId, columns: live.columns},
        routing: {status: 'resolved', message: '原应用查询成功', suggestions: []}, ai_used: false, ai_duration_ms: 0, trace_id: 'synthetic-old-query'});
    }
    if (url === `/api/v1/applications/${createdId}/query`) {
      requests.push({url, method: options.method, body});
      return response({data: {source: 'mock', identity: 'synthetic', start_date: '2026-10-20', end_date: '2026-10-20', courses: []},
        ui_hint: {release_id: createdRelease, columns: createdLive.columns}, routing: {status: 'resolved', message: '新应用查询成功', suggestions: []}, ai_used: false, ai_duration_ms: 0, trace_id: 'synthetic-query'});
    }
    return originalFetch(url, options);
  };
  window.history.replaceState(null, '', '/#workbench');
  const identity = {subject: 'synthetic-admin', tenant_id: 'local-demo', roles: ['config_admin'], mode: 'demo'};
  await act(async () => root.render(React.createElement(Workspace, {session: {token: 'synthetic-token', name: '测试管理员', identity}, onLogout() {}})));
  await flush();
  assert.ok(host.querySelector('.application-workbench'), 'Direct workbench URL must open the redesigned application manager');
  assert.ok([...host.querySelectorAll('nav[aria-label="主导航"] button')].some(element => element.textContent.includes('应用工作台')), 'Administrator needs a discoverable direct entry');
  assert.equal(tab('basic').getAttribute('aria-selected'), 'true');
  assert.equal(host.querySelectorAll('aside.application-list').length, 1, 'The embedded editor must not render a second application list');
  assert.ok(host.querySelector('aside.application-list').textContent.includes('已停用'), 'List must distinguish disabled from merely published');
  applicationItems[0].lifecycle = {status: 'active', revision: 2};
  await click(button('＋ 新建应用'));
  assert.ok(host.querySelector('dialog[open]'));
  await click(host.querySelector('input[value="course-compact"]'));
  await input(host.querySelector('[aria-label="新应用名称"]'), '可操作的新课程应用');
  await click(button('创建并开始配置'));
  assert.ok(button('确认上次创建结果'), 'Lost create response must retain the original command');
  await click(button('确认上次创建结果'));
  assert.ok(button('确认上次创建结果'), 'A rejected refresh after a successful create must retain its key');
  await click(button('确认上次创建结果'));
  assert.deepEqual(createCommands[0], createCommands[1], 'Create retry must be identical');
  assert.deepEqual(createCommands[1], createCommands[2], 'List-refresh failures must never create a second application');
  assert.equal(applicationItems.length, 2);
  assert.equal(host.querySelector('dialog[open]'), null);
  assert.equal(host.querySelector('#config-panel-basic input').value, '可操作的新课程应用');
  assert.equal(host.querySelectorAll('.multi-app-layout>.application-list .application-list-item').length, 2);
  assert.equal(host.querySelectorAll('[role=tab]').length, 4, 'New applications must not show the legacy-only AI designer');
  await input(host.querySelector('#config-panel-basic input'), '尚未暂存的应用名称');
  assert.ok(button('AI / 复制 / 文档创建').disabled, 'Alternative creation cannot discard unsaved application edits');
  window.confirm = () => false;
  await click([...host.querySelectorAll('.multi-app-layout>.application-list button')].find(element => element.textContent.includes('合成修改名称')));
  assert.equal(host.querySelector('#config-panel-basic input').value, '尚未暂存的应用名称', 'Canceling a switch preserves unsaved edits');
  window.confirm = () => true;
  await click([...host.querySelectorAll('.multi-app-layout>.application-list button')].find(element => element.textContent.includes('合成修改名称')));
  assert.equal(host.querySelector('#config-panel-basic input').value, '合成修改名称');
  await click([...host.querySelectorAll('.multi-app-layout>.application-list button')].find(element => element.textContent.includes('可操作的新课程应用')));
  assert.equal(host.querySelector('#config-panel-basic input').value, '可操作的新课程应用', 'Discarded edits cannot overwrite another application');
  await input(host.querySelector('#config-panel-basic input'), '新应用独立名称');
  await click(button('暂存更改'));
  assert.equal(createdConfig.application.name, '新应用独立名称');
  assert.equal(config.application.name, '合成修改名称', 'Editing the new application must not change the original');
  const legacyButton = [...host.querySelectorAll('.multi-app-layout>.application-list button')].find(element => element.textContent.includes('合成修改名称'));
  await click(legacyButton);
  assert.equal(host.querySelector('#config-panel-basic input').value, '合成修改名称');
  await click([...host.querySelectorAll('.multi-app-layout>.application-list button')].find(element => element.textContent.includes('新应用独立名称')));
  assert.equal(host.querySelector('#config-panel-basic input').value, '新应用独立名称');
  await click(button('试跑发布')); assert.equal(button('确认发布').disabled, true);
  await click(button('校验并试跑草稿')); await click(button('确认发布'));
  assert.equal(createdRelease, 'created-synthetic-release');
  await click(button('使用应用 ↗'));
  assert.ok(host.querySelector('.composer'));
  assert.ok(host.textContent.includes('新应用独立名称'));
  await input(host.querySelector('#question'), '今天课表');
  await act(async () => host.querySelector('.composer').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})));
  await flush();
  assert.ok(host.textContent.includes('新应用查询成功'));
  assert.ok(requests.some(request => request.url === `/api/v1/applications/${createdId}/query`));
  const submit = async text => {
    await input(host.querySelector('#question'), text);
    await act(async () => host.querySelector('.composer').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})));
    await flush();
  };
  await click([...host.querySelectorAll('nav[aria-label="主导航"] button')].find(element => element.textContent.includes('校园助手')));
  assert.ok(host.querySelector('.assistant-context').textContent.includes('自动匹配应用'));
  const businessBeforeAmbiguity = requests.filter(request => request.url.endsWith('/query')).length;
  await submit('今天课表');
  assert.ok(host.querySelector('.assistant-application-choices'));
  assert.equal(requests.filter(request => request.url.endsWith('/query')).length, businessBeforeAmbiguity, 'Ambiguous app matching must not run a business query');
  await click(host.querySelector('.assistant-application-choices [data-application-id="schedule"]'));
  assert.ok(host.textContent.includes('原应用查询成功'));
  await submit('打开新应用独立名称');
  assert.ok(host.querySelector('.assistant-context').textContent.includes('新应用独立名称'));
  const newAppFeature = [...host.querySelectorAll('[data-feature-id="query-today"]')].at(-1);
  assert.ok(newAppFeature, 'Selecting an application provides clickable feature actions');
  await submit('打开合成修改名称');
  assert.ok(host.querySelector('.assistant-context').textContent.includes('合成修改名称'));
  await click(newAppFeature);
  assert.ok(host.querySelector('.assistant-context').textContent.includes('新应用独立名称'), 'An action from an older message stays bound to that message application');
  const businessAfterSwitch = requests.filter(request => request.url.endsWith('/query')).length;
  await submit('明天呢');
  assert.equal(requests.filter(request => request.url.endsWith('/query')).length, businessAfterSwitch + 1);
  assert.equal(requests.filter(request => request.url === `/api/v1/applications/${createdId}/query`).at(-1).body.text, '明天课表');
  assert.ok([...host.querySelectorAll('.answer-label')].some(element => element.textContent.includes('合成修改名称')), 'Past turns retain their actual application name after a switch');
  const resolverCount = requests.filter(request => request.url === '/api/v1/assistant/resolve').length;
  await input(host.querySelector('#question'), '今天课表');
  await act(async () => {
    host.querySelector('.composer').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
    host.querySelector('.composer').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
  });
  await flush();
  assert.equal(requests.filter(request => request.url === '/api/v1/assistant/resolve').length, resolverCount + 1, 'Rapid repeated submit cannot duplicate routing/model work');
  await submit('搜索应用独立');
  const searchChoices = [...host.querySelectorAll('.assistant-application-choices')].at(-1);
  assert.equal(searchChoices.querySelectorAll('button').length, 1);
  await click(searchChoices.querySelector('button'));
  await click(button('查看功能', host.querySelector('.assistant-context')));
  assert.ok([...host.querySelectorAll('.assistant-feature-actions')].at(-1));
  await submit('有哪些应用');
  const latestChoices = [...host.querySelectorAll('.assistant-application-choices')].at(-1);
  await click(latestChoices.querySelector(`[data-application-id="${createdId}"]`));
  assert.ok(host.querySelector('.assistant-context').textContent.includes('新应用独立名称'));
  await click([...host.querySelectorAll('[data-feature-id="configure-questions"]')].at(-1));
  await click(button('进入应用配置 ↗'));
  assert.equal(tab('questions').getAttribute('aria-selected'), 'true');
  assert.ok(host.querySelector('.application-detail-heading h2').textContent.includes('新应用独立名称'));
  assert.equal(host.querySelectorAll('aside.application-list').length, 1);
  await click([...host.querySelectorAll('nav[aria-label="主导航"] button')].find(element => element.textContent.includes('校园助手')));
  const previousFetch = globalThis.fetch;
  let requirements = []; let saveKeys = []; let loseResponse = true;
  const savedKeys = new Map(); let prepared; let creationCount = 0; let loseCreationResponse = true;
  let historicalGeneration;
  const prepareRequests = [];
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    if (url === '/api/v1/assistant/resolve' && ['我要请假', '创建这个应用'].includes(body?.text)) {
      return response({status: 'clarification', message: '请假能力待接入，可以保存建设需求', construction_subject: '请假', candidates: [], features: [], target: null, application_id: null});
    }
    if (url === '/api/v1/assistant/requirements' && options.method === 'GET') return response({items: clone(requirements), total: requirements.length});
    if (url === '/api/v1/assistant/requirements' && options.method === 'POST') {
      saveKeys.push(body.idempotency_key);
      if (!savedKeys.has(body.idempotency_key)) {
        const item = {id: requirements.length ? 'synthetic-course-requirement' : 'synthetic-requirement', title: body.title, description: body.description, business_kind: body.business_kind,
          status: 'pending_capability', revision: 1, allowed_actions: body.business_kind === 'course_query' ? ['view', 'edit', 'cancel', 'prepare_creation'] : ['view', 'edit', 'cancel']};
        requirements.push(item); savedKeys.set(body.idempotency_key, item);
      }
      if (loseResponse) {loseResponse = false; throw new TypeError('Synthetic response loss after save');}
      return response({item: clone(savedKeys.get(body.idempotency_key)), replayed: true});
    }
    if (url === '/api/v1/assistant/requirements/synthetic-requirement/cancel') {
      requirements[0] = {...requirements[0], status: 'cancelled', revision: 2}; return response({item: clone(requirements[0])});
    }
    if (url === '/api/v1/assistant/requirements/synthetic-course-requirement/prepare') {
      prepareRequests.push(clone(body));
      prepared = {command_id: 'synthetic-create-command', command_key: body.idempotency_key,
        expires_at: new Date(Date.now() + (prepareRequests.length === 1 ? -1000 : 900000)).toISOString(), summary: {requirement_title: requirements[1].title,
          type_id: 'campus.course-query', type_version: '1.0.0', template_id: body.template_id, visible_roles: ['student', 'config_admin'], payload: {...body, task_id: 'synthetic-course-requirement'}}, result: null};
      if (body.source_application_id) prepared.summary.source = {application_id: body.source_application_id, revision: 4, content: clone(config)};
      if (body.generation_key) {assert.equal(body.generation_key, 'synthetic-historical-generation'); prepared.summary.generated = {generation_key: body.generation_key, content: clone(config)};}
      return response(prepared);
    }
    if (url === '/api/v1/assistant/requirements/confirmations/synthetic-create-command/execute') {
      creationCount++; requirements[1] = {...requirements[1], revision: 2, status: 'converted', target_application_id: createdId,
        allowed_actions: ['view', 'cancel', 'configure'], progress: {draft_revision: 0, release_id: null, unpublished_changes: true}};
      prepared.result = {item: clone(requirements[1]), application_id: createdId};
      if (loseCreationResponse) {loseCreationResponse = false; throw new TypeError('Synthetic response loss after atomic create');}
      return response(prepared.result);
    }
    if (url === '/api/v1/assistant/requirements/commands/' + prepared?.command_key) return response({found: true, result: prepared.result, confirmation: prepared});
    if (url === '/api/v1/assistant/requirements/commands/synthetic-historical-generation') return response({found: true, status: 'succeeded', result: historicalGeneration});
    if (url === '/api/v1/assistant/requirements/command-history') {
      historicalGeneration ??= {item: clone(requirements[1]), generation_status: 'proposed', summary: 'Historical synthetic proposal', content: clone(config), ai_duration_ms: 1200};
      return response({items: [...(prepared?.result ? [{id: 'history-create', action: 'create', status: 'succeeded', result: prepared.result}] : []),
        {id: 'history-generate', command_key: 'synthetic-historical-generation', action: 'generate', status: 'succeeded', result: historicalGeneration}]});
    }
    if (url === '/api/v1/assistant/requirements/synthetic-course-requirement') {
      if (options.method === 'PUT') requirements[1] = {...requirements[1], ...body, revision: requirements[1].revision + 1};
      return response({item: clone(requirements[1]), audit: []});
    }
    return previousFetch(url, options);
  };
  await click([...host.querySelectorAll('nav[aria-label="主导航"] button')].find(element => element.textContent.includes('应用工作台')));
  const commandsBeforeGuide = createCommands.length;
  await click(button('AI / 复制 / 文档创建'));
  const guide = host.querySelector('[aria-label="我的建设需求"]');
  assert.ok(guide, 'Alternative workbench creation opens the real requirements workflow');
  assert.ok(guide.textContent.includes('先新建并保存建设需求'));
  assert.equal(createCommands.length, commandsBeforeGuide, 'Navigating to creation must not create an application');
  assert.equal(saveKeys.length, 0, 'Opening the guide does not silently save a requirement');
  await click(button('收起', guide));
  await submit('我要请假');
  await submit('创建这个应用');
  await click([...host.querySelectorAll('button')].filter(element => element.textContent === '完善并保存建设需求').at(-1));
  let panel = host.querySelector('[aria-label="我的建设需求"]');
  assert.ok(panel);
  await click(button('确认保存建设需求', panel));
  assert.ok(panel.textContent.includes('结果待核对'));
  assert.ok(panel.querySelector('input').matches(':disabled'), 'Unknown result locks original payload including inherited fieldset lock');
  await click(button('使用原请求重试保存', panel));
  assert.equal(saveKeys[0], saveKeys[1], 'Unknown result retries the same durable command');
  assert.equal(requirements.length, 1);
  assert.ok(panel.textContent.includes('建设需求已保存'));
  await click(button('收起', panel));
  await click(button('我的建设需求', host.querySelector('.assistant-context')));
  panel = host.querySelector('[aria-label="我的建设需求"]');
  assert.ok(panel.textContent.includes('希望建设请假应用'));
  await click(button('取消需求', panel));
  assert.ok(panel.textContent.includes('已取消'));
  await click(button('新建需求', panel));
  await input(panel.querySelector('form input'), '课程建设需求');
  await input(panel.querySelector('form textarea'), '按日期查询课程');
  await input(panel.querySelector('form select'), 'course_query');
  await click(button('确认保存建设需求', panel));
  await click(button('查看 / 编辑需求', panel));
  await input(panel.querySelector('form textarea'), '按日期查询课程并支持本周查询');
  await click(button('保存需求修改', panel));
  assert.equal(requirements[1].revision, 2);
  await click(button('核对最近操作', panel));
  assert.ok(panel.textContent.includes('AI 生成设计 · 已成功'));
  await click(button('继续核对此 AI 请求', panel));
  assert.equal(panel.querySelector('.construction-options select').value, 'ai');
  assert.equal(button('生成创建确认', panel).disabled, true);
  await click(button('核对原 AI 请求', panel));
  await click(panel.querySelector('.construction-options input[type="checkbox"]'));
  assert.equal(window.sessionStorage.getItem('campus-construction-ai:local-demo:synthetic-admin:synthetic-course-requirement:2'), 'synthetic-historical-generation');
  await click(button('继续核对此 AI 请求', panel));
  assert.equal(panel.querySelector('.construction-options input[type="checkbox"]'), null, 'Reopening the same history starts a fresh review');
  assert.equal(button('生成创建确认', panel).disabled, true);
  await click(button('核对原 AI 请求', panel));
  await click(panel.querySelector('.construction-options input[type="checkbox"]'));
  await click(button('生成创建确认', panel));
  assert.ok(panel.querySelector('[aria-label="创建确认摘要"]').textContent.includes('已校验 AI 提案'));
  assert.equal(button('确认创建应用草稿', panel).disabled, true, 'Expired summary cannot be executed');
  assert.ok(panel.textContent.includes('创建确认已过期'));
  await click(button('重新生成创建确认', panel));
  assert.equal(button('确认创建应用草稿', panel).disabled, false);
  assert.equal(prepareRequests.length, 2);
  assert.notEqual(prepareRequests[0].idempotency_key, prepareRequests[1].idempotency_key);
  assert.equal(prepareRequests[1].generation_key, prepareRequests[0].generation_key, 'Renewal reuses the original AI proposal');
  assert.equal(prepareRequests[1].name, prepareRequests[0].name);
  requirements[1].revision++;
  await click(button('重新生成创建确认', panel));
  assert.ok(panel.textContent.includes('原需求已变化或不再允许创建'));
  assert.equal(prepareRequests.length, 2, 'Changed requirement must not prepare another confirmation');
  requirements[1].revision--;
  assert.equal(creationCount, 0, 'Recovering a historical proposal never creates or publishes automatically');
  await click(button('暂不创建', panel));
  await click(button('选择方式创建', panel));
  await input(panel.querySelector('.construction-options select'), 'copy');
  const copySource = panel.querySelectorAll('.construction-options select')[1];
  assert.ok(copySource.options.length > 1);
  await input(copySource, copySource.options[1].value);
  await click(button('生成创建确认', panel));
  assert.ok(panel.querySelector('[aria-label="创建确认摘要"]'));
  const confirmedDesign = panel.querySelector('[aria-label="创建确认摘要"] [aria-label="设计提案核对"]');
  assert.ok(confirmedDesign.textContent.includes('应用：课程建设需求'), 'Final review uses destination name rather than source application name');
  assert.ok(confirmedDesign.textContent.includes('课程展示顺序'));
  assert.ok(panel.textContent.includes('修订 4'));
  assert.equal(creationCount, 0, 'Reviewing a copied design does not create the app');
  await click(button('确认创建应用草稿', panel));
  assert.ok(panel.textContent.includes('结果待核对'));
  assert.equal(panel.querySelector('[aria-label="应用创建结果"]'), null, 'Unknown write outcome must not show a success card');
  await click(button('核对原操作', panel));
  assert.equal(creationCount, 1, 'Reconcile committed creation instead of creating another app');
  assert.ok(panel.textContent.includes('待配置发布'));
  const creationResult = panel.querySelector('[aria-label="应用创建结果"]');
  assert.ok(creationResult.textContent.includes(createdId));
  assert.ok(creationResult.textContent.includes('课程建设需求'));
  assert.ok(creationResult.textContent.includes('创建不会自动发布'));
  await click(button('核对最近操作', panel));
  assert.ok(panel.textContent.includes('创建应用 · 已成功'));
  await click(button('继续核对此 AI 请求', panel));
  assert.ok(panel.textContent.includes('原需求已变化或不再允许创建'));
  assert.equal(panel.querySelector('.construction-options'), null, 'Historical proposal cannot reopen creation for a converted requirement');
  assert.equal(creationCount, 1);
  await click(button('继续配置并发布', creationResult));
  assert.ok(window.location.hash.includes('application=' + createdId));
  assert.ok(host.querySelector('[aria-label="当前建设任务"]'));
  assert.ok(host.querySelector('.application-detail-heading h2').textContent.includes('新应用独立名称'));
  // Publication mechanics are covered above; verify the requirement reads actual publication progress and returns to this app.
  requirements[1].progress = {draft_revision: 1, release_id: createdRelease, unpublished_changes: false};
  await click(button('返回建设需求'));
  panel = host.querySelector('[aria-label="我的建设需求"]');
  assert.ok(panel.textContent.includes('当前草稿与发布一致'));
  await click(button('返回应用使用', panel));
  assert.ok(host.querySelector('.assistant-context').textContent.includes('新应用独立名称'));
  const {RequirementPanel} = load(resolve(web, 'src/RequirementPanel.tsx'));
  let releaseOldList; let savedDuringLoad; let listCalls = 0; let dirtyRequirement = false;
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/v1/assistant/requirements' && options.method === 'GET') {
      listCalls++;
      if (listCalls === 1) return new Promise(resolve => {releaseOldList = () => resolve(response({items: [], total: 0}));});
      return response({items: savedDuringLoad ? [savedDuringLoad] : [], total: savedDuringLoad ? 1 : 0});
    }
    if (url === '/api/v1/assistant/requirements' && options.method === 'POST') {
      const body = JSON.parse(options.body); savedDuringLoad = {...body, id: 'late-read-task', revision: 1, status: 'pending_capability'};
      return response({item: clone(savedDuringLoad)});
    }
    throw new Error('Unexpected recovery test HTTP');
  };
  await act(async () => root.render(React.createElement(RequirementPanel, {token: 'synthetic', scope: 'read-race', open: true, subject: '课程查询', onClose() {}, onExpired() {}, onDirtyChange(value) {dirtyRequirement = value;}})));
  await flush(); panel = host.querySelector('[aria-label="我的建设需求"]');
  assert.ok(panel.textContent.includes('正在加载建设需求'));
  assert.ok(!panel.textContent.includes('暂无已保存需求'));
  await input(panel.querySelector('form textarea'), 'Keep my input');
  assert.equal(dirtyRequirement, true);
  const oldConfirm = window.confirm; window.confirm = () => false;
  await click(button('收起', panel));
  assert.equal(panel.querySelector('form textarea').value, 'Keep my input');
  window.confirm = oldConfirm;
  await click(button('确认保存建设需求', panel));
  await act(async () => releaseOldList()); await flush();
  assert.ok(panel.textContent.includes('Keep my input'), 'Late pre-write read cannot overwrite a successful save');
  assert.equal(dirtyRequirement, false);
  console.log('Workbench DOM checks passed: existing lifecycle/routing; requirement edit, save/create response loss and reconciliation, confirmed creation, configuration navigation, publication progress and return to use.');
} finally {await act(async () => root.unmount()); dom.window.close();}
