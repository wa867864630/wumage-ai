// Dependency-free server-render smoke checks; no browser/DOM interaction is claimed.
import {readFileSync, existsSync} from 'node:fs';
import {resolve, dirname, extname} from 'node:path';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const web = resolve(root, 'apps/web');
const requireWeb = createRequire(resolve(web, 'package.json'));
const ts = requireWeb('typescript');
const React = requireWeb('react');
const {renderToString} = requireWeb('react-dom/server');
const cache = new Map();
function load(path) {
  if (cache.has(path)) return cache.get(path).exports;
  const module = {exports: {}}; cache.set(path, module);
  const source = ts.transpileModule(readFileSync(path, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
  }}).outputText;
  const localRequire = name => {
    if (name.endsWith('.css')) return {};
    if (name === 'react-dom/client') return {createRoot: () => ({render: () => {}})};
    if (!name.startsWith('.')) return requireWeb(name);
    const base = resolve(dirname(path), name);
    const target = extname(base) ? base : ['.tsx', '.ts'].map(extension => base + extension).find(existsSync);
    assert.ok(target, `Missing local module: ${name}`);
    return load(target);
  };
  new Function('require', 'module', 'exports', 'document', source)(localRequire, module, module.exports, {getElementById: () => ({})});
  return module.exports;
}
const {Login} = load(resolve(web, 'src/Login.tsx'));
const {Workspace} = load(resolve(web, 'src/main.tsx'));
const {CourseProposalPanel} = load(resolve(web, 'src/CourseProposalPanel.tsx'));
const {QuestionLibrary} = load(resolve(web, 'src/QuestionLibrary.tsx'));
const questionLibrary = renderToString(React.createElement(QuestionLibrary, {token: 'do-not-render-question-token',
  draftRevision: 0, disabled: false, onBusyChange() {}, onEditingChange() {}, async onDraftChanged() {}}));
assert.match(questionLibrary, /课表问法库/); assert.match(questionLibrary, /AI 批量生成待审核问法/);
assert.match(questionLibrary, /新增问法/); assert.match(questionLibrary, /状态与生效情况/); assert.doesNotMatch(questionLibrary, /do-not-render-question-token/);
const {ConfigAdmin} = load(resolve(web, 'src/ConfigAdmin.tsx'));
const workbench = renderToString(React.createElement(ConfigAdmin, {initialToken: 'do-not-render-management-token'}));
assert.match(workbench, /可配置应用/); assert.match(workbench, /应用列表/);
assert.match(workbench, /role="tab" aria-selected="true" aria-controls="config-panel-basic"/);
assert.equal((workbench.match(/role="tab"/g) ?? []).length, 5);
assert.doesNotMatch(workbench, /type="password"|do-not-render-management-token/);
const proposalPanel = renderToString(React.createElement(CourseProposalPanel, {token: 'do-not-render-proposal-token', revision: 0,
  resetVersion: 0, disabled: false, onBusyChange() {}, onPendingChange() {}, async onAccepted() {}}));
assert.match(proposalPanel, /载入当前基线与示例提案/); assert.match(proposalPanel, /校验并预览提案/);
assert.match(proposalPanel, /使用 AI 生成提案/); assert.doesNotMatch(proposalPanel, /do-not-render-proposal-token/);
const login = renderToString(React.createElement(Login, {onLogin() {}, notice: '会话已过期'}));
assert.match(login, /登录并进入工作台/); assert.match(login, /会话已过期/);
assert.doesNotMatch(login, /query-history/);
const session = {token: 'not-a-real-token', name: '<模拟学生>', identity: {subject: 'student', tenant_id: 'local-demo', roles: ['student'], student_id: 'student', mode: 'demo'}};
const student = renderToString(React.createElement(Workspace, {session, onLogout() {}}));
assert.match(student, /微服务广场/); assert.match(student, /自动匹配应用/); assert.match(student, /退出登录/);
assert.doesNotMatch(student, /配置管理/); assert.doesNotMatch(student, /not-a-real-token/);
assert.match(student, /&lt;模拟学生&gt;/);
const admin = renderToString(React.createElement(Workspace, {session: {...session, identity: {...session.identity, roles: ['config_admin']}}, initialPage: 'services', onLogout() {}}));
assert.match(admin, /应用管理/); assert.match(admin, /应用工作台/); assert.match(admin, /服务器部署/);
assert.match(admin, /AI 模型配置/);
const aiSettings = renderToString(React.createElement(Workspace, {session: {...session, identity: {...session.identity, roles: ['config_admin']}}, initialPage: 'ai-model', onLogout() {}}));
assert.match(aiSettings, /加载模型配置/); assert.match(aiSettings, /测试已保存的模型/);
assert.doesNotMatch(aiSettings, /not-a-real-token/);
const deployment = renderToString(React.createElement(Workspace, {session: {...session, identity: {...session.identity, roles: ['config_admin']}}, initialPage: 'deployment', onLogout() {}}));
assert.match(deployment, /部署审阅计划/); assert.match(deployment, /重新检查并生成计划/);
assert.match(deployment, /SHA256/); assert.doesNotMatch(deployment, /not-a-real-token/);
const studentServices = renderToString(React.createElement(Workspace, {session, initialPage: 'services', onLogout() {}}));
assert.doesNotMatch(studentServices, /应用管理|服务器部署|应用工作台|AI 模型配置/);
console.log('Web SSR smoke checks passed (login, workspace, role navigation, deployment planning, escaping, token non-disclosure).');

// Exercise the shared HTTP client with fake responses; no real credentials or network.
const {api, SessionExpired, JSONRequest} = load(resolve(web, 'src/api.ts'));
const originalFetch = globalThis.fetch;
try {
  globalThis.fetch = async () => new Response(JSON.stringify({error: {message: '日期无效', details: [{path: 'query/end_date', message: '日期倒置'}]}, trace_id: 'trace-fixture'}), {status: 422, headers: {'Content-Type': 'application/json'}});
  await assert.rejects(api('/fixture'), error => /日期无效/.test(error.message) && /日期倒置/.test(error.message) && /trace-fixture/.test(error.message));
  globalThis.fetch = async () => new Response('expired', {status: 401});
  await assert.rejects(api('/fixture'), error => error instanceof SessionExpired);
  globalThis.fetch = async () => {throw new TypeError('Failed to fetch');};
  await assert.rejects(api('/fixture'), /无法连接服务/);
  globalThis.fetch = async () => {throw new DOMException('timed out', 'TimeoutError');};
  await assert.rejects(api('/fixture'), /超时/);
  globalThis.fetch = async () => new Response('{', {headers: {'Content-Type': 'application/json'}});
  await assert.rejects(api('/fixture'), /不完整的响应/);
  let command;
  globalThis.fetch = async (_url, options) => {command = options; return new Response('{"revision":2}', {headers: {'Content-Type': 'application/json'}});};
  assert.deepEqual(await api('/fixture', 'fake', {expected_revision: 1}, undefined, 'PUT'), {revision: 2});
  assert.equal(command.method, 'PUT'); assert.equal(command.body, '{"expected_revision":1}');
  const duplicateKeys = '{"status":"proposed","status":"proposed"}';
  await api('/fixture', 'fake', new JSONRequest(duplicateKeys));
  assert.equal(command.body, duplicateKeys); assert.equal(command.method, 'POST');
} finally {globalThis.fetch = originalFetch;}
console.log('Web HTTP checks passed (validation details, trace, expiry, network, timeout, incomplete JSON, PUT).');
