"""Tests for monitoring.memory_metrics module."""

from claude_monitor.monitoring.memory_metrics import (
    MemoryMetricsTracker,
    _p95,
    evaluate_memory_budget,
)


def test_p95_basic() -> None:
    """p95 should select high-end percentile element."""
    assert _p95([1, 2, 3, 4, 5]) == 5
    assert _p95([100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]) == 1000


def test_memory_metrics_tracker_window_and_metrics() -> None:
    """Tracker should keep bounded samples and expose expected metrics."""
    values = [10, 20, 30, 40, 50]
    tracker = MemoryMetricsTracker(
        sample_window=3,
        rss_reader=lambda: values.pop(0),
    )

    tracker.sample()
    tracker.sample()
    tracker.sample()
    tracker.sample()

    metrics = tracker.get_metrics()
    assert metrics["sample_count"] == 3
    assert metrics["rss_current_bytes"] == 40
    assert metrics["rss_peak_bytes"] == 40
    assert metrics["rss_p95_bytes"] == 40


def test_evaluate_memory_budget() -> None:
    """Budget evaluation should flag out-of-budget p95."""
    result = evaluate_memory_budget({"rss_p95_mb": 81.0}, budget_mb=80.0)
    assert result["within_budget"] is False
    assert result["budget_mb"] == 80.0
