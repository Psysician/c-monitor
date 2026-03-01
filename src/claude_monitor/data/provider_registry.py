"""Provider registry for data source discovery and adapter lookup."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

PROVIDERS: tuple[str, str] = ("claude", "codex")


def normalize_provider(value: str) -> str:
    """Normalize and validate provider name."""
    provider = value.strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"Unsupported provider: {value}")
    return provider


def get_standard_provider_paths(provider: str) -> List[str]:
    """Return default data roots for the given provider."""
    normalized = normalize_provider(provider)
    roots: Dict[str, List[str]] = {
        "claude": ["~/.claude/projects", "~/.config/claude/projects"],
        "codex": ["~/.codex/sessions"],
    }
    return roots[normalized]


def discover_provider_data_paths(
    provider: str, custom_paths: Optional[List[str]] = None
) -> List[Path]:
    """Resolve existing data directories for a provider."""
    paths_to_check = custom_paths or get_standard_provider_paths(provider)
    discovered: List[Path] = []
    for path_str in paths_to_check:
        path = Path(path_str).expanduser().resolve()
        if path.exists() and path.is_dir():
            discovered.append(path)
    return discovered


@dataclass(frozen=True)
class ProviderAdapter:
    """Provider adapter exposing an iterator contract for normalized records."""

    name: str
    default_paths: List[str]

    def iter_jsonl_files(self, data_root: Path) -> Iterator[Path]:
        """Yield provider JSONL files from the selected data root."""
        if not data_root.exists():
            return
        for file_path in data_root.rglob("*.jsonl"):
            if file_path.is_file():
                yield file_path

    def iter_normalized_records(self, file_path: Path) -> Iterator[Dict[str, Any]]:
        """Yield normalized records from a JSONL file."""
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                yield self.normalize_record(raw)

    def normalize_record(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize provider-specific record fields to shared keys."""
        normalized = dict(raw)
        message = normalized.get("message")
        payload = normalized.get("payload")

        message_dict = message if isinstance(message, dict) else {}
        payload_dict = payload if isinstance(payload, dict) else {}
        info_dict = payload_dict.get("info")
        info = info_dict if isinstance(info_dict, dict) else {}

        message_id = (
            normalized.get("message_id")
            or message_dict.get("id")
            or normalized.get("event_id")
            or normalized.get("id")
        )
        request_id = (
            normalized.get("request_id")
            or normalized.get("requestId")
            or payload_dict.get("request_id")
            or payload_dict.get("id")
        )
        model = (
            normalized.get("model")
            or message_dict.get("model")
            or payload_dict.get("model")
            or info.get("model")
        )

        if message_id and "message_id" not in normalized:
            normalized["message_id"] = message_id
        if request_id and "request_id" not in normalized:
            normalized["request_id"] = request_id
        if model and "model" not in normalized:
            normalized["model"] = model

        normalized["provider"] = self.name
        return normalized


def get_provider_adapter(provider: str) -> ProviderAdapter:
    """Return adapter metadata for the target provider."""
    normalized = normalize_provider(provider)
    return ProviderAdapter(
        name=normalized, default_paths=get_standard_provider_paths(normalized)
    )
