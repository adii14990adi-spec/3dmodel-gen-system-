#!/usr/bin/env node
/* Official Khronos glTF Validator CLI wrapper.
 * Usage: node validate.js <path.glb|path.gltf>
 * Emits a single JSON object: {issues:{messages:[...]}, ...}
 * The wrapper does not fabricate results; validator failures are reported.
 */
const fs = require('fs');
const path = require('path');
const validator = require('gltf-validator');

const file = process.argv[2];
if (!file) {
  console.log(JSON.stringify({issues: {messages: [{code: 'USAGE', message: 'missing path', severity: 0}]}}));
  process.exit(2);
}
const ext = path.extname(file).toLowerCase();
const isBinary = ext === '.glb';
const raw = fs.readFileSync(file);
const bytes = new Uint8Array(raw);
const severity = ['ERROR', 'WARNING', 'INFO', 'HINT'];

const options = {
  uri: path.basename(file),
  maxIssues: 1000,
  ignoredIssues: [],
  severityOverrides: {},
  externalResourceFunction: (uri) => {
    const resolved = path.resolve(path.dirname(file), uri);
    if (!fs.existsSync(resolved)) {
      return Promise.reject(new Error(`External resource not found: ${uri}`));
    }
    return Promise.resolve(new Uint8Array(fs.readFileSync(resolved)));
  }
};

// A Uint8Array's toString() returns "123,34,..." - it ignores the encoding argument and
// turns every .gltf into INVALID_JSON. Decode the Buffer instead.
const task = isBinary ? validator.validateBytes(bytes, options)
                      : validator.validateString(raw.toString('utf8'), options);
task.then((report) => {
  const issues = report.issues || {};
  issues.messages = (issues.messages || []).map((m) => ({
    code: m.code,
    message: m.message,
    pointer: m.pointer || '',
    severity: severity[m.severity] || 'INFO'
  }));
  console.log(JSON.stringify({issues, info: report.info || {}}));
}).catch((err) => {
  console.log(JSON.stringify({issues: {messages: [
    {code: 'VALIDATOR_EXCEPTION', message: String(err && err.message || err), pointer: '', severity: 'ERROR'}
  ]}}));
  process.exit(1);
});
