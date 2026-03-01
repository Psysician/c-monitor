# Memory Benchmark Plan

## Goal

Validate runtime memory behavior for realtime monitoring after M-003 integration.

Targets:

- `rss_p95 <= 80 MB`
- `rss_peak <= 120 MB`

## Metrics

Collected during monitoring loop:

- `rss_current_mb`
- `rss_peak_mb`
- `rss_p95_mb`
- `sample_count`

## Workloads

1. `claude` provider only, active session stream.
2. `codex` provider only, active session stream.
3. `both` providers active concurrently (`--provider both`).
4. mixed active + inactive historical blocks.

## Pass/Fail

- PASS: all workloads satisfy both targets.
- REVISE: any workload violates `rss_p95` or `rss_peak` target.

## Regression Gate

Required checks before release:

- targeted unit tests for retention + metrics
- integration tests for orchestrator memory payload
- benchmark run capturing p95/peak against the workloads above
