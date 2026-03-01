"""Simplified CLI entry point using pydantic-settings."""

import argparse
import contextlib
import logging
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, NoReturn, Optional, Union

from rich.console import Console

from claude_monitor import __version__
from claude_monitor.cli.bootstrap import (
    ensure_directories,
    init_timezone,
    setup_environment,
    setup_logging,
)
from claude_monitor.core.plans import Plans, PlanType, get_token_limit
from claude_monitor.core.settings import Settings
from claude_monitor.data.aggregator import UsageAggregator
from claude_monitor.data.analysis import analyze_usage
from claude_monitor.data.provider_registry import (
    discover_provider_data_paths as discover_provider_paths_from_registry,
)
from claude_monitor.data.provider_registry import (
    get_standard_provider_paths,
)
from claude_monitor.error_handling import report_error
from claude_monitor.monitoring.orchestrator import (
    MonitoringOrchestrator,
    MultiProviderMonitoringOrchestrator,
)
from claude_monitor.terminal.manager import (
    enter_alternate_screen,
    handle_cleanup_and_exit,
    handle_error_and_exit,
    restore_terminal,
    setup_terminal,
)
from claude_monitor.terminal.themes import get_themed_console, print_themed
from claude_monitor.ui.display_controller import DisplayController
from claude_monitor.ui.table_views import TableViewsController

# Type aliases for CLI callbacks
DataUpdateCallback = Callable[[Dict[str, Any]], None]
SessionChangeCallback = Callable[[str, str, Optional[Dict[str, Any]]], None]


def get_standard_claude_paths() -> List[str]:
    """Get list of standard Claude data directory paths to check."""
    return get_standard_provider_paths("claude")


def get_standard_codex_paths() -> List[str]:
    """Get list of standard Codex data directory paths to check."""
    return get_standard_provider_paths("codex")


def discover_provider_data_paths(
    provider: str, custom_paths: Optional[List[str]] = None
) -> List[Path]:
    """Discover all available provider data directories."""
    return discover_provider_paths_from_registry(
        provider=provider, custom_paths=custom_paths
    )


def discover_claude_data_paths(custom_paths: Optional[List[str]] = None) -> List[Path]:
    """Discover all available Claude data directories.

    Args:
        custom_paths: Optional list of custom paths to check instead of standard ones

    Returns:
        List of Path objects for existing Claude data directories
    """
    return discover_provider_data_paths("claude", custom_paths=custom_paths)


def resolve_provider_data_paths(
    provider: str, provider_data_path: Optional[str] = None
) -> Dict[str, Path]:
    """Resolve provider data roots for single-provider or multi-provider mode."""
    normalized_provider = provider.strip().lower()
    providers: List[str] = (
        ["claude", "codex"] if normalized_provider == "both" else [normalized_provider]
    )
    custom_paths = [provider_data_path] if provider_data_path else None

    resolved: Dict[str, Path] = {}
    for provider_name in providers:
        discovered = discover_provider_data_paths(
            provider=provider_name,
            custom_paths=custom_paths,
        )
        if discovered:
            resolved[provider_name] = discovered[0]

    return resolved


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point with direct pydantic-settings integration."""
    if argv is None:
        argv = sys.argv[1:]

    if "--version" in argv or "-v" in argv:
        print(f"claude-monitor {__version__}")
        return 0

    try:
        settings = Settings.load_with_last_used(argv)

        setup_environment()
        ensure_directories()

        if settings.log_file:
            setup_logging(settings.log_level, settings.log_file, disable_console=True)
        else:
            setup_logging(settings.log_level, disable_console=True)

        init_timezone(settings.timezone)

        args = settings.to_namespace()

        _run_monitoring(args)

        return 0

    except KeyboardInterrupt:
        print("\n\nMonitoring stopped by user.")
        return 0
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Monitor failed: {e}", exc_info=True)
        traceback.print_exc()
        return 1


def _run_monitoring(args: argparse.Namespace) -> None:
    """Main monitoring implementation without facade."""
    view_mode = getattr(args, "view", "realtime")

    if hasattr(args, "theme") and args.theme:
        console = get_themed_console(force_theme=args.theme.lower())
    else:
        console = get_themed_console()

    old_terminal_settings = setup_terminal()
    live_display_active: bool = False

    try:
        provider = getattr(args, "provider", "claude").strip().lower()
        provider_data_path = getattr(args, "provider_data_path", None)
        provider_paths = resolve_provider_data_paths(provider, provider_data_path)

        if not provider_paths:
            print_themed(
                "No provider data directories found. Use --provider-data-path to override.",
                style="error",
            )
            return

        logger = logging.getLogger(__name__)
        for provider_name, provider_path in provider_paths.items():
            logger.info(f"Using {provider_name} data path: {provider_path}")

        if provider == "both":
            missing_providers = [
                provider_name
                for provider_name in ["claude", "codex"]
                if provider_name not in provider_paths
            ]
            if missing_providers:
                print_themed(
                    "Missing data directories for: "
                    + ", ".join(missing_providers)
                    + ". Monitoring available providers only.",
                    style="warning",
                )

        # Handle different view modes
        if view_mode in ["daily", "monthly"]:
            _run_table_view(args, provider_paths, view_mode, console)
            return

        token_limit: int = _get_initial_token_limit_for_paths(
            args=args,
            provider_paths=provider_paths,
        )
        memory_budget_mb = float(getattr(args, "memory_budget_mb", 80.0))
        max_entries_per_block = int(getattr(args, "max_entries_per_block", 200))
        retain_entries_for_inactive_blocks = bool(
            getattr(args, "retain_entries_for_inactive_blocks", False)
        )

        display_controller = DisplayController()
        display_controller.live_manager._console = console

        refresh_per_second: float = getattr(args, "refresh_per_second", 0.75)
        logger.info(
            f"Display refresh rate: {refresh_per_second} Hz ({1000 / refresh_per_second:.0f}ms)"
        )
        logger.info(f"Data refresh rate: {args.refresh_rate} seconds")

        live_display = display_controller.live_manager.create_live_display(
            auto_refresh=True, console=console, refresh_per_second=refresh_per_second
        )

        loading_display = display_controller.create_loading_display(
            args.plan, args.timezone
        )

        enter_alternate_screen()

        live_display_active = False

        try:
            # Enter live context and show loading screen immediately
            live_display.__enter__()
            live_display_active = True
            live_display.update(loading_display)

            update_interval = args.refresh_rate if hasattr(args, "refresh_rate") else 10
            if provider == "both":
                orchestrator = MultiProviderMonitoringOrchestrator(
                    update_interval=update_interval,
                    provider_configs={
                        provider_name: str(path)
                        for provider_name, path in provider_paths.items()
                    },
                    memory_budget_mb=memory_budget_mb,
                    max_entries_per_block=max_entries_per_block,
                    retain_entries_for_inactive_blocks=(
                        retain_entries_for_inactive_blocks
                    ),
                )
            else:
                data_path = provider_paths[provider]
                orchestrator = MonitoringOrchestrator(
                    update_interval=update_interval,
                    data_path=str(data_path),
                    provider=provider,
                    memory_budget_mb=memory_budget_mb,
                    max_entries_per_block=max_entries_per_block,
                    retain_entries_for_inactive_blocks=(
                        retain_entries_for_inactive_blocks
                    ),
                )
            orchestrator.set_args(args)

            # Setup monitoring callback
            def on_data_update(monitoring_data: Dict[str, Any]) -> None:
                """Handle data updates from orchestrator."""
                try:
                    data: Dict[str, Any] = monitoring_data.get("data", {})
                    blocks: List[Dict[str, Any]] = data.get("blocks", [])

                    logger.debug(f"Display data has {len(blocks)} blocks")
                    if blocks:
                        active_blocks: List[Dict[str, Any]] = [
                            b for b in blocks if b.get("isActive")
                        ]
                        logger.debug(f"Active blocks: {len(active_blocks)}")
                        if active_blocks:
                            total_tokens: int = active_blocks[0].get("totalTokens", 0)
                            logger.debug(f"Active block tokens: {total_tokens}")

                    renderable = display_controller.create_data_display(
                        data, args, monitoring_data.get("token_limit", token_limit)
                    )

                    if live_display:
                        live_display.update(renderable)

                except Exception as e:
                    logger.error(f"Display update error: {e}", exc_info=True)
                    report_error(
                        exception=e,
                        component="cli_main",
                        context_name="display_update_error",
                    )

            # Register callbacks
            orchestrator.register_update_callback(on_data_update)

            # Optional: Register session change callback
            def on_session_change(
                event_type: str, session_id: str, session_data: Optional[Dict[str, Any]]
            ) -> None:
                """Handle session changes."""
                if event_type == "session_start":
                    logger.info(f"New session detected: {session_id}")
                elif event_type == "session_end":
                    logger.info(f"Session ended: {session_id}")

            orchestrator.register_session_callback(on_session_change)

            # Start monitoring
            orchestrator.start()

            # Wait for initial data
            logger.info("Waiting for initial data...")
            if not orchestrator.wait_for_initial_data(timeout=10.0):
                logger.warning("Timeout waiting for initial data")

            # Main loop - live display is already active
            # Use signal.pause() for more efficient waiting
            try:
                signal.pause()
            except AttributeError:
                # Fallback for Windows which doesn't support signal.pause()
                while True:
                    time.sleep(1)
        finally:
            # Stop monitoring first
            if "orchestrator" in locals():
                orchestrator.stop()

            # Exit live display context if it was activated
            if live_display_active:
                with contextlib.suppress(Exception):
                    live_display.__exit__(None, None, None)

    except KeyboardInterrupt:
        # Clean exit from live display if it's active
        if "live_display" in locals():
            with contextlib.suppress(Exception):
                live_display.__exit__(None, None, None)
        handle_cleanup_and_exit(old_terminal_settings)
    except Exception as e:
        # Clean exit from live display if it's active
        if "live_display" in locals():
            with contextlib.suppress(Exception):
                live_display.__exit__(None, None, None)
        handle_error_and_exit(old_terminal_settings, e)
    finally:
        restore_terminal(old_terminal_settings)


def _get_initial_token_limit(
    args: argparse.Namespace, data_path: Union[str, Path], provider: str = "claude"
) -> int:
    """Get initial token limit for the plan."""
    logger = logging.getLogger(__name__)
    plan: str = getattr(args, "plan", PlanType.PRO.value)

    # For custom plans, check if custom_limit_tokens is provided first
    if plan == "custom":
        # If custom_limit_tokens is explicitly set, use it
        if hasattr(args, "custom_limit_tokens") and args.custom_limit_tokens:
            custom_limit = int(args.custom_limit_tokens)
            print_themed(
                f"Using custom token limit: {custom_limit:,} tokens",
                style="info",
            )
            return custom_limit

        # Otherwise, analyze usage data to calculate P90
        print_themed("Analyzing usage data to determine cost limits...", style="info")

        try:
            # Use quick start mode for faster initial load
            usage_data: Optional[Dict[str, Any]] = analyze_usage(
                hours_back=96 * 2,
                quick_start=False,
                use_cache=False,
                data_path=str(data_path),
                provider=provider,
            )

            if usage_data and "blocks" in usage_data:
                blocks: List[Dict[str, Any]] = usage_data["blocks"]
                token_limit: int = get_token_limit(plan, blocks)

                print_themed(
                    f"P90 session limit calculated: {token_limit:,} tokens",
                    style="info",
                )

                return token_limit

        except Exception as e:
            logger.warning(f"Failed to analyze usage data: {e}")

        # Fallback to default limit
        print_themed("Using default limit as fallback", style="warning")
        return Plans.DEFAULT_TOKEN_LIMIT

    # For standard plans, just get the limit
    return get_token_limit(plan)


def _get_initial_token_limit_for_paths(
    args: argparse.Namespace, provider_paths: Dict[str, Path]
) -> int:
    """Get startup token limit for one or more provider data paths."""
    if not provider_paths:
        return Plans.DEFAULT_TOKEN_LIMIT

    plan: str = getattr(args, "plan", PlanType.PRO.value)
    if plan != "custom":
        return get_token_limit(plan)

    if hasattr(args, "custom_limit_tokens") and args.custom_limit_tokens:
        return int(args.custom_limit_tokens)

    if len(provider_paths) == 1:
        provider, path = next(iter(provider_paths.items()))
        return _get_initial_token_limit(args, str(path), provider=provider)

    print_themed(
        "Analyzing usage data to determine multi-provider cost limits...",
        style="info",
    )
    logger = logging.getLogger(__name__)
    merged_blocks: List[Dict[str, Any]] = []

    for provider, path in provider_paths.items():
        try:
            usage_data = analyze_usage(
                hours_back=96 * 2,
                quick_start=False,
                use_cache=False,
                data_path=str(path),
                provider=provider,
            )
            blocks = usage_data.get("blocks", []) if usage_data else []
            if isinstance(blocks, list):
                merged_blocks.extend(blocks)
        except Exception as e:
            logger.warning(f"Failed to analyze usage data for provider {provider}: {e}")

    if merged_blocks:
        token_limit = get_token_limit(plan, merged_blocks)
        print_themed(
            f"Multi-provider P90 session limit calculated: {token_limit:,} tokens",
            style="info",
        )
        return token_limit

    print_themed("Using default limit as fallback", style="warning")
    return Plans.DEFAULT_TOKEN_LIMIT


def handle_application_error(
    exception: Exception,
    component: str = "cli_main",
    exit_code: int = 1,
) -> NoReturn:
    """Handle application-level errors with proper logging and exit.

    Args:
        exception: The exception that occurred
        component: Component where the error occurred
        exit_code: Exit code to use when terminating
    """
    logger = logging.getLogger(__name__)

    # Log the error with traceback
    logger.error(f"Application error in {component}: {exception}", exc_info=True)

    # Report to error handling system
    from claude_monitor.error_handling import report_application_startup_error

    report_application_startup_error(
        exception=exception,
        component=component,
        additional_context={
            "exit_code": exit_code,
            "args": sys.argv,
        },
    )

    # Print user-friendly error message
    print(f"\nError: {exception}", file=sys.stderr)
    print("For more details, check the log files.", file=sys.stderr)

    sys.exit(exit_code)


def validate_cli_environment() -> Optional[str]:
    """Validate the CLI environment and return error message if invalid.

    Returns:
        Error message if validation fails, None if successful
    """
    try:
        # Check Python version compatibility
        if sys.version_info < (3, 8):
            return f"Python 3.8+ required, found {sys.version_info.major}.{sys.version_info.minor}"

        # Check for required dependencies
        required_modules = ["rich", "pydantic", "watchdog"]
        missing_modules: List[str] = []

        for module in required_modules:
            try:
                __import__(module)
            except ImportError:
                missing_modules.append(module)

        if missing_modules:
            return f"Missing required modules: {', '.join(missing_modules)}"

        return None

    except Exception as e:
        return f"Environment validation failed: {e}"


def _run_table_view(
    args: argparse.Namespace,
    provider_paths: Dict[str, Path],
    view_mode: str,
    console: Console,
) -> None:
    """Run table view mode (daily/monthly)."""
    logger = logging.getLogger(__name__)

    try:
        provider_aggregates: Dict[str, List[Dict[str, Any]]] = {}
        for provider, data_path in provider_paths.items():
            aggregator = UsageAggregator(
                data_path=str(data_path),
                aggregation_mode=view_mode,
                timezone=args.timezone,
                provider=provider,
            )
            logger.info(f"Loading {view_mode} usage data for provider={provider}...")
            provider_data = aggregator.aggregate()
            if provider_data:
                provider_aggregates[provider] = provider_data

        if not provider_aggregates:
            print_themed(f"No usage data found for {view_mode} view", style="warning")
            return

        if len(provider_aggregates) == 1:
            aggregated_data = next(iter(provider_aggregates.values()))
        else:
            aggregated_data = _merge_aggregated_period_data(
                provider_aggregates=provider_aggregates,
                view_mode=view_mode,
            )

        # Create table controller
        controller = TableViewsController(console=console)

        # Display the table
        controller.display_aggregated_view(
            data=aggregated_data,
            view_mode=view_mode,
            timezone=args.timezone,
            plan=args.plan,
            token_limit=_get_initial_token_limit_for_paths(
                args=args,
                provider_paths=provider_paths,
            ),
        )

        # Wait for user to press Ctrl+C
        print_themed("\nPress Ctrl+C to exit", style="info")
        try:
            # Use signal.pause() for more efficient waiting
            try:
                signal.pause()
            except AttributeError:
                # Fallback for Windows which doesn't support signal.pause()
                while True:
                    time.sleep(1)
        except KeyboardInterrupt:
            print_themed("\nExiting...", style="info")

    except Exception as e:
        logger.error(f"Error in table view: {e}", exc_info=True)
        print_themed(f"Error displaying {view_mode} view: {e}", style="error")


def _merge_aggregated_period_data(
    provider_aggregates: Dict[str, List[Dict[str, Any]]], view_mode: str
) -> List[Dict[str, Any]]:
    """Merge per-provider aggregated daily/monthly rows into one timeline."""
    period_key = "date" if view_mode == "daily" else "month"
    merged_periods: Dict[str, Dict[str, Any]] = {}

    for provider_data in provider_aggregates.values():
        for row in provider_data:
            period_value = row.get(period_key)
            if not period_value:
                continue
            period = str(period_value)

            if period not in merged_periods:
                merged_periods[period] = {
                    period_key: period,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 0,
                    "total_cost": 0.0,
                    "models_used": set(),
                    "model_breakdowns": {},
                    "entries_count": 0,
                }

            merged_row = merged_periods[period]
            merged_row["input_tokens"] += int(row.get("input_tokens", 0) or 0)
            merged_row["output_tokens"] += int(row.get("output_tokens", 0) or 0)
            merged_row["cache_creation_tokens"] += int(
                row.get("cache_creation_tokens", 0) or 0
            )
            merged_row["cache_read_tokens"] += int(
                row.get("cache_read_tokens", 0) or 0
            )
            merged_row["total_cost"] += float(row.get("total_cost", 0.0) or 0.0)
            merged_row["entries_count"] += int(row.get("entries_count", 0) or 0)

            models = row.get("models_used", [])
            if isinstance(models, list):
                merged_row["models_used"].update(models)

            model_breakdowns = row.get("model_breakdowns", {})
            if not isinstance(model_breakdowns, dict):
                continue

            for model, stats in model_breakdowns.items():
                if not isinstance(stats, dict):
                    continue
                merged_stats = merged_row["model_breakdowns"].setdefault(
                    model,
                    {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_creation_tokens": 0,
                        "cache_read_tokens": 0,
                        "cost": 0.0,
                        "count": 0,
                    },
                )
                merged_stats["input_tokens"] += int(stats.get("input_tokens", 0) or 0)
                merged_stats["output_tokens"] += int(
                    stats.get("output_tokens", 0) or 0
                )
                merged_stats["cache_creation_tokens"] += int(
                    stats.get("cache_creation_tokens", 0) or 0
                )
                merged_stats["cache_read_tokens"] += int(
                    stats.get("cache_read_tokens", 0) or 0
                )
                merged_stats["cost"] += float(stats.get("cost", 0.0) or 0.0)
                merged_stats["count"] += int(stats.get("count", 0) or 0)

    merged_data: List[Dict[str, Any]] = []
    for period in sorted(merged_periods.keys()):
        merged_row = merged_periods[period]
        models_used = merged_row["models_used"]
        merged_row["models_used"] = (
            sorted(models_used) if isinstance(models_used, set) else []
        )
        merged_data.append(merged_row)

    return merged_data


if __name__ == "__main__":
    sys.exit(main())
