const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const Capabilities = require('../../static/frontpage-v3/inbox-capabilities.js');
const Intake = require('../../static/frontpage-v3/inbox-intake.js');


function bytes(text) {
  return new TextEncoder().encode(text);
}


test('registry stays small, document-focused and advisory-only', () => {
  const registry = Capabilities.listDocumentCapabilities();
  const suffixes = registry.flatMap(entry => entry.suffixes);

  assert.equal(registry.length, 8);
  for (const required of ['.md', '.txt', '.pdf', '.docx', '.csv', '.xlsx', '.rtf']) {
    assert.ok(suffixes.includes(required), required);
  }
  for (const excluded of ['.exe', '.png', '.mp3', '.zip']) {
    assert.ok(!suffixes.includes(excluded), excluded);
  }
  registry.forEach(entry => {
    assert.equal(entry.advisory_only, true);
    assert.equal(entry.server_authoritative, true);
    assert.equal(entry.can_enable_server_action, false);
  });
});


test('bounded UTF-8 decoding preserves BOM and reports truncation', () => {
  const input = Uint8Array.from([0xef, 0xbb, 0xbf, ...bytes('hello world')]);
  const result = Intake.decodeBoundedText(input, { maxBytes: 8 });

  assert.equal(result.encoding, 'utf-8');
  assert.equal(result.bom, 'utf-8');
  assert.equal(result.bom_preserved, true);
  assert.equal(result.text, '\uFEFFhello');
  assert.equal(result.bytes_consumed, 8);
  assert.equal(result.total_bytes, input.length);
  assert.equal(result.truncated, true);
  assert.deepEqual(result.warning_codes, ['text_truncated']);
});


test('UTF-16LE BOM decoding remains explicit and invalid UTF-8 fails soft', () => {
  const utf16 = Uint8Array.from([0xff, 0xfe, 0x48, 0x00, 0x69, 0x00]);
  const decoded = Intake.decodeBoundedText(utf16);
  const invalid = Intake.decodeBoundedText(Uint8Array.from([0xc3, 0x28]));

  assert.equal(decoded.encoding, 'utf-16le');
  assert.equal(decoded.text, '\uFEFFHi');
  assert.equal(decoded.bom_preserved, true);
  assert.equal(invalid.text, '�(');
  assert.deepEqual(invalid.warning_codes, ['invalid_encoding_replaced']);
});


test('matching PDF filename and magic yield a sorted high-confidence candidate', () => {
  const result = Intake.analyzeAdvisoryIntake({
    filename: 'invoice.pdf',
    bytes: bytes('%PDF-1.7 private bytes follow'),
    mimeType: 'application/pdf'
  });

  assert.equal(result.authority, 'advisory');
  assert.equal(result.server_authoritative, true);
  assert.equal(result.can_enable_server_action, false);
  assert.equal(result.may_widen_server_policy, false);
  assert.equal(result.capability.id, 'pdf');
  assert.equal(result.magic.kind, 'pdf');
  assert.equal(result.candidates[0].suffix, '.pdf');
  assert.equal(result.candidates[0].confidence, 0.92);
  assert.deepEqual(result.risk_codes, []);
  assert.ok(!JSON.stringify(result).includes('private bytes follow'));
});


test('filename and magic mismatch is visible without inventing authority', () => {
  const result = Intake.analyzeAdvisoryIntake({
    filename: 'claimed.pdf',
    bytes: Uint8Array.from([0x50, 0x4b, 0x03, 0x04, 0x00])
  });

  assert.equal(result.capability.id, 'pdf');
  assert.equal(result.magic.kind, 'zip_container');
  assert.ok(result.risk_codes.includes('filename_magic_mismatch'));
  assert.deepEqual(
    result.candidates.map(candidate => candidate.suffix),
    ['.docx', '.epub', '.pptx', '.xlsx', '.pdf']
  );
  assert.equal(result.server_policy_action, 'defer_to_server');
});


test('executable warnings survive a user override and suppress confidence', () => {
  const result = Intake.analyzeAdvisoryIntake({
    filename: 'invoice.pdf.exe',
    bytes: Uint8Array.from([0x4d, 0x5a, 0x90, 0x00]),
    userOverride: '.pdf'
  });

  assert.equal(result.user_override_applied, true);
  assert.equal(result.capability.id, 'pdf');
  assert.equal(result.magic.executable, true);
  assert.equal(result.can_enable_server_action, false);
  assert.equal(result.may_widen_server_policy, false);
  for (const risk of [
    'disguised_executable_extension',
    'executable_extension',
    'executable_signature',
    'filename_magic_mismatch',
    'multiple_extensions',
    'user_override_requires_server_confirmation'
  ]) {
    assert.ok(result.risk_codes.includes(risk), risk);
  }
  assert.equal(result.candidates[0].confidence, 0.05);
});


test('empty and unknown inputs use an explicit safe fallback', () => {
  const empty = Intake.analyzeAdvisoryIntake({});
  const unknown = Intake.analyzeAdvisoryIntake({
    filename: 'mystery.blob',
    bytes: Uint8Array.from([0x01, 0x02, 0x03])
  });

  assert.equal(empty.capability.id, 'unknown');
  assert.equal(empty.confidence, 0);
  assert.deepEqual(empty.risk_codes, ['empty_filename', 'empty_input']);
  assert.equal(unknown.capability.id, 'unknown');
  assert.equal(unknown.magic.kind, 'unknown');
  assert.deepEqual(unknown.risk_codes, ['unknown_format']);
});


test('unknown user override is rejected and cannot widen the server contract', () => {
  const result = Intake.analyzeAdvisoryIntake({
    filename: 'notes.md',
    bytes: bytes('# Notes'),
    userOverride: '.exe'
  });

  assert.equal(result.user_override_applied, false);
  assert.equal(result.user_override_rejected, true);
  assert.equal(result.capability.id, 'markdown');
  assert.equal(result.can_enable_server_action, false);
  assert.ok(result.risk_codes.includes('user_override_rejected'));
});


test('filename risks are normalized without returning path components', () => {
  const result = Intake.analyzeFilename('C:\\private\\safe\u202Efdp.txt.exe');

  assert.equal(result.display_name, 'safefdp.txt.exe');
  for (const risk of [
    'disguised_executable_extension',
    'executable_extension',
    'multiple_extensions',
    'path_components_removed',
    'unicode_control_character'
  ]) {
    assert.ok(result.risk_codes.includes(risk), risk);
  }
  assert.ok(!JSON.stringify(result).includes('C:\\private'));
});


test('pure helpers contain no DOM, shell, network or vendor coupling', () => {
  const root = path.resolve(__dirname, '..', '..');
  const sources = [
    fs.readFileSync(path.join(root, 'static/frontpage-v3/inbox-capabilities.js'), 'utf8'),
    fs.readFileSync(path.join(root, 'static/frontpage-v3/inbox-intake.js'), 'utf8')
  ].join('\n');

  for (const forbidden of [
    'document.querySelector',
    'child_process',
    'execSync',
    'fetch(',
    'XMLHttpRequest',
    'docs/vendor/'
  ]) {
    assert.ok(!sources.includes(forbidden), forbidden);
  }
  assert.match(sources, /Clean-room/);
  assert.match(sources, /contains no\s+\*\s+copied source/);
  assert.match(sources, /MIT-licensed/);
});
