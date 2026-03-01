"""Orchestrator for monitoring components."""

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from claude_monitor.core.plans import DEFAULT_TOKEN_LIMIT, get_token_limit
from claude_monitor.error_handling import report_error
from claude_monitor.monitoring.data_manager import DataManager
from claude_monitor.monitoring.session_monitor import SessionMonitor

logger = logging.getLogger(__name__)


class MonitoringOrchestrator:
    """Orchestrates monitoring components following SRP."""

    def __init__(
        self,
        update_interval: int = 10,
        data_path: Optional[str] = None,
        provider: str = "claude",
    ) -> None:
        """Initialize orchestrator with components.

        Args:
            update_interval: Seconds between updates
            data_path: Optional path to provider data directory
            provider: Provider name (claude or codex)
        """
        self.update_interval: int = update_interval

        self.data_manager: DataManager = DataManager(
            cache_ttl=5, data_path=data_path, provider=provider
        )
        self.session_monitor: SessionMonitor = SessionMonitor()

        self._monitoring: bool = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event: threading.Event = threading.Event()
        self._update_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._last_valid_data: Optional[Dict[str, Any]] = None
        self._args: Optional[Any] = None
        self._first_data_event: threading.Event = threading.Event()

    def start(self) -> None:
        """Start monitoring."""
        if self._monitoring:
            logger.warning("Monitoring already running")
            return

        logger.info(f"Starting monitoring with {self.update_interval}s interval")
        self._monitoring = True
        self._stop_event.clear()

        # Start monitoring thread
        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop, name="MonitoringThread", daemon=True
        )
        self._monitor_thread.start()

    def stop(self) -> None:
        """Stop monitoring."""
        if not self._monitoring:
            return

        logger.info("Stopping monitoring")
        self._monitoring = False
        self._stop_event.set()

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)

        self._monitor_thread = None
        self._first_data_event.clear()

    def set_args(self, args: Any) -> None:
        """Set command line arguments for token limit calculation.

        Args:
            args: Command line arguments
        """
        self._args = args

    def register_update_callback(
        self, callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Register callback for data updates.

        Args:
            callback: Function to call with monitoring data
        """
        if callback not in self._update_callbacks:
            self._update_callbacks.append(callback)
            logger.debug("Registered update callback")

    def register_session_callback(
        self, callback: Callable[[str, str, Optional[Dict[str, Any]]], None]
    ) -> None:
        """Register callback for session changes.

        Args:
            callback: Function(event_type, session_id, session_data)
        """
        self.session_monitor.register_callback(callback)

    def force_refresh(self) -> Optional[Dict[str, Any]]:
        """Force immediate data refresh.

        Returns:
            Fresh data or None if fetch fails
        """
        return self._fetch_and_process_data(force_refresh=True)

    def wait_for_initial_data(self, timeout: float = 10.0) -> bool:
        """Wait for initial data to be fetched.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if data was received, False if timeout
        """
        return self._first_data_event.wait(timeout=timeout)

    def _monitoring_loop(self) -> None:
        """Main monitoring loop."""
        logger.info("Monitoring loop started")

        # Initial fetch
        self._fetch_and_process_data()

        while self._monitoring:
            # Wait for interval or stop
            if self._stop_event.wait(timeout=self.update_interval):
                if not self._monitoring:
                    break

            # Fetch and process
            self._fetch_and_process_data()

        logger.info("Monitoring loop ended")

    def _fetch_and_process_data(
        self, force_refresh: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Fetch data and notify callbacks.

        Args:
            force_refresh: Force cache refresh

        Returns:
            Processed data or None if failed
        """
        try:
            # Fetch data
            start_time: float = time.time()
            data: Optional[Dict[str, Any]] = self.data_manager.get_data(
                force_refresh=force_refresh
            )

            if data is None:
                logger.warning("No data fetched")
                return None

            # Validate and update session tracking
            is_valid: bool
            errors: List[str]
            is_valid, errors = self.session_monitor.update(data)
            if not is_valid:
                logger.error(f"Data validation failed: {errors}")
                return None

            # Calculate token limit
            token_limit: int = self._calculate_token_limit(data)

            # Prepare monitoring data
            monitoring_data: Dict[str, Any] = {
                "data": data,
                "token_limit": token_limit,
                "args": self._args,
                "session_id": self.session_monitor.current_session_id,
                "session_count": self.session_monitor.session_count,
            }

            # Store last valid data
            self._last_valid_data = monitoring_data

            # Signal that first data has been received
            if not self._first_data_event.is_set():
                self._first_data_event.set()

            # Notify callbacks
            for callback in self._update_callbacks:
                try:
                    callback(monitoring_data)
                except Exception as e:
                    logger.error(f"Callback error: {e}", exc_info=True)
                    report_error(
                        exception=e,
                        component="orchestrator",
                        context_name="callback_error",
                    )

            elapsed: float = time.time() - start_time
            logger.debug(f"Data processing completed in {elapsed:.3f}s")

            return monitoring_data

        except Exception as e:
            logger.error(f"Error in monitoring cycle: {e}", exc_info=True)
            report_error(
                exception=e, component="orchestrator", context_name="monitoring_cycle"
            )
            return None

    def _calculate_token_limit(self, data: Dict[str, Any]) -> int:
        """Calculate token limit based on plan and data.

        Args:
            data: Monitoring data

        Returns:
            Token limit
        """
        if not self._args:
            return DEFAULT_TOKEN_LIMIT

        plan: str = getattr(self._args, "plan", "pro")

        try:
            if plan == "custom":
                blocks: List[Any] = data.get("blocks", [])
                return get_token_limit(plan, blocks)
            return get_token_limit(plan)
        except Exception as e:
            logger.exception(f"Error calculating token limit: {e}")
            return DEFAULT_TOKEN_LIMIT


class MultiProviderMonitoringOrchestrator:
    """Coordinates multiple provider orchestrators and emits merged updates."""

    def __init__(
        self,
        update_interval: int = 10,
        provider_configs: Optional[Dict[str, Optional[str]]] = None,
    ) -> None:
        """Initialize child orchestrators for each configured provider.

        Args:
            update_interval: Seconds between updates
            provider_configs: Mapping of provider name to optional data path
        """
        self.update_interval = update_interval
        self.provider_configs: Dict[str, Optional[str]] = (
            provider_configs if provider_configs is not None else {"claude": None}
        )

        self.orchestrators: Dict[str, MonitoringOrchestrator] = {}
        self._update_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._provider_latest_data: Dict[str, Dict[str, Any]] = {}
        self._first_data_event: threading.Event = threading.Event()
        self._last_valid_data: Optional[Dict[str, Any]] = None
        self._args: Optional[Any] = None
        self._monitoring: bool = False

        def _build_provider_callback(
            provider_name: str,
        ) -> Callable[[Dict[str, Any]], None]:
            def _callback(monitoring_data: Dict[str, Any]) -> None:
                self._handle_provider_update(provider_name, monitoring_data)

            return _callback

        for provider, data_path in self.provider_configs.items():
            provider_orchestrator = MonitoringOrchestrator(
                update_interval=update_interval,
                data_path=data_path,
                provider=provider,
            )
            provider_orchestrator.register_update_callback(
                _build_provider_callback(provider)
            )
            self.orchestrators[provider] = provider_orchestrator

    def start(self) -> None:
        """Start all provider orchestrators."""
        if self._monitoring:
            logger.warning("Multi-provider monitoring already running")
            return

        self._monitoring = True
        for provider, orchestrator in self.orchestrators.items():
            logger.info(f"Starting provider monitoring: {provider}")
            orchestrator.start()

    def stop(self) -> None:
        """Stop all provider orchestrators."""
        if not self._monitoring:
            return

        self._monitoring = False
        for provider, orchestrator in self.orchestrators.items():
            logger.info(f"Stopping provider monitoring: {provider}")
            orchestrator.stop()
        self._first_data_event.clear()

    def set_args(self, args: Any) -> None:
        """Set command line arguments for token limit calculation."""
        self._args = args
        for orchestrator in self.orchestrators.values():
            orchestrator.set_args(args)

    def register_update_callback(
        self, callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Register callback for merged data updates."""
        if callback not in self._update_callbacks:
            self._update_callbacks.append(callback)
            logger.debug("Registered multi-provider update callback")

    def register_session_callback(
        self, callback: Callable[[str, str, Optional[Dict[str, Any]]], None]
    ) -> None:
        """Register callback for session changes across all providers."""
        for orchestrator in self.orchestrators.values():
            orchestrator.register_session_callback(callback)

    def force_refresh(self) -> Optional[Dict[str, Any]]:
        """Force refresh across providers and return merged data."""
        for provider_orchestrator in self.orchestrators.values():
            provider_orchestrator.force_refresh()

        return self._last_valid_data

    def wait_for_initial_data(self, timeout: float = 10.0) -> bool:
        """Wait for first merged data update."""
        return self._first_data_event.wait(timeout=timeout)

    def _handle_provider_update(
        self, provider: str, monitoring_data: Dict[str, Any]
    ) -> None:
        """Handle updates from child orchestrators."""
        self._provider_latest_data[provider] = monitoring_data
        merged_data = self._build_merged_monitoring_data()

        if merged_data is None:
            return

        self._last_valid_data = merged_data
        if not self._first_data_event.is_set():
            self._first_data_event.set()

        for callback in self._update_callbacks:
            try:
                callback(merged_data)
            except Exception as e:
                logger.error(f"Multi-provider callback error: {e}", exc_info=True)
                report_error(
                    exception=e,
                    component="multi_provider_orchestrator",
                    context_name="callback_error",
                )

    def _build_merged_monitoring_data(self) -> Optional[Dict[str, Any]]:
        """Merge latest provider snapshots into a single monitoring payload."""
        if not self._provider_latest_data:
            return None

        merged_blocks: List[Dict[str, Any]] = []
        entries_count = 0
        total_tokens = 0
        total_cost = 0.0
        provider_metadata: Dict[str, Dict[str, Any]] = {}
        provider_session_ids: List[str] = []
        session_count = 0

        for provider, snapshot in self._provider_latest_data.items():
            data = snapshot.get("data", {})
            if not isinstance(data, dict):
                continue

            provider_metadata[provider] = data.get("metadata", {})
            entries_count += int(data.get("entries_count", 0) or 0)
            total_tokens += int(data.get("total_tokens", 0) or 0)
            total_cost += float(data.get("total_cost", 0.0) or 0.0)

            provider_session_id = snapshot.get("session_id")
            if provider_session_id:
                provider_session_ids.append(f"{provider}:{provider_session_id}")
            session_count += int(snapshot.get("session_count", 0) or 0)

            for block in data.get("blocks", []):
                if not isinstance(block, dict):
                    continue
                merged_blocks.append(
                    self._attach_provider_to_block(block=block, provider=provider)
                )

        merged_blocks.sort(key=lambda block: block.get("startTime", ""))

        merged_data: Dict[str, Any] = {
            "blocks": merged_blocks,
            "metadata": {
                "provider": "both",
                "providers": sorted(self._provider_latest_data.keys()),
                "provider_metadata": provider_metadata,
            },
            "entries_count": entries_count,
            "total_tokens": total_tokens,
            "total_cost": total_cost,
        }

        return {
            "data": merged_data,
            "token_limit": self._calculate_token_limit(merged_data),
            "args": self._args,
            "session_id": ",".join(provider_session_ids),
            "session_count": session_count,
            "providers": sorted(self._provider_latest_data.keys()),
            "provider_data": dict(self._provider_latest_data),
        }

    def _attach_provider_to_block(
        self, block: Dict[str, Any], provider: str
    ) -> Dict[str, Any]:
        """Annotate a block and its entries with provider metadata."""
        block_with_provider = dict(block)
        block_with_provider.setdefault("provider", provider)

        entries = block_with_provider.get("entries")
        if isinstance(entries, list):
            normalized_entries: List[Any] = []
            for entry in entries:
                if isinstance(entry, dict):
                    entry_with_provider = dict(entry)
                    entry_with_provider.setdefault("provider", provider)
                    normalized_entries.append(entry_with_provider)
                else:
                    normalized_entries.append(entry)
            block_with_provider["entries"] = normalized_entries

        return block_with_provider

    def _calculate_token_limit(self, data: Dict[str, Any]) -> int:
        """Calculate token limit from merged blocks and current plan."""
        if not self._args:
            return DEFAULT_TOKEN_LIMIT

        plan: str = getattr(self._args, "plan", "pro")

        try:
            if plan == "custom":
                blocks: List[Any] = data.get("blocks", [])
                return get_token_limit(plan, blocks)
            return get_token_limit(plan)
        except Exception as e:
            logger.exception(f"Error calculating merged token limit: {e}")
            return DEFAULT_TOKEN_LIMIT
