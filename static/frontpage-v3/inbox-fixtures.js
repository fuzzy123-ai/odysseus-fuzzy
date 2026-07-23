/* Explicitly labeled preview fixtures; never evidence of a live Inbox read. */
(function exposeInboxFixtures(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && typeof root === 'object') root.HarborInboxFixtures = api;
})(typeof globalThis === 'object' ? globalThis : null, function buildInboxFixtures() {
  'use strict';

  const FIXTURE_SCHEMA = 'odysseus.universal_inbox.fixture_read_model.v1';
  const SCENARIOS = Object.freeze([
    'fixture',
    'stale',
    'empty',
    'unauthorized',
    'unavailable',
    'error'
  ]);

  const capability = {
    schema: 'odysseus.universal_inbox.workbench_capability.v1',
    source_suffix: '.pdf',
    server_family: 'document',
    mvp_tier: 'P0',
    owner_authorized: true,
    server_authoritative: true,
    raw_content_visible: false,
    absolute_path_visible: false,
    live_write_authorized: false,
    actions: []
  };

  const fixtureItem = {
    schema: 'odysseus.universal_inbox.item.v1',
    source_ref: `upload:${'f'.repeat(32)}.pdf`,
    source_kind: 'upload',
    display_name: 'Preview invoice.pdf',
    status: 'uploaded',
    metadata: {
      suffix: '.pdf',
      mime_type: 'application/pdf',
      family: 'document',
      category: 'document_extractable',
      size_bytes: 2048,
      uploaded_at: '2026-07-23T09:00:00+00:00',
      extractable_now: true,
      review_required: false,
      blocked: false,
      reason_codes: []
    },
    capability,
    absolute_path_visible: false,
    raw_content_visible: false,
    owner_identifier_visible: false,
    hash_visible: false
  };

  function buildFixtureScenario(scenario = 'fixture') {
    const normalized = String(scenario || 'fixture');
    if (!SCENARIOS.includes(normalized)) {
      throw new RangeError('unknown Inbox fixture scenario');
    }
    const hasItem = !['empty', 'unauthorized', 'unavailable', 'error'].includes(
      normalized
    );
    const list = buildList(hasItem ? [fixtureItem] : []);
    const snapshot = buildSnapshot(hasItem ? 1 : 0);
    return deepClone({
      schema: FIXTURE_SCHEMA,
      scenario: normalized,
      mode: normalized,
      origin: {
        state: 'fixture',
        source: 'labeled_preview_fixture',
        label: 'Preview fixture',
        counts_as_live_evidence: false
      },
      list,
      snapshot,
      error: ['unauthorized', 'unavailable', 'error'].includes(normalized)
        ? { code: normalized, status: 0 }
        : null
    });
  }

  function buildList(items) {
    return {
      schema: 'odysseus.universal_inbox.items.v1',
      scope: {
        source_kind: 'upload',
        owner_scoped: true,
        admin_override: false,
        owner_identifier_visible: false
      },
      items: deepClone(items),
      page: {
        limit: 25,
        returned_count: items.length,
        has_more: false,
        next_cursor: null
      },
      absolute_paths_visible: false,
      raw_content_visible: false
    };
  }

  function buildSnapshot(total) {
    return {
      schema: 'odysseus.universal_inbox.snapshot.v1',
      scope: {
        source_kind: 'upload',
        owner_scoped: true,
        admin_override: false,
        owner_identifier_visible: false
      },
      total_count: total,
      counts: {
        uploaded: total,
        needs_review: 0,
        blocked: 0,
        unsupported: 0
      },
      family_counts: total ? { document: total } : {},
      readiness: {
        state: total ? 'ready' : 'empty',
        ready_count: total,
        attention_count: 0,
        blocked_count: 0
      },
      item_names_visible: false,
      source_refs_visible: false,
      absolute_paths_visible: false,
      raw_content_visible: false
    };
  }

  function deepClone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  return Object.freeze({
    FIXTURE_SCHEMA,
    SCENARIOS,
    buildFixtureScenario
  });
});
