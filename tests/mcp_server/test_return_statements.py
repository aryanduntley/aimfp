"""
Every MCP tool must fetch its return statements.

The server serializes whatever a helper returns and injects nothing, so a helper
that never calls get_return_statements("<tool>") silently drops both the core
statements from aimfp_core.db and any custom statements a user attached to that
tool via set_custom_return_statement.
"""
import dataclasses
import importlib
import inspect
import typing

import pytest

from aimfp.mcp_server.registry import TOOL_REGISTRY


def _tool_function(name):
    module_path, function_name = TOOL_REGISTRY[name]
    return getattr(importlib.import_module(module_path), function_name)


@pytest.mark.parametrize("tool", sorted(TOOL_REGISTRY))
def test_tool_fetches_its_own_return_statements(tool):
    source = inspect.getsource(_tool_function(tool))
    assert (f'get_return_statements("{tool}")' in source
            or f"get_return_statements('{tool}')" in source), (
        f"{tool} never calls get_return_statements('{tool}')"
    )


@pytest.mark.parametrize("tool", sorted(TOOL_REGISTRY))
def test_tool_result_type_can_carry_return_statements(tool):
    result_type = typing.get_type_hints(_tool_function(tool)).get("return")
    if dataclasses.is_dataclass(result_type):
        assert "return_statements" in {f.name for f in dataclasses.fields(result_type)}, (
            f"{tool} returns {result_type.__name__}, which has no return_statements field"
        )
