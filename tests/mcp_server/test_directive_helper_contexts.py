"""
directive_helpers.execution_context must name a real step in the directive's workflow.

Helper specs (dev/helpers-json used_by_directives) record the workflow step where a
directive uses a helper. When a directive workflow is rewritten, those labels silently
go stale — get_directive_by_name / get_helpers_for_directive then point the AI at
steps that no longer exist. Step names are every trunk, then, step, and string
fallback in the workflow. 'self_implementation' / 'self_invocation' mark a tool that
IS the directive's implementation rather than one step of it.
"""
import json
import sqlite3

import pytest

from aimfp.helpers.orchestrators._common import get_core_db_path

CONVENTION_CONTEXTS = frozenset({"self_implementation", "self_invocation"})


def _workflow_steps(node) -> set:
    steps = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("trunk", "then", "step", "fallback") and isinstance(value, str):
                steps.add(value)
            steps |= _workflow_steps(value)
    elif isinstance(node, list):
        for value in node:
            steps |= _workflow_steps(value)
    return steps


def _mappings():
    conn = sqlite3.connect(get_core_db_path())
    try:
        return conn.execute(
            "SELECT d.name, h.name, dh.execution_context, d.workflow "
            "FROM directive_helpers dh "
            "JOIN directives d ON d.id = dh.directive_id "
            "JOIN helper_functions h ON h.id = dh.helper_function_id"
        ).fetchall()
    finally:
        conn.close()


def test_core_db_has_directive_helper_mappings():
    assert _mappings()


@pytest.mark.parametrize("directive, helper, context, workflow", _mappings(),
                         ids=lambda v: v if isinstance(v, str) and len(v) < 60 else "")
def test_execution_context_is_a_workflow_step(directive, helper, context, workflow):
    steps = _workflow_steps(json.loads(workflow or "{}"))
    assert context in steps | CONVENTION_CONTEXTS, (
        f"{helper} -> {directive}: execution_context '{context}' is not a step in the "
        f"current workflow ({sorted(steps)})"
    )
