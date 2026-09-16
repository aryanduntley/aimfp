"""
Hook registration contract tests (design: project notes 34, 35, 37).

The invariant these protect: a hook is AIMFP's library surface for code
running OUTSIDE AIMFP, and must NEVER become AI-callable. If run_directive
were dispatchable as an MCP tool, the AI could execute a user's automation
inside an MCP session at an arbitrary moment, with the AI's process as the
runtime - the exact thing the hook execution model exists to prevent.

Also guards the failure mode that motivated the is_hook column: the previous
query described hooks as "is_tool = 0 AND is_sub_helper = 0" - an absence, not
a declaration - and so returned an empty list silently for as long as nothing
populated that gap. test_get_hooks_returns_registered_hooks fails loudly now.
"""
import sqlite3

import pytest

from aimfp.helpers.core.directives_1 import get_all_directives
from aimfp.helpers.core.directives_2 import get_helper_by_name, get_hooks
from aimfp.database.connection import get_core_db_path
from aimfp.mcp_server.registry import TOOL_REGISTRY

# The UC2 runtime hooks that must always be registered
EXPECTED_HOOKS = {
    'get_due_directives',
    'run_directive',
    'record_execution_start',
    'record_execution_end',
    'record_directive_error',
    'set_next_scheduled_time',
    'hooks_available',
}


@pytest.fixture(scope="module")
def core_rows():
    """Every helper_functions row from the shipped core database."""
    conn = sqlite3.connect(get_core_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT name, is_tool, is_sub_helper, is_hook, file_path "
        "FROM helper_functions"
    ).fetchall()
    conn.close()
    return rows


# ============================================================================
# Registration
# ============================================================================

def test_get_hooks_returns_registered_hooks():
    """The exact failure the old absence-query had: silently empty."""
    result = get_hooks()
    assert result.success is True
    assert result.helpers, "get_hooks returned nothing - hooks are unregistered"
    assert {h.name for h in result.helpers} == EXPECTED_HOOKS


def test_every_hook_is_flagged_and_not_a_tool():
    for hook in get_hooks().helpers:
        assert hook.is_hook is True, hook.name
        assert hook.is_tool is False, hook.name


def test_hooks_live_in_the_hooks_package():
    """A hook outside src/aimfp/hooks/ means the boundary has blurred."""
    for hook in get_hooks().helpers:
        assert hook.file_path.startswith('hooks/'), \
            f"{hook.name} is flagged is_hook but lives at {hook.file_path}"


def test_hook_parameters_are_populated():
    """Signatures are the whole point: the AI generates calls from them."""
    for hook in get_hooks().helpers:
        if hook.name == 'hooks_available':
            expected = {'project_root'}
        else:
            expected = None
        names = {p['name'] for p in hook.parameters}
        assert names, f"{hook.name} has no parameter specs"
        if expected:
            assert names == expected


def test_record_execution_end_spec_includes_traceback_text():
    """
    Regression guard for note 37: the parameter was added after the first
    implementation, when run_directive was double-logging failures.
    """
    hook = next(h for h in get_hooks().helpers if h.name == 'record_execution_end')
    assert 'traceback_text' in {p['name'] for p in hook.parameters}


# ============================================================================
# The invariant: hooks must never be AI-callable
# ============================================================================

def test_no_hook_is_in_the_tool_registry():
    hooks = {h.name for h in get_hooks().helpers}
    leaked = hooks & set(TOOL_REGISTRY)
    assert not leaked, f"hooks are AI-callable: {sorted(leaked)}"


def test_no_helper_is_both_hook_and_tool(core_rows):
    contradictory = [r['name'] for r in core_rows if r['is_hook'] and r['is_tool']]
    assert not contradictory, f"marked both is_hook and is_tool: {contradictory}"


def test_get_hooks_itself_is_a_tool_not_a_hook():
    """The discovery tool is callable; what it describes is not."""
    result = get_helper_by_name('get_hooks')
    assert result.success is True
    assert result.helper.is_tool is True
    assert result.helper.is_hook is False
    assert 'get_hooks' in TOOL_REGISTRY


def test_get_hooks_return_statements_carry_import_guidance():
    """
    A code-generation target needs the import path and shape, which a plain
    tool listing does not carry.
    """
    statements = ' '.join(get_hooks().return_statements)
    assert 'aimfp.hooks' in statements
    assert 'run_directive' in statements
    assert 'CANNOT call' in statements or 'cannot call' in statements


# ============================================================================
# The rename left nothing behind
# ============================================================================

def test_old_absence_query_is_gone(core_rows):
    names = {r['name'] for r in core_rows}
    assert 'get_helpers_not_tool_not_sub' not in names
    assert 'get_helpers_not_tool_not_sub' not in TOOL_REGISTRY


def test_is_hook_defaults_false_for_every_other_helper(core_rows):
    """Absence of is_hook in a spec must never be an error, just false."""
    non_hooks = [r for r in core_rows if r['name'] not in EXPECTED_HOOKS]
    assert non_hooks
    assert all(r['is_hook'] == 0 for r in non_hooks)


def test_sub_helpers_are_untouched(core_rows):
    """User decision 2026-09-15: sub-helpers stay exactly as they are."""
    subs = [r['name'] for r in core_rows if r['is_sub_helper']]
    assert len(subs) == 5, subs


# ============================================================================
# Regression: is_hook was briefly added to the wrong record mapper
# ============================================================================

def test_directive_mapping_still_works():
    """
    row_to_directive and row_to_helper end in identical lines; an is_hook
    kwarg landed in the directive mapper first, which DirectiveRecord does
    not accept.
    """
    result = get_all_directives()
    assert result.success is True
    assert len(result.directives) > 100


def test_normal_tools_report_is_hook_false():
    result = get_helper_by_name('reserve_file')
    assert result.success is True
    assert result.helper.is_hook is False
    assert result.helper.is_tool is True
