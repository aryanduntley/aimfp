"""
AIMFP Helper Functions - User Directives Validation

Schema validation and CHECK constraint extraction for user_directives database.
Parses schema SQL to extract allowed values from CHECK constraints.

All functions are pure FP - immutable data, explicit parameters, Result types.

Helpers in this file:
- user_directives_allowed_check_constraints: Get allowed values from CHECK constraint
- validate_trigger_config: Check a trigger_config against AIMFP's grammar
- validate_action_config: Check an action_config against AIMFP's envelope
"""

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from ..utils import get_return_statements, _parse_check_constraint

# Import common user_directives utilities (DRY principle)
from ._common import get_cached_project_root, _open_directives_connection

# The grammar lives in hooks/ because BOTH sides need it and the dependency
# can only point one way: hooks must never import helpers, but helpers may
# import hooks. Single-sourcing it here is what stops the AI (which calls the
# tool below) and a generated runner (which calls the hook directly) reaching
# different verdicts about the same config.
from ...hooks.actions import action_config_grammar
from ...hooks.actions import validate_action_config as _hook_validate_action_config
from ...hooks.triggers import trigger_config_grammar
from ...hooks.triggers import validate_trigger_config as _hook_validate_trigger_config


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class CheckConstraintResult:
    """Result of CHECK constraint query."""
    success: bool
    values: Tuple[str, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# CHECK parsing is shared: database/connection.py _parse_check_constraint.

# ============================================================================
# Effect Functions - Database Operations
# ============================================================================

def _get_table_schema(conn: sqlite3.Connection, table_name: str) -> Optional[str]:
    """
    Effect: Get CREATE TABLE SQL for a table.

    Args:
        conn: Database connection
        table_name: Table name

    Returns:
        CREATE TABLE SQL or None if table not found
    """
    cursor = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    row = cursor.fetchone()
    return row['sql'] if row else None


def _check_field_exists(conn: sqlite3.Connection, table_name: str, field_name: str) -> bool:
    """
    Effect: Check if field exists in table.

    Args:
        conn: Database connection
        table_name: Table name
        field_name: Field name

    Returns:
        True if field exists, False otherwise
    """
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    rows = cursor.fetchall()
    field_names = [row['name'] for row in rows]
    return field_name in field_names


# ============================================================================
# Public Helper Functions
# ============================================================================

def user_directives_allowed_check_constraints(
    table: str,
    field: str
) -> CheckConstraintResult:
    """
    Returns list of allowed values for CHECK constraint enum fields in user_directives.db.
    Parses schema to extract CHECK(field IN (...)) constraints.

    Args:
        table: Table name to check for CHECK constraints
        field: Field name to get allowed values for

    Returns:
        CheckConstraintResult with allowed values or error

    Examples:
        >>> result = user_directives_allowed_check_constraints(
        ...     "user_directives",
        ...     "status"
        ... )
        >>> result.values
        ('pending_validation', 'validated', 'implementing', 'implemented',
         'active', 'paused', 'error', 'deprecated')
    """
    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

    try:
        # Check if table exists
        table_schema = _get_table_schema(conn, table)
        if table_schema is None:
            conn.close()
            return CheckConstraintResult(
                success=False,
                error=f"Table {table} not found in user_directives database"
            )

        # Check if field exists
        if not _check_field_exists(conn, table, field):
            conn.close()
            return CheckConstraintResult(
                success=False,
                error=f"Field {field} not found in table {table}"
            )

        # Extract CHECK constraint values
        values = _parse_check_constraint(table_schema, field)
        if values is None:
            conn.close()
            return CheckConstraintResult(
                success=False,
                error=f"Field {field} does not have a CHECK constraint list"
            )

        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("user_directives_allowed_check_constraints")

        return CheckConstraintResult(
            success=True,
            values=values,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return CheckConstraintResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


@dataclass(frozen=True)
class TriggerValidationResult:
    """
    Result of checking one trigger_config against AIMFP's grammar.

    grammar is populated only on failure, and carries the spec for that
    trigger_type. Telling the AI what IS expected, at the moment it got it
    wrong, is the whole point of AIMFP owning this schema rather than leaving
    each project to invent one.
    """
    success: bool
    valid: bool = False
    trigger_type: Optional[str] = None
    kind: Optional[str] = None
    errors: Tuple[str, ...] = ()
    grammar: Optional[dict] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionValidationResult:
    """
    Result of checking one action_config against AIMFP's envelope.

    caller_resolved marks the two action types AIMFP can only describe:
    function_call and command name a target that exists in the caller's
    project, so valid=True on those means "the envelope is right, now check
    it resolves" rather than "this will work".

    A separate type from TriggerValidationResult rather than a reuse of it.
    Reusing it would have put an action_type inside a field named
    trigger_type, which is exactly the sort of confusion this milestone
    exists to remove.
    """
    success: bool
    valid: bool = False
    action_type: Optional[str] = None
    caller_resolved: bool = False
    errors: Tuple[str, ...] = ()
    grammar: Optional[dict] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Trigger Configuration Validation
# ============================================================================

def _reject_invalid_trigger_config(
    trigger_type: Optional[str],
    trigger_config: Any,
) -> Optional[str]:
    """
    Pure: Return a rejection message for a non-conforming config, else None.

    The gate behind add_user_custom_entry and update_user_custom_entry. A
    schedule that never fires produces no error anywhere - no log line, no
    recorded failure, just silence - so the only place to catch it is before
    it is stored.

    Args:
        trigger_type: The row's trigger_type
        trigger_config: The config being written, as a mapping or JSON string

    Returns:
        A message listing every field-level problem, or None when it conforms
    """
    if trigger_type is None or trigger_config is None:
        return None

    validation = _hook_validate_trigger_config(trigger_type, trigger_config)
    if validation.valid:
        return None

    return (
        f"trigger_config does not conform to AIMFP's grammar for "
        f"trigger_type '{trigger_type}': " + '; '.join(validation.errors)
    )


def _reject_invalid_action_config(
    action_type: Optional[str],
    action_config: Any,
) -> Optional[str]:
    """
    Pure: Return a rejection message for a non-conforming action, else None.

    The envelope only. For function_call and command AIMFP checks that a
    target is named and well-formed, never that it resolves - that target
    exists in the caller's project, not in AIMFP.

    Args:
        action_type: The row's action_type
        action_config: The config being written, as a mapping or JSON string

    Returns:
        A message listing every field-level problem, or None when it conforms
    """
    if action_type is None or action_config is None:
        return None

    validation = _hook_validate_action_config(action_type, action_config)
    if validation.valid:
        return None

    return (
        f"action_config does not carry the envelope AIMFP requires for "
        f"action_type '{action_type}': " + '; '.join(validation.errors)
    )


def validate_trigger_config(
    trigger_type: str,
    trigger_config: Any,
) -> TriggerValidationResult:
    """
    Check a trigger_config against AIMFP's grammar for its trigger_type.

    Call this during user_directive_parse and user_directive_validate,
    before writing the row - add_user_custom_entry will refuse a
    non-conforming config, and knowing why beforehand is cheaper than
    discovering it at the insert.

    A thin wrapper over the hook of the same name. The hook itself is not and
    must never be an MCP tool, but both sides run the same implementation, so
    the AI and a generated runner cannot disagree about what conforms.

    Args:
        trigger_type: 'time', 'event', 'condition', or 'manual'
        trigger_config: The config to check, as a dict or a JSON string

    Returns:
        TriggerValidationResult. On failure, errors names each offending key
        and grammar carries the expected shape for that trigger_type.
    """
    validation = _hook_validate_trigger_config(trigger_type, trigger_config)

    return TriggerValidationResult(
        success=True,
        valid=validation.valid,
        trigger_type=validation.trigger_type,
        kind=validation.kind,
        errors=validation.errors,
        grammar=None if validation.valid else trigger_config_grammar(trigger_type),
        return_statements=get_return_statements("validate_trigger_config"),
    )


def validate_action_config(
    action_type: str,
    action_config: Any,
) -> TriggerValidationResult:
    """
    Check an action_config against AIMFP's envelope for its action_type.

    Call this during user_directive_parse and user_directive_validate, before
    writing the row - add_user_custom_entry will refuse a non-conforming
    config.

    AIMFP validates the ENVELOPE. For function_call and command it checks
    that a target is named and well-formed but never that it resolves: the
    function or executable exists in the generated project, not in AIMFP. A
    valid result for those means "the shape is right, now check it resolves",
    which the returned grammar says explicitly.

    Args:
        action_type: 'api_call', 'script_execution', 'function_call',
            'command', or 'notification'
        action_config: The config to check, as a dict or a JSON string

    Returns:
        ActionValidationResult. On failure, errors names each offending key
        and grammar carries the expected envelope for that action_type. On
        success, caller_resolved=True means AIMFP has validated everything it
        can and the remaining check - does the target resolve - is yours.
    """
    validation = _hook_validate_action_config(action_type, action_config)

    return ActionValidationResult(
        success=True,
        valid=validation.valid,
        action_type=validation.action_type,
        caller_resolved=validation.caller_resolved,
        errors=validation.errors,
        grammar=None if validation.valid else action_config_grammar(action_type),
        return_statements=get_return_statements("validate_action_config"),
    )
