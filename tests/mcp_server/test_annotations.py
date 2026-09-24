"""Explicit per-tool MCP annotations (task 39; svamanas item 10)."""
import pytest

from aimfp.mcp_server import server
from aimfp.mcp_server.server import _effect_load_and_cache_tools, build_tool_annotations


@pytest.fixture(scope="module")
def hints():
    _effect_load_and_cache_tools()
    return {t["name"]: t["annotations"] for t in server._cached_tool_dicts}


def test_aimfp_run_is_not_read_only(hints):
    assert hints["aimfp_run"]["readOnlyHint"] is False
    assert hints["aimfp_run"]["destructiveHint"] is False


@pytest.mark.parametrize("tool", [
    "scan_source_tree", "scan_call_graph", "validate_trigger_config",
    "check_directive_health", "summarize_state_changeset", "plan_disjoint_partitions",
])
def test_read_only_despite_name(hints, tool):
    assert hints[tool]["readOnlyHint"] is True
    assert "destructiveHint" not in hints[tool]


@pytest.mark.parametrize("tool", [
    "restore_project_backup", "remove_files_from_module", "unlink_files_from_task",
    "clear_watchdog", "apply_state_changeset", "create_project_backup",
])
def test_destructive_despite_name(hints, tool):
    assert hints[tool]["readOnlyHint"] is False
    assert hints[tool]["destructiveHint"] is True


@pytest.mark.parametrize("tool", ["add_interactions", "add_types_functions", "catalog_functions"])
def test_idempotent_linkers(hints, tool):
    assert hints[tool]["idempotentHint"] is True
    assert hints[tool]["destructiveHint"] is False


def test_name_rule_still_covers_the_rest(hints):
    assert hints["get_file_by_name"]["readOnlyHint"] is True
    assert hints["delete_task"]["destructiveHint"] is True
    assert hints["add_task"] == {
        "title": "Add Task", "readOnlyHint": False, "openWorldHint": False,
        "destructiveHint": False, "idempotentHint": False,
    }


def test_every_tool_has_complete_hints(hints):
    for tool, a in hints.items():
        assert isinstance(a["readOnlyHint"], bool), tool
        assert isinstance(a["idempotentHint"], bool), tool
        assert a["openWorldHint"] is False, tool
        assert ("destructiveHint" in a) == (not a["readOnlyHint"]), tool


def test_explicit_override_merge():
    a = build_tool_annotations("get_widget", {"readOnlyHint": False, "bogus": True})
    assert a["readOnlyHint"] is False and a["destructiveHint"] is False
    assert "bogus" not in a
    b = build_tool_annotations("add_widget", {"readOnlyHint": True})
    assert b["readOnlyHint"] is True and "destructiveHint" not in b and b["idempotentHint"] is True
