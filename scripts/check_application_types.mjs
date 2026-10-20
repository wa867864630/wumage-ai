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
