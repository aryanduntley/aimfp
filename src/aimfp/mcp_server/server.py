"""
AIMFP MCP Server - Server Core (Zero-Dependency)

Custom JSON-RPC 2.0 handler over stdio.
Implements: initialize, notifications/initialized, tools/list, tools/call.
No MCP SDK dependency — stdlib only.

Tool call flow:
  stdin → json.loads → dispatch_message → handler → json.dumps → stdout
"""

import sys
import json
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Final

from ..database.connection import get_core_db_path
from .registry import TOOL_REGISTRY, is_registered_tool, _effect_import_tool_function
from .schema import params_to_input_schema
from .serialization import serialize_result, is_error_result
from .errors import (
    METHOD_NOT_FOUND,
    INTERNAL_ERROR,
    PARSE_ERROR,
    format_tool_not_found_error,
    format_internal_error,
    format_import_error,
)


# ============================================================================
# Server Metadata
# ============================================================================

SERVER_NAME: Final[str] = "aimfp"
SERVER_VERSION: Final[str] = "1.34.0"
PROTOCOL_VERSION: Final[str] = "2025-06-18"


# ============================================================================
# Tool Annotation Constants
# ============================================================================

_READ_ONLY_PREFIXES: Final[tuple] = (
    "get_", "search_", "query_", "find_", "list_", "detect_",
)

_READ_ONLY_SPECIAL: Final[frozenset] = frozenset({
    "aimfp_run",
    "aimfp_status",
    "core_allowed_check_constraints",
    "project_allowed_check_constraints",
    "user_preferences_allowed_check_constraints",
    "user_directives_allowed_check_constraints",
    "blueprint_has_changed",
    "file_has_changed",
})

_DESTRUCTIVE_SPECIAL: Final[frozenset] = frozenset({
    "execute_merge",
    "aimfp_end",
})


# ============================================================================
# Module-level cache (populated once at startup)
# ============================================================================

_cached_tool_dicts: List[Dict[str, Any]] = []
_cached_instructions: str = ""


# ============================================================================
# Pure Functions — JSON-RPC Builders
# ============================================================================

def build_jsonrpc_response(request_id: Any, result: Any) -> Dict[str, Any]:
    """Pure: Build a JSON-RPC 2.0 success response."""
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def build_jsonrpc_error(request_id: Any, code: int,
                        message: str) -> Dict[str, Any]:
    """Pure: Build a JSON-RPC 2.0 error response."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def build_text_content(text: str) -> Dict[str, str]:
    """Pure: Build a text content dict for tool call responses."""
    return {"type": "text", "text": text}


def build_tool_result(text: str,
                      is_error: bool = False) -> Dict[str, Any]:
    """Pure: Build a tools/call result with content array."""
    return {
        "content": [build_text_content(text)],
        "isError": is_error,
    }


def build_tool_annotations(tool_name: str) -> Dict[str, Any]:
    """Pure: Generate MCP ToolAnnotations from tool name conventions.

    Classification:
      - Read-only: get_*, search_*, query_*, find_*, list_*, detect_*,
        plus special cases (aimfp_run, *_has_changed, *_check_constraints)
      - Destructive: delete_*, execute_merge, aimfp_end
      - Write (non-destructive): everything else
      All tools: openWorldHint=false (AIMFP is entirely local).
    """
    title = tool_name.replace("_", " ").title()

    is_read_only = (
        any(tool_name.startswith(p) for p in _READ_ONLY_PREFIXES)
        or tool_name in _READ_ONLY_SPECIAL
    )
    is_destructive = (
        tool_name.startswith("delete_")
        or tool_name in _DESTRUCTIVE_SPECIAL
    )

    annotations: Dict[str, Any] = {
        "title": title,
        "readOnlyHint": is_read_only,
        "openWorldHint": False,
    }

    if is_read_only:
        annotations["idempotentHint"] = True
    else:
        annotations["destructiveHint"] = is_destructive
        annotations["idempotentHint"] = (
            tool_name.startswith("update_") or tool_name.startswith("set_")
        )

    return annotations


def build_tool_dict(name: str, description: str,
                    input_schema: Dict[str, Any],
                    annotations: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: Build a tool definition dict for tools/list response."""
    return {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
        "annotations": annotations,
    }


# ============================================================================
# Pure Functions — Request Handlers
# ============================================================================

def handle_initialize(request_id: Any,
                      params: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: Handle initialize handshake. Includes system prompt as instructions."""
    result: Dict[str, Any] = {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }
    if _cached_instructions:
        result["instructions"] = _cached_instructions
    return build_jsonrpc_response(request_id, result)


def handle_list_tools(request_id: Any) -> Dict[str, Any]:
    """Pure: Handle tools/list — return cached tool definitions."""
    return build_jsonrpc_response(request_id, {"tools": _cached_tool_dicts})


def handle_call_tool(request_id: Any,
                     params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle tools/call — dispatch to helper, serialize result."""
    name = params.get("name", "")
    arguments = params.get("arguments", {}) or {}

    # Unknown tool
    if not is_registered_tool(name):
        return build_jsonrpc_response(
            request_id,
            build_tool_result(format_tool_not_found_error(name), is_error=True),
        )

    # Lazy import
    try:
        tool_fn = _effect_import_tool_function(name)
    except (ImportError, AttributeError) as e:
        return build_jsonrpc_response(
            request_id,
            build_tool_result(format_import_error(name, e), is_error=True),
        )

    # Call helper
    try:
        result = tool_fn(**arguments)
    except Exception as e:
        return build_jsonrpc_response(
            request_id,
            build_tool_result(format_internal_error(name, e), is_error=True),
        )

    # Serialize and return
    serialized = serialize_result(result)
    return build_jsonrpc_response(
        request_id,
        build_tool_result(serialized, is_error=is_error_result(result)),
    )


def dispatch_message(message: Dict[str, Any]) -> Dict[str, Any] | None:
    """Pure: Route a JSON-RPC message to the appropriate handler.

    Returns None for notifications (messages without an id).
    """
    method = message.get("method", "")
    request_id = message.get("id")
    params = message.get("params", {}) or {}

    # Notifications (no id) — acknowledge silently
    if request_id is None:
        return None

    if method == "initialize":
        return handle_initialize(request_id, params)
    elif method == "tools/list":
        return handle_list_tools(request_id)
    elif method == "tools/call":
        return handle_call_tool(request_id, params)
    else:
        return build_jsonrpc_error(
            request_id, METHOD_NOT_FOUND,
            f"Method not found: {method}",
        )


# ============================================================================
# Effect Functions
# ============================================================================

def _effect_load_and_cache_instructions() -> None:
    """Effect: Load system prompt from reference/system_prompt.txt into module cache."""
    global _cached_instructions
    prompt_path = Path(__file__).parent.parent / "reference" / "system_prompt.txt"
    if prompt_path.is_file():
        _cached_instructions = prompt_path.read_text(encoding="utf-8")


def _effect_load_and_cache_tools() -> None:
    """Effect: Load tool definitions from aimfp_core.db into module cache."""
    global _cached_tool_dicts
    db_path = get_core_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        placeholders = ",".join("?" for _ in TOOL_REGISTRY)
        cursor = conn.execute(
            f"SELECT name, purpose, parameters FROM helper_functions "
            f"WHERE name IN ({placeholders})",
            tuple(TOOL_REGISTRY.keys()),
        )

        tools = []
        for row in cursor.fetchall():
            name = row["name"]
            description = row["purpose"] or f"AIMFP tool: {name}"
            input_schema = params_to_input_schema(row["parameters"] or "[]")
            annotations = build_tool_annotations(name)
            tools.append(build_tool_dict(name, description, input_schema,
                                         annotations))

        _cached_tool_dicts = tools

    finally:
        conn.close()


def _effect_stdio_loop() -> None:
    """Effect: Read JSON-RPC messages from stdin, write responses to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            error = build_jsonrpc_error(None, PARSE_ERROR, "Parse error")
            sys.stdout.write(json.dumps(error) + "\n")
            sys.stdout.flush()
            continue

        response = dispatch_message(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


# ============================================================================
# Public Entry Point
# ============================================================================

def run_server() -> None:
    """Start the AIMFP MCP server on stdio transport (blocking)."""
    _effect_load_and_cache_instructions()
    _effect_load_and_cache_tools()
    _effect_stdio_loop()
