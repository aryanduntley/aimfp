"""
MCP argument layer (task 37; svamanas items 7, 8, 5).

- every array parameter publishes an item shape (items or anyOf)
- values are coerced to their declared types, recursively ("False" -> False)
- mismatches are reported by path with the expected shape, before the helper runs
"""
import json

import pytest

from aimfp.mcp_server import server
from aimfp.mcp_server.arguments import (
    check_tool_arguments,
    coerce_arguments,
    coerce_value,
    describe_schema,
    validate_arguments,
    validate_value,
)
from aimfp.mcp_server.server import _effect_load_and_cache_tools, handle_call_tool


@pytest.fixture(scope="module")
def schemas():
    _effect_load_and_cache_tools()
    return dict(server._cached_tool_schemas)


# ============================================================================
# Published schemas
# ============================================================================

class TestPublishedSchemas:
    def test_every_array_param_has_an_item_shape(self, schemas):
        bare = [
            (tool, name)
            for tool, schema in schemas.items()
            for name, prop in schema["properties"].items()
            if prop.get("type") == "array" and "items" not in prop and "prefixItems" not in prop
        ]
        assert bare == []

    def test_reserve_functions_item_shape(self, schemas):
        items = schemas["reserve_functions"]["properties"]["functions"]["items"]
        assert items["required"] == ["name", "file_id"]
        assert items["properties"]["file_id"] == {"type": "integer"}
        assert items["properties"]["parameters"]["items"]["required"] == ["name"]

    def test_tuple_shape(self, schemas):
        items = schemas["add_interactions"]["properties"]["interactions"]["items"]
        assert items["minItems"] == items["maxItems"] == 4
        assert items["prefixItems"][2]["enum"] == ["call", "chain", "borrow", "compose", "pipe"]

    def test_any_of_param_has_no_type(self, schemas):
        prop = schemas["find_directives_by_intent_keyword"]["properties"]["keywords"]
        assert "type" not in prop and len(prop["anyOf"]) == 2

    def test_empty_call_never_crashes(self, schemas):
        for tool, schema in schemas.items():
            check_tool_arguments(tool, schema, {})


# ============================================================================
# Coercion
# ============================================================================

class TestCoercion:
    @pytest.mark.parametrize("schema,raw,expected", [
        ({"type": "boolean"}, "False", False),
        ({"type": "boolean"}, " true ", True),
        ({"type": "integer"}, "7", 7),
        ({"type": "integer"}, 7.0, 7),
        ({"type": "number"}, "2.5", 2.5),
        ({"type": "array", "items": {"type": "integer"}}, "[1, 2]", [1, 2]),
        ({"type": "array", "items": {"type": "integer"}}, ["1", "2"], [1, 2]),
        ({"type": "object"}, '{"a": 1}', {"a": 1}),
        ({"type": "string"}, {"k": "v"}, '{"k": "v"}'),
        ({"type": "string"}, 5, "5"),
        ({"type": ["string", "null"]}, None, None),
    ])
    def test_scalar_conversions(self, schema, raw, expected):
        assert coerce_value(schema, raw) == expected

    @pytest.mark.parametrize("schema,raw", [
        ({"type": "boolean"}, "no"),
        ({"type": "boolean"}, "0"),
        ({"type": "integer"}, "seven"),
        ({"type": "array"}, "not json"),
    ])
    def test_unconvertible_left_for_validation(self, schema, raw):
        assert coerce_value(schema, raw) == raw
        assert validate_value(schema, raw, "x")

    def test_nested_boolean_string(self, schemas):
        args = {"functions": json.dumps([
            {"function_id": "3", "name": "f", "file_id": 1, "skip_id_naming": "False"}
        ])}
        coerced, error = check_tool_arguments("finalize_functions", schemas["finalize_functions"], args)
        assert error is None
        assert coerced["functions"][0] == {
            "function_id": 3, "name": "f", "file_id": 1, "skip_id_naming": False,
        }

    def test_tuple_positions_coerced(self, schemas):
        coerced, error = check_tool_arguments(
            "add_interactions", schemas["add_interactions"],
            {"interactions": [["1", "2", "call", None]]},
        )
        assert error is None and coerced["interactions"] == [[1, 2, "call", None]]

    def test_any_of_picks_first_fit(self, schemas):
        prop = schemas["find_directives_by_intent_keyword"]["properties"]["keywords"]
        assert coerce_value(prop, "purity") == "purity"
        assert coerce_value(prop, '["a", "b"]') in ('["a", "b"]', ["a", "b"])
        assert not validate_value(prop, "purity", "keywords")


# ============================================================================
# Validation messages
# ============================================================================

class TestValidation:
    def test_wrong_item_shape_names_path_and_shape(self, schemas):
        _, error = check_tool_arguments(
            "reserve_functions", schemas["reserve_functions"], {"functions": ["print_hi"]}
        )
        assert "functions[0]: expected {name: string, file_id: integer" in error
        assert "Expected functions: array of {" in error

    def test_missing_nested_required(self, schemas):
        _, error = check_tool_arguments(
            "reserve_functions", schemas["reserve_functions"],
            {"functions": [{"name": "f", "purpose": "p"}]},
        )
        assert "functions[0].file_id: required" in error

    def test_unknown_and_missing_top_level(self, schemas):
        errors = validate_arguments(schemas["add_item"], {"bogus": 1})
        assert any(e.startswith("bogus: unknown argument") for e in errors)
        assert any(e.endswith(": required") for e in errors)

    def test_enum_and_tuple_length(self, schemas):
        errors = validate_arguments(
            schemas["add_types_functions"], {"relationships": [[1, 2, "maker"], [1, 2]]}
        )
        assert any("relationships[0][2]" in e and "not one of" in e for e in errors)
        assert any("relationships[1]: needs at least 3" in e for e in errors)

    def test_null_allowed_for_optional(self, schemas):
        assert validate_arguments(schemas["update_task"], {"id": 1, "name": None, "flow_ids": None}) == ()

    def test_extra_nested_keys_allowed(self, schemas):
        assert validate_arguments(
            schemas["add_items"],
            {"reference_table": "tasks", "reference_id": 1,
             "items": [{"name": "a", "note": "extra keys are fine"}]},
        ) == ()

    def test_error_list_is_capped(self, schemas):
        _, error = check_tool_arguments(
            "update_items", schemas["update_items"],
            {"ids": ["a"] * 9, "data": {}, "reference_table": "tasks", "reference_id": 1},
        )
        assert "...and 4 more" in error

    def test_describe_schema(self):
        assert describe_schema({"type": ["string", "null"]}) == "string or null"
        assert describe_schema({"type": "array", "prefixItems": [{"type": "integer"}, {"enum": ["a"]}]}) \
            == '[integer, "a"]'


class TestServerDispatch:
    def test_bad_shape_is_caught_before_the_helper(self, schemas):
        resp = handle_call_tool(1, {"name": "reserve_functions",
                                    "arguments": {"functions": ["print_hi"]}})
        result = resp["result"]
        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert text.startswith("Invalid arguments for 'reserve_functions':")
        assert "AttributeError" not in text

    def test_stringified_boolean_reaches_helper_as_bool(self, schemas, monkeypatch):
        seen = {}

        def fake_helper(**kwargs):
            seen.update(kwargs)
            return {"success": True}

        monkeypatch.setattr(server, "_effect_import_tool_function", lambda name: fake_helper)
        resp = handle_call_tool(2, {"name": "get_function_by_name", "arguments": {
            "function_name": "f", "include_details": "False", "details_only": "true"}})
        assert resp["result"]["isError"] is False
        assert seen == {"function_name": "f", "include_details": False, "details_only": True}
