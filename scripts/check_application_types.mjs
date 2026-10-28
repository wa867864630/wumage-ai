import {createRequire} from 'node:module';
import {readFileSync, readdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
// Optional external installation is supported without adding runtime dependencies.
const Ajv = require(process.env.APPLICATION_TYPES_AJV_PATH || 'ajv/dist/2020.js').default;
const root = new URL('../packages/contracts/application-types/v1/', import.meta.url);
const read = name => JSON.parse(readFileSync(new URL(name, root), 'utf8'));
const ajv = new Ajv({strict: true, allErrors: true});
for (const name of readdirSync(fileURLToPath(root)).filter(name => name.endsWith('.schema.json'))) {
  ajv.addSchema(read(name));
}
const prefix = 'urn:campus:application-types:v1:';
const validators = {
  manifest: ajv.getSchema(prefix + 'manifest'),
  course: ajv.getSchema(prefix + 'course-application'),
};
const examples = {manifest: read('examples/course-type.json'), course: read('examples/course-application.json')};
let count = 0;
for (const test of read('conformance-cases.json')) {
  const input = structuredClone(examples[test.target]);
  if (test.change) {
    const {path, value, remove} = test.change;
    const parent = path.slice(0, -1).reduce((node, key) => node[key], input);
    if (remove) delete parent[path.at(-1)];
    else parent[path.at(-1)] = value;
  }
  const validate = validators[test.target];
  assert.equal(validate(input), test.valid, `${test.name}: ${JSON.stringify(validate.errors)}`);
  count++;
}
console.log(`PASS: ${count} application-type structural cases (not runtime or authorization validation)`);
assert.ok(validators.manifest(read('examples/form-type.json')), JSON.stringify(validators.manifest.errors));
const formValidator = ajv.getSchema(prefix + 'form-config');
const formExample = read('examples/form-config.json');
assert.ok(formValidator(formExample), JSON.stringify(formValidator.errors));
const applicationDesign = JSON.parse(readFileSync(new URL('../../application-design/v1/application.schema.json', root), 'utf8'));
// The existing blueprint's conditional branches inherit array types from their parent.
// Disable only this optional Ajv lint; keep all actual JSON Schema assertions active.
const designAjv = new Ajv({strict: true, strictTypes: false, allErrors: true});
designAjv.addSchema(applicationDesign);
const designId = applicationDesign.$id;
for (const [name, value] of [['model', formExample.model], ['form', formExample.form]]) {
  const validate = designAjv.compile({$ref: designId + '#/$defs/' + name});
  assert.ok(validate(value), `Executable form subset must obey original ${name} design: ${JSON.stringify(validate.errors)}`);
}
const scripted = structuredClone(formExample); scripted.model.fields[0].script = 'not-allowed';
assert.equal(formValidator(scripted), false, 'Runtime form schema rejects executable/undeclared properties');
const nullConstraint = structuredClone(formExample); nullConstraint.model.fields[0].constraints.minimum = null;
assert.equal(formValidator(nullConstraint), false, 'Unset constraint is omitted, not JSON null; schema agrees with runtime');
console.log('PASS: form manifest/schema, original model/form blueprint compatibility and script rejection');
assert.ok(validators.manifest(read('examples/leave-type.json')), JSON.stringify(validators.manifest.errors));
const leave = read('examples/leave-config.json');
const leaveValidator = ajv.getSchema(prefix + 'leave-config');
assert.ok(leaveValidator(leave), JSON.stringify(leaveValidator.errors));
for (const [name, value] of [['model', leave.model], ['form', leave.form], ['workflow', leave.workflow]]) {
  const validate = designAjv.compile({$ref: designId + '#/$defs/' + name});
  assert.ok(validate(value), `Leave subset must obey original ${name} blueprint: ${JSON.stringify(validate.errors)}`);
}
const selfApproving = structuredClone(leave); selfApproving.workflow.nodes.find(node => node.type === 'approval').allow_self_approval = true;
assert.equal(leaveValidator(selfApproving), false, 'Self approval is not a configurable bypass');
console.log('PASS: leave manifest, executable workflow blueprint compatibility and self-approval rejection');
const constructionRequest = ajv.getSchema(prefix + 'construction-request');
assert.ok(constructionRequest({expected_revision: 1, text: '设计登记字段', idempotency_key: 'synthetic-ai-key-one'}), JSON.stringify(constructionRequest.errors));
assert.equal(constructionRequest({expected_revision: 1, text: '设计登记字段', idempotency_key: 'synthetic-ai-key-one', tenant_id: 'client-override'}), false);
const constructionProposal = ajv.getSchema(prefix + 'construction-proposal');
assert.ok(constructionProposal({status: 'proposed', binding: '0'.repeat(64), summary: '字段提案', questions: [], content: formExample, evidence: []}), JSON.stringify(constructionProposal.errors));
console.log('PASS: named construction request/proposal schemas and client authority override rejection; exact content semantics remain server-validated');
