#!/usr/bin/env python3
"""Run fixture-based memory benchmarks for c-monitor."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Tuple

from claude_monitor.monitoring.orchestrator import (
    MonitoringOrchestrator,
    MultiProviderMonitoringOrchestrator,
)


def _iso_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _build_record(provider: str, idx: int, ts: datetime) -> Dict[str, Any]:
    if provider == "codex":
        cache_read_tokens = 12 if idx % 4 == 0 else 0
        return {
            "timestamp": _iso_timestamp(ts),
            "type": "assistant",
            "message": {
                "id": f"codex-msg-{idx}",
                "model": "gpt-5-codex",
                "usage": {
                    "input_tokens": 130 + (idx % 11),
                    "output_tokens": 55 + (idx % 7),
                    "cache_read_tokens": cache_read_tokens,
                },
            },
            "request_id": f"codex-req-{idx}",
        }

    return {
        "timestamp": _iso_timestamp(ts),
        "type": "assistant",
        "message": {
            "id": f"claude-msg-{idx}",
            "model": "claude-3-5-sonnet",
            "usage": {
                "input_tokens": 150 + (idx % 9),
                "output_tokens": 65 + (idx % 5),
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
        "request_id": f"claude-req-{idx}",
    }


def _write_provider_dataset(
    root: Path, provider: str, timestamps: Iterable[datetime]
) -> Path:
    data_root = root / provider
    session_dir = data_root / "session-001"
    session_dir.mkdir(parents=True, exist_ok=True)
    file_path = session_dir / f"{provider}.jsonl"

    with open(file_path, "w", encoding="utf-8") as f:
        for idx, ts in enumerate(timestamps):
            f.write(json.dumps(_build_record(provider, idx, ts)))
            f.write("\n")

    return data_root


def _build_workloads(base_dir: Path) -> Dict[str, Dict[str, Path]]:
    now = datetime.now(timezone.utc)

    active_stream = [now - timedelta(minutes=45) + timedelta(seconds=i * 20) for i in range(240)]
    historical = [now - timedelta(hours=12) + timedelta(minutes=i * 2) for i in range(60)]
    current = [now - timedelta(minutes=90) + timedelta(seconds=i * 30) for i in range(120)]

    claude_active = _write_provider_dataset(base_dir / "claude-active", "claude", active_stream)
    codex_active = _write_provider_dataset(base_dir / "codex-active", "codex", active_stream)
    claude_mixed = _write_provider_dataset(
        base_dir / "claude-mixed",
        "claude",
        [*historical, *current],
    )

    return {
        "claude_realtime_active": {"claude": claude_active},
        "codex_realtime_active": {"codex": codex_active},
        "both_realtime_active": {"claude": claude_active, "codex": codex_active},
        "claude_mixed_active_inactive": {"claude": claude_mixed},
    }


def _extract_memory_metrics(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    memory = snapshot.get("memory", {})
    if not isinstance(memory, dict):
        raise RuntimeError("Missing memory metrics in monitoring snapshot")
    return {
        "rss_current_mb": float(memory.get("rss_current_mb", 0.0)),
        "rss_peak_mb": float(memory.get("rss_peak_mb", 0.0)),
        "rss_p95_mb": float(memory.get("rss_p95_mb", 0.0)),
        "sample_count": int(memory.get("sample_count", 0)),
        "within_budget": bool(memory.get("within_budget", False)),
    }


def _run_single_provider_workload(
    provider: str,
    data_root: Path,
    samples: int,
    memory_budget_mb: float,
    max_entries_per_block: int,
) -> Dict[str, Any]:
    orchestrator = MonitoringOrchestrator(
        update_interval=1,
        data_path=str(data_root),
        provider=provider,
        memory_budget_mb=memory_budget_mb,
        max_entries_per_block=max_entries_per_block,
        retain_entries_for_inactive_blocks=False,
    )
    orchestrator.set_args(SimpleNamespace(plan="pro"))

    latest: Dict[str, Any] = {}
    for _ in range(samples):
        snapshot = orchestrator.force_refresh()
        if isinstance(snapshot, dict):
            latest = snapshot

    if not latest:
        raise RuntimeError(f"No monitoring payload received for provider={provider}")
    return _extract_memory_metrics(latest)


def _run_multi_provider_workload(
    provider_paths: Dict[str, Path],
    samples: int,
    memory_budget_mb: float,
    max_entries_per_block: int,
) -> Dict[str, Any]:
    orchestrator = MultiProviderMonitoringOrchestrator(
        update_interval=1,
        provider_configs={provider: str(path) for provider, path in provider_paths.items()},
        memory_budget_mb=memory_budget_mb,
        max_entries_per_block=max_entries_per_block,
        retain_entries_for_inactive_blocks=False,
    )
    orchestrator.set_args(SimpleNamespace(plan="pro"))

    latest: Dict[str, Any] = {}
    for _ in range(samples):
        snapshot = orchestrator.force_refresh()
        if isinstance(snapshot, dict):
            latest = snapshot

    if not latest:
        raise RuntimeError("No monitoring payload received for multi-provider workload")
    return _extract_memory_metrics(latest)


def _evaluate_workload_result(
    metrics: Dict[str, Any], p95_budget_mb: float, peak_budget_mb: float
) -> Dict[str, Any]:
    rss_p95_mb = float(metrics.get("rss_p95_mb", 0.0))
    rss_peak_mb = float(metrics.get("rss_peak_mb", 0.0))
    within_budget = rss_p95_mb <= p95_budget_mb
    peak_within_budget = rss_peak_mb <= peak_budget_mb
    passes = within_budget and peak_within_budget
    return {
        **metrics,
        "within_budget": within_budget,
        "peak_within_budget": peak_within_budget,
        "rss_p95_budget_mb": p95_budget_mb,
        "rss_peak_budget_mb": peak_budget_mb,
        "pass": passes,
    }


def run_benchmark(
    samples: int,
    p95_budget_mb: float,
    peak_budget_mb: float,
    max_entries_per_block: int,
    output_path: Path,
    keep_fixtures: bool,
) -> Tuple[bool, Dict[str, Any]]:
    fixtures_root = Path(".benchmarks") / "fixtures"
    if fixtures_root.exists():
        shutil.rmtree(fixtures_root)
    fixtures_root.mkdir(parents=True, exist_ok=True)

    workloads = _build_workloads(fixtures_root)
    results: Dict[str, Dict[str, Any]] = {}

    for workload_name, provider_paths in workloads.items():
        if len(provider_paths) == 1:
            provider, path = next(iter(provider_paths.items()))
            metrics = _run_single_provider_workload(
                provider=provider,
                data_root=path,
                samples=samples,
                memory_budget_mb=p95_budget_mb,
                max_entries_per_block=max_entries_per_block,
            )
        else:
            metrics = _run_multi_provider_workload(
                provider_paths=provider_paths,
                samples=samples,
                memory_budget_mb=p95_budget_mb,
                max_entries_per_block=max_entries_per_block,
            )

        results[workload_name] = _evaluate_workload_result(
            metrics=metrics,
            p95_budget_mb=p95_budget_mb,
            peak_budget_mb=peak_budget_mb,
        )

    overall_pass = all(result.get("pass", False) for result in results.values())

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "samples_per_workload": samples,
        "thresholds": {
            "rss_p95_budget_mb": p95_budget_mb,
            "rss_peak_budget_mb": peak_budget_mb,
            "max_entries_per_block": max_entries_per_block,
        },
        "fixtures_root": str(fixtures_root.resolve()),
        "results": results,
        "overall_pass": overall_pass,
    }

    if not keep_fixtures:
        shutil.rmtree(fixtures_root, ignore_errors=True)
        report["fixtures_root"] = None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return overall_pass, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run c-monitor memory benchmarks.")
    parser.add_argument("--samples", type=int, default=30, help="Samples per workload")
    parser.add_argument(
        "--rss-p95-budget-mb",
        type=float,
        default=80.0,
        help="RSS p95 pass threshold in MB",
    )
    parser.add_argument(
        "--rss-peak-budget-mb",
        type=float,
        default=120.0,
        help="RSS peak pass threshold in MB",
    )
    parser.add_argument(
        "--max-entries-per-block",
        type=int,
        default=200,
        help="Max retained entries per block during benchmark",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".benchmarks/memory-benchmark-report.json"),
        help="Output JSON report path",
    )
    parser.add_argument(
        "--keep-fixtures",
        action="store_true",
        help="Keep generated fixture data under .benchmarks/fixtures",
    )
    args = parser.parse_args()

    ok, report = run_benchmark(
        samples=args.samples,
        p95_budget_mb=args.rss_p95_budget_mb,
        peak_budget_mb=args.rss_peak_budget_mb,
        max_entries_per_block=args.max_entries_per_block,
        output_path=args.output,
        keep_fixtures=args.keep_fixtures,
    )

    print(f"Memory benchmark report written to {args.output}")
    for workload, result in report["results"].items():
        print(
            f"- {workload}: p95={result['rss_p95_mb']:.3f}MB "
            f"peak={result['rss_peak_mb']:.3f}MB pass={result['pass']}"
        )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
