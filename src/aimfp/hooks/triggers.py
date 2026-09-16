"""
AIMFP Hooks - Trigger Configuration Grammar

What user_directives.trigger_config is allowed to contain, for every one of
the four trigger_type values the schema already constrains.

WHY AIMFP OWNS THIS. The column has always been JSON NOT NULL whose entire
specification was an example in a SQL comment, so every Use Case 2 project
that parsed it invented its own shape. That is the same drift run_directive
exists to prevent - one implementation shared by every UC2 project is what
stops the format drifting per project - and the stakes are higher here. A log
format that drifts is an annoyance; a schedule format that drifts means two
projects disagree about when 17:00 is.

WHAT THIS DOES NOT DO. Defining the structure of a condition is not deciding
whether it holds. AIMFP still cannot evaluate an event or a condition, and
is_due still hands those to the caller untouched. Shape is AIMFP's; judgment
is the caller's.

THE GRAMMAR. Time triggers are a discriminated union on "kind", mirroring the
trigger_type and action_type enums already in the schema:

    {"kind": "interval", "seconds": 900}
    {"kind": "daily",    "at": "17:00", "timezone": "America/New_York"}
    {"kind": "weekly",   "at": "17:00", "weekdays": ["mon", "wed", "fri"]}
    {"kind": "monthly",  "at": "09:00", "days": [1, 15]}

    event:     {"event": "stove_on", "source": "home_assistant"}
    condition: {"expression": "cpu > 90", "evaluate_every_seconds": 60}
    manual:    {}

Unknown keys are rejected rather than ignored. A typo'd key in a schedule is
precisely the failure this module exists to surface: a bad action fails
loudly with a recorded error, while a bad schedule fails silently by never
firing.

TIMEZONES ARE CHECKED FOR SHAPE, NOT RESOLVED. Whether this machine has a tz
database installed is a property of the machine, not of the config, and a
pure validator must give the same answer everywhere. Rejecting
"America/New_York" at insert time because tzdata is missing would be wrong.
Resolution failure is next_fire_time's to report, at the moment it actually
matters.

Stdlib only, by package contract - no croniter, no dateutil.
"""

import re
from typing import Any, Dict, Optional, Tuple

from ._common import (
    check_keys,
    check_positive_int,
    check_non_empty_string,
    check_sequence,
    check_values,
    coerce_config,
    is_int,
    shape,
)
from .results import TriggerConfigValidation

_COLUMN = 'trigger_config'

# ============================================================================
# Grammar
# ============================================================================

# Time triggers are a discriminated union; the other three are fixed shapes.
TIME_KINDS: Tuple[str, ...] = ('interval', 'daily', 'weekly', 'monthly')

# Lowercase three-letter tokens, ordered Monday-first to match datetime's
# weekday() so next_fire_time can index straight into this tuple.
WEEKDAY_TOKENS: Tuple[str, ...] = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')

TRIGGER_TYPES: Tuple[str, ...] = ('time', 'event', 'condition', 'manual')

# (required keys, optional keys) per time kind. "kind" is required everywhere
# because it is the discriminator. An interval takes no timezone: it counts
# elapsed seconds, so accepting one would imply a wall-clock meaning it does
# not have.
_TIME_KIND_KEYS: Dict[str, Tuple[frozenset, frozenset]] = {
    'interval': (frozenset({'kind', 'seconds'}), frozenset()),
    'daily': (frozenset({'kind', 'at'}), frozenset({'timezone'})),
    'weekly': (frozenset({'kind', 'at', 'weekdays'}), frozenset({'timezone'})),
    'monthly': (frozenset({'kind', 'at', 'days'}), frozenset({'timezone'})),
}

# (required keys, optional keys) for the triggers AIMFP cannot evaluate. The
# shapes are deliberately thin: AIMFP records what the caller is watching for
# without claiming to understand it.
_NON_TIME_KEYS: Dict[str, Tuple[frozenset, frozenset]] = {
    'event': (frozenset({'event'}), frozenset({'source'})),
    'condition': (frozenset({'expression'}), frozenset({'evaluate_every_seconds'})),
    'manual': (frozenset(), frozenset()),
}

_EXAMPLES: Dict[str, Any] = {
    'time': {
        'interval': {'kind': 'interval', 'seconds': 900},
        'daily': {'kind': 'daily', 'at': '17:00', 'timezone': 'America/New_York'},
        'weekly': {'kind': 'weekly', 'at': '17:00', 'weekdays': ['mon', 'wed', 'fri']},
        'monthly': {'kind': 'monthly', 'at': '09:00', 'days': [1, 15]},
    },
    'event': {'event': 'stove_on', 'source': 'home_assistant'},
    'condition': {'expression': 'cpu_percent > 90', 'evaluate_every_seconds': 60},
    'manual': {},
}

# HH:MM, 24-hour, both parts zero-padded. Deliberately strict: accepting
# "9:00" as well as "09:00" is the first step toward two projects formatting
# the same schedule differently.
_TIME_OF_DAY = re.compile(r'^([01][0-9]|2[0-3]):([0-5][0-9])$')

# An IANA name is slash-separated segments of word characters, plus and
# minus. Checked for shape only - see the module docstring on why this does
# not resolve the zone.
_IANA_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$')

_MAX_INTERVAL_SECONDS = 31_622_400  # 366 days; beyond this, use monthly


# ============================================================================
# Pure Field Validators
# ============================================================================


def _check_time_of_day(value: Any, key: str) -> Tuple[str, ...]:
    """Pure: Validate an HH:MM 24-hour clock time."""
    if not isinstance(value, str):
        return (f"{key}: must be a string in 24-hour HH:MM form, got {type(value).__name__}",)
    if not _TIME_OF_DAY.match(value):
        return (f"{key}: must be 24-hour HH:MM with both parts zero-padded "
                f"(e.g. '09:00', '17:30'), got {value!r}",)
    return ()


def _check_timezone(value: Any, key: str) -> Tuple[str, ...]:
    """
    Pure: Validate an IANA timezone name by shape.

    Does not resolve the zone. See the module docstring: tz database
    availability is a property of the machine, not of the config.
    """
    if not isinstance(value, str):
        return (f"{key}: must be an IANA timezone name string, got {type(value).__name__}",)
    if not _IANA_NAME.match(value):
        return (f"{key}: must be an IANA timezone name such as 'America/New_York' "
                f"or 'UTC', got {value!r}",)
    return ()


def _check_interval_seconds(value: Any, key: str) -> Tuple[str, ...]:
    """Pure: Validate a repeat interval in seconds."""
    if not is_int(value):
        return (f"{key}: must be an integer number of seconds, got {type(value).__name__}",)
    if value <= 0:
        return (f"{key}: must be greater than 0, got {value}",)
    if value > _MAX_INTERVAL_SECONDS:
        return (f"{key}: must be at most {_MAX_INTERVAL_SECONDS} seconds (366 days); "
                f"use kind 'monthly' for longer cycles, got {value}",)
    return ()





def _check_weekdays(value: Any, key: str) -> Tuple[str, ...]:
    """Pure: Validate a weekly trigger's weekday set."""
    shape = check_sequence(value, key)
    if shape:
        return shape

    errors = []
    seen = set()
    for index, token in enumerate(value):
        position = f"{key}[{index}]"
        if not isinstance(token, str):
            errors.append(f"{position}: must be a string, got {type(token).__name__}")
            continue
        if token not in WEEKDAY_TOKENS:
            errors.append(f"{position}: must be one of {list(WEEKDAY_TOKENS)}, got {token!r}")
            continue
        if token in seen:
            errors.append(f"{position}: duplicate weekday {token!r}")
        seen.add(token)
    return tuple(errors)


def _check_days(value: Any, key: str) -> Tuple[str, ...]:
    """
    Pure: Validate a monthly trigger's day-of-month set.

    31 is accepted for every month. What a directive scheduled for the 31st
    does in February is next_fire_time's decision, not a reason to reject the
    config.
    """
    shape = check_sequence(value, key)
    if shape:
        return shape

    errors = []
    seen = set()
    for index, day in enumerate(value):
        position = f"{key}[{index}]"
        if not is_int(day):
            errors.append(f"{position}: must be an integer, got {type(day).__name__}")
            continue
        if not 1 <= day <= 31:
            errors.append(f"{position}: must be between 1 and 31, got {day}")
            continue
        if day in seen:
            errors.append(f"{position}: duplicate day {day}")
        seen.add(day)
    return tuple(errors)


# Field name -> validator, so a key is checked the same way wherever it appears.
_FIELD_CHECKS = {
    'seconds': _check_interval_seconds,
    'at': _check_time_of_day,
    'timezone': _check_timezone,
    'weekdays': _check_weekdays,
    'days': _check_days,
    'event': check_non_empty_string,
    'source': check_non_empty_string,
    'expression': check_non_empty_string,
    'evaluate_every_seconds': check_positive_int,
}


# ============================================================================
# Pure Structure Validators
# ============================================================================


def _validate_time(config: Dict[str, Any]) -> Tuple[Optional[str], Tuple[str, ...]]:
    """
    Pure: Validate a time trigger, dispatching on its kind discriminator.

    Returns the resolved kind alongside the errors so the caller can report
    which branch of the union was actually checked.
    """
    kind = config.get('kind')
    if kind is None:
        return None, (f"trigger_config.kind: is required for a time trigger; "
                      f"must be one of {list(TIME_KINDS)}",)
    if not isinstance(kind, str) or kind not in _TIME_KIND_KEYS:
        return None, (f"trigger_config.kind: must be one of {list(TIME_KINDS)}, "
                      f"got {kind!r}",)

    required, optional = _TIME_KIND_KEYS[kind]
    label = f"a time trigger of kind {kind!r}"
    errors = check_keys(config, required, optional, label, _COLUMN)
    errors += check_values(
        config, required | optional, _FIELD_CHECKS, _COLUMN, skip=frozenset({'kind'}))
    return kind, errors


def _validate_non_time(
    trigger_type: str,
    config: Dict[str, Any],
) -> Tuple[str, ...]:
    """Pure: Validate an event, condition or manual trigger's fixed shape."""
    required, optional = _NON_TIME_KEYS[trigger_type]

    if trigger_type == 'manual' and config:
        return (f"trigger_config: a manual trigger takes no configuration; "
                f"expected {{}}, got keys {sorted(config)}",)

    label = f"a {trigger_type} trigger"
    errors = check_keys(config, required, optional, label, _COLUMN)
    errors += check_values(config, required | optional, _FIELD_CHECKS, _COLUMN)
    return errors


# ============================================================================
# Public Hooks - Grammar
# ============================================================================

def validate_trigger_config(
    trigger_type: str,
    trigger_config: Any,
) -> TriggerConfigValidation:
    """
    HOOK (library API, not an MCP tool).

    Pure: Decide whether a trigger_config conforms to AIMFP's grammar.

    Accepts either a mapping or the raw JSON text stored in the column, so
    insert-time validation and runner-side inspection cannot reach different
    verdicts about the same row.

    Every field-level problem is reported, not just the first, and each
    message names the offending key - one call should be enough to fix the
    whole config.

    This never decides whether an event fired or a condition holds. It
    decides whether the config describing them is well-formed.

    Args:
        trigger_type: 'time', 'event', 'condition', or 'manual'
        trigger_config: Mapping or JSON string to check

    Returns:
        TriggerConfigValidation. valid is the verdict; errors carries the
        details; kind echoes the resolved time-trigger kind when one applies;
        config carries the payload normalised to a dict, so a caller that
        validates and then uses it parses the JSON exactly once.
    """
    if trigger_type not in TRIGGER_TYPES:
        return TriggerConfigValidation(
            valid=False,
            trigger_type=None,
            errors=(f"trigger_type: must be one of {list(TRIGGER_TYPES)}, "
                    f"got {trigger_type!r}",),
        )

    config, errors = coerce_config(trigger_config, _COLUMN)
    if config is None:
        return TriggerConfigValidation(
            valid=False,
            trigger_type=trigger_type,
            errors=errors,
        )

    if trigger_type == 'time':
        kind, time_errors = _validate_time(config)
        return TriggerConfigValidation(
            valid=not time_errors,
            trigger_type=trigger_type,
            kind=kind,
            errors=time_errors,
            config=config,
        )

    non_time_errors = _validate_non_time(trigger_type, config)
    return TriggerConfigValidation(
        valid=not non_time_errors,
        trigger_type=trigger_type,
        errors=non_time_errors,
        config=config,
    )


def trigger_config_grammar(trigger_type: Optional[str] = None) -> Dict[str, Any]:
    """
    HOOK (library API, not an MCP tool).

    Pure: Return the grammar every trigger_config must satisfy.

    The validator, the MCP tool's failure response and the directive
    documentation all read the spec from here, so what AIMFP documents cannot
    drift from what AIMFP enforces.

    Args:
        trigger_type: Narrow the result to one trigger type; omit for all four

    Returns:
        A plain dict keyed by trigger_type. Time triggers carry a 'kinds' map
        of required/optional keys and an example per kind; the others carry
        required/optional keys and one example. An unrecognised trigger_type
        yields {}.
    """
    grammar: Dict[str, Any] = {
        'time': {
            'discriminator': 'kind',
            'kinds': {
                kind: {
                    **shape(*keys),
                    'example': _EXAMPLES['time'][kind],
                }
                for kind, keys in _TIME_KIND_KEYS.items()
            },
        },
    }
    for name, keys in _NON_TIME_KEYS.items():
        grammar[name] = {**shape(*keys), 'example': _EXAMPLES[name]}

    grammar['time']['weekday_tokens'] = list(WEEKDAY_TOKENS)

    if trigger_type is None:
        return grammar
    entry = grammar.get(trigger_type)
    return {trigger_type: entry} if entry is not None else {}
