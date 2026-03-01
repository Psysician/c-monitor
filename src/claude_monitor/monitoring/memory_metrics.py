"""Runtime RSS sampling and memory budget evaluation helpers."""

from __future__ import annotations

import math
import os
import resource
import sys
from collections import deque
from typing import Callable, Deque, Dict, Optional


def _read_rss_bytes() -> int:
    """Read current process RSS in bytes with platform fallbacks."""
    # Linux fast path
    statm_path = "/proc/self/statm"
    if os.path.exists(statm_path):
        with open(statm_path, encoding="utf-8") as f:
            parts = f.read().strip().split()
            if len(parts) >= 2:
                return int(parts[1]) * os.sysconf("SC_PAGE_SIZE")

    # Fallback to max RSS reported by getrusage.
    usage = resource.getrusage(resource.RUSAGE_SELF)
    ru_maxrss = int(usage.ru_maxrss)

    # On macOS ru_maxrss is bytes; on Linux/BSD it is kilobytes.
    if sys.platform == "darwin":
        return ru_maxrss
    return ru_maxrss * 1024


def _p95(values: list[int]) -> int:
    """Compute p95 for a non-empty integer list."""
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
    return ordered[index]


class MemoryMetricsTracker:
    """Tracks process RSS samples with bounded history."""

    def __init__(
        self,
        sample_window: int = 360,
        rss_reader: Optional[Callable[[], int]] = None,
    ) -> None:
        self._samples: Deque[int] = deque(maxlen=max(1, sample_window))
        self._rss_reader: Callable[[], int] = rss_reader or _read_rss_bytes

    def sample(self) -> int:
        """Capture one RSS sample and return the sampled value in bytes."""
        try:
            rss_bytes = max(0, int(self._rss_reader()))
        except Exception:
            rss_bytes = 0

        self._samples.append(rss_bytes)
        return rss_bytes

    def get_metrics(self) -> Dict[str, float | int]:
        """Return current/peak/p95 RSS values in bytes and MB."""
        if not self._samples:
            current = 0
            peak = 0
            p95 = 0
        else:
            values = list(self._samples)
            current = values[-1]
            peak = max(values)
            p95 = _p95(values)

        return {
            "rss_current_bytes": current,
            "rss_peak_bytes": peak,
            "rss_p95_bytes": p95,
            "rss_current_mb": round(current / (1024 * 1024), 3),
            "rss_peak_mb": round(peak / (1024 * 1024), 3),
            "rss_p95_mb": round(p95 / (1024 * 1024), 3),
            "sample_count": len(self._samples),
        }

    def sample_and_get_metrics(self) -> Dict[str, float | int]:
        """Capture a sample and return current aggregate metrics."""
        self.sample()
        return self.get_metrics()


def evaluate_memory_budget(
    memory_metrics: Dict[str, float | int], budget_mb: float = 80.0
) -> Dict[str, float | bool]:
    """Evaluate whether memory p95 stays within the configured budget."""
    p95_mb = float(memory_metrics.get("rss_p95_mb", 0.0))
    return {
        "budget_mb": float(budget_mb),
        "rss_p95_mb": p95_mb,
        "within_budget": p95_mb <= float(budget_mb),
    }
