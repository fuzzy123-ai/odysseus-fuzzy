# Code Intelligence Three-Way Evaluation Protocol

Stand: 2026-07-18

Status: `CBM-01 synthetic harness / real engine not invoked`

## Purpose

The protocol compares three retrieval conditions without giving any condition a
different model, prompt, repository revision or budget:

1. `grep_read`: existing exact file search/read baseline;
2. `cbm_only`: structural engine evidence without an exact source follow-up;
3. `cbm_plus_exact_read`: structural evidence followed by the existing exact
   reader for every cited result.

CBM-01 freezes the schema and exercises it only with in-process fake providers.
It performs no real source search, model call, engine process, download,
listener, network request or productive write.

## Question Matrix

The committed matrix contains 36 IDs, six per category:

- structural;
- exact/exhaustive;
- semantic;
- architecture;
- impact;
- negative.

Question wording is deliberately absent. Each row stores only an opaque case ID,
category, opaque expected-fact IDs and ground-truth method classes. Real prompts
may exist only in an approval-scoped runtime fixture and are represented in the
report by one SHA-256 fingerprint.

Ground truth may combine direct source inspection, AST/LSP structure, Git
evidence and manually reviewed edge labels. Negative cases have an empty
expected set and fail on any false fact.

The 36 fixed questions measure comparative retrieval quality only. The hard
two-tool-call certification corpus below is additional and is generated from
the revision's coverage manifest, rather than from these six-case samples.

## Fairness Contract

Every quality cell uses the same:

- model ID;
- prompt fingerprint;
- repository commit;
- locked engine commit;
- configuration fingerprint;
- tool-call budget;
- time budget;
- hardware profile and operating-system class;
- repeat ordinals.

At least two repeated quality observations are required for all 36 questions in
all three arms. A missing arm/question/run cell invalidates the report instead
of being treated as zero quality. The hybrid arm must record exact-reader use;
the other two arms may not claim it.

## Hard Two-Tool-Call Retrieval Certification

This is a separate, non-negotiable certification lane for code sources. It is
not a quality score and no semantic, structural or embedding result can pass it
by itself. It remains fixture-only and default-off until the Forge--USI code
adapter, its coverage manifest and the canonical tool binding are accepted.

The certified public sequence is exactly:

```text
call 1: query_knowledge -> one Forge-bound locator or an honest non-success state
call 2: read_file(locator returned by call 1, Forge-locator mode) -> authoritative bytes/lines
```

`query_knowledge` may inspect USI records and rebuildable projections, but it
must neither return source text nor invoke `read_file` in Forge-locator mode in
call 1. Call 2 is the existing canonical public `read_file` identity, not a
new Forge/public tool. In Forge-locator mode it accepts only the returned
locator and its pinned revision. The locator selects one source range and
carries at least the
Forge repository identity, immutable Forge revision, normalized repository-
relative path, inclusive line range, locator schema version and the coverage-
manifest digest/generation which produced it. Call 2 accepts that locator and
the same immutable revision only; a caller cannot substitute a branch, HEAD,
path, range or revision. The verifier compares a content-free digest of the
read range with the pinned Forge snapshot, never with projection text.

An *eligible indexed code line* is every line in an in-scope textual file of
the Forge revision. A `complete` coverage state requires the code adapter to
record an occurrence and deterministic direct-retrieval witness for every one
of those lines. Comments, string literals, whitespace-only layout, generated,
minified, dependency and vendor text are eligible on the same terms as
executable lines. A chunk boundary never makes an otherwise eligible line
ineligible: its witness may legitimately return an enclosing syntax unit whose
locator covers the line.

Only an explicit `policy_out_of_scope` source-text decision or genuine
`non_text`/binary classification may leave the direct-line denominator, and
each requires an individual bounded manifest reason. A policy-excluded
source-text file forbids a universal/whole-codebase certification claim even
when the selected in-scope denominator is complete. Unsupported encoding,
read/decode, size/budget or fallback failure is never an exclusion; it makes
the affected revision/source `partial` or `failed` and therefore unable to
receive a complete certificate.

For every coverage-complete revision, the harness derives the entire
certification corpus from the manifest; it may not sample it or hand-author a
favourable subset. Each opaque case binds `case_id`, repository/revision,
manifest digest, target-line digest, expected line number and a deterministic
direct witness. A witness is constructed only from the pinned repository-
relative path plus a lexical or declared containing-symbol anchor. It contains
no locator, absolute path or target line number, so call 1 must locate rather
than echo a supplied location. The call-1 response passes only when its one
authoritative Forge locator covers the target line and its revision and manifest
binding equal the case binding. Call 2 then passes only when Forge returns that
same range from that same revision and the target-line digest matches the
snapshot. Case receipts and reports retain opaque IDs/digests, never anchors or
source text.

Direct/exact and semantic retrieval have deliberately different claims:

- `direct_exact` is the only mode eligible for line certification. It must
  return the bound locator or an honest non-success state.
- `structural` may propose a declared symbol/unit locator, but is certified
  only after the direct-exact constraint above is met; its graph score alone is
  not a locator proof.
- `semantic` may rank useful candidates, but is recall/ranking evidence only.
  It is excluded from the line-certification denominator and may never be
  relabelled `complete` because a similar chunk was returned.

The external-tool ledger counts public tool dispatches, including failed,
retried and cancelled dispatches. A positive certificate requires exactly one
`query_knowledge` dispatch and exactly one canonical `read_file` dispatch in
Forge-locator mode; its maximum is therefore two and it introduces no new
public tool identity. Internal USI stores, lexical indices, graph/vector
providers and in-process planning spans are recorded separately and do not
consume this public-tool budget. A missing, partial, stale, failed or malformed
call-1 result must stop before call 2; it has one public dispatch and cannot
receive a certificate. Hidden public reader calls, a second knowledge query,
retry after a result, or a direct provider/MCP call invalidate the case even if
the final bytes happen to match.

Coverage state is revision-scoped and honest: `complete` means every manifest
line in scope has an accepted occurrence and witness; `partial`, `stale`,
`failed`, `not_indexed` and `out_of_scope` are non-success outcomes with a
bounded reason/count. A projection generation different from the locator's
coverage generation, or a Forge revision different from the locator revision,
is `stale`, never a fallback to current HEAD. Only `complete` permits the
positive corpus and a certificate. `partial` may expose covered candidates with
its state, but it must not claim exhaustive retrieval or mint a line
certificate.

Required negative cases are generated alongside the positive corpus for:

- an explicit policy-out-of-scope/non-text manifest entry or absent occurrence;
- a partial/failed/not-indexed source scope;
- a stale projection or changed Forge revision;
- a locator with a forged path, range, manifest digest or revision;
- a semantic-only candidate presented as direct/exact; and
- an exact query with no matching witness.

They pass only through the specified non-success state (and no unauthorized
Forge read). Any locator for a different revision, an unrecorded range, or a
false `complete` state is a failure, not a zero-recall observation.

The certification SLO is correctness-first: for each submitted
coverage-complete revision, 100% of the generated eligible-line cases must
pass, with zero false certificates, zero revision/locator mismatches and two
or fewer public tool dispatches per positive case. The run also records p50/p95
combined public-call duration against the configured runtime budget; a budget
overrun remains `budget_exceeded`, never a pass. The evidence packet contains
the manifest/revision/config/tool-descriptor digests, positive and negative
denominators, outcome and reason counts, exact public-tool ledger counts,
latency aggregates, bounded mismatch categories and a canonical evidence
digest. It contains no source text, query text, path beyond the approved
repository-relative test fixture identity, provider output or raw read bytes.

## Quality Receipts

Providers return aggregate receipts only:

- opaque returned and false fact IDs;
- outcome (`success`, `partial`, `failed`, `timeout`, `provider_error` or
  `budget_exceeded`);
- calls, tokens and duration;
- exact-reader and coverage-complete booleans;
- an explicit `raw_content_visible=false` assertion.

Two-call certification receipts additionally contain opaque case and target-line
digests; locator/revision/manifest binding booleans; retrieval mode; coverage
state and bounded reason; call-1 and call-2 public dispatch counts; internal
provider-span count; exact-read digest-match boolean; and a no-extra-public-
tool-call boolean. They must not contain a locator value, raw query, source
content, engine output or absolute path.

The evaluator calculates precision, recall and F1 per observation, then mean
quality by arm and category. It also reports success and coverage rates, total
and mean calls/tokens, p50/p95 duration and typed failure counts. Ranking is by
mean F1, then calls and tokens; it is evidence, not an activation decision.

## Performance Receipts

The CBM provider supplies at least three repeated aggregate receipts for each
scenario:

1. empty-projection cold start;
2. warm reopen;
3. first query;
4. no-op sync;
5. edit sync;
6. add sync;
7. delete sync;
8. rename sync;
9. bounded multi-file burst.

Each receipt contains wall time, CPU time, peak RSS, database size/growth,
touched-file/byte counts and outcome. Reports contain p50/p95 wall and CPU time,
maximum RSS/database size and bounded work counts. Extraction/indexing,
query-ready and query latency therefore remain distinguishable rather than
being compressed into one benchmark number.

## Privacy And Failure Rules

Input or report fields named `prompt`, `question`, `source_text`, `snippet`,
`content`, `response`, `model_output`, `absolute_path` or `host_path` are
rejected. Raw content visibility, engine/model invocation, productive reads or
network calls also invalidate the input. Identifiers use a bounded low-entropy
alphabet and numeric values have explicit limits.

The report contains no timestamps generated by the harness. Canonical JSON
produces a deterministic SHA-256 evidence digest. Repeated evaluation of the
same receipts must therefore be byte-for-byte equivalent.

Provider errors, timeouts, budget overruns, incomplete coverage and partial
answers remain explicit. They never disappear from averages and never become a
false successful empty result.

## Real Evaluation Boundary

A later real run still requires the CBM-00 artifact verification and separate
local execution/dependency approval. It must use a content-free synthetic or
approved public fixture, an egress-denied sandbox, a headless binary, an
explicit allowed root and disabled installer/update/watcher/auto-index/UI/config
surfaces. CBM-01 does not provide that approval and does not make
`CBM-LIVE-ACTIVATION` eligible.
