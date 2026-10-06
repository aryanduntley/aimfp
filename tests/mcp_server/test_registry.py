"""
Tests for aimfp.mcp_server.registry

Verifies every registry entry is valid (modules importable, functions exist,
helpers work) and that the Claude Code allowlist shipped in
documentation/settings.local.json stays in sync with TOOL_REGISTRY.
"""

import importlib
import json
from pathlib import Path

import pytest

from aimfp.mcp_server.registry import (
    TOOL_REGISTRY,
    is_registered_tool,
    get_registry_entry,
    _effect_import_tool_function,
)


# ============================================================================
# Registry Structure Tests
# ============================================================================

def test_registry_has_sane_entry_count():
    """Loose floor, not an exact count — a hardcoded exact number is the
    brittleness that let the registry and allowlist drift apart. Catches
    accidental mass-deletion without breaking every time a tool is added."""
    assert len(TOOL_REGISTRY) >= 250


def test_registry_values_are_tuples():
    for name, entry in TOOL_REGISTRY.items():
        assert isinstance(entry, tuple), f"{name}: expected tuple, got {type(entry)}"
        assert len(entry) == 2, f"{name}: expected 2-tuple, got {len(entry)}-tuple"


def test_registry_module_paths_are_strings():
    for name, (module_path, _) in TOOL_REGISTRY.items():
        assert isinstance(module_path, str), f"{name}: module_path not a string"
        assert module_path.startswith("aimfp."), f"{name}: module_path doesn't start with 'aimfp.'"


def test_registry_function_names_are_strings():
    for name, (_, function_name) in TOOL_REGISTRY.items():
        assert isinstance(function_name, str), f"{name}: function_name not a string"
        assert len(function_name) > 0, f"{name}: empty function_name"


def test_registry_keys_match_function_names():
    """Tool name in registry key should match the function_name value."""
    for tool_name, (_, function_name) in TOOL_REGISTRY.items():
        assert tool_name == function_name, (
            f"Registry key '{tool_name}' doesn't match function name '{function_name}'"
        )


def test_registry_no_duplicate_module_function_pairs():
    """No two registry entries should point to the same (module, function)."""
    seen = set()
    for name, entry in TOOL_REGISTRY.items():
        assert entry not in seen, f"{name}: duplicate target {entry}"
        seen.add(entry)


# ============================================================================
# is_registered_tool / get_tool_info Tests
# ============================================================================

def test_is_registered_tool_known():
    assert is_registered_tool("aimfp_run") is True


def test_is_registered_tool_unknown():
    assert is_registered_tool("nonexistent_tool_xyz") is False


def test_get_registry_entry_known():
    module_path, function_name = get_registry_entry("aimfp_run")
    assert module_path == "aimfp.helpers.orchestrators.entry_points"
    assert function_name == "aimfp_run"


def test_get_registry_entry_unknown_raises():
    with pytest.raises(KeyError):
        get_registry_entry("nonexistent_tool_xyz")


# ============================================================================
# Module Import Tests
# ============================================================================

def test_all_registry_modules_importable():
    """Every module_path in the registry must be importable."""
    unique_modules = {module_path for module_path, _ in TOOL_REGISTRY.values()}
    failures = []

    for module_path in sorted(unique_modules):
        try:
            importlib.import_module(module_path)
        except Exception as e:
            failures.append(f"{module_path}: {type(e).__name__}: {e}")

    assert not failures, f"Failed to import {len(failures)} modules:\n" + "\n".join(failures)


def test_all_registry_functions_exist_in_modules():
    """Every function_name must exist as an attribute in its module."""
    failures = []

    for tool_name, (module_path, function_name) in TOOL_REGISTRY.items():
        try:
            module = importlib.import_module(module_path)
            if not hasattr(module, function_name):
                failures.append(f"{tool_name}: {function_name} not found in {module_path}")
        except ImportError:
            pass  # Module import failures caught by separate test

    assert not failures, f"{len(failures)} missing functions:\n" + "\n".join(failures)


def test_all_registry_functions_are_callable():
    """Every imported function must be callable."""
    failures = []

    for tool_name, (module_path, function_name) in TOOL_REGISTRY.items():
        try:
            module = importlib.import_module(module_path)
            fn = getattr(module, function_name, None)
            if fn is not None and not callable(fn):
                failures.append(f"{tool_name}: {function_name} is not callable")
        except ImportError:
            pass

    assert not failures, f"{len(failures)} non-callable functions:\n" + "\n".join(failures)


# ============================================================================
# _effect_import_tool_function Tests
# ============================================================================

def test_effect_import_tool_function_returns_callable():
    fn = _effect_import_tool_function("get_all_directive_names")
    assert callable(fn)


def test_effect_import_tool_function_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        _effect_import_tool_function("nonexistent_tool_xyz")


# ============================================================================
# Category Coverage Tests
# ============================================================================

def test_registry_covers_entry_points():
    entry_points = {"aimfp_run", "aimfp_init", "aimfp_status", "aimfp_end"}
    for ep in entry_points:
        assert ep in TOOL_REGISTRY, f"Missing entry point: {ep}"


def test_registry_covers_core_directives():
    core_tools = {"get_all_directives", "get_directive_by_name", "search_directives"}
    for tool in core_tools:
        assert tool in TOOL_REGISTRY, f"Missing core tool: {tool}"


def test_registry_covers_project_crud():
    project_tools = {"reserve_file", "finalize_file", "reserve_function", "finalize_function"}
    for tool in project_tools:
        assert tool in TOOL_REGISTRY, f"Missing project tool: {tool}"


def test_registry_covers_user_preferences():
    pref_tools = {"set_custom_return_statement", "delete_custom_return_statement"}
    for tool in pref_tools:
        assert tool in TOOL_REGISTRY, f"Missing user preference tool: {tool}"


def test_registry_covers_git():
    git_tools = {"create_user_branch", "detect_conflicts_before_merge"}
    for tool in git_tools:
        assert tool in TOOL_REGISTRY, f"Missing git tool: {tool}"


# ============================================================================
# Claude Code Allowlist Sync
# ============================================================================
#
# documentation/settings.local.json is the pre-built Claude Code allowlist
# users copy into their project's .claude/ folder to pre-approve every AIMFP
# tool. TOOL_REGISTRY is the single source of truth; the allowlist must mirror
# it exactly. When it doesn't, users get a permission prompt for every tool
# missing from the file. This test turns "forgot to update the allowlist" into
# a failing test instead of a silent prompt-storm for every user.
#
# To fix a failure, regenerate the file from the registry:
#
#   PYTHONPATH=src python3 -c "from aimfp.helpers.shared.claude_setup import \
#   get_claude_permissions as g; \
#   open('documentation/settings.local.json','w').write(g().data['settings_json'])"
#
# The file allowlists every tool under both prefixes: mcp__aimfp__ (project
# .mcp.json) and mcp__plugin_aimfp_aimfp__ (Claude Code plugin).

ALLOWLIST_PATH = Path(__file__).parents[2] / "documentation" / "settings.local.json"
_TOOL_PREFIX = "mcp__aimfp__"
_PLUGIN_TOOL_PREFIX = "mcp__plugin_aimfp_aimfp__"


def _load_allowlist_tool_names(prefix: str = _TOOL_PREFIX) -> set[str]:
    data = json.loads(ALLOWLIST_PATH.read_text())
    allow = data["permissions"]["allow"]
    return {entry[len(prefix):] for entry in allow if entry.startswith(prefix)}


def test_allowlist_plugin_prefix_matches_registry():
    """Plugin installs name tools mcp__plugin_aimfp_aimfp__*; the shipped
    allowlist must cover them exactly as it covers mcp__aimfp__*."""
    assert _load_allowlist_tool_names(_PLUGIN_TOOL_PREFIX) == set(TOOL_REGISTRY)


def test_allowlist_file_exists_and_is_valid_json():
    assert ALLOWLIST_PATH.is_file(), f"Missing allowlist: {ALLOWLIST_PATH}"
    data = json.loads(ALLOWLIST_PATH.read_text())
    assert "permissions" in data and "allow" in data["permissions"]
    assert data.get("enableAllProjectMcpServers") is True
    assert data.get("enabledMcpjsonServers") == ["aimfp"]


def test_allowlist_has_no_duplicate_entries():
    """Duplicates inflate the count and obscure drift — keep entries unique."""
    allow = json.loads(ALLOWLIST_PATH.read_text())["permissions"]["allow"]
    seen = set()
    dupes = sorted({e for e in allow if e in seen or seen.add(e)})
    assert not dupes, f"{len(dupes)} duplicate allowlist entries:\n" + "\n".join(dupes)


def test_allowlist_matches_registry_exactly():
    """The allowlist must mirror TOOL_REGISTRY 1:1 — no missing tools (users
    would get a prompt for each), no stale tools (point to nothing)."""
    registry = set(TOOL_REGISTRY)
    allowlist = _load_allowlist_tool_names()

    missing = sorted(registry - allowlist)
    stale = sorted(allowlist - registry)

    assert not missing and not stale, (
        "documentation/settings.local.json is out of sync with TOOL_REGISTRY. "
        "Regenerate it (see module note above).\n"
        f"  Registered but missing from allowlist ({len(missing)}): {missing}\n"
        f"  In allowlist but not registered ({len(stale)}): {stale}"
    )
