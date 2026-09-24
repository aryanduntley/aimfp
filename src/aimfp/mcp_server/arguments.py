"""
AIMFP MCP Server - Tool Argument Coercion and Validation

Runs between tools/call and the helper, against the tool's inputSchema:

1. coerce_arguments — convert values to their declared JSON types. Small models
   send booleans, numbers, arrays and objects as strings ("False", "7",
   "[1, 2]"). "False" is truthy in Python, so a boolean sent as a string used
   to flip the meaning of the call.
2. validate_arguments — check the coerced arguments against the schema and
   report every mismatch by path (functions[0].file_id), so the model can fix
   its call on the first retry instead of reading an exception string raised
   from inside the helper.

Deliberately lenient where a stricter check could reject a call that works
today: extra keys inside nested objects are allowed, and null is accepted for
every field that is not required ("NULL = don't update" is a common pattern).

All functions are pure — no side effects, deterministic output.
"""

import json
import re
from typing import Any, Dict, Final, List, Optional, Tuple

MAX_REPORTED_ERRORS: Final[int] = 5

_INTEGER_RE: Final = re.compile(r"^[+-]?\d+$")
_NUMBER_RE: Final = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


# ============================================================================
# Type helpers
# ============================================================================

def schema_types(schema: Dict[str, Any]) -> Tuple[str, ...]:
    """Pure: The declared JSON types of a schema node ('type' may be a list)."""
    declared = schema.get("type")
    if declared is None:
        return ()
    if isinstance(declared, list):
        return tuple(declared)
    return (declared,)


def matches_type(json_type: str, value: Any) -> bool:
    """Pure: True when value is an instance of the JSON type (bool is not a number)."""
    if json_type == "null":
        return value is None
    if json_type == "boolean":
        return isinstance(value, bool)
    if json_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if json_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if json_type == "string":
        return isinstance(value, str)
    if json_type == "array":
        return isinstance(value, (list, tuple))
    if json_type == "object":
        return isinstance(value, dict)
    return True


def _json_or_none(text: str) -> Any:
    """Pure: Parse text as JSON, or None when it is not JSON."""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


# ============================================================================
# Coercion
# ============================================================================

def _coerce_string(types: Tuple[str, ...], text: str) -> Any:
    """Pure: Convert a string to the first declared non-string type it fits."""
    stripped = text.strip()
    if "boolean" in types and stripped.lower() in ("true", "false"):
        return stripped.lower() == "true"
    if "integer" in types and _INTEGER_RE.match(stripped):
        return int(stripped)
    if "number" in types and _NUMBER_RE.match(stripped):
        number = float(stripped)
        return int(number) if number.is_integer() and "." not in stripped else number
    if "array" in types or "object" in types:
        parsed = _json_or_none(stripped)
        if ("array" in types and isinstance(parsed, list)) or (
            "object" in types and isinstance(parsed, dict)
        ):
            return parsed
    if "null" in types and stripped.lower() in ("null", "none"):
        return None
    return text


def coerce_value(schema: Dict[str, Any], value: Any) -> Any:
    """
    Pure: Convert value toward its schema, recursively. Never raises.

    Values that cannot be converted are returned unchanged for
    validate_value to report.
    """
    if value is None or not isinstance(schema, dict):
        return value

    alternatives = schema.get("anyOf")
    if alternatives:
        candidates = tuple(coerce_value(sub, value) for sub in alternatives)
        return next(
            (c for sub, c in zip(alternatives, candidates) if not validate_value(sub, c, "")),
            value,
        )

    types = schema_types(schema)
    if isinstance(value, str) and types and "string" not in types:
        value = _coerce_string(types, value)
    elif "string" in types and not isinstance(value, str) and not any(
        matches_type(t, value) for t in types
    ):
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
    elif "integer" in types and isinstance(value, float) and value.is_integer():
        value = int(value)

    if isinstance(value, (list, tuple)):
        prefix = schema.get("prefixItems")
        items = schema.get("items")
        if prefix:
            return [
                coerce_value(prefix[i], v) if i < len(prefix) else coerce_value(items or {}, v)
                for i, v in enumerate(value)
            ]
        if isinstance(items, dict):
            return [coerce_value(items, v) for v in value]
        return value

    if isinstance(value, dict) and isinstance(schema.get("properties"), dict):
        properties = schema["properties"]
        return {k: coerce_value(properties.get(k, {}), v) for k, v in value.items()}

    return value


def coerce_arguments(input_schema: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: coerce_value over a tool's top-level arguments."""
    properties = input_schema.get("properties", {})
    return {
        name: coerce_value(properties.get(name, {}), value)
        for name, value in arguments.items()
    }


# ============================================================================
# Validation
# ============================================================================

def _preview(value: Any) -> str:
    """Pure: Short 'type value' rendering of an offending value."""
    kind = {
        bool: "boolean", int: "integer", float: "number", str: "string",
        list: "array", tuple: "array", dict: "object", type(None): "null",
    }.get(type(value), type(value).__name__)
    text = json.dumps(value, default=str)
    return f"{kind} {text[:60]}{'...' if len(text) > 60 else ''}"


def validate_value(schema: Dict[str, Any], value: Any, path: str) -> Tuple[str, ...]:
    """
    Pure: Every way value fails its schema, as 'path: problem' strings.

    Supports the subset AIMFP schemas use: type (single or list), enum,
    anyOf, items, prefixItems, minItems, maxItems, properties, required.
    Nested objects allow extra keys and null for non-required properties.
    """
    if not isinstance(schema, dict) or not schema:
        return ()
    where = path or "value"

    alternatives = schema.get("anyOf")
    if alternatives:
        if any(not validate_value(sub, value, path) for sub in alternatives):
            return ()
        return (f"{where}: expected {describe_schema(schema)}, got {_preview(value)}",)

    types = schema_types(schema)
    if types and not any(matches_type(t, value) for t in types):
        return (f"{where}: expected {describe_schema(schema)}, got {_preview(value)}",)

    enum = schema.get("enum")
    if enum is not None and value not in enum:
        allowed = ", ".join(json.dumps(e) for e in enum)
        return (f"{where}: {json.dumps(value)} is not one of {allowed}",)

    if isinstance(value, (list, tuple)):
        return _validate_array(schema, tuple(value), path)
    if isinstance(value, dict):
        return _validate_object(schema, value, path)
    return ()


def _validate_array(schema: Dict[str, Any], values: Tuple[Any, ...], path: str) -> Tuple[str, ...]:
    """Pure: Length bounds, then each element against prefixItems/items."""
    where = path or "value"
    min_items, max_items = schema.get("minItems"), schema.get("maxItems")
    if min_items is not None and len(values) < min_items:
        return (f"{where}: needs at least {min_items} item(s), got {len(values)}",)
    if max_items is not None and len(values) > max_items:
        return (f"{where}: allows at most {max_items} item(s), got {len(values)}",)
    prefix = schema.get("prefixItems") or ()
    items = schema.get("items") if isinstance(schema.get("items"), dict) else {}
    return tuple(
        error
        for index, element in enumerate(values)
        for error in validate_value(
            prefix[index] if index < len(prefix) else items, element, f"{path}[{index}]"
        )
    )


def _validate_object(schema: Dict[str, Any], value: Dict[str, Any], path: str) -> Tuple[str, ...]:
    """Pure: Required keys present (non-null), known properties valid, extras allowed."""
    prefix = f"{path}." if path else ""
    required = tuple(schema.get("required") or ())
    missing = tuple(
        f"{prefix}{key}: required" for key in required if value.get(key) is None
    )
    properties = schema.get("properties") or {}
    invalid = tuple(
        error
        for key, sub in properties.items()
        if value.get(key) is not None
        for error in validate_value(sub, value[key], f"{prefix}{key}")
    )
    return missing + invalid


def validate_arguments(input_schema: Dict[str, Any], arguments: Dict[str, Any]) -> Tuple[str, ...]:
    """
    Pure: Every problem with a tool call's top-level arguments.

    Unknown argument names and missing required ones are reported first
    (the helper would otherwise fail with a TypeError). A non-required
    argument may be null.
    """
    properties = input_schema.get("properties", {})
    required = tuple(input_schema.get("required", ()))
    unknown = tuple(
        f"{name}: unknown argument (valid: {', '.join(sorted(properties)) or 'none'})"
        for name in arguments if name not in properties
    )
    missing = tuple(
        f"{name}: required" for name in required if arguments.get(name) is None
    )
    invalid = tuple(
        error
        for name, value in arguments.items()
        if name in properties and value is not None
        for error in validate_value(properties[name], value, name)
    )
    return unknown + missing + invalid


# ============================================================================
# Shape description (for error messages)
# ============================================================================

def describe_schema(schema: Dict[str, Any], depth: int = 0) -> str:
    """Pure: Compact human/model-readable shape, e.g. 'array of {name: string, file_id?: integer}'."""
    if not isinstance(schema, dict) or not schema:
        return "any"
    if depth > 3:
        return "..."
    if schema.get("anyOf"):
        return " or ".join(describe_schema(sub, depth + 1) for sub in schema["anyOf"])
    if schema.get("enum") is not None:
        return " | ".join(json.dumps(e) for e in schema["enum"])

    types = tuple(t for t in schema_types(schema) if t != "null")
    nullable = "null" in schema_types(schema)
    if types == ("array",):
        prefix = schema.get("prefixItems")
        if prefix:
            text = "[" + ", ".join(describe_schema(p, depth + 1) for p in prefix) + "]"
        else:
            text = "array of " + describe_schema(schema.get("items") or {}, depth + 1)
    elif types == ("object",) and schema.get("properties"):
        required = set(schema.get("required") or ())
        fields = ", ".join(
            f"{key}{'' if key in required else '?'}: {describe_schema(sub, depth + 1)}"
            for key, sub in schema["properties"].items()
        )
        text = "{" + fields + "}"
    else:
        text = " or ".join(types) if types else "any"
    return f"{text} or null" if nullable else text


def format_argument_errors(
    tool_name: str,
    input_schema: Dict[str, Any],
    errors: Tuple[str, ...],
) -> str:
    """
    Pure: One error message naming each bad field and the expected shape of
    every top-level argument involved.
    """
    shown = errors[:MAX_REPORTED_ERRORS]
    more = len(errors) - len(shown)
    properties = input_schema.get("properties", {})
    involved: List[str] = []
    for error in shown:
        top = re.split(r"[.\[:]", error, maxsplit=1)[0]
        if top in properties and top not in involved:
            involved.append(top)
    shapes = "; ".join(f"{name}: {describe_schema(properties[name])}" for name in involved)
    lines = [f"Invalid arguments for '{tool_name}':"]
    lines.extend(f"- {error}" for error in shown)
    if more > 0:
        lines.append(f"- ...and {more} more")
    if shapes:
        lines.append(f"Expected {shapes}")
    return "\n".join(lines)


def check_tool_arguments(
    tool_name: str,
    input_schema: Optional[Dict[str, Any]],
    arguments: Dict[str, Any],
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Pure: Coerce then validate. Returns (coerced arguments, error text or None).

    A tool with no known schema passes through unchanged.
    """
    if not input_schema:
        return (arguments, None)
    coerced = coerce_arguments(input_schema, arguments)
    errors = validate_arguments(input_schema, coerced)
    if errors:
        return (coerced, format_argument_errors(tool_name, input_schema, errors))
    return (coerced, None)
