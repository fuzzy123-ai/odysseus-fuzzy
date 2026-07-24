# GraphRAG/RAPTOR Memory Metrics Contract

Stand: 2026-07-18

Status: `extended_for_usi12 / live_default_off`

Dieser Vertrag ist die normative GRO-00-Grenze fuer die Memory- und
RaptorGraph-Instrumentierung. Menschenlesbare Erlaeuterungen sind informativ;
der markierte JSON-Block ist die maschinenlesbare Quelle. Aenderungen an
Metriknamen, Labels, Buckets, SLOs, Privacy oder Betriebsgrenzen benoetigen
einen expliziten Contract-Review und duerfen eine schlechte Baseline nicht
stillschweigend durch hoehere Grenzwerte gruen faerben.

## Normativer maschinenlesbarer Vertrag

<!-- GRO_CONTRACT_JSON_BEGIN -->
```json
{
  "schema": "odysseus.graphrag_raptor_memory_metrics_contract.v1",
  "contract_version": "1.1.0",
  "effective_date": "2026-07-18",
  "status": "extended_for_usi12",
  "terminology": {
    "product_name": "RaptorGraph",
    "current_capability_level": "graph_cluster_summary",
    "capability_levels": [
      "source_metadata",
      "derived_index",
      "graph_cluster_summary",
      "recursive_raptor_tree"
    ],
    "recursive_raptor_tree_claim_allowed": false,
    "promotion_rule": "Code and integration evidence must prove a recursive hierarchy before promotion."
  },
  "registry": {
    "process_local": true,
    "thread_safe": true,
    "atomic_snapshot": true,
    "max_series": 256,
    "clock_injectable": true,
    "test_reset_only": true,
    "counter_reset_on_process_restart_allowed": true,
    "pid_label_allowed": false,
    "invalid_sample_policy": "drop_and_increment_odysseus_metrics_samples_dropped_total",
    "arbitrary_labels_allowed": false,
    "arbitrary_metric_names_allowed": false
  },
  "labels": {
    "component": ["memory", "raptorgraph", "source_index"],
    "operation": [
      "query",
      "memory_status",
      "raptor_status",
      "rebuild",
      "cache_lookup",
      "automation",
      "index",
      "projection",
      "delete"
    ],
    "phase": [
      "total",
      "load_index",
      "discover",
      "read_hash",
      "build_graph",
      "cluster",
      "serialize",
      "write_artifact",
      "retrieve",
      "rank",
      "build_response",
      "invalidate"
    ],
    "outcome": ["success", "blocked", "error", "cancelled"],
    "cache_result": ["hit", "miss", "stale", "evicted", "bypass"],
    "profile": ["quick", "standard", "stress"],
    "runtime": ["app", "worker", "benchmark"],
    "record_kind": [
      "source",
      "source_version",
      "chunk",
      "entity",
      "relation",
      "lineage",
      "projection_manifest",
      "derived_run",
      "job",
      "tombstone"
    ]
  },
  "histogram_buckets_seconds": {
    "operation": [
      0.0005,
      0.001,
      0.0025,
      0.005,
      0.01,
      0.025,
      0.05,
      0.1,
      0.25,
      0.5,
      0.75,
      1.0,
      2.5,
      5.0,
      15.0,
      30.0,
      60.0
    ],
    "event_loop_lag": [
      0.001,
      0.0025,
      0.005,
      0.01,
      0.025,
      0.05,
      0.075,
      0.1,
      0.25,
      0.5,
      1.0
    ],
    "rebuild": [
      0.01,
      0.025,
      0.05,
      0.1,
      0.25,
      0.5,
      1.0,
      2.5,
      5.0,
      10.0,
      20.0,
      30.0,
      45.0,
      60.0,
      90.0,
      120.0,
      300.0
    ],
    "render": [
      0.0005,
      0.001,
      0.0025,
      0.005,
      0.01,
      0.025,
      0.05,
      0.1,
      0.25,
      0.5
    ]
  },
  "metrics": [
    {
      "name": "odysseus_memory_operations_total",
      "type": "counter",
      "labels": ["component", "operation", "outcome", "runtime"]
    },
    {
      "name": "odysseus_memory_operation_duration_seconds",
      "type": "histogram",
      "labels": ["component", "operation", "phase", "outcome", "runtime"],
      "buckets": "operation"
    },
    {
      "name": "odysseus_memory_event_loop_lag_seconds",
      "type": "histogram",
      "labels": ["component", "operation", "runtime"],
      "buckets": "event_loop_lag"
    },
    {
      "name": "odysseus_memory_worker_queue_depth",
      "type": "gauge",
      "labels": ["component", "operation", "runtime"]
    },
    {
      "name": "odysseus_raptor_cache_requests_total",
      "type": "counter",
      "labels": ["cache_result", "runtime"]
    },
    {
      "name": "odysseus_raptor_cache_entries",
      "type": "gauge",
      "labels": ["runtime"]
    },
    {
      "name": "odysseus_query_cache_entries",
      "type": "gauge",
      "labels": ["runtime"]
    },
    {
      "name": "odysseus_query_cache_bytes",
      "type": "gauge",
      "labels": ["runtime"]
    },
    {
      "name": "odysseus_raptor_rebuild_duration_seconds",
      "type": "histogram",
      "labels": ["phase", "outcome", "runtime"],
      "buckets": "rebuild"
    },
    {
      "name": "odysseus_raptor_rebuild_sources",
      "type": "gauge",
      "labels": ["runtime"]
    },
    {
      "name": "odysseus_raptor_rebuild_sources_per_second",
      "type": "gauge",
      "labels": ["runtime"]
    },
    {
      "name": "odysseus_raptor_rebuild_rss_delta_bytes",
      "type": "gauge",
      "labels": ["runtime"]
    },
    {
      "name": "odysseus_raptor_artifact_age_seconds",
      "type": "gauge",
      "labels": ["runtime"]
    },
    {
      "name": "odysseus_usi_operations_total",
      "type": "counter",
      "labels": ["operation", "outcome", "runtime"]
    },
    {
      "name": "odysseus_usi_operation_duration_seconds",
      "type": "histogram",
      "labels": ["operation", "phase", "outcome", "runtime"],
      "buckets": "operation"
    },
    {
      "name": "odysseus_usi_queue_depth",
      "type": "gauge",
      "labels": ["operation", "runtime"]
    },
    {
      "name": "odysseus_usi_stale_projections",
      "type": "gauge",
      "labels": ["runtime"]
    },
    {
      "name": "odysseus_usi_records",
      "type": "gauge",
      "labels": ["record_kind", "runtime"]
    },
    {
      "name": "odysseus_metrics_render_duration_seconds",
      "type": "histogram",
      "labels": ["outcome", "runtime"],
      "buckets": "render"
    },
    {
      "name": "odysseus_metrics_samples_dropped_total",
      "type": "counter",
      "labels": []
    }
  ],
  "correctness": {
    "histogram_exposition": ["bucket", "sum", "count"],
    "finite_values_only": true,
    "nonnegative_counters_and_durations": true,
    "ledger_derived_values_as_counters_allowed": false,
    "raptor_failures_only_from_raptor_instrumentation": true,
    "unknown_model_latency_attribution_allowed": false,
    "scrape_snapshot_only": true,
    "scrape_forbidden_io": [
      "filesystem",
      "ledger",
      "vault",
      "corpus",
      "query",
      "provider",
      "model",
      "network"
    ]
  },
  "slo": {
    "minimum_samples_for_percentile_gate": 30,
    "below_minimum_samples_status": "insufficient_data",
    "windows": {
      "runtime_acceptance_minutes": 15,
      "cache_hit_rate_minutes": 30,
      "live_soak_hours_min": 12,
      "live_soak_hours_max": 24
    },
    "gates": {
      "derived_retrieval_p95_ms_lt": 500,
      "memory_status_p95_ms_lt": 750,
      "raptor_status_p95_ms_lt": 250,
      "query_cache_hit_p95_ms_lt": 100,
      "event_loop_lag_max_ms_lte": 100,
      "instrumentation_span_p95_ms_lt": 0.2,
      "instrumentation_total_regression_percent_lt": 1,
      "metrics_scrape_p95_ms_lt": 100,
      "metrics_scrape_forbidden_io_count_eq": 0,
      "comparable_profile_regression_percent_lte": 15
    },
    "standard_rebuild": {
      "source_count": 1000,
      "wall_seconds_lt": 60,
      "sources_per_second_gte": 20,
      "rss_delta_mib_lt": 512,
      "cpu_seconds_lt": 60,
      "temporary_plus_report_mib_lt": 256
    }
  },
  "benchmark_profiles": {
    "quick": {
      "source_count": 120,
      "warm_samples": 30,
      "purpose": "developer_feedback"
    },
    "standard": {
      "source_count": 1000,
      "warm_samples": 30,
      "purpose": "release_acceptance"
    },
    "stress": {
      "source_count": 5000,
      "warm_samples": 30,
      "purpose": "bounded_scale_diagnostic",
      "release_blocking_by_default": false
    }
  },
  "real_backend_benchmark": {
    "temporary_synthetic_markdown_only": true,
    "required_steps": [
      "rebuild",
      "raptor_status_cold",
      "raptor_status_warm",
      "memory_status_cold",
      "memory_status_warm",
      "derived_retrieval",
      "query_cache_miss",
      "query_cache_hit",
      "source_mutation_invalidation",
      "bounded_rebuild_after_invalidation"
    ],
    "required_measurements": [
      "wall_time",
      "cpu_time",
      "rss_delta",
      "disk_bytes",
      "source_count",
      "chunk_count",
      "p50",
      "p95",
      "p99",
      "cache_hit_rate",
      "event_loop_lag"
    ],
    "report_allowed_content": [
      "counts",
      "timings",
      "fixed_profile_names",
      "synthetic_fixture_content_hashes"
    ]
  },
  "cache_contract": {
    "raptor": {
      "key_source": "mutation_or_artifact_generation",
      "full_scan_on_warm_hit_allowed": false,
      "external_change_fallback_seconds_lte": 5,
      "thread_safe": true,
      "bounded_entries": true,
      "invalidation_events": ["rebuild", "write", "feature_flag_change"]
    },
    "query_v2": {
      "key": "sha256_normalized_parameters_and_artifact_generation",
      "plaintext_query_in_key_allowed": false,
      "hit_check_before_expensive_retrieval": true,
      "ttl_days": 7,
      "max_entries": 512,
      "max_bytes": 8388608,
      "atomic_writes": true,
      "locking": true,
      "full_json_rewrite_on_hit_allowed": false,
      "canonical_memory_data": false
    }
  },
  "event_loop_isolation": {
    "memory_specific_bounded_worker": true,
    "global_system_queue_allowed": false,
    "bounded_read_parallelism": true,
    "serialize_writes_and_rebuilds_per_internal_vault_scope": true,
    "vault_scope_metric_label_allowed": false,
    "backpressure": "bounded_block_or_reject",
    "preserve_cancellation_timeout_locked_vault_gates": true
  },
  "alerts": {
    "query_p95": {"threshold_ms_gt": 500, "for_minutes": 15},
    "memory_status_p95": {"threshold_ms_gt": 750, "for_minutes": 15},
    "raptor_status_p95": {"threshold_ms_gt": 250, "for_minutes": 15},
    "event_loop_lag": {"threshold_ms_gt": 100},
    "rebuild_error": {"for_minutes": 5, "repeated_error_immediate": true},
    "cache_hit_rate": {
      "ratio_lt": 0.6,
      "minimum_requests": 20,
      "window_minutes": 30
    },
    "query_cache_entries": {"threshold_gt": 512},
    "query_cache_bytes": {"threshold_gt": 8388608},
    "target_down": {"for_minutes": 2},
    "dirty_artifact_age": {"hours_gt": 24},
    "samples_dropped": {"threshold_gt": 0},
    "maintenance_window_suppresses_rebuild_error": false
  },
  "operations": {
    "prometheus_retention_days": 30,
    "prometheus_size_limit_bytes": 5368709120,
    "scrape_interval_seconds": 15,
    "scrape_timeout_seconds": 5,
    "bind_scope": ["localhost", "private_container_network", "approved_vpn_path"],
    "public_exposure_allowed": false,
    "external_alert_delivery_v1": false
  },
  "security": {
    "browser_admin_session_allowed": true,
    "api_token_exact_scopes": ["observability:read"],
    "additional_api_token_scopes_allowed": false,
    "token_in_query_string_allowed": false,
    "secrets_in_repository_allowed": false,
    "forbidden_labels_or_payload": [
      "query_text",
      "vault",
      "owner",
      "user",
      "session",
      "path",
      "source_hash",
      "document_id",
      "prompt",
      "output",
      "model_id",
      "token",
      "credential"
    ],
    "synthetic_fixture_hash_in_offline_report_allowed": true,
    "raw_payload_in_metrics_allowed": false
  },
  "current_baseline": {
    "schema": "gro00.baseline.v1",
    "observed_at": "2026-07-18T11:49:00+02:00",
    "environment_class": "developer_workstation",
    "synthetic_only": true,
    "network_io": false,
    "model_calls": 0,
    "productive_corpus_reads": 0,
    "productive_writes": 0,
    "test_suites": {
      "contract_and_arithmetic_simulation": {"passed": 10, "failed": 0},
      "memory_raptor_backend": {"passed": 56, "failed": 0},
      "gmi_exporter_handoff": {"passed": 14, "failed": 0}
    },
    "raptor_cache_key_1000_sources": {
      "samples": 40,
      "p50_ms": 104.2493,
      "p95_ms": 168.287,
      "max_ms": 183.2555
    },
    "raptor_warm_hit_1000_sources": {
      "samples": 40,
      "p50_ms": 99.7904,
      "p95_ms": 122.7627,
      "max_ms": 152.0171,
      "loader_calls": 1,
      "known_full_scan_on_hit": true
    },
    "graph_build_120_sources": {
      "samples": 5,
      "median_ms": 314.34,
      "max_ms": 334.55,
      "nodes": 124,
      "edges": 14878,
      "within_preexisting_threshold": true
    },
    "arithmetic_raptor_simulation": {
      "nodes": 100000,
      "edges": 300000,
      "wall_ms": 50.2966,
      "cache_hit_ratio": 0.6666666666666666,
      "passed": true,
      "real_backend_evidence": false
    },
    "interpretation": "comparison_baseline_only_not_release_go"
  },
  "inventory": {
    "findings": [
      {
        "id": "GRO-I01",
        "state": "open",
        "fact": "Exporter has no bounded Memory/RaptorGraph histogram registry.",
        "resolved_by": "GRO-01_GRO-02"
      },
      {
        "id": "GRO-I02",
        "state": "open",
        "fact": "RAPTOR cache key scans Markdown source metadata before every hit.",
        "resolved_by": "GRO-05"
      },
      {
        "id": "GRO-I03",
        "state": "open",
        "fact": "Query cache key contains normalized plaintext and hit statistics rewrite the complete JSON cache.",
        "resolved_by": "GRO-06"
      },
      {
        "id": "GRO-I04",
        "state": "open",
        "fact": "Async Memory and Raptor status routes call synchronous backend work directly.",
        "resolved_by": "GRO-07"
      },
      {
        "id": "GRO-I05",
        "state": "open",
        "fact": "Existing 100k RAPTOR suite is arithmetic simulation and does not prove production backend performance.",
        "resolved_by": "GRO-08"
      }
    ],
    "pinned_sha256": {
      "src/observability_metrics.py": "FB2F62384FEC3225E958CB2F70AA9026B10B9FAD4A86CC02984B7935159A21F7",
      "src/memory_perf_suite_raptor.py": "B6EA8EA12EB663A60A94D1BDE365681798E42E8142D2E7E8D248551E8325C6A0",
      "plugins/obsidian/backend/performance_fixtures.py": "BD19CA36AB6D387929A6E4C1CBE8C6D08F4BEABEB02F59A3FDDD841A1BE7E32D",
      "plugins/obsidian/backend/raptor_cache.py": "3B4764119D9FA7E329BBBE47F1CBAC885F2A7395A18416D5403AB8FD57674F3B",
      "plugins/obsidian/backend/raptor_rebuild.py": "459E82984602B0CB7AD5FB8EBD84E86D8CE5D552584DDED694262011D3BBC632",
      "plugins/obsidian/backend/derived_index.py": "D40FEE83F68611119393F33ED4FD5CED4C17FC9FDF4827C9949DF40223E6D5B8",
      "plugins/obsidian/backend/query_layer.py": "939B17CBB17EFBDBA5334A6E1125CC8E6CC53E4F3528A07B1821BCA1DD3EABDF",
      "plugins/obsidian/backend/routes.py": "8818ED1FBBCCAA94851C83497BCE072F62C05BC88AB21DFBA58EBF16CF6FF2B0"
    }
  },
  "activation": {
    "metrics_scrape_enabled": false,
    "prometheus_enabled": false,
    "grafana_enabled": false,
    "service_or_container_start_allowed_before_live_gate": false,
    "live_token_creation_allowed_before_live_gate": false,
    "productive_rebuild_allowed_before_live_gate": false,
    "live_gate": "GRO-LIVE-ACTIVATION"
  },
  "gro00_acceptance": {
    "verdict": "go_to_gro01",
    "next_claimable": ["GRO-01"],
    "successors_blocked_by_dependency": [
      "GRO-02",
      "GRO-03",
      "GRO-04",
      "GRO-05",
      "GRO-06",
      "GRO-07",
      "GRO-08",
      "GRO-09",
      "GRO-10",
      "GRO-11",
      "GRO-12",
      "GRO-13",
      "GRO-14"
    ],
    "live_default_off": true
  }
}
```
<!-- GRO_CONTRACT_JSON_END -->

## Readback

Der JSON-Block muss ohne Normalisierung parsebar sein. Eine Abnahme prueft
mindestens: eindeutige Metriknamen, bekannte Typen, geschlossene Label-Enums,
streng steigende endliche Histogramm-Buckets, Serienlimit 256, mindestens 30
Samples fuer Percentile-Gates, Standardprofil mit 1.000 Quellen, Query-Cache-
Grenzen 512/8 MiB, 30 Tage/5 GiB Retention, exakten
`observability:read`-Scope und ausgeschaltete Live-Komponenten.

GRO-00 aktiviert keine Laufzeitfunktion. Der erste erlaubte Nachfolger ist
ausschliesslich GRO-01; erst dort wird die neue bounded Registry implementiert.
