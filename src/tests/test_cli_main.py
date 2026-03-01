"""Simplified tests for CLI main module."""

from pathlib import Path
from typing import List
from unittest.mock import Mock, patch

from claude_monitor.cli.main import main


class TestMain:
    """Test cases for main function."""

    def test_version_flag(self) -> None:
        """Test --version flag returns 0 and prints version."""
        with patch("builtins.print") as mock_print:
            result = main(["--version"])
            assert result == 0
            mock_print.assert_called_once()
            assert "claude-monitor" in mock_print.call_args[0][0]

    def test_v_flag(self) -> None:
        """Test -v flag returns 0 and prints version."""
        with patch("builtins.print") as mock_print:
            result = main(["-v"])
            assert result == 0
            mock_print.assert_called_once()
            assert "claude-monitor" in mock_print.call_args[0][0]

    @patch("claude_monitor.core.settings.Settings.load_with_last_used")
    def test_keyboard_interrupt_handling(self, mock_load: Mock) -> None:
        """Test keyboard interrupt returns 0."""
        mock_load.side_effect = KeyboardInterrupt()
        with patch("builtins.print") as mock_print:
            result = main(["--plan", "pro"])
            assert result == 0
            mock_print.assert_called_once_with("\n\nMonitoring stopped by user.")

    @patch("claude_monitor.core.settings.Settings.load_with_last_used")
    def test_exception_handling(self, mock_load_settings: Mock) -> None:
        """Test exception handling returns 1."""
        mock_load_settings.side_effect = Exception("Test error")

        with patch("builtins.print"), patch("traceback.print_exc"):
            result = main(["--plan", "pro"])
            assert result == 1

    @patch("claude_monitor.core.settings.Settings.load_with_last_used")
    def test_successful_main_execution(self, mock_load_settings: Mock) -> None:
        """Test successful main execution by mocking core components."""
        mock_args = Mock()
        mock_args.theme = None
        mock_args.plan = "pro"
        mock_args.timezone = "UTC"
        mock_args.refresh_per_second = 1.0
        mock_args.refresh_rate = 10
        mock_args.provider = "claude"
        mock_args.provider_data_path = None
        mock_args.memory_budget_mb = 77.0
        mock_args.max_entries_per_block = 321
        mock_args.retain_entries_for_inactive_blocks = True

        mock_settings = Mock()
        mock_settings.log_file = None
        mock_settings.log_level = "INFO"
        mock_settings.timezone = "UTC"
        mock_settings.to_namespace.return_value = mock_args

        mock_load_settings.return_value = mock_settings

        # Get the actual module to avoid Python version compatibility issues with mock.patch
        import sys

        actual_module = sys.modules["claude_monitor.cli.main"]

        # Manually replace the function - this works across all Python versions
        original_discover = actual_module.discover_provider_data_paths
        actual_module.discover_provider_data_paths = Mock(
            return_value=[Path("/test/path")]
        )

        try:
            with (
                patch("claude_monitor.terminal.manager.setup_terminal"),
                patch("claude_monitor.terminal.themes.get_themed_console"),
                patch("claude_monitor.ui.display_controller.DisplayController"),
                patch(
                    "claude_monitor.cli.main.MonitoringOrchestrator"
                ) as mock_orchestrator,
                patch("signal.pause", side_effect=KeyboardInterrupt()),
                patch("time.sleep", side_effect=KeyboardInterrupt()),
                patch("sys.exit"),
            ):  # Don't actually exit
                # Configure mocks to not interfere with the KeyboardInterrupt
                mock_orchestrator.return_value.wait_for_initial_data.return_value = True
                mock_orchestrator.return_value.start.return_value = None
                mock_orchestrator.return_value.stop.return_value = None

                result = main(["--plan", "pro"])
                assert result == 0
                mock_orchestrator.assert_called_once_with(
                    update_interval=10,
                    data_path="/test/path",
                    provider="claude",
                    memory_budget_mb=77.0,
                    max_entries_per_block=321,
                    retain_entries_for_inactive_blocks=True,
                )
        finally:
            # Restore the original function
            actual_module.discover_provider_data_paths = original_discover

    @patch("claude_monitor.core.settings.Settings.load_with_last_used")
    def test_successful_main_execution_both_provider(
        self, mock_load_settings: Mock
    ) -> None:
        """Realtime startup should route to multi-provider orchestrator for provider=both."""
        mock_args = Mock()
        mock_args.theme = None
        mock_args.plan = "pro"
        mock_args.timezone = "UTC"
        mock_args.refresh_per_second = 1.0
        mock_args.refresh_rate = 10
        mock_args.provider = "both"
        mock_args.provider_data_path = None
        mock_args.memory_budget_mb = 66.0
        mock_args.max_entries_per_block = 111
        mock_args.retain_entries_for_inactive_blocks = False

        mock_settings = Mock()
        mock_settings.log_file = None
        mock_settings.log_level = "INFO"
        mock_settings.timezone = "UTC"
        mock_settings.to_namespace.return_value = mock_args
        mock_load_settings.return_value = mock_settings

        import sys

        actual_module = sys.modules["claude_monitor.cli.main"]
        original_discover = actual_module.discover_provider_data_paths

        def discover_side_effect(provider: str, custom_paths: object = None) -> List[Path]:
            _ = custom_paths
            if provider == "claude":
                return [Path("/test/claude")]
            if provider == "codex":
                return [Path("/test/codex")]
            return []

        actual_module.discover_provider_data_paths = Mock(side_effect=discover_side_effect)

        try:
            with (
                patch("claude_monitor.terminal.manager.setup_terminal"),
                patch("claude_monitor.terminal.themes.get_themed_console"),
                patch("claude_monitor.ui.display_controller.DisplayController"),
                patch(
                    "claude_monitor.cli.main.MultiProviderMonitoringOrchestrator"
                ) as mock_multi,
                patch("signal.pause", side_effect=KeyboardInterrupt()),
                patch("time.sleep", side_effect=KeyboardInterrupt()),
                patch("sys.exit"),
            ):
                mock_multi.return_value.wait_for_initial_data.return_value = True
                mock_multi.return_value.start.return_value = None
                mock_multi.return_value.stop.return_value = None

                result = main(["--plan", "pro", "--provider", "both"])
                assert result == 0
                mock_multi.assert_called_once_with(
                    update_interval=10,
                    provider_configs={
                        "claude": "/test/claude",
                        "codex": "/test/codex",
                    },
                    memory_budget_mb=66.0,
                    max_entries_per_block=111,
                    retain_entries_for_inactive_blocks=False,
                )
        finally:
            actual_module.discover_provider_data_paths = original_discover


class TestFunctions:
    """Test module functions."""

    def test_get_standard_claude_paths(self) -> None:
        """Test getting standard Claude paths."""
        from claude_monitor.cli.main import get_standard_claude_paths

        paths = get_standard_claude_paths()
        assert isinstance(paths, list)
        assert len(paths) > 0
        assert "~/.claude/projects" in paths

    def test_get_standard_codex_paths(self) -> None:
        """Test getting standard Codex paths."""
        from claude_monitor.cli.main import get_standard_codex_paths

        paths = get_standard_codex_paths()
        assert isinstance(paths, list)
        assert "~/.codex/sessions" in paths

    def test_discover_claude_data_paths_no_paths(self) -> None:
        """Test discover with no existing paths."""
        from claude_monitor.cli.main import discover_claude_data_paths

        with patch("pathlib.Path.exists", return_value=False):
            paths = discover_claude_data_paths()
            assert paths == []

    def test_discover_claude_data_paths_with_custom(self) -> None:
        """Test discover with custom paths."""
        from claude_monitor.cli.main import discover_claude_data_paths

        custom_paths = ["/custom/path"]
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.is_dir", return_value=True),
        ):
            paths = discover_claude_data_paths(custom_paths)
            assert len(paths) == 1
            assert paths[0].name == "path"

    def test_resolve_provider_data_paths_single_provider(self) -> None:
        """Test provider path resolution for a single provider."""
        from claude_monitor.cli.main import resolve_provider_data_paths

        with patch(
            "claude_monitor.cli.main.discover_provider_data_paths",
            return_value=[Path("/tmp/claude")],
        ) as mock_discover:
            paths = resolve_provider_data_paths("claude")

        assert paths == {"claude": Path("/tmp/claude")}
        mock_discover.assert_called_once_with(provider="claude", custom_paths=None)

    def test_resolve_provider_data_paths_both(self) -> None:
        """Test provider path resolution in multi-provider mode."""
        from claude_monitor.cli.main import resolve_provider_data_paths

        def discover_side_effect(
            provider: str, custom_paths: object = None
        ) -> List[Path]:
            _ = custom_paths
            if provider == "claude":
                return [Path("/tmp/claude")]
            if provider == "codex":
                return [Path("/tmp/codex")]
            return []

        with patch(
            "claude_monitor.cli.main.discover_provider_data_paths",
            side_effect=discover_side_effect,
        ) as mock_discover:
            paths = resolve_provider_data_paths("both")

        assert paths == {
            "claude": Path("/tmp/claude"),
            "codex": Path("/tmp/codex"),
        }
        assert mock_discover.call_count == 2

    def test_get_initial_token_limit_for_paths_custom_multi_provider(self) -> None:
        """Test custom plan startup token limit from merged provider blocks."""
        from claude_monitor.cli.main import _get_initial_token_limit_for_paths

        args = Mock()
        args.plan = "custom"
        args.custom_limit_tokens = None

        provider_paths = {
            "claude": Path("/tmp/claude"),
            "codex": Path("/tmp/codex"),
        }

        with (
            patch(
                "claude_monitor.cli.main.analyze_usage",
                side_effect=[
                    {"blocks": [{"id": "claude-block"}]},
                    {"blocks": [{"id": "codex-block"}]},
                ],
            ) as mock_analyze,
            patch(
                "claude_monitor.cli.main.get_token_limit",
                return_value=12345,
            ) as mock_limit,
        ):
            token_limit = _get_initial_token_limit_for_paths(args, provider_paths)

        assert token_limit == 12345
        assert mock_analyze.call_count == 2
        mock_limit.assert_called_once_with(
            "custom",
            [{"id": "claude-block"}, {"id": "codex-block"}],
        )

    def test_merge_aggregated_period_data_daily(self) -> None:
        """Test merging daily table rows from multiple providers."""
        from claude_monitor.cli.main import _merge_aggregated_period_data

        provider_aggregates = {
            "claude": [
                {
                    "date": "2026-03-01",
                    "input_tokens": 100,
                    "output_tokens": 40,
                    "cache_creation_tokens": 10,
                    "cache_read_tokens": 5,
                    "total_cost": 0.5,
                    "models_used": ["claude-3-5-sonnet"],
                    "model_breakdowns": {
                        "claude-3-5-sonnet": {
                            "input_tokens": 100,
                            "output_tokens": 40,
                            "cache_creation_tokens": 10,
                            "cache_read_tokens": 5,
                            "cost": 0.5,
                            "count": 2,
                        }
                    },
                    "entries_count": 2,
                }
            ],
            "codex": [
                {
                    "date": "2026-03-01",
                    "input_tokens": 70,
                    "output_tokens": 30,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 0,
                    "total_cost": 0.25,
                    "models_used": ["gpt-5-codex"],
                    "model_breakdowns": {
                        "gpt-5-codex": {
                            "input_tokens": 70,
                            "output_tokens": 30,
                            "cache_creation_tokens": 0,
                            "cache_read_tokens": 0,
                            "cost": 0.25,
                            "count": 1,
                        }
                    },
                    "entries_count": 1,
                }
            ],
        }

        merged = _merge_aggregated_period_data(
            provider_aggregates=provider_aggregates,
            view_mode="daily",
        )

        assert len(merged) == 1
        day = merged[0]
        assert day["date"] == "2026-03-01"
        assert day["input_tokens"] == 170
        assert day["output_tokens"] == 70
        assert day["cache_creation_tokens"] == 10
        assert day["cache_read_tokens"] == 5
        assert day["entries_count"] == 3
        assert day["total_cost"] == 0.75
        assert set(day["models_used"]) == {"claude-3-5-sonnet", "gpt-5-codex"}
