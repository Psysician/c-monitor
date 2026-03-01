"""Tests for monitoring.data_manager module."""

from unittest.mock import Mock, patch

from claude_monitor.monitoring.data_manager import DataManager


class TestDataManager:
    """Test suite for DataManager memory-retention integration."""

    @patch("claude_monitor.monitoring.data_manager.analyze_usage")
    def test_get_data_passes_entry_retention_policy(self, mock_analyze: Mock) -> None:
        """Data manager should request bounded block entries for realtime usage."""
        mock_analyze.return_value = {"blocks": []}

        manager = DataManager(
            provider="codex",
            data_path="/tmp/data",
            max_entries_per_block=42,
            retain_entries_for_inactive_blocks=False,
        )
        result = manager.get_data(force_refresh=True)

        assert result == {"blocks": []}
        mock_analyze.assert_called_once_with(
            hours_back=192,
            quick_start=False,
            use_cache=False,
            data_path="/tmp/data",
            provider="codex",
            include_entries=True,
            max_entries_per_block=42,
            retain_entries_for_inactive_blocks=False,
        )
