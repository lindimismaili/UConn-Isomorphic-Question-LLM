"""Utilities for writing and reading workflow artifact records."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AGENT_ARTIFACTS_DIRNAME = "agent_outputs"
AGENT_METADATA_FILENAME = "agent_metadata_output.json"
AGENT_METADATA_RELATIVE_PATH = f"{AGENT_ARTIFACTS_DIRNAME}/{AGENT_METADATA_FILENAME}"
LEGACY_AGENT_OUTPUTS_FILENAME = "agent_outputs.json"

WORKFLOW_AGENT_SEQUENCE = [
    "normalizer",
    "extracter",
    "verifier",
    "generator",
    "latex",
    "evaluation",
    "output",
]

AGENT_DISPLAY_NAMES = {
    "normalizer": "Normalizer",
    "extracter": "Extracter",
    "verifier": "Verifier",
    "generator": "Generator",
    "latex": "Latexifier",
    "evaluation": "Evaluation",
    "output": "Output",
}

_METADATA_KEYS = {
    "agent_key",
    "agent_name",
    "workflow_id",
    "model",
    "reasoning_level",
    "execution_time_seconds",
    "received_from_node",
    "sent_to_node",
    "agent_received_from",
    "agent_sent_to",
    "input_summary",
    "questions_count",
    "filename",
    "pdf_filename",
    "status",
    "started_at",
    "ended_at",
    "reused_from_output_folder",
    "output_folder",
    "files",
    "input_metadata",
    "output_metadata",
}

_WRAPPED_RECORD_MARKER_KEYS = {"recorded_at", "agent_key", "attempt"}


def utc_timestamp() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def to_jsonable(value: Any) -> Any:
    """Convert nested values into JSON-serializable data."""
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, set):
        return [to_jsonable(item) for item in sorted(value, key=str)]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def agent_artifact_paths(outputs_dir: Path, agent_key: str) -> tuple[Path, Path]:
    """Return the input and output JSONL paths for one agent."""
    agent_dir = outputs_dir / AGENT_ARTIFACTS_DIRNAME / agent_key
    return agent_dir / "agent_input.jsonl", agent_dir / "agent_output.jsonl"


def workflow_metadata_path(outputs_dir: Path) -> Path:
    """Return the canonical workflow metadata file path."""
    return outputs_dir / AGENT_ARTIFACTS_DIRNAME / AGENT_METADATA_FILENAME


def append_jsonl(path: Path, payload: Any) -> None:
    """Append one JSONL record to disk, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(to_jsonable(payload), ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[Any]:
    """Read a JSONL file and skip blank or malformed lines."""
    if not path.exists():
        return []
    records: list[Any] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        records.append(value)
    return records


def read_last_jsonl(path: Path) -> Any | None:
    """Return the final JSONL record from a file, if any."""
    records = read_jsonl(path)
    return records[-1] if records else None


def _relative_artifact_path(outputs_dir: Path, path: Path) -> str:
    """Render an artifact path relative to the output root when possible."""
    try:
        return path.relative_to(outputs_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _metadata_subset(metadata: dict[str, Any]) -> dict[str, Any]:
    """Keep only metadata fields that are safe to persist in the workflow index."""
    return {
        key: to_jsonable(value)
        for key, value in metadata.items()
        if key in _METADATA_KEYS and value is not None
    }


def _json_type_name(value: Any) -> str:
    """Return a human-readable JSON type label for an arbitrary value."""
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


def _json_shape_metadata(value: Any) -> dict[str, Any]:
    """Summarize the JSON shape of a value for artifact metadata."""
    if isinstance(value, dict):
        return {
            "json_type": "object",
            "keys": [str(key) for key in value.keys()],
            "field_types": {
                str(key): _json_type_name(item) for key, item in value.items()
            },
        }
    if isinstance(value, list):
        item_types = sorted({_json_type_name(item) for item in value})
        return {
            "json_type": "array",
            "length": len(value),
            "item_types": item_types,
        }
    return {"json_type": _json_type_name(value)}


def _text_metadata(value: Any) -> dict[str, Any]:
    """Describe a string payload as plain text or nested JSON."""
    if not isinstance(value, str):
        return {"type": _json_type_name(value)}

    metadata: dict[str, Any] = {
        "type": "text",
        "length": len(value),
        "format": "empty" if not value.strip() else "text",
    }
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return metadata

    metadata["format"] = "json"
    metadata.update(_json_shape_metadata(parsed))
    return metadata


def _resource_reference_metadata(value: Any) -> dict[str, Any]:
    """Capture a compact description of a file or image reference payload."""
    if isinstance(value, dict):
        return {
            "type": "object",
            "keys": [str(key) for key in value.keys()],
        }
    if isinstance(value, str):
        return {
            "type": "string",
            "present": bool(value),
        }
    return {"type": _json_type_name(value)}


def _content_item_metadata(item: Any) -> dict[str, Any]:
    """Summarize a single content item from an OpenAI message payload."""
    if not isinstance(item, dict):
        metadata = {
            "type": _json_type_name(item),
            "length": len(item) if isinstance(item, (list, str)) else None,
        }
        return {key: value for key, value in metadata.items() if value is not None}

    metadata: dict[str, Any] = {
        "type": item.get("type") or "object",
        "keys": [str(key) for key in item.keys()],
    }
    for key in ("file_id", "filename", "mime_type", "media_type", "detail"):
        if key in item:
            metadata[key] = to_jsonable(item[key])
    if "text" in item:
        metadata["text"] = _text_metadata(item["text"])
    if "image_url" in item:
        metadata["image_url"] = _resource_reference_metadata(item["image_url"])
    return {key: value for key, value in metadata.items() if value is not None}


def _message_metadata(message: Any) -> dict[str, Any]:
    """Summarize a full message payload for workflow metadata."""
    if not isinstance(message, dict):
        metadata = {
            "type": _json_type_name(message),
            "length": len(message) if isinstance(message, (list, str)) else None,
        }
        return {key: value for key, value in metadata.items() if value is not None}

    metadata: dict[str, Any] = {
        "role": message.get("role"),
        "keys": [str(key) for key in message.keys()],
    }
    content = message.get("content")
    if isinstance(content, list):
        metadata["content"] = [_content_item_metadata(item) for item in content]
    elif content is not None:
        metadata["content"] = _content_item_metadata(content)
    return {key: value for key, value in metadata.items() if value is not None}


def input_payload_metadata(input_payload: Any) -> dict[str, Any]:
    """Build metadata for an agent input payload."""
    if isinstance(input_payload, list) and all(
        isinstance(item, dict) and "role" in item for item in input_payload
    ):
        return {
            "payload_type": "messages",
            "message_count": len(input_payload),
            "messages": [_message_metadata(message) for message in input_payload],
        }

    jsonable = to_jsonable(input_payload)
    metadata = {
        "payload_type": _json_type_name(jsonable),
        **_json_shape_metadata(jsonable),
    }
    if isinstance(jsonable, str):
        metadata["text"] = _text_metadata(jsonable)
    return metadata


def output_payload_metadata(output_payload: Any) -> dict[str, Any]:
    """Build metadata for an agent output payload."""
    jsonable = to_jsonable(output_payload)
    metadata = {
        "payload_type": _json_type_name(jsonable),
        "python_type": type(output_payload).__name__,
        **_json_shape_metadata(jsonable),
    }
    if isinstance(jsonable, str):
        metadata["text"] = _text_metadata(jsonable)
    return metadata


def _is_wrapped_agent_record(record: Any, payload_key: str) -> bool:
    """Return True when a JSONL record uses the wrapper format."""
    return (
        isinstance(record, dict)
        and payload_key in record
        and _WRAPPED_RECORD_MARKER_KEYS.issubset(record.keys())
    )


def write_agent_artifacts(
    *,
    outputs_dir: Path,
    workflow_id: str,
    agent_key: str,
    agent_name: str,
    input_payload: Any,
    output_payload: Any,
    metadata: dict[str, Any] | None = None,
    status: str = "completed",
) -> dict[str, Any]:
    """Persist one agent run's input, output, and metadata artifacts."""
    metadata = metadata or {}
    input_path, output_path = agent_artifact_paths(outputs_dir, agent_key)
    attempt = len(read_jsonl(output_path)) + 1
    recorded_at = utc_timestamp()
    common = {
        "recorded_at": recorded_at,
        "workflow_id": workflow_id,
        "agent_key": agent_key,
        "agent_name": agent_name,
        "attempt": attempt,
        "status": status,
        "received_from_node": metadata.get("received_from_node"),
        "sent_to_node": metadata.get("sent_to_node"),
    }

    append_jsonl(input_path, input_payload)
    append_jsonl(output_path, output_payload)

    metadata_record = _metadata_subset(
        {
            **metadata,
            "agent_key": agent_key,
            "agent_name": agent_name,
            "workflow_id": workflow_id,
            "status": status,
            "input_metadata": input_payload_metadata(input_payload),
            "output_metadata": output_payload_metadata(output_payload),
        }
    )
    metadata_record.update(
        {
            **common,
            "attempt": attempt,
            "input_artifact": _relative_artifact_path(outputs_dir, input_path),
            "output_artifact": _relative_artifact_path(outputs_dir, output_path),
        }
    )
    return metadata_record


def write_workflow_metadata(
    *,
    outputs_dir: Path,
    workflow_id: str,
    agents: list[dict[str, Any]],
    output_folder: str = "",
    workflow_type: str = "full",
    source_files: list[str] | None = None,
    resumed_from: dict[str, Any] | None = None,
) -> Path:
    """Write the workflow index describing all recorded agent artifacts."""
    source_file_metadata = []
    for raw_path in source_files or []:
        path = Path(raw_path)
        source_file_metadata.append(
            {
                "name": path.name,
                "path": str(path),
                "exists": path.exists(),
            }
        )

    payload = {
        "generated_at": utc_timestamp(),
        "workflow_id": workflow_id,
        "workflow_type": workflow_type,
        "output_folder": output_folder,
        "resumed_from": resumed_from,
        "source_files": source_file_metadata,
        "agents": agents,
    }
    metadata_path = workflow_metadata_path(outputs_dir)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata_path


def load_workflow_metadata(outputs_dir: Path) -> dict[str, Any]:
    """Load the workflow index from the current or legacy metadata location."""
    metadata_path = workflow_metadata_path(outputs_dir)
    if not metadata_path.exists():
        metadata_path = outputs_dir / AGENT_METADATA_FILENAME
    if not metadata_path.exists():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_source_file_paths(outputs_dir: Path) -> list[str]:
    """Return existing source file paths recorded in workflow metadata."""
    metadata = load_workflow_metadata(outputs_dir)
    source_files = metadata.get("source_files", [])
    paths: list[str] = []
    if isinstance(source_files, list):
        for item in source_files:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if isinstance(path, str) and Path(path).exists():
                paths.append(path)
    return paths


def load_agent_input_payload(outputs_dir: Path, agent_key: str) -> Any:
    """Load the most recent input payload for an agent from artifacts."""
    input_path, _ = agent_artifact_paths(outputs_dir, agent_key)
    record = read_last_jsonl(input_path)
    if _is_wrapped_agent_record(record, "input"):
        return record["input"]
    if record is not None:
        return record

    legacy_record = _load_legacy_agent_record(outputs_dir, agent_key)
    if legacy_record is not None:
        return legacy_record.get("input_messages")
    return None


def load_agent_output_payload(outputs_dir: Path, agent_key: str) -> Any:
    """Load the most recent output payload for an agent from artifacts."""
    _, output_path = agent_artifact_paths(outputs_dir, agent_key)
    record = read_last_jsonl(output_path)
    if _is_wrapped_agent_record(record, "output"):
        return record["output"]
    if record is not None:
        return record

    legacy_record = _load_legacy_agent_record(outputs_dir, agent_key)
    if legacy_record is not None:
        for key in ("output", "output_object"):
            if key in legacy_record:
                return legacy_record[key]

    return _load_final_result_agent_output(outputs_dir, agent_key)


def _load_legacy_agent_record(outputs_dir: Path, agent_key: str) -> dict[str, Any] | None:
    """Load a matching record from the legacy flat agent output file."""
    legacy_path = outputs_dir / LEGACY_AGENT_OUTPUTS_FILENAME
    if not legacy_path.exists():
        return None
    try:
        payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    agents = payload.get("agents") if isinstance(payload, dict) else None
    if not isinstance(agents, list):
        return None

    expected_names = {
        AGENT_DISPLAY_NAMES.get(agent_key, agent_key).lower(),
        agent_key.lower(),
    }
    if agent_key == "latex":
        expected_names.update({"latexifier", "latex formatter", "latex"})
    for record in reversed(agents):
        if not isinstance(record, dict):
            continue
        agent_name = str(record.get("agent_name", "")).lower()
        if agent_name in expected_names:
            return record
    return None


def _load_final_result_agent_output(outputs_dir: Path, agent_key: str) -> Any:
    """Recover a best-effort agent output from final_result.json."""
    result_path = outputs_dir / "final_result.json"
    if not result_path.exists():
        return None
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    if agent_key == "normalizer":
        return payload.get("config")
    if agent_key == "extracter":
        return {"questions": payload.get("source_questions", [])}
    if agent_key == "verifier":
        return {"questions": payload.get("verified_questions", [])}
    if agent_key == "generator":
        return {"isomorphic_questions": payload.get("generated_questions", [])}
    if agent_key == "latex":
        filename = payload.get("latex_file") or "isomorphic_questions.tex"
        latex_path = outputs_dir / str(filename)
        latex_content = latex_path.read_text(encoding="utf-8") if latex_path.exists() else ""
        return {
            "latex_content": latex_content,
            "filename": filename,
            "pdf_filename": payload.get("pdf_file") or "",
        }
    if agent_key == "evaluation":
        return payload.get("evaluation")
    return None
