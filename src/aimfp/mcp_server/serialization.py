"""
AIMFP MCP Server - Result Serialization

Converts helper function results (frozen dataclass instances) to JSON
strings suitable for MCP TextContent responses.

Strategy: dataclasses.asdict() + json.dumps() with custom default handler
for edge cases (frozenset, Path, datetime, bytes).

All functions are pure — no side effects, deterministic output.
"""

import dataclasses
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, FrozenSet, Tuple


# ============================================================================
# Pure Functions
# ============================================================================

def _json_default(obj: Any) -> Any:
    """
    Pure: Custom JSON serializer for types not handled by default.

    Handles: frozenset, set, tuple, Path, datetime, bytes, dataclass instances.
    """
    if isinstance(obj, (frozenset, set)):
        return sorted(obj) if all(isinstance(x, str) for x in obj) else list(obj)
    if isinstance(obj, tuple):
        return list(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def serialize_result(result: Any) -> str:
    """
    Pure: Serialize any helper result to a JSON string.

    Handles frozen dataclass results via dataclasses.asdict(),
    plain dicts, lists, strings, and primitive types.

    Args:
        result: Helper function return value (typically a frozen dataclass)

    Returns:
        JSON string representation
    """
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        data = dataclasses.asdict(result)
        return json.dumps(data, default=_json_default)

    if isinstance(result, str):
        return result

    return json.dumps(result, default=_json_default)


def result_payload(result: Any) -> Any:
    """Pure: The JSON-ready payload of a helper result (asdict for dataclasses)."""
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        return dataclasses.asdict(result)
    return result


def serialize_payload(payload: Any) -> str:
    """Pure: JSON text of an already-extracted payload (strings pass through)."""
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, default=_json_default)


def compact_return_pointer(tool_name: str) -> str:
    """Pure: The one-line stand-in for a tool's already-sent return statements."""
    return (
        f"(compact returns) Same guidance as the first {tool_name} result this "
        f"session. If it is no longer in your context, re-read it with "
        f"get_helper_by_name('{tool_name}') (custom statements are not included there)."
    )


def compact_return_statements(
    tool_name: str,
    payload: Any,
    sent: FrozenSet[Tuple[str, str]],
) -> Tuple[Any, FrozenSet[Tuple[str, str]]]:
    """
    Pure: Replace return_statements already sent this session with a pointer.

    Keyed on (tool, digest of the statement list), so a tool whose statements
    change (a custom statement added, a different branch) sends the new set
    in full. Payloads without statements pass through untouched.

    Returns:
        (payload to send, updated sent-set)
    """
    if not isinstance(payload, dict):
        return (payload, sent)
    statements = payload.get("return_statements")
    if not statements:
        return (payload, sent)
    digest = hashlib.sha1(
        json.dumps(list(statements), default=_json_default).encode("utf-8")
    ).hexdigest()[:12]
    key = (tool_name, digest)
    if key in sent:
        return ({**payload, "return_statements": [compact_return_pointer(tool_name)]}, sent)
    return (payload, sent | {key})


def is_error_result(result: Any) -> bool:
    """
    Pure: Check if a result represents an error.

    AIMFP helper results use success=False to indicate errors.

    Args:
        result: Helper function return value

    Returns:
        True if the result indicates an error
    """
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        return not getattr(result, "success", True)
    if isinstance(result, dict):
        return not result.get("success", True)
    return False
