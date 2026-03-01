"""Dual-provider smoke tests for realtime and table modes."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict
from unittest.mock import Mock, patch

from claude_monitor.cli.main import _run_table_view
from claude_monitor.monitoring.orchestrator import MultiProviderMonitoringOrchestrator


def _write_provider_fixture(base_dir: Path, provider: str) -> Path:
    provider_root = base_dir / provider
    session_dir = provider_root / "session-001"
    session_dir.mkdir(parents=True, exist_ok=True)
    file_path = session_dir / f"{provider}.jsonl"

    now = datetime.now(timezone.utc)
    rows = []
    for idx in range(30):
        timestamp = (now - timedelta(minutes=20) + timedelta(seconds=idx * 20)).isoformat()
        model = "gpt-5-codex" if provider == "codex" else "claude-3-5-sonnet"
        rows.append(
            {
                "timestamp": timestamp,
                "type": "assistant",
                "message": {
                    "id": f"{provider}-msg-{idx}",
                    "model": model,
                    "usage": {
                        "input_tokens": 120 + idx,
                        "output_tokens": 50 + (idx % 6),
                    },
                },
                "request_id": f"{provider}-req-{idx}",
            }
        )

    with open(file_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row))
            f.write("\n")

    return provider_root


def _build_provider_paths(tmp_path: Path) -> Dict[str, Path]:
    return {
        "claude": _write_provider_fixture(tmp_path, "claude"),
        "codex": _write_provider_fixture(tmp_path, "codex"),
    }


def test_dual_provider_realtime_smoke(tmp_path: Path) -> None:
    """Realtime merged monitoring should produce a combined payload."""
    provider_paths = _build_provider_paths(tmp_path)
    orchestrator = MultiProviderMonitoringOrchestrator(
        update_interval=1,
        provider_configs={provider: str(path) for provider, path in provider_paths.items()},
    )
    orchestrator.set_args(SimpleNamespace(plan="pro"))

    snapshot = orchestrator.force_refresh()

    assert snapshot is not None
    assert snapshot["data"]["metadata"]["provider"] == "both"
    assert set(snapshot["providers"]) == {"claude", "codex"}
    assert snapshot["data"]["total_tokens"] > 0
    assert len(snapshot["data"]["blocks"]) > 0
    assert "rss_p95_mb" in snapshot["memory"]


def test_dual_provider_table_smoke(tmp_path: Path) -> None:
    """Daily table mode should merge fixture data from both providers."""
    provider_paths = _build_provider_paths(tmp_path)
    args = SimpleNamespace(timezone="UTC", plan="pro")
    console = Mock()

    with (
        patch("claude_monitor.cli.main.TableViewsController") as mock_table_views,
        patch("claude_monitor.cli.main.signal.pause", side_effect=KeyboardInterrupt()),
        patch("claude_monitor.cli.main.print_themed"),
    ):
        _run_table_view(
            args=args,
            provider_paths=provider_paths,
            view_mode="daily",
            console=console,
        )

    mock_table_views.return_value.display_aggregated_view.assert_called_once()
    call_kwargs = mock_table_views.return_value.display_aggregated_view.call_args.kwargs
    rows = call_kwargs["data"]

    assert rows
    assert rows[0]["entries_count"] > 0
    assert rows[0]["input_tokens"] > 0
    assert {"claude-3-5-sonnet", "gpt-5-codex"}.issubset(set(rows[0]["models_used"]))
