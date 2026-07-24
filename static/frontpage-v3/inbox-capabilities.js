/*
 * Clean-room Universal Inbox advisory capability registry.
 *
 * The roadmap records JDEworks/file-viewer commit
 * b99b6767a9b9caa7dca7924e66aa0af4cb822094 as product research. No source
 * code or vendor asset from that MIT-licensed project is copied or adapted
 * here, so no third-party license artifact is bundled for this module.
 */
(function exposeInboxCapabilities(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && typeof root === 'object') root.HarborInboxCapabilities = api;
})(typeof globalThis === 'object' ? globalThis : null, function buildInboxCapabilities() {
  'use strict';

  const SCHEMA = 'odysseus.universal_inbox.advisory_capability.v1';

  const definitions = [
    {
      id: 'markdown',
      suffixes: ['.md', '.markdown'],
      family: 'text',
      tier: 'P0',
      textDecodable: true,
      signature: 'text'
    },
    {
      id: 'plain_text',
      suffixes: ['.txt'],
      family: 'text',
      tier: 'P0',
      textDecodable: true,
      signature: 'text'
    },
    {
      id: 'pdf',
      suffixes: ['.pdf'],
      family: 'document',
      tier: 'P0',
      textDecodable: false,
      signature: 'pdf'
    },
    {
      id: 'docx',
      suffixes: ['.docx'],
      family: 'document',
      tier: 'P0',
      textDecodable: false,
      signature: 'zip_container'
    },
    {
      id: 'web_source',
      suffixes: ['.html', '.htm', '.svg', '.xml'],
      family: 'text',
      tier: 'P1',
      textDecodable: true,
      signature: 'text'
    },
    {
      id: 'csv',
      suffixes: ['.csv'],
      family: 'text',
      tier: 'P1',
      textDecodable: true,
      signature: 'text'
    },
    {
      id: 'spreadsheet',
      suffixes: ['.xls', '.xlsx'],
      family: 'document',
      tier: 'P1',
      textDecodable: false,
      signature: 'office_container'
    },
    {
      id: 'extended_document',
      suffixes: ['.pptx', '.odt', '.ods', '.odp', '.rtf', '.epub'],
      family: 'document',
      tier: 'P2',
      textDecodable: false,
      signature: 'document_container'
    }
  ];

  const registry = Object.freeze(definitions.map(definition => Object.freeze({
    id: definition.id,
    suffixes: Object.freeze(definition.suffixes.slice()),
    family: definition.family,
    tier: definition.tier,
    text_decodable: definition.textDecodable,
    expected_signature: definition.signature,
    advisory_only: true,
    server_authoritative: true,
    can_enable_server_action: false
  })));

  const bySuffix = new Map();
  registry.forEach(capability => {
    capability.suffixes.forEach(suffix => bySuffix.set(suffix, capability));
  });

  function capabilityForFilename(filename) {
    const suffix = suffixFromFilename(filename);
    return capabilityForSuffix(suffix);
  }

  function capabilityForSuffix(value) {
    const suffix = normalizeSuffix(value);
    return bySuffix.get(suffix) || unknownCapability('unknown_suffix');
  }

  function resolveAdvisoryCapability({ filename = '', userOverride = null } = {}) {
    const claimed = capabilityForFilename(filename);
    const overrideSuffix = normalizeOverride(userOverride);
    const overridden = overrideSuffix ? capabilityForSuffix(overrideSuffix) : null;
    const overrideAccepted = Boolean(
      overridden && overridden.id !== 'unknown'
    );
    const capability = overrideAccepted ? overridden : claimed;

    return {
      schema: SCHEMA,
      authority: 'advisory',
      server_authoritative: true,
      can_enable_server_action: false,
      may_widen_server_policy: false,
      user_override_applied: overrideAccepted,
      user_override_rejected: Boolean(userOverride) && !overrideAccepted,
      claimed_suffix: suffixFromFilename(filename),
      advisory_suffix: capability.suffixes[0] || '',
      capability
    };
  }

  function listDocumentCapabilities() {
    return registry.slice();
  }

  function suffixFromFilename(filename) {
    const leaf = String(filename || '').replace(/\\/g, '/').split('/').pop();
    const match = String(leaf || '').toLowerCase().match(/(\.[a-z0-9]+)$/);
    return match ? match[1] : '';
  }

  function normalizeSuffix(value) {
    const normalized = String(value || '').trim().toLowerCase();
    if (!normalized) return '';
    return normalized.startsWith('.') ? normalized : `.${normalized}`;
  }

  function normalizeOverride(value) {
    if (value && typeof value === 'object') {
      return normalizeSuffix(value.suffix || value.format || '');
    }
    return normalizeSuffix(value);
  }

  function unknownCapability(reason) {
    return Object.freeze({
      id: 'unknown',
      suffixes: Object.freeze([]),
      family: 'unknown',
      tier: 'unsupported',
      text_decodable: false,
      expected_signature: 'unknown',
      reason,
      advisory_only: true,
      server_authoritative: true,
      can_enable_server_action: false
    });
  }

  return Object.freeze({
    SCHEMA,
    capabilityForFilename,
    capabilityForSuffix,
    listDocumentCapabilities,
    resolveAdvisoryCapability,
    suffixFromFilename
  });
});
