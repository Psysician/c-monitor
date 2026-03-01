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

## Reproducible Command

```bash
uv run python scripts/memory_benchmark.py \
  --samples 30 \
  --rss-p95-budget-mb 80 \
  --rss-peak-budget-mb 120 \
  --max-entries-per-block 200 \
  --output .benchmarks/memory-benchmark-report.json
```

The benchmark writes one JSON report with per-workload `rss_current_mb`, `rss_peak_mb`,
`rss_p95_mb`, `sample_count`, and pass/fail status.

## Latest Fixture Benchmark (2026-03-01)

Command:

```bash
uv run python scripts/memory_benchmark.py --samples 30 --rss-p95-budget-mb 80 --rss-peak-budget-mb 120 --output .benchmarks/memory-benchmark-report.json
```

Results:

- `claude_realtime_active`: `rss_p95=21.809 MB`, `rss_peak=21.809 MB` (PASS)
- `codex_realtime_active`: `rss_p95=21.809 MB`, `rss_peak=21.809 MB` (PASS)
- `both_realtime_active`: `rss_p95=22.184 MB`, `rss_peak=22.184 MB` (PASS)
- `claude_mixed_active_inactive`: `rss_p95=22.184 MB`, `rss_peak=22.184 MB` (PASS)

## Pass/Fail

- PASS: all workloads satisfy both targets.
- REVISE: any workload violates `rss_p95` or `rss_peak` target.

## Regression Gate

Required checks before release:

- targeted unit tests for retention + metrics
- integration tests for orchestrator memory payload
- benchmark run capturing p95/peak against the workloads above
- CI gate: `.github/workflows/memory-benchmark.yml` runs the benchmark and fails on threshold breach
