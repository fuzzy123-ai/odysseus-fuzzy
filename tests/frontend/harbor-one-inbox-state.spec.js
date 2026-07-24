const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const InboxApi = require('../../static/frontpage-v3/inbox-api.js');
const InboxFixtures = require('../../static/frontpage-v3/inbox-fixtures.js');
const InboxState = require('../../static/frontpage-v3/inbox-state.js');


function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return body;
    }
  };
}


function item(id = 'a', name = 'Owner document.pdf') {
  return {
    schema: 'odysseus.universal_inbox.item.v1',
    source_ref: `upload:${id.repeat(32)}.pdf`,
    source_kind: 'upload',
    display_name: name,
    status: 'uploaded',
    metadata: {
      suffix: '.pdf',
      mime_type: 'application/pdf',
      family: 'document',
      category: 'document_extractable',
      size_bytes: 100,
      uploaded_at: '2026-07-23T09:00:00+00:00',
      extractable_now: true,
      review_required: false,
      blocked: false,
      reason_codes: []
    },
    capability: {
      schema: 'odysseus.universal_inbox.workbench_capability.v1',
      server_authoritative: true,
      raw_content_visible: false,
      absolute_path_visible: false,
      actions: []
    },
    absolute_path_visible: false,
    raw_content_visible: false,
    owner_identifier_visible: false,
    hash_visible: false
  };
}


function listPayload(items = [item()]) {
  return {
    schema: 'odysseus.universal_inbox.items.v1',
    scope: {
      source_kind: 'upload',
      owner_scoped: true,
      admin_override: false,
      owner_identifier_visible: false
    },
    items,
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


function snapshotPayload(total = 1) {
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


function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  return { promise, resolve, reject };
}


test('API client sends bounded owner/cursor queries and validates both contracts', async () => {
  const calls = [];
  const client = new InboxApi.InboxApiClient({
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (url.includes('/items?')) return response(200, listPayload());
      return response(200, snapshotPayload());
    }
  });

  const result = await client.readInbox({
    limit: 10,
    cursor: 'cursor-token',
    owner: 'alice'
  });

  assert.equal(result.list.items.length, 1);
  assert.equal(result.snapshot.total_count, 1);
  assert.match(calls[0].url, /\/items\?/);
  assert.match(calls[0].url, /limit=10/);
  assert.match(calls[0].url, /cursor=cursor-token/);
  assert.match(calls[0].url, /owner=alice/);
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[1].url, '/api/universal-inbox/snapshot?owner=alice');
});


test('API boundary rejects paths, content and invalid redaction flags', () => {
  const withPath = listPayload();
  withPath.items[0].metadata.path = 'C:/private/file.pdf';
  const withContent = listPayload();
  withContent.items[0].content = 'private bytes';
  const withStoragePath = listPayload();
  withStoragePath.items[0].metadata.storage_path = 'C:/private/file.pdf';
  const unsafeSnapshot = snapshotPayload();
  unsafeSnapshot.source_refs_visible = true;
  const identitySnapshot = snapshotPayload();
  identitySnapshot.items = [item()];

  assert.throws(
    () => InboxApi.assertItemsPayload(withPath),
    error => error.code === 'unsafe_payload_key'
  );
  assert.throws(
    () => InboxApi.assertItemsPayload(withContent),
    error => error.code === 'unsafe_payload_key'
  );
  assert.throws(
    () => InboxApi.assertItemsPayload(withStoragePath),
    error => error.code === 'unsafe_payload_key'
  );
  assert.throws(
    () => InboxApi.assertSnapshotPayload(unsafeSnapshot),
    error => error.code === 'invalid_snapshot_payload'
  );
  assert.throws(
    () => InboxApi.assertSnapshotPayload(identitySnapshot),
    error => error.code === 'unsafe_snapshot_key'
  );
});


test('API failures map authentication, availability, HTTP and network separately', async () => {
  const cases = [
    [async () => response(403, {}), 'unauthorized', 403],
    [async () => response(503, {}), 'unavailable', 503],
    [async () => response(500, {}), 'request_failed', 500],
    [async () => { throw new Error('network private detail'); }, 'unavailable', 0]
  ];

  for (const [fetchImpl, code, status] of cases) {
    const client = new InboxApi.InboxApiClient({ fetchImpl });
    await assert.rejects(
      () => client.listItems(),
      error => error.code === code && error.status === status
    );
  }
});


test('fixture source is explicit, labeled and never counts as live evidence', async () => {
  let calls = 0;
  const model = new InboxState.InboxReadModel({
    apiClient: { async readInbox() { calls += 1; } }
  });

  const outcome = await model.load({ source: 'fixture' });
  const state = model.getState();

  assert.equal(outcome.mode, 'fixture');
  assert.equal(state.mode, 'fixture');
  assert.equal(state.origin.state, 'fixture');
  assert.equal(state.origin.label, 'Preview fixture');
  assert.equal(state.origin.counts_as_live_evidence, false);
  assert.equal(state.list.items.length, 1);
  assert.equal(calls, 0);
});


test('fixture catalog exposes every non-live diagnostic mode without impersonating live', () => {
  assert.deepEqual(InboxFixtures.SCENARIOS, [
    'fixture',
    'stale',
    'empty',
    'unauthorized',
    'unavailable',
    'error'
  ]);
  InboxFixtures.SCENARIOS.forEach(scenario => {
    const fixture = InboxFixtures.buildFixtureScenario(scenario);
    assert.equal(fixture.mode, scenario);
    assert.equal(fixture.origin.state, 'fixture');
    assert.equal(fixture.origin.counts_as_live_evidence, false);
  });
});


test('live load transitions through loading and preserves authoritative data', async () => {
  const modes = [];
  const result = { list: listPayload(), snapshot: snapshotPayload() };
  const model = new InboxState.InboxReadModel({
    apiClient: { async readInbox() { return result; } },
    onChange: state => modes.push(state.mode)
  });

  const outcome = await model.load({ source: 'live' });
  const state = model.getState();

  assert.deepEqual(modes, ['loading', 'live']);
  assert.equal(outcome.mode, 'live');
  assert.equal(state.mode, 'live');
  assert.equal(state.origin.state, 'live');
  assert.equal(state.origin.counts_as_live_evidence, true);
  assert.equal(state.list.items[0].display_name, 'Owner document.pdf');
  assert.equal(state.snapshot.total_count, 1);
  assert.equal(state.request.pending, false);
});


test('authoritative empty response enters empty mode and still counts as live readback', async () => {
  const model = new InboxState.InboxReadModel({
    apiClient: {
      async readInbox() {
        return { list: listPayload([]), snapshot: snapshotPayload(0) };
      }
    }
  });

  await model.load();
  const state = model.getState();

  assert.equal(state.mode, 'empty');
  assert.equal(state.origin.state, 'live');
  assert.equal(state.origin.counts_as_live_evidence, true);
  assert.equal(state.snapshot.readiness.state, 'empty');
});


test('live failures produce explicit unauthorized, unavailable and error modes', async () => {
  const cases = [
    [new InboxApi.InboxApiError('unauthorized', { status: 403 }), 'unauthorized'],
    [new InboxApi.InboxApiError('unavailable', { status: 503 }), 'unavailable'],
    [new InboxApi.InboxApiError('invalid_response', { status: 200 }), 'error']
  ];

  for (const [failure, expectedMode] of cases) {
    const model = new InboxState.InboxReadModel({
      apiClient: { async readInbox() { throw failure; } }
    });
    const outcome = await model.load();
    const state = model.getState();
    assert.equal(outcome.mode, expectedMode);
    assert.equal(state.mode, expectedMode);
    assert.equal(state.origin.counts_as_live_evidence, false);
    assert.deepEqual(state.error, {
      code: failure.code,
      status: failure.status
    });
  }
});


test('sequence guard prevents a late response from overwriting the newest read', async () => {
  const first = deferred();
  const second = deferred();
  let call = 0;
  const model = new InboxState.InboxReadModel({
    apiClient: {
      readInbox() {
        call += 1;
        return call === 1 ? first.promise : second.promise;
      }
    }
  });

  const firstLoad = model.load();
  const secondLoad = model.load();
  second.resolve({
    list: listPayload([item('b', 'Newest.pdf')]),
    snapshot: snapshotPayload(1)
  });
  const secondOutcome = await secondLoad;
  first.resolve({
    list: listPayload([item('c', 'Late stale.pdf')]),
    snapshot: snapshotPayload(1)
  });
  const firstOutcome = await firstLoad;

  assert.equal(secondOutcome.applied, true);
  assert.equal(firstOutcome.applied, false);
  assert.equal(firstOutcome.reason, 'stale_response');
  assert.equal(model.getState().list.items[0].display_name, 'Newest.pdf');
  assert.equal(model.getState().request.sequence, 2);
});


test('markStale retains the last projection but removes live-evidence status', async () => {
  const model = new InboxState.InboxReadModel({
    apiClient: {
      async readInbox() {
        return { list: listPayload(), snapshot: snapshotPayload() };
      }
    }
  });
  await model.load();

  const stale = model.markStale('refresh_deadline_elapsed');

  assert.equal(stale.mode, 'stale');
  assert.equal(stale.origin.reason, 'refresh_deadline_elapsed');
  assert.equal(stale.origin.counts_as_live_evidence, false);
  assert.equal(stale.list.items[0].display_name, 'Owner document.pdf');
  assert.equal(stale.snapshot.total_count, 1);
});


test('subscriber failure cannot roll back or corrupt a successful read', async () => {
  const model = new InboxState.InboxReadModel({
    apiClient: {
      async readInbox() {
        return { list: listPayload(), snapshot: snapshotPayload() };
      }
    },
    onChange() {
      throw new Error('view callback failed');
    }
  });

  const outcome = await model.load();

  assert.equal(outcome.mode, 'live');
  assert.equal(model.getState().mode, 'live');
  assert.equal(model.getState().error, null);
});


test('read-model modules remain DOM-free and do not mutate Harbor shell files', () => {
  const root = path.resolve(__dirname, '..', '..');
  const sources = [
    'inbox-api.js',
    'inbox-fixtures.js',
    'inbox-state.js'
  ].map(name => fs.readFileSync(
    path.join(root, 'static/frontpage-v3', name),
    'utf8'
  )).join('\n');

  for (const forbidden of [
    'document.',
    'querySelector',
    'innerHTML',
    'localStorage',
    'sessionStorage'
  ]) {
    assert.ok(!sources.includes(forbidden), forbidden);
  }
  assert.match(sources, /counts_as_live_evidence: false/);
  assert.deepEqual(InboxState.MODES, [
    'fixture',
    'loading',
    'live',
    'stale',
    'empty',
    'unauthorized',
    'unavailable',
    'error'
  ]);
});
