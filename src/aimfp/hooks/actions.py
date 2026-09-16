"""
AIMFP Hooks - Action Configuration Grammar

What user_directives.action_config is allowed to contain, for every one of
the five action_type values the schema already constrains. The same gap as
trigger_config, one line down in user_directives.sql - a JSON column whose
only specification was an example in a comment.

WHY THIS GETS A DIFFERENT ANSWER THAN TRIGGERS. api_call, script_execution
and notification are generic: AIMFP can say what they must contain and be
right for every project. function_call and command are inherently
project-bound - the function or the executable exists only in the caller's
own registry, and AIMFP has no way to know whether it resolves. So AIMFP
defines the ENVELOPE per action_type, and the caller validates that the
target actually resolves. That is the same line already drawn for event and
condition triggers: shape is AIMFP's, resolution is the caller's.

WHY THIS MATTERED LESS THAN TRIGGERS. A bad action fails LOUDLY - the
handler raises, record_execution_end writes an error log line, and the
monitoring tools surface it. A bad schedule fails silently by never firing,
producing no log line and no recorded error. Silence is the harder failure
to find, so triggers were done first.

THE ENVELOPES:

    api_call:         {"endpoint": "/lights/off", "api": "homeassistant"}
    script_execution: {"script": "scripts/backup.sh", "args": ["--full"]}
    command:          {"command": "systemctl restart nginx"}
    function_call:    {"function": "handlers.lights.turn_off"}
    notification:     {"message": "Backup finished", "channel": "ops"}

Unknown keys are errors, not ignored, exactly as in the trigger grammar.

Pure throughout - no clock, no database, no filesystem. Stdlib only, by
package contract.
"""

from typing import Any, Dict, Optional, Tuple

from ._common import (
    check_enum,
    check_keys,
    check_mapping,
    check_non_empty_string,
    check_positive_int,
    check_sequence,
    check_string_array,
    check_values,
    coerce_config,
    shape,
)
from .results import ActionConfigValidation

_COLUMN = 'action_config'

ACTION_TYPES: Tuple[str, ...] = (
    'api_call', 'script_execution', 'function_call', 'command', 'notification')

HTTP_METHODS: Tuple[str, ...] = (
    'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS')

# Action types AIMFP can only describe, never verify. The name of a function
# or an executable is meaningful solely inside the caller's project.
CALLER_RESOLVED_ACTIONS: Tuple[str, ...] = ('function_call', 'command')

# (required keys, optional keys) per action_type.
_ACTION_KEYS: Dict[str, Tuple[frozenset, frozenset]] = {
    'api_call': (
        frozenset({'endpoint'}),
        frozenset({'api', 'method', 'headers', 'body', 'timeout_seconds'}),
    ),
    'script_execution': (
        frozenset({'script'}),
        frozenset({'args', 'cwd', 'env', 'timeout_seconds'}),
    ),
    'function_call': (
        frozenset({'function'}),
        frozenset({'module', 'args', 'kwargs'}),
    ),
    'command': (
        frozenset({'command'}),
        frozenset({'cwd', 'env', 'timeout_seconds'}),
    ),
    'notification': (
        frozenset({'message'}),
        frozenset({'channel', 'title', 'priority'}),
    ),
}

_EXAMPLES: Dict[str, Any] = {
    'api_call': {'api': 'homeassistant', 'endpoint': '/lights/off', 'method': 'POST'},
    'script_execution': {'script': 'scripts/backup.sh', 'args': ['--full']},
    'function_call': {'function': 'handlers.lights.turn_off'},
    'command': {'command': 'systemctl restart nginx'},
    'notification': {'message': 'Nightly backup finished', 'channel': 'ops'},
}

_NOTES: Dict[str, str] = {
    'function_call': (
        'AIMFP validates the envelope only. Whether "function" names something '
        'importable is the caller\'s to check - it exists in the generated '
        'project, not in AIMFP.'
    ),
    'command': (
        'AIMFP validates the envelope only. Whether the executable exists and '
        'is permitted is the caller\'s to check.'
    ),
}


# ============================================================================
# Pure Field Validators
# ============================================================================

def _check_method(value: Any, key: str) -> Tuple[str, ...]:
    """Pure: Validate an HTTP method, uppercase and from the known set."""
    return check_enum(value, key, HTTP_METHODS)


def _check_priority(value: Any, key: str) -> Tuple[str, ...]:
    """Pure: Validate a notification priority."""
    return check_enum(value, key, ('low', 'normal', 'high', 'urgent'))


def _check_command(value: Any, key: str) -> Tuple[str, ...]:
    """
    Pure: Validate a command as either a string or an argv array.

    Both spellings are accepted because both are genuinely common and neither
    is a formatting preference: a string goes through a shell, an array does
    not, and that difference is the caller's to make deliberately.
    """
    if isinstance(value, str):
        return check_non_empty_string(value, key)
    if isinstance(value, (list, tuple)):
        return check_string_array(value, key)
    return (f"{key}: must be a string or an array of strings, "
            f"got {type(value).__name__}",)


def _check_any_array(value: Any, key: str) -> Tuple[str, ...]:
    """Pure: Validate a non-empty array whose element types are unconstrained."""
    return check_sequence(value, key)


_FIELD_CHECKS = {
    'endpoint': check_non_empty_string,
    'api': check_non_empty_string,
    'method': _check_method,
    'headers': check_mapping,
    'timeout_seconds': check_positive_int,
    'script': check_non_empty_string,
    'args': check_string_array,
    'cwd': check_non_empty_string,
    'env': check_mapping,
    'function': check_non_empty_string,
    'module': check_non_empty_string,
    'kwargs': check_mapping,
    'command': _check_command,
    'message': check_non_empty_string,
    'channel': check_non_empty_string,
    'title': check_non_empty_string,
    'priority': _check_priority,
}

# 'body' takes any JSON value - a request payload is the caller's shape, not
# AIMFP's - so it is deliberately absent from _FIELD_CHECKS. Being a
# recognised key is the whole of its specification.

# function_call passes positional arguments of arbitrary type, so its 'args'
# cannot use the string-array check that script_execution's does.
_PER_ACTION_CHECKS: Dict[str, Dict[str, Any]] = {
    'function_call': {**_FIELD_CHECKS, 'args': _check_any_array},
}


# ============================================================================
# Public Hooks - Action Grammar
# ============================================================================

def validate_action_config(
    action_type: str,
    action_config: Any,
) -> ActionConfigValidation:
    """
    HOOK (library API, not an MCP tool).

    Pure: Decide whether an action_config carries the envelope its
    action_type requires.

    Accepts a mapping or the raw JSON text stored in the column, so
    insert-time validation and runner-side inspection cannot reach different
    verdicts about the same row.

    THE ENVELOPE ONLY. For function_call and command, AIMFP checks that a
    target is named and well-formed; whether it resolves is the caller's to
    determine, because the function or executable exists in the generated
    project rather than in AIMFP. caller_resolved marks exactly that case.

    Args:
        action_type: One of the five action types the schema constrains
        action_config: Mapping or JSON string to check

    Returns:
        ActionConfigValidation. valid is the verdict; errors names each
        offending key; caller_resolved is True when AIMFP has validated all
        it can and the remaining check belongs to the caller.
    """
    if action_type not in ACTION_TYPES:
        return ActionConfigValidation(
            valid=False,
            errors=(f"action_type: must be one of {list(ACTION_TYPES)}, "
                    f"got {action_type!r}",),
        )

    config, errors = coerce_config(action_config, _COLUMN)
    if config is None:
        return ActionConfigValidation(
            valid=False,
            action_type=action_type,
            errors=errors,
        )

    required, optional = _ACTION_KEYS[action_type]
    label = f"an action of type '{action_type}'"
    field_checks = _PER_ACTION_CHECKS.get(action_type, _FIELD_CHECKS)

    problems = check_keys(config, required, optional, label, _COLUMN)
    problems += check_values(config, required | optional, field_checks, _COLUMN)

    return ActionConfigValidation(
        valid=not problems,
        action_type=action_type,
        errors=problems,
        config=config,
        caller_resolved=action_type in CALLER_RESOLVED_ACTIONS,
    )


def action_config_grammar(action_type: Optional[str] = None) -> Dict[str, Any]:
    """
    HOOK (library API, not an MCP tool).

    Pure: Return the envelope every action_config must satisfy.

    Single source for the validator's rules, the MCP tool's failure response
    and the directive documentation, so the documented spec cannot drift from
    the enforced one.

    Args:
        action_type: Narrow the result to one action type; omit for all five

    Returns:
        A plain dict keyed by action_type, each entry carrying required and
        optional keys, one conforming example, and - for the two AIMFP cannot
        verify - a note saying what the caller must still check. An
        unrecognised action_type yields {}.
    """
    grammar: Dict[str, Any] = {}
    for name, keys in _ACTION_KEYS.items():
        entry = {**shape(*keys), 'example': _EXAMPLES[name]}
        if name in CALLER_RESOLVED_ACTIONS:
            entry['caller_resolved'] = True
            entry['note'] = _NOTES[name]
        grammar[name] = entry

    grammar['api_call']['http_methods'] = list(HTTP_METHODS)

    if action_type is None:
        return grammar
    entry = grammar.get(action_type)
    return {action_type: entry} if entry is not None else {}
