"""
Tests for aimfp.mcp_server.server

Integration tests for tool loading, handler dispatch, and error paths.
Tests the zero-dependency JSON-RPC server (no MCP SDK).
"""

import json

import pytest

from aimfp.mcp_server.server import (
    _effect_load_and_cache_tools,
    _cached_tool_dicts,
    build_jsonrpc_response,
    build_jsonrpc_error,
    build_text_content,
    build_tool_result,
    build_tool_dict,
    build_tool_annotations,
    handle_initialize,
    handle_list_tools,
    handle_call_tool,
    dispatch_message,
    SERVER_NAME,
    SERVER_VERSION,
    PROTOCOL_VERSION,
)
from aimfp.mcp_server.registry import TOOL_REGISTRY


# ============================================================================
# Pure Function Tests — JSON-RPC Builders
# ============================================================================

def test_build_jsonrpc_response():
    resp = build_jsonrpc_response(1, {"key": "value"})
    assert resp == {"jsonrpc": "2.0", "id": 1, "result": {"key": "value"}}


def test_build_jsonrpc_error():
    resp = build_jsonrpc_error(1, -32601, "Method not found")
    assert resp == {
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": -32601, "message": "Method not found"},
    }


def test_build_text_content():
    content = build_text_content("hello")
    assert content == {"type": "text", "text": "hello"}


def test_build_tool_result_success():
    result = build_tool_result("data", is_error=False)
    assert result["isError"] is False
    assert len(result["content"]) == 1
    assert result["content"][0]["type"] == "text"
    assert result["content"][0]["text"] == "data"


def test_build_tool_result_error():
    result = build_tool_result("error msg", is_error=True)
    assert result["isError"] is True
    assert result["content"][0]["text"] == "error msg"


def test_build_tool_dict():
    ann = {"title": "My Tool", "readOnlyHint": True, "openWorldHint": False}
    td = build_tool_dict("my_tool", "Does stuff", {"type": "object", "properties": {}}, ann)
    assert td["name"] == "my_tool"
    assert td["description"] == "Does stuff"
    assert td["inputSchema"]["type"] == "object"
    assert td["annotations"] == ann


# ============================================================================
# Tool Loading Tests
# ============================================================================

def test_load_and_cache_tools_populates_cache():
    _effect_load_and_cache_tools()
    from aimfp.mcp_server import server
    assert len(server._cached_tool_dicts) == len(TOOL_REGISTRY)


def test_cached_tools_are_dicts():
    _effect_load_and_cache_tools()
    from aimfp.mcp_server import server
    for tool in server._cached_tool_dicts:
        assert isinstance(tool, dict)


def test_cached_tools_have_required_keys():
    _effect_load_and_cache_tools()
    from aimfp.mcp_server import server
    for tool in server._cached_tool_dicts:
        assert "name" in tool, f"Missing 'name' key"
        assert "description" in tool, f"Missing 'description' for {tool.get('name')}"
        assert "inputSchema" in tool, f"Missing 'inputSchema' for {tool.get('name')}"


def test_cached_tools_have_valid_schemas():
    _effect_load_and_cache_tools()
    from aimfp.mcp_server import server
    for tool in server._cached_tool_dicts:
        schema = tool["inputSchema"]
        assert isinstance(schema, dict), f"{tool['name']}: schema not a dict"
        assert schema.get("type") == "object", f"{tool['name']}: schema type not 'object'"
        assert "properties" in schema, f"{tool['name']}: schema missing 'properties'"


def test_cached_tools_have_descriptions():
    _effect_load_and_cache_tools()
    from aimfp.mcp_server import server
    for tool in server._cached_tool_dicts:
        assert isinstance(tool["description"], str)
        assert len(tool["description"]) > 0, f"{tool['name']}: empty description"


def test_cached_tools_names_match_registry():
    _effect_load_and_cache_tools()
    from aimfp.mcp_server import server
    names = {t["name"] for t in server._cached_tool_dicts}
    for tool_name in TOOL_REGISTRY:
        assert tool_name in names, f"Tool '{tool_name}' missing from cached tools"


# ============================================================================
# Handler Tests — initialize
# ============================================================================

def test_handle_initialize_response_format():
    resp = handle_initialize(1, {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    })
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    result = resp["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"]["name"] == SERVER_NAME
    assert result["serverInfo"]["version"] == SERVER_VERSION


# ============================================================================
# Handler Tests — tools/list
# ============================================================================

def test_handle_list_tools_returns_all_registered_tools():
    _effect_load_and_cache_tools()
    resp = handle_list_tools(2)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 2
    assert len(resp["result"]["tools"]) == len(TOOL_REGISTRY)


# ============================================================================
# Handler Tests — tools/call
# ============================================================================

def test_handle_call_tool_unknown_tool():
    """Unknown tools return error text, not crash."""
    _effect_load_and_cache_tools()
    resp = handle_call_tool(3, {"name": "nonexistent_tool_xyz", "arguments": {}})
    result = resp["result"]
    assert result["isError"] is True
    assert "not found" in result["content"][0]["text"].lower()


def test_handle_call_tool_get_all_directive_names():
    """Known tool returns valid JSON response."""
    _effect_load_and_cache_tools()
    resp = handle_call_tool(4, {"name": "get_all_directive_names", "arguments": {}})
    result = resp["result"]
    assert result["isError"] is False
    parsed = json.loads(result["content"][0]["text"])
    assert parsed["success"] is True


def test_handle_call_tool_missing_required_args():
    """Tool called without required args returns error (not crash)."""
    _effect_load_and_cache_tools()
    resp = handle_call_tool(5, {"name": "reserve_function", "arguments": {}})
    result = resp["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    # Caught by schema validation before the helper runs, naming each field
    assert "Invalid arguments for 'reserve_function'" in text
    assert "name: required" in text and "file_id: required" in text


def test_handle_call_tool_get_supportive_context():
    """Shared helper with no required DB args should work."""
    _effect_load_and_cache_tools()
    resp = handle_call_tool(6, {"name": "get_supportive_context", "arguments": {}})
    result = resp["result"]
    assert result["isError"] is False
    assert len(result["content"][0]["text"]) > 0


def test_handle_call_tool_returns_text_content_dicts():
    """All tool calls must return content as list of text dicts."""
    _effect_load_and_cache_tools()
    resp = handle_call_tool(7, {"name": "get_all_directive_names", "arguments": {}})
    result = resp["result"]
    for item in result["content"]:
        assert isinstance(item, dict)
        assert item["type"] == "text"
        assert "text" in item


# ============================================================================
# Dispatch Tests
# ============================================================================

def test_dispatch_initialize():
    resp = dispatch_message({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"}},
    })
    assert resp is not None
    assert resp["id"] == 1
    assert "protocolVersion" in resp["result"]


def test_dispatch_tools_list():
    _effect_load_and_cache_tools()
    resp = dispatch_message({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    assert resp is not None
    assert resp["id"] == 2
    assert "tools" in resp["result"]


def test_dispatch_tools_call():
    _effect_load_and_cache_tools()
    resp = dispatch_message({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "get_all_directive_names", "arguments": {}},
    })
    assert resp is not None
    assert resp["id"] == 3
    assert "content" in resp["result"]


def test_dispatch_notification_returns_none():
    """Notifications (no id) should return None — no response sent."""
    resp = dispatch_message({
        "jsonrpc": "2.0", "method": "notifications/initialized",
    })
    assert resp is None


def test_dispatch_unknown_method():
    resp = dispatch_message({
        "jsonrpc": "2.0", "id": 99, "method": "unknown/method", "params": {},
    })
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32601
    assert "unknown/method" in resp["error"]["message"]


# ============================================================================
# Tool Annotation Tests
# ============================================================================

class TestBuildToolAnnotations:
    """Tests for build_tool_annotations() — convention-based classification."""

    # -- Title generation --

    def test_title_from_snake_case(self):
        ann = build_tool_annotations("get_all_directive_names")
        assert ann["title"] == "Get All Directive Names"

    def test_title_for_aimfp_run(self):
        ann = build_tool_annotations("aimfp_run")
        assert ann["title"] == "Aimfp Run"

    # -- Read-only tools (prefix-based) --

    @pytest.mark.parametrize("name", [
        "get_project", "get_all_themes", "get_file_by_name",
        "search_directives", "search_notes", "search_user_directives",
        "query_project", "query_core", "query_settings",
        "find_directive_by_intent",
        "list_active_branches",
        "detect_external_changes", "detect_conflicts_before_merge",
    ])
    def test_read_prefix_tools_are_read_only(self, name):
        ann = build_tool_annotations(name)
        assert ann["readOnlyHint"] is True
        assert ann["idempotentHint"] is True
        assert "destructiveHint" not in ann

    # -- Read-only tools (special cases) --

    @pytest.mark.parametrize("name", [
        "aimfp_run", "aimfp_status",
        "core_allowed_check_constraints",
        "project_allowed_check_constraints",
        "user_preferences_allowed_check_constraints",
        "user_directives_allowed_check_constraints",
        "blueprint_has_changed", "file_has_changed",
    ])
    def test_special_read_only_tools(self, name):
        ann = build_tool_annotations(name)
        assert ann["readOnlyHint"] is True
        assert ann["idempotentHint"] is True
        assert "destructiveHint" not in ann

    # -- Write non-destructive tools --

    @pytest.mark.parametrize("name", [
        "add_note", "add_milestone", "add_task", "add_flow",
        "reserve_file", "reserve_function", "reserve_type",
        "finalize_file", "finalize_function", "finalize_type",
        "create_project", "create_state_database", "create_user_branch",
        "aimfp_init", "activate_user_directive", "deactivate_user_directive",
        "sync_git_state", "toggle_tracking_feature",
        "add_directive_preference", "batch_update_progress",
    ])
    def test_write_non_destructive_tools(self, name):
        ann = build_tool_annotations(name)
        assert ann["readOnlyHint"] is False
        assert ann["destructiveHint"] is False
        assert ann["idempotentHint"] is False

    # -- Write idempotent tools (update_*, set_*) --

    @pytest.mark.parametrize("name", [
        "update_project", "update_file", "update_function",
        "update_task", "update_milestone", "update_note",
        "update_project_entry", "update_settings_entry",
        "set_custom_return_statement",
    ])
    def test_write_idempotent_tools(self, name):
        ann = build_tool_annotations(name)
        assert ann["readOnlyHint"] is False
        assert ann["destructiveHint"] is False
        assert ann["idempotentHint"] is True

    # -- Destructive tools (delete_ prefix) --

    @pytest.mark.parametrize("name", [
        "delete_file", "delete_function", "delete_item",
        "delete_task", "delete_milestone", "delete_flow", "delete_theme",
        "delete_project_entry", "delete_reserved",
        "delete_sidequest", "delete_subtask", "delete_type",
        "delete_completion_path", "delete_user_custom_entry",
        "delete_settings_entry", "delete_custom_return_statement",
    ])
    def test_delete_tools_are_destructive(self, name):
        ann = build_tool_annotations(name)
        assert ann["readOnlyHint"] is False
        assert ann["destructiveHint"] is True
        assert ann["idempotentHint"] is False

    # -- Destructive tools (special cases) --

    @pytest.mark.parametrize("name", ["execute_merge", "aimfp_end"])
    def test_special_destructive_tools(self, name):
        ann = build_tool_annotations(name)
        assert ann["readOnlyHint"] is False
        assert ann["destructiveHint"] is True

    # -- All tools: openWorldHint is always false --

    def test_open_world_always_false(self):
        for name in TOOL_REGISTRY:
            ann = build_tool_annotations(name)
            assert ann["openWorldHint"] is False, f"{name}: openWorldHint should be False"


# ============================================================================
# Cached Tools — Annotations Present
# ============================================================================

def test_cached_tools_have_annotations():
    """Every tool dict in the cache must have an annotations key."""
    _effect_load_and_cache_tools()
    from aimfp.mcp_server import server
    for tool in server._cached_tool_dicts:
        assert "annotations" in tool, f"{tool['name']}: missing 'annotations'"


def test_cached_tools_annotations_have_required_fields():
    """Every annotations dict must have title, readOnlyHint, openWorldHint."""
    _effect_load_and_cache_tools()
    from aimfp.mcp_server import server
    for tool in server._cached_tool_dicts:
        ann = tool["annotations"]
        assert "title" in ann, f"{tool['name']}: annotations missing 'title'"
        assert "readOnlyHint" in ann, f"{tool['name']}: annotations missing 'readOnlyHint'"
        assert "openWorldHint" in ann, f"{tool['name']}: annotations missing 'openWorldHint'"
        assert isinstance(ann["title"], str) and len(ann["title"]) > 0
