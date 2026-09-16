"""
AIMFP Hooks - Shared Config Validation

The field and structure checks that both config grammars need, extracted the
moment there were two of them. trigger_config and action_config are
different specifications, but "is this a positive integer", "which required
keys are missing", and "did this arrive as a mapping or as JSON text" are the
same questions in both, and two copies would drift in their wording before
they drifted in their rules.

Follows the per-category _common.py convention already used by
helpers/core/, helpers/project/ and helpers/user_directives/.

Everything here is pure: no clock, no database, no filesystem. A config must
validate identically at insert time inside an MCP session and at 3am inside
an unattended runner, so nothing in this module may consult the environment.
Stdlib only, by package contract.
"""

import json
from typing import Any, Dict, Mapping, Optional, Tuple


# ============================================================================
# Field Checks
# ============================================================================

def is_int(value: Any) -> bool:
    """
    Pure: True for a real integer.

    bool subclasses int in Python, so a naive isinstance check accepts
    {"seconds": True}. It is not an integer for any purpose here.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def check_positive_int(value: Any, key: str) -> Tuple[str, ...]:
    """Pure: Validate a field that must be an integer greater than zero."""
    if not is_int(value):
        return (f"{key}: must be an integer, got {type(value).__name__}",)
    if value <= 0:
        return (f"{key}: must be greater than 0, got {value}",)
    return ()


def check_non_empty_string(value: Any, key: str) -> Tuple[str, ...]:
    """Pure: Validate a required, non-blank string field."""
    if not isinstance(value, str):
        return (f"{key}: must be a string, got {type(value).__name__}",)
    if not value.strip():
        return (f"{key}: must not be empty",)
    return ()


def check_sequence(value: Any, key: str) -> Tuple[str, ...]:
    """Pure: Validate that a field is a non-empty JSON array."""
    if not isinstance(value, (list, tuple)):
        return (f"{key}: must be an array, got {type(value).__name__}",)
    if not value:
        return (f"{key}: must not be empty",)
    return ()


def check_string_array(value: Any, key: str) -> Tuple[str, ...]:
    """Pure: Validate a non-empty array whose every element is a string."""
    shape = check_sequence(value, key)
    if shape:
        return shape
    return tuple(
        f"{key}[{index}]: must be a string, got {type(item).__name__}"
        for index, item in enumerate(value)
        if not isinstance(item, str)
    )


def check_mapping(value: Any, key: str) -> Tuple[str, ...]:
    """Pure: Validate that a field is a JSON object, empty permitted."""
    if not isinstance(value, Mapping):
        return (f"{key}: must be an object, got {type(value).__name__}",)
    return ()


def check_enum(value: Any, key: str, allowed: Tuple[str, ...]) -> Tuple[str, ...]:
    """Pure: Validate a field against a closed set of string values."""
    if not isinstance(value, str):
        return (f"{key}: must be a string, got {type(value).__name__}",)
    if value not in allowed:
        return (f"{key}: must be one of {list(allowed)}, got {value!r}",)
    return ()


# ============================================================================
# Structure Checks
# ============================================================================

def coerce_config(
    value: Any,
    column: str,
) -> Tuple[Optional[Dict[str, Any]], Tuple[str, ...]]:
    """
    Pure: Normalise a stored JSON column into a dict.

    These columns arrive as a dict from an in-memory caller and as JSON text
    from the database, and both must validate identically - otherwise
    insert-time and runner-side checks could reach different verdicts about
    the very same row.

    Args:
        value: The payload, as a mapping, JSON text, or something wrong
        column: Column name, so error messages name the real field

    Returns:
        (config, errors). config is None exactly when errors is non-empty.
    """
    if value is None:
        return None, (f'{column}: is required and must be a JSON object',)

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as exc:
            return None, (f'{column}: is not valid JSON ({exc})',)
        return coerce_config(parsed, column)

    if isinstance(value, Mapping):
        return dict(value), ()

    return None, (f'{column}: must be a JSON object, got {type(value).__name__}',)


def check_keys(
    config: Dict[str, Any],
    required: frozenset,
    optional: frozenset,
    label: str,
    column: str,
) -> Tuple[str, ...]:
    """
    Pure: Report missing required keys and keys that do not belong.

    Unknown keys are errors, not noise. Ignoring them means a config with a
    typo'd key validates cleanly and then behaves in a way nobody asked for -
    silently, in the trigger case, which is the whole reason these grammars
    exist.

    Args:
        config: The normalised payload
        required: Keys that must be present
        optional: Keys that may be present
        label: Human phrase naming what is being validated, for the message
        column: Column name, so each message names the real field

    Returns:
        One message per missing or unrecognised key, sorted for determinism
    """
    present = frozenset(config)
    errors = []

    for key in sorted(required - present):
        errors.append(f"{column}.{key}: is required for {label}")

    for key in sorted(present - required - optional):
        allowed = sorted(required | optional)
        errors.append(f"{column}.{key}: is not a recognised key for {label}; "
                      f"allowed keys are {allowed}")

    return tuple(errors)


def check_values(
    config: Dict[str, Any],
    known: frozenset,
    field_checks: Dict[str, Any],
    column: str,
    skip: frozenset = frozenset(),
) -> Tuple[str, ...]:
    """
    Pure: Run every present, recognised key through its field validator.

    Only recognised keys are checked: an unknown key has already been
    reported by check_keys, and running a validator it was never meant for
    would bury that message under a second, more confusing one.

    Args:
        config: The normalised payload
        known: Keys recognised for this variant
        field_checks: Key -> checker taking (value, qualified_key)
        column: Column name, used to qualify each key in messages
        skip: Keys handled elsewhere, such as a discriminator

    Returns:
        Every field-level error, in sorted key order
    """
    errors = []
    for key in sorted((frozenset(config) & known) - skip):
        check = field_checks.get(key)
        if check is not None:
            errors.extend(check(config[key], f'{column}.{key}'))
    return tuple(errors)


def shape(required: frozenset, optional: frozenset) -> Dict[str, Any]:
    """Pure: Render one variant's key sets for a published grammar."""
    return {'required': sorted(required), 'optional': sorted(optional)}
