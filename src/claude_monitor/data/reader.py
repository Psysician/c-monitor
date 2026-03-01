"""Simplified data reader for Claude Monitor.

Combines functionality from file_reader, filter, mapper, and processor
into a single cohesive module.
"""

import json
import logging
from datetime import datetime, timedelta
from datetime import timezone as tz
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from claude_monitor.core.data_processors import (
    DataConverter,
    TimestampProcessor,
    TokenExtractor,
)
from claude_monitor.core.models import CostMode, UsageEntry
from claude_monitor.core.pricing import PricingCalculator
from claude_monitor.data.provider_registry import (
    ProviderAdapter,
    discover_provider_data_paths,
    get_provider_adapter,
    get_standard_provider_paths,
    normalize_provider,
)
from claude_monitor.error_handling import report_file_error
from claude_monitor.utils.time_utils import TimezoneHandler

FIELD_COST_USD = "cost_usd"
FIELD_MODEL = "model"
TOKEN_INPUT = "input_tokens"
TOKEN_OUTPUT = "output_tokens"

logger = logging.getLogger(__name__)


def load_usage_entries(
    data_path: Optional[str] = None,
    hours_back: Optional[int] = None,
    mode: CostMode = CostMode.AUTO,
    include_raw: bool = False,
    provider: str = "claude",
    raw_mode: str = "full",
) -> Tuple[List[UsageEntry], Optional[List[Dict[str, Any]]]]:
    """Load and convert JSONL files to UsageEntry objects.

    Args:
        data_path: Path to provider data directory
        hours_back: Only include entries from last N hours
        mode: Cost calculation mode
        include_raw: Whether to return raw JSON data alongside entries
        provider: Provider name (claude or codex)
        raw_mode: Raw entry payload mode ("full" or "compact")

    Returns:
        Tuple of (usage_entries, raw_data) where raw_data is None unless include_raw=True
    """
    normalized_provider = normalize_provider(provider)
    if raw_mode not in {"full", "compact"}:
        raise ValueError(f"Unsupported raw mode: {raw_mode}")
    adapter = get_provider_adapter(normalized_provider)
    if data_path:
        resolved_path = Path(data_path).expanduser()
    else:
        discovered_paths = discover_provider_data_paths(normalized_provider)
        if discovered_paths:
            resolved_path = discovered_paths[0]
        else:
            # Keep behavior deterministic even if path does not exist yet.
            resolved_path = Path(
                get_standard_provider_paths(normalized_provider)[0]
            ).expanduser()
    timezone_handler = TimezoneHandler()
    pricing_calculator = PricingCalculator()

    cutoff_time = None
    if hours_back:
        cutoff_time = datetime.now(tz.utc) - timedelta(hours=hours_back)

    jsonl_files = _find_jsonl_files(resolved_path)
    if not jsonl_files:
        logger.warning("No JSONL files found in %s", resolved_path)
        return [], None

    all_entries: List[UsageEntry] = []
    raw_entries: Optional[List[Dict[str, Any]]] = [] if include_raw else None
    processed_hashes: Set[str] = set()

    for file_path in jsonl_files:
        entries, raw_data = _process_single_file(
            file_path,
            mode,
            cutoff_time,
            processed_hashes,
            include_raw,
            timezone_handler,
            pricing_calculator,
            normalized_provider,
            adapter,
            raw_mode,
        )
        all_entries.extend(entries)
        if include_raw and raw_data:
            raw_entries.extend(raw_data)

    all_entries.sort(key=lambda e: e.timestamp)

    logger.info(f"Processed {len(all_entries)} entries from {len(jsonl_files)} files")

    return all_entries, raw_entries


def load_all_raw_entries(data_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load all raw JSONL entries without processing.

    Args:
        data_path: Path to Claude data directory

    Returns:
        List of raw JSON dictionaries
    """
    data_path = Path(data_path if data_path else "~/.claude/projects").expanduser()
    jsonl_files = _find_jsonl_files(data_path)

    all_raw_entries: List[Dict[str, Any]] = []
    for file_path in jsonl_files:
        try:
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        all_raw_entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.exception(f"Error loading raw entries from {file_path}: {e}")

    return all_raw_entries


def _find_jsonl_files(data_path: Path) -> List[Path]:
    """Find all .jsonl files in the data directory."""
    if not data_path.exists():
        logger.warning("Data path does not exist: %s", data_path)
        return []
    return list(data_path.rglob("*.jsonl"))


def _process_single_file(
    file_path: Path,
    mode: CostMode,
    cutoff_time: Optional[datetime],
    processed_hashes: Set[str],
    include_raw: bool,
    timezone_handler: TimezoneHandler,
    pricing_calculator: PricingCalculator,
    provider: str = "claude",
    adapter: Optional[ProviderAdapter] = None,
    raw_mode: str = "full",
) -> Tuple[List[UsageEntry], Optional[List[Dict[str, Any]]]]:
    """Process a single JSONL file."""
    entries: List[UsageEntry] = []
    raw_data: Optional[List[Dict[str, Any]]] = [] if include_raw else None

    try:
        entries_read = 0
        entries_filtered = 0
        entries_mapped = 0

        for data in _iter_file_records(file_path, adapter):
            entries_read += 1

            if not _should_process_entry(data, cutoff_time, processed_hashes, timezone_handler):
                entries_filtered += 1
                continue

            entry = _map_to_usage_entry(
                data,
                mode,
                timezone_handler,
                pricing_calculator,
                provider,
            )
            if entry:
                entries_mapped += 1
                entries.append(entry)
                _update_processed_hashes(data, processed_hashes)

            if include_raw:
                raw_data.append(_compact_raw_entry(data) if raw_mode == "compact" else data)

        logger.debug(
            f"File {file_path.name}: {entries_read} read, "
            f"{entries_filtered} filtered out, {entries_mapped} successfully mapped"
        )

    except Exception as e:
        logger.warning("Failed to read file %s: %s", file_path, e)
        report_file_error(
            exception=e,
            file_path=str(file_path),
            operation="read",
            additional_context={"file_exists": file_path.exists()},
        )
        return [], None

    return entries, raw_data


def _iter_file_records(
    file_path: Path, adapter: Optional[ProviderAdapter] = None
) -> Iterator[Dict[str, Any]]:
    """Yield normalized records from a file using adapter contract when available."""
    if adapter:
        yield from adapter.iter_normalized_records(file_path)
        return

    with open(file_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                logger.debug(f"Failed to parse JSON line in {file_path}: {e}")
                continue
            if isinstance(data, dict):
                yield data


def _compact_raw_entry(data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only fields required for limit detection and context extraction."""
    compact: Dict[str, Any] = {}
    passthrough_keys = (
        "type",
        "content",
        "timestamp",
        "message_id",
        "messageId",
        "request_id",
        "requestId",
        "session_id",
        "sessionId",
        "version",
        "model",
    )
    for key in passthrough_keys:
        if key in data:
            compact[key] = data[key]

    message = data.get("message")
    if isinstance(message, dict):
        compact["message"] = _compact_message(message)

    return compact


def _compact_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """Compact nested message payload while preserving limit-related signals."""
    compact: Dict[str, Any] = {}
    for key in ("id", "model", "usage", "stop_reason"):
        if key in message:
            compact[key] = message[key]

    content = message.get("content")
    if not isinstance(content, list):
        return compact

    compact_content: List[Dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        compact_item: Dict[str, Any] = {}
        if "type" in item:
            compact_item["type"] = item["type"]
        if "text" in item and isinstance(item["text"], str):
            compact_item["text"] = item["text"]
        if "content" in item and isinstance(item["content"], list):
            nested_items: List[Dict[str, Any]] = []
            for nested in item["content"]:
                if not isinstance(nested, dict):
                    continue
                nested_entry: Dict[str, Any] = {}
                if "type" in nested:
                    nested_entry["type"] = nested["type"]
                if "text" in nested and isinstance(nested["text"], str):
                    nested_entry["text"] = nested["text"]
                if nested_entry:
                    nested_items.append(nested_entry)
            if nested_items:
                compact_item["content"] = nested_items
        if compact_item:
            compact_content.append(compact_item)

    if compact_content:
        compact["content"] = compact_content
    return compact


def _should_process_entry(
    data: Dict[str, Any],
    cutoff_time: Optional[datetime],
    processed_hashes: Set[str],
    timezone_handler: TimezoneHandler,
) -> bool:
    """Check if entry should be processed based on time and uniqueness."""
    if cutoff_time:
        timestamp_str = data.get("timestamp")
        if timestamp_str:
            processor = TimestampProcessor(timezone_handler)
            timestamp = processor.parse_timestamp(timestamp_str)
            if timestamp and timestamp < cutoff_time:
                return False

    unique_hash = _create_unique_hash(data)
    return not (unique_hash and unique_hash in processed_hashes)


def _create_unique_hash(data: Dict[str, Any]) -> Optional[str]:
    """Create unique hash for deduplication."""
    message_id = (
        data.get("message_id")
        or (
            data.get("message", {}).get("id")
            if isinstance(data.get("message"), dict)
            else None
        )
        or data.get("event_id")
        or data.get("id")
    )
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    request_id = (
        data.get("requestId")
        or data.get("request_id")
        or payload.get("request_id")
        or payload.get("id")
    )

    return f"{message_id}:{request_id}" if message_id and request_id else None


def _update_processed_hashes(data: Dict[str, Any], processed_hashes: Set[str]) -> None:
    """Update the processed hashes set with current entry's hash."""
    unique_hash = _create_unique_hash(data)
    if unique_hash:
        processed_hashes.add(unique_hash)


def _map_to_usage_entry(
    data: Dict[str, Any],
    mode: CostMode,
    timezone_handler: TimezoneHandler,
    pricing_calculator: PricingCalculator,
    provider: str = "claude",
) -> Optional[UsageEntry]:
    """Map raw data to UsageEntry with proper cost calculation."""
    try:
        timestamp_processor = TimestampProcessor(timezone_handler)
        timestamp = timestamp_processor.parse_timestamp(data.get("timestamp", ""))
        if not timestamp:
            return None

        token_data = TokenExtractor.extract_tokens(data)
        if not any(v for k, v in token_data.items() if k != "total_tokens"):
            return None

        default_model = "codex-unknown" if provider == "codex" else "unknown"
        model = DataConverter.extract_model_name(data, default=default_model)
        cache_read_tokens = token_data.get("cache_read_tokens", 0)
        input_tokens = token_data["input_tokens"]
        # Codex logs can expose input_tokens including cached tokens.
        if provider == "codex" and cache_read_tokens > 0:
            input_tokens = max(input_tokens - cache_read_tokens, 0)

        entry_data: Dict[str, Any] = {
            FIELD_MODEL: model,
            TOKEN_INPUT: input_tokens,
            TOKEN_OUTPUT: token_data["output_tokens"],
            "cache_creation_tokens": token_data.get("cache_creation_tokens", 0),
            "cache_read_tokens": cache_read_tokens,
            FIELD_COST_USD: data.get("cost") or data.get(FIELD_COST_USD),
        }
        cost_usd = pricing_calculator.calculate_cost_for_entry(entry_data, mode)

        message = data.get("message", {})
        payload = data.get("payload", {})
        message_id = (
            data.get("message_id")
            or (message.get("id") if isinstance(message, dict) else None)
            or data.get("event_id")
            or data.get("id")
            or ""
        )
        request_id = (
            data.get("request_id")
            or data.get("requestId")
            or (payload.get("request_id") if isinstance(payload, dict) else None)
            or (payload.get("id") if isinstance(payload, dict) else None)
            or "unknown"
        )

        return UsageEntry(
            timestamp=timestamp,
            input_tokens=input_tokens,
            output_tokens=token_data["output_tokens"],
            cache_creation_tokens=token_data.get("cache_creation_tokens", 0),
            cache_read_tokens=cache_read_tokens,
            cost_usd=cost_usd,
            model=model,
            message_id=message_id,
            request_id=request_id,
            provider=provider,
        )

    except (KeyError, ValueError, TypeError, AttributeError) as e:
        logger.debug(f"Failed to map entry: {type(e).__name__}: {e}")
        return None


class UsageEntryMapper:
    """Compatibility wrapper for legacy UsageEntryMapper interface.

    This class provides backward compatibility for tests that expect
    the old UsageEntryMapper interface, wrapping the new functional
    approach in _map_to_usage_entry.
    """

    def __init__(
        self, pricing_calculator: PricingCalculator, timezone_handler: TimezoneHandler
    ):
        """Initialize with required components."""
        self.pricing_calculator = pricing_calculator
        self.timezone_handler = timezone_handler

    def map(self, data: Dict[str, Any], mode: CostMode) -> Optional[UsageEntry]:
        """Map raw data to UsageEntry - compatibility interface."""
        return _map_to_usage_entry(
            data, mode, self.timezone_handler, self.pricing_calculator
        )

    def _has_valid_tokens(self, tokens: Dict[str, int]) -> bool:
        """Check if tokens are valid (for test compatibility)."""
        return any(v > 0 for v in tokens.values())

    def _extract_timestamp(self, data: Dict[str, Any]) -> Optional[datetime]:
        """Extract timestamp (for test compatibility)."""
        if "timestamp" not in data:
            return None
        processor = TimestampProcessor(self.timezone_handler)
        return processor.parse_timestamp(data["timestamp"])

    def _extract_model(self, data: Dict[str, Any]) -> str:
        """Extract model name (for test compatibility)."""
        return DataConverter.extract_model_name(data, default="unknown")

    def _extract_metadata(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Extract metadata (for test compatibility)."""
        message = data.get("message", {})
        return {
            "message_id": data.get("message_id") or message.get("id", ""),
            "request_id": data.get("request_id") or data.get("requestId", "unknown"),
        }
