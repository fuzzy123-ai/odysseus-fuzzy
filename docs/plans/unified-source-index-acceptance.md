# Unified Source Index Acceptance Matrix

Status: `USI-14 synthetic/repo-only`
Scope: temporary SQLite stores and in-process fakes only; productive sources, provider SDKs, network, writes outside temporary test targets, and live activation are excluded.

| Requirement | Focused evidence | Expected bounded result |
| --- | --- | --- |
| Owner, classification and content-policy negatives | `test_owner_classification_and_content_policy_negatives_fail_closed` | Cross-owner or weakened policy fails closed; no accepted provider item leaks. |
| Traversal, malformed locators and oversized values | `test_traversal_and_malformed_locators_fail_closed` | Relative, typed, bounded locators only. |
| FTS hostile syntax, literal compilation and owner boundary | `test_fts_hostile_operators_compile_to_literal_tokens`; `test_fts_direct_binding_and_oversized_bounds_are_owner_scoped_and_sanitized` | Operators compile into literal tokens through every match mode; bound direct SQLite FTS remains owner-scoped and sanitizes invalid/oversized input. |
| Federated, snippet and inline-content bounds | `test_federated_snippet_and_inline_content_bounds_fail_closed_without_payload_echo` | Query text, snippets, and inline chunk content reject over-limit values with typed, content-free errors. |
| Content-free metrics, Lens and logs | `test_private_sentinel_is_absent_from_metrics_lens_payloads_and_logs` | Private sentinel is absent from outcomes, metrics, context, Lens payloads, and captured logs. |
| SQLite unavailable/corrupt backing file | `test_corrupt_sqlite_backing_file_is_fail_closed_sanitized_and_content_free` | A post-construction corrupt temporary backing file produces no false result and only a sanitized typed error. |
| Optional Chroma/CBM/RAPTOR projection or lane | `test_embedding_sink_and_optional_query_lane_fail_soft_without_false_success` | Unavailable semantic sink reports `UNAVAILABLE` plus fallback; synthetic optional lanes remain explicitly partial while authorized lexical evidence survives. |
| Tombstone and stale result | `test_tombstone_hides_truth_and_stale_results_are_excluded_from_lens` | Tombstone leaves no FTS hit; stale evidence is excluded by default and has no provenance. |

## Execution

Run only:

```powershell
python -m pytest -q tests/test_unified_source_index_security.py tests/test_unified_source_index_failure_matrix.py
```

## Non-claims

`FakeChromaGenerationSink`, synthetic federated providers, and temporary SQLite prove contract behavior only. This slice makes no live Chroma, CBM, RAPTOR, provider-SDK, network, productive-source, activation, or deployment claim. CBM and RAPTOR identifiers in synthetic lanes do not assert that either is a registered live USI provider.
