"""Tests for provider registry adapter and discovery behavior."""

import json
from pathlib import Path

import pytest

from claude_monitor.data.provider_registry import (
    discover_provider_data_paths,
    get_provider_adapter,
    get_standard_provider_paths,
    normalize_provider,
)


def test_normalize_provider_accepts_supported_values() -> None:
    assert normalize_provider("claude") == "claude"
    assert normalize_provider("CoDeX") == "codex"


def test_normalize_provider_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="Unsupported provider"):
        normalize_provider("unknown")


def test_get_standard_provider_paths() -> None:
    claude_paths = get_standard_provider_paths("claude")
    codex_paths = get_standard_provider_paths("codex")
    assert "~/.claude/projects" in claude_paths
    assert "~/.codex/sessions" in codex_paths


def test_discover_provider_data_paths_custom(tmp_path: Path) -> None:
    custom_dir = tmp_path / "provider-data"
    custom_dir.mkdir()
    discovered = discover_provider_data_paths("claude", [str(custom_dir)])
    assert discovered == [custom_dir.resolve()]


def test_adapter_iterates_and_normalizes_records(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.jsonl"
    rows = [
        {
            "timestamp": "2026-03-01T01:00:00Z",
            "message": {"id": "msg-1", "model": "claude-3-5-sonnet"},
            "requestId": "req-1",
            "type": "assistant",
        },
        {
            "timestamp": "2026-03-01T01:01:00Z",
            "payload": {"id": "evt-2", "info": {"model": "gpt-5.1-codex"}},
            "type": "event_msg",
        },
    ]
    file_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    adapter = get_provider_adapter("codex")
    normalized = list(adapter.iter_normalized_records(file_path))

    assert len(normalized) == 2
    assert normalized[0]["provider"] == "codex"
    assert normalized[0]["message_id"] == "msg-1"
    assert normalized[0]["request_id"] == "req-1"
    assert normalized[0]["model"] == "claude-3-5-sonnet"
    assert normalized[1]["provider"] == "codex"
    assert normalized[1]["request_id"] == "evt-2"
    assert normalized[1]["model"] == "gpt-5.1-codex"


def test_adapter_iter_jsonl_files(tmp_path: Path) -> None:
    adapter = get_provider_adapter("claude")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "one.jsonl").write_text("{}", encoding="utf-8")
    (nested / "two.txt").write_text("x", encoding="utf-8")

    found = list(adapter.iter_jsonl_files(tmp_path))
    assert (nested / "one.jsonl") in found
    assert all(path.suffix == ".jsonl" for path in found)
