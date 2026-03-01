# c-monitor Plan

## Objective

Build `c-monitor` as a fork that:

1. Supports both Claude and OpenAI Codex usage monitoring.
2. Reduces runtime memory footprint versus current baseline behavior.
3. Preserves existing `claude-monitor` workflows during migration.

## Strategy (Deep Think + Critical Review)

1. Optimize the current Python architecture first (streaming ingestion + bounded caches).
2. Add provider adapters so Claude and Codex feed a shared normalized contract.
3. Only consider framework/language migration after objective benchmark gates fail.

This avoids a risky rewrite before we know whether measured optimization already meets the memory target.

## Decision Gates

Use explicit go/no-go criteria before changing framework/language:

- `rss_peak` and `rss_p95` on representative workloads
- cold start time
- provider compatibility pass rate

Recommended initial target:

- `rss_p95 <= 80 MB`
- cold start `<= 1.5s`

If optimized Python misses targets by a material margin, proceed to hybrid extraction (memory-hot path in Rust/Go). If not, stay on Python.

## Milestones

### M-001: Provider Contract + Compatibility Surface

- Add provider selection (default `claude`) and normalized adapter interface.
- Keep aliases and module path compatibility.
- Ensure Codex provider path resolves without breaking current behavior.

### M-002: Codex Ingestion + Normalization

- Parse Claude and Codex transcripts into shared usage entries.
- Normalize token/model fields across schema variants.
- Extend pricing behavior for Codex mappings with safe fallback.

### M-003: Memory Budget Enforcement + Observability

- Cap cache growth in monitor loops.
- Measure and track `rss_peak` / `rss_p95`.
- Preserve key realtime output fields and compatibility.

### M-004: Migration Gate + Operating Playbook

- Publish benchmark workloads and pass/fail thresholds.
- Document provider compatibility matrix and fallback semantics.
- Define objective criteria for optional language/framework migration.

## Risks and Mitigations

1. Codex schema variants may break parsing.
   Mitigation: fixture matrix + CI normalization tests.
2. Memory changes may regress latency or output compatibility.
   Mitigation: integration checks for latency and behavior parity.
3. Rewrite decisions may become subjective.
   Mitigation: explicit benchmark thresholds and documented gate outcomes.

## Framework/Language Recommendation

1. Keep Python now for fastest safe delivery.
2. Evaluate hybrid Rust/Go only for hot paths if benchmark gates fail.
3. Avoid full rewrite unless hybrid and optimization still miss targets.
