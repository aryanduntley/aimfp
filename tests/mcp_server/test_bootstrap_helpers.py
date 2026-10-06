"""
Tests for the bootstrap helpers in aimfp.helpers.shared.claude_setup

- get_system_prompt: returns the AIMFP system prompt + placement policy
  (create / prepend / consolidate-if-extensive; AIMFP content first; never
  discard existing content). Returns content + path, never writes.
- get_claude_permissions: returns the Claude Code allowlist (smoke coverage —
  it shares the module and previously had no direct tests).

Both must stay registered and in sync with TOOL_REGISTRY / the shipped
allowlist (the registry/allowlist invariant itself is enforced in
test_registry.py).
"""

from pathlib import Path

import pytest

from aimfp.helpers.shared import claude_setup
from aimfp.helpers.shared.claude_setup import (
    get_system_prompt,
    get_claude_permissions,
    EXTENSIVE_CHARS,
)
from aimfp.mcp_server.registry import TOOL_REGISTRY, _effect_import_tool_function


# ============================================================================
# get_system_prompt
# ============================================================================

def test_fresh_returns_full_prompt_verbatim():
    r = get_system_prompt()
    assert r.success, r.error
    d = r.data
    expected = Path(claude_setup.SYSTEM_PROMPT_PATH).read_text(encoding="utf-8")
    assert d["content"] == expected, "content must be the bundled prompt verbatim"
    assert d["content"].strip()
    assert d["target_path"] == "CLAUDE.md"
    assert d["placement"] == "prepend"
    assert d["existing_is_extensive"] is False
    assert "create" in d["instructions"].lower()


def test_small_existing_prepends_without_clobber():
    r = get_system_prompt("# My project\nUse tabs.")
    d = r.data
    assert d["existing_is_extensive"] is False
    assert "PREPEND" in d["instructions"]
    assert "overwrite" in d["instructions"].lower()  # warns against clobbering
    assert "FIRST" in d["instructions"]


def test_extensive_existing_triggers_consolidation_discussion():
    r = get_system_prompt("x" * (EXTENSIVE_CHARS + 1))
    d = r.data
    assert d["existing_is_extensive"] is True
    instr = d["instructions"].lower()
    assert "consolidat" in instr
    assert "do not blindly" in instr
    assert "FIRST" in d["instructions"]  # AIMFP content still first


def test_threshold_boundary_is_not_extensive():
    # exactly EXTENSIVE_CHARS is NOT extensive (strictly greater triggers it)
    r = get_system_prompt("y" * EXTENSIVE_CHARS)
    assert r.data["existing_is_extensive"] is False


def test_whitespace_only_existing_treated_as_no_file():
    r = get_system_prompt("   \n\t  ")
    assert r.success
    assert "create" in r.data["instructions"].lower()


def test_missing_prompt_file_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(
        claude_setup, "SYSTEM_PROMPT_PATH", Path("/nonexistent/system_prompt.txt")
    )
    r = get_system_prompt()
    assert r.success is False
    assert "not found" in r.error.lower()


def test_return_statements_offer_permissions_with_reassurance():
    """return_statements come from the synced core DB and must steer the AI to
    offer the permissions allowlist next, with the no-security-risk reassurance."""
    r = get_system_prompt()
    joined = " ".join(r.return_statements).lower()
    assert r.return_statements, "expected DB-backed return_statements (run sync)"
    assert "get_claude_permissions" in joined
    assert ".aimfp-project" in joined
    assert "no network" in joined and "credentials" in joined


def test_does_not_write_anything(tmp_path, monkeypatch):
    """Pure read: calling it must not create CLAUDE.md or touch the cwd."""
    monkeypatch.chdir(tmp_path)
    get_system_prompt()
    get_system_prompt("existing content")
    assert list(tmp_path.iterdir()) == [], "helper must not write any file"


# ============================================================================
# Registration
# ============================================================================

def test_both_bootstrap_tools_registered_and_importable():
    for name in ("get_system_prompt", "get_claude_permissions"):
        assert name in TOOL_REGISTRY, f"{name} not registered"
        assert callable(_effect_import_tool_function(name))


# ============================================================================
# get_claude_permissions (backfill smoke — shares the module, had no tests)
# ============================================================================

def test_permissions_fresh_lists_all_registered_tools():
    r = get_claude_permissions()
    assert r.success
    d = r.data
    allow = d["settings"]["permissions"]["allow"]
    assert d["aimfp_tool_count"] == len(TOOL_REGISTRY)
    assert len(allow) == 2 * len(TOOL_REGISTRY)  # project .mcp.json + plugin
    assert d["merged"] is False
    assert d["settings"]["enableAllProjectMcpServers"] is True
    assert d["settings"]["enabledMcpjsonServers"] == ["aimfp"]
    assert "mcp__aimfp__get_system_prompt" in allow  # the new tool is included
    assert "mcp__plugin_aimfp_aimfp__get_system_prompt" in allow


def test_permissions_merge_refreshes_both_prefixes():
    import json
    existing = json.dumps({"permissions": {"allow": [
        "mcp__aimfp__no_such_tool", "mcp__plugin_aimfp_aimfp__no_such_tool", "Bash(ls)",
    ]}})
    d = get_claude_permissions(existing).data
    assert d["removed_stale"] == [
        "mcp__aimfp__no_such_tool", "mcp__plugin_aimfp_aimfp__no_such_tool",
    ]
    assert d["preserved_other_permissions"] == ["Bash(ls)"]


def test_permissions_merge_preserves_foreign_settings():
    import json
    existing = json.dumps({
        "permissions": {"allow": ["Bash(ls)"], "deny": ["Bash(rm)"]},
        "hooks": {"PostToolUse": []},
        "enabledMcpjsonServers": ["otherServer"],
    })
    s = get_claude_permissions(existing).data["settings"]
    assert "Bash(ls)" in s["permissions"]["allow"]
    assert s["permissions"]["deny"] == ["Bash(rm)"]
    assert "hooks" in s
    assert set(s["enabledMcpjsonServers"]) == {"otherServer", "aimfp"}


def test_permissions_malformed_existing_warns_not_raises():
    r = get_claude_permissions("{not json")
    assert r.success and "warning" in r.data and r.data["merged"] is False
