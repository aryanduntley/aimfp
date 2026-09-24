"""
AIMFP MCP Server - JSON Schema Generation

Converts helper parameter definitions from aimfp_core.db into
MCP-compatible JSON Schema for tool input validation.

All functions are pure — no side effects, deterministic output.
"""

import json
from typing import Dict, Any, Tuple, List, Final


# ============================================================================
# Parameter Type Mapping
# ============================================================================

PARAM_TYPE_MAP: Final[Dict[str, str]] = {
    "string": "string",
    "str": "string",
    "boolean": "boolean",
    "bool": "boolean",
    "integer": "integer",
    "int": "integer",
    "number": "number",
    "float": "number",
    "array": "array",
    "object": "object",
}


# JSON Schema keys a parameter definition may carry verbatim (dev/helpers-json).
# 'required' is NOT here: at parameter level it is AIMFP's boolean flag; a
# nested object states its required keys inside its own items/properties schema.
SCHEMA_PASSTHROUGH_KEYS: Final[Tuple[str, ...]] = (
    "items", "prefixItems", "minItems", "maxItems",
    "enum", "anyOf", "properties", "additionalProperties",
)


# ============================================================================
# Pure Functions
# ============================================================================

def map_param_type(param_type: str) -> str:
    """
    Pure: Map an AIMFP parameter type string to a JSON Schema type.

    Args:
        param_type: AIMFP type string (e.g., 'string', 'int', 'array')

    Returns:
        JSON Schema type string (defaults to 'string' for unknown types)
    """
    return PARAM_TYPE_MAP.get(param_type.lower(), "string")


def param_to_schema_property(param: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pure: Convert a single parameter definition to a JSON Schema property.

    Args:
        param: Parameter dict with keys: name, type, required, default, description,
            plus any SCHEMA_PASSTHROUGH_KEYS (item shapes, enums, alternatives).
            A parameter with anyOf gets no top-level type.

    Returns:
        JSON Schema property definition
    """
    prop: Dict[str, Any] = (
        {} if "anyOf" in param
        else {"type": map_param_type(param.get("type", "string"))}
    )
    prop.update({key: param[key] for key in SCHEMA_PASSTHROUGH_KEYS if key in param})

    description = param.get("description")
    if description:
        prop["description"] = description

    default = param.get("default")
    if default is not None:
        prop["default"] = default

    return prop


def params_to_input_schema(params_json: str) -> Dict[str, Any]:
    """
    Pure: Convert parameter definitions JSON to MCP-compatible JSON Schema.

    Args:
        params_json: JSON string of parameter definitions from aimfp_core.db
                     Format: [{"name": "x", "type": "string", "required": true, ...}, ...]

    Returns:
        JSON Schema object suitable for MCP Tool inputSchema
    """
    params: List[Dict[str, Any]] = json.loads(params_json) if params_json else []

    if not params:
        return {
            "type": "object",
            "properties": {},
        }

    properties: Dict[str, Any] = {}
    required: List[str] = []

    for param in params:
        name = param.get("name", "")
        if not name:
            continue

        properties[name] = param_to_schema_property(param)

        if param.get("required", False):
            required.append(name)

    schema: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }

    if required:
        schema["required"] = required

    return schema
