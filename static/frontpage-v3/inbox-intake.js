/*
 * Clean-room, DOM-free Universal Inbox intake diagnostics.
 *
 * The roadmap records JDEworks/file-viewer commit
 * b99b6767a9b9caa7dca7924e66aa0af4cb822094 as product research. This module
 * independently implements the bounded Odysseus contract and contains no
 * copied source or vendor dependency. The researched project is MIT-licensed;
 * no third-party license artifact is bundled for this clean-room module.
 */
(function exposeInboxIntake(root, factory) {
  let capabilities = root && root.HarborInboxCapabilities;
  if (typeof module === 'object' && module.exports) {
    capabilities = require('./inbox-capabilities.js');
  }
  const api = factory(capabilities);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && typeof root === 'object') root.HarborInboxIntake = api;
})(typeof globalThis === 'object' ? globalThis : null, function buildInboxIntake(Capabilities) {
  'use strict';

  if (!Capabilities) throw new Error('HarborInboxCapabilities is required');

  const SCHEMA = 'odysseus.universal_inbox.advisory_intake.v1';
  const DEFAULT_TEXT_LIMIT = 256 * 1024;
  const MAX_TEXT_LIMIT = 1024 * 1024;
  const MAGIC_SAMPLE_LIMIT = 32;
  const EXECUTABLE_SUFFIXES = new Set([
    '.bat', '.cmd', '.com', '.dll', '.exe', '.jar', '.msi', '.ps1', '.scr',
    '.sh', '.vbs'
  ]);
  const BIDI_OR_ZERO_WIDTH_TEST = /[\u200b-\u200f\u202a-\u202e\u2066-\u2069]/u;
  const BIDI_OR_ZERO_WIDTH_REPLACE = /[\u200b-\u200f\u202a-\u202e\u2066-\u2069]/gu;

  function analyzeAdvisoryIntake({
    filename = '',
    bytes = new Uint8Array(),
    mimeType = '',
    userOverride = null
  } = {}) {
    const byteView = toByteView(bytes);
    const filenameResult = analyzeFilename(filename);
    const magic = detectMagic(byteView);
    const advisory = Capabilities.resolveAdvisoryCapability({
      filename: filenameResult.display_name,
      userOverride
    });
    const risks = new Set(filenameResult.risk_codes);

    if (magic.executable) risks.add('executable_signature');
    if (advisory.user_override_applied) {
      risks.add('user_override_requires_server_confirmation');
    }
    if (advisory.user_override_rejected) risks.add('user_override_rejected');
    if (hasFilenameMagicMismatch(advisory.capability, magic)) {
      risks.add('filename_magic_mismatch');
    }
    if (byteView.length === 0) risks.add('empty_input');
    if (
      advisory.capability.id === 'unknown' &&
      magic.kind === 'unknown' &&
      byteView.length > 0
    ) {
      risks.add('unknown_format');
    }

    const candidates = buildCandidates({
      advisory,
      magic,
      filenameRisk: filenameResult.risk_codes.length > 0
    });
    const confidence = candidates.length ? candidates[0].confidence : 0;

    return {
      schema: SCHEMA,
      authority: 'advisory',
      server_authoritative: true,
      can_enable_server_action: false,
      may_widen_server_policy: false,
      server_policy_action: 'defer_to_server',
      display_name: filenameResult.display_name,
      claimed_suffix: advisory.claimed_suffix,
      mime_hint: safeMimeHint(mimeType),
      capability: advisory.capability,
      user_override_applied: advisory.user_override_applied,
      user_override_rejected: advisory.user_override_rejected,
      magic,
      confidence,
      candidates,
      risk_codes: Array.from(risks).sort(),
      bytes_observed: Math.min(byteView.length, MAGIC_SAMPLE_LIMIT),
      full_input_length: byteView.length
    };
  }

  function decodeBoundedText(bytes, { maxBytes = DEFAULT_TEXT_LIMIT } = {}) {
    const view = toByteView(bytes);
    const boundedLimit = normalizeTextLimit(maxBytes);
    const truncated = view.length > boundedLimit;
    const selected = view.subarray(0, Math.min(view.length, boundedLimit));
    const bom = detectBom(selected);
    const payload = selected.subarray(bom.length);
    const warnings = [];
    let text = '';

    if (!selected.length) {
      return {
        text: '',
        encoding: 'empty',
        bom: 'none',
        bom_preserved: false,
        bytes_consumed: 0,
        total_bytes: 0,
        truncated: false,
        warning_codes: ['empty_input']
      };
    }

    try {
      text = new TextDecoder(bom.encoding, { fatal: true }).decode(payload);
    } catch {
      text = new TextDecoder(bom.encoding, { fatal: false }).decode(payload);
      warnings.push('invalid_encoding_replaced');
    }
    if (bom.kind !== 'none') text = `\uFEFF${text}`;
    if (truncated) warnings.push('text_truncated');

    return {
      text,
      encoding: bom.encoding,
      bom: bom.kind,
      bom_preserved: bom.kind !== 'none',
      bytes_consumed: selected.length,
      total_bytes: view.length,
      truncated,
      warning_codes: warnings.sort()
    };
  }

  function analyzeFilename(filename) {
    const raw = String(filename || '');
    const normalizedSeparators = raw.replace(/\\/g, '/');
    const parts = normalizedSeparators.split('/');
    let displayName = parts.pop() || '';
    const risks = new Set();

    if (parts.some(Boolean)) risks.add('path_components_removed');
    if (BIDI_OR_ZERO_WIDTH_TEST.test(displayName)) {
      risks.add('unicode_control_character');
      displayName = displayName.replace(BIDI_OR_ZERO_WIDTH_REPLACE, '');
    }
    displayName = Array.from(displayName)
      .filter(character => character >= ' ' && character !== '\u007f')
      .join('')
      .trim();
    if (displayName.length > 255) {
      displayName = displayName.slice(0, 255);
      risks.add('filename_truncated');
    }
    if (!displayName) {
      displayName = 'unnamed';
      risks.add('empty_filename');
    }

    const suffixes = displayName.toLowerCase().match(/\.[a-z0-9]+/g) || [];
    const finalSuffix = suffixes.at(-1) || '';
    if (suffixes.length > 1) risks.add('multiple_extensions');
    if (EXECUTABLE_SUFFIXES.has(finalSuffix)) {
      risks.add('executable_extension');
    }
    if (
      suffixes.length > 1 &&
      EXECUTABLE_SUFFIXES.has(finalSuffix) &&
      Capabilities.capabilityForSuffix(suffixes.at(-2)).id !== 'unknown'
    ) {
      risks.add('disguised_executable_extension');
    }

    return {
      display_name: displayName,
      suffix: finalSuffix,
      risk_codes: Array.from(risks).sort()
    };
  }

  function detectMagic(bytes) {
    const view = toByteView(bytes).subarray(0, MAGIC_SAMPLE_LIMIT);
    if (!view.length) return magicResult('empty', false, []);
    if (startsWithAscii(view, '%PDF-')) return magicResult('pdf', false, ['.pdf']);
    if (startsWithBytes(view, [0x50, 0x4b, 0x03, 0x04])) {
      return magicResult('zip_container', false, ['.docx', '.xlsx', '.pptx', '.epub']);
    }
    if (startsWithBytes(view, [0xd0, 0xcf, 0x11, 0xe0])) {
      return magicResult('ole_container', false, ['.xls']);
    }
    if (startsWithAscii(view, '{\\rtf')) return magicResult('rtf', false, ['.rtf']);
    if (startsWithBytes(view, [0x4d, 0x5a])) return magicResult('pe_executable', true, []);
    if (startsWithBytes(view, [0x7f, 0x45, 0x4c, 0x46])) {
      return magicResult('elf_executable', true, []);
    }
    if (startsWithAscii(view, '#!')) return magicResult('script', true, []);
    if (startsWithBytes(view, [0x89, 0x50, 0x4e, 0x47])) {
      return magicResult('png', false, ['.png']);
    }
    if (startsWithBytes(view, [0xff, 0xd8, 0xff])) {
      return magicResult('jpeg', false, ['.jpg', '.jpeg']);
    }
    return magicResult('unknown', false, []);
  }

  function buildCandidates({ advisory, magic, filenameRisk }) {
    const scores = new Map();
    if (advisory.capability.id !== 'unknown') {
      const suffix = advisory.capability.suffixes[0];
      scores.set(suffix, advisory.user_override_applied ? 0.55 : 0.72);
    }
    magic.suffix_hints.forEach(suffix => {
      scores.set(suffix, Math.max(scores.get(suffix) || 0, 0.92));
    });
    if (filenameRisk) {
      scores.forEach((score, suffix) => scores.set(suffix, score - 0.12));
    }
    if (magic.executable) {
      scores.forEach((_score, suffix) => scores.set(suffix, 0.05));
    }
    return Array.from(scores, ([suffix, score]) => ({
      suffix,
      confidence: clampConfidence(score)
    })).sort((left, right) => (
      right.confidence - left.confidence || left.suffix.localeCompare(right.suffix)
    ));
  }

  function hasFilenameMagicMismatch(capability, magic) {
    if (!capability || capability.id === 'unknown') return false;
    if (magic.kind === 'empty' || magic.kind === 'unknown') return false;
    if (magic.executable) return true;
    const expected = capability.expected_signature;
    if (expected === 'text') {
      return !['unknown'].includes(magic.kind);
    }
    if (expected === 'pdf') return magic.kind !== 'pdf';
    if (expected === 'zip_container') return magic.kind !== 'zip_container';
    if (expected === 'office_container') {
      return !['zip_container', 'ole_container'].includes(magic.kind);
    }
    if (expected === 'document_container') {
      return !['zip_container', 'ole_container', 'rtf'].includes(magic.kind);
    }
    return false;
  }

  function detectBom(view) {
    if (startsWithBytes(view, [0xef, 0xbb, 0xbf])) {
      return { kind: 'utf-8', encoding: 'utf-8', length: 3 };
    }
    if (startsWithBytes(view, [0xff, 0xfe])) {
      return { kind: 'utf-16le', encoding: 'utf-16le', length: 2 };
    }
    if (startsWithBytes(view, [0xfe, 0xff])) {
      return { kind: 'utf-16be', encoding: 'utf-16be', length: 2 };
    }
    return { kind: 'none', encoding: 'utf-8', length: 0 };
  }

  function normalizeTextLimit(value) {
    const numeric = Number(value);
    if (!Number.isInteger(numeric) || numeric < 1) return DEFAULT_TEXT_LIMIT;
    return Math.min(numeric, MAX_TEXT_LIMIT);
  }

  function toByteView(value) {
    if (value instanceof Uint8Array) return value;
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    if (ArrayBuffer.isView(value)) {
      return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    }
    if (Array.isArray(value)) return Uint8Array.from(value);
    throw new TypeError('bytes must be an ArrayBuffer or byte array');
  }

  function magicResult(kind, executable, suffixHints) {
    return {
      kind,
      executable,
      suffix_hints: suffixHints.slice(),
      advisory_only: true
    };
  }

  function startsWithAscii(view, text) {
    return startsWithBytes(view, Array.from(text, character => character.charCodeAt(0)));
  }

  function startsWithBytes(view, expected) {
    if (view.length < expected.length) return false;
    return expected.every((value, index) => view[index] === value);
  }

  function clampConfidence(value) {
    return Math.max(0, Math.min(1, Number(value) || 0));
  }

  function safeMimeHint(value) {
    const normalized = String(value || '').trim().toLowerCase();
    return /^[a-z0-9][a-z0-9!#$&^_.+-]*\/[a-z0-9][a-z0-9!#$&^_.+-]*$/.test(normalized)
      ? normalized
      : '';
  }

  return Object.freeze({
    DEFAULT_TEXT_LIMIT,
    MAGIC_SAMPLE_LIMIT,
    MAX_TEXT_LIMIT,
    SCHEMA,
    analyzeAdvisoryIntake,
    analyzeFilename,
    decodeBoundedText,
    detectMagic
  });
});
