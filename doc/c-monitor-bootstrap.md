# c-monitor Bootstrap

This repository was bootstrapped as a fork workspace from
`Maciek-roboblog/Claude-Code-Usage-Monitor` and renamed locally to `c-monitor`.

## Immediate Objectives

1. Add robust OpenAI/Codex usage ingestion and reporting.
2. Cut runtime memory usage versus the baseline monitor behavior.
3. Keep CLI compatibility while introducing a cleaner provider abstraction.

## Baseline Targets

- Memory: define and track `rss_peak` and `rss_p95` under representative workloads.
- Startup time: keep cold start predictable while adding Codex support.
- Compatibility: no regression for existing Claude usage monitoring output.

## Proposed Execution Shape

1. Introduce provider abstraction (`claude`, `codex`) behind existing commands.
2. Optimize memory hotspots with streaming parsers and bounded caches.
3. Re-evaluate framework/language only after measuring optimized current stack.

## Notes

- Package internals still use `claude_monitor` module paths for bootstrap safety.
- Rename/refactor can happen later behind passing compatibility tests.

## Execution Status (2026-03-01)

- M-001 complete:
  - provider registry exposes adapter iterator contract and normalized record surface
  - provider settings + CLI path selection/override persist with `claude` default behavior
  - compatibility aliases remain mapped to the same entrypoint
- M-002 complete:
  - reader, processors, and pricing normalize Claude/Codex into shared `UsageEntry`
  - analysis path uses compact raw payloads for Claude-only limit detection to reduce memory overhead
  - mixed-provider normalization and codex fallback behavior are covered by tests
- M-003 complete:
  - realtime data manager applies bounded per-block entry retention for lower steady-state memory
  - monitoring orchestrators emit `rss_current`, `rss_peak`, and `rss_p95` telemetry in payload metadata
  - memory budget evaluation (`rss_p95 <= 80 MB`) is surfaced with warning logs when exceeded
- M-004 bootstrap docs added:
  - `doc/memory-benchmark-plan.md` defines benchmark workloads and pass/fail thresholds
  - `doc/provider-compatibility-matrix.md` captures claude/codex/both compatibility status
