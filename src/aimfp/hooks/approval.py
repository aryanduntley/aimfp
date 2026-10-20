"""
AIMFP Hooks - User Directive Approval

Approval is a HUMAN decision, so it is written only here, by a host's
human-facing path: a terminal prompt, a UI button, svamanas's
`/directives approve`. Nothing in this module is an MCP tool, and nothing here
may become one. If the AI could call approve_user_directive, "approved by the
user" would be something the model grants itself - the exact hole this module
exists to close. The generic user_custom write tools refuse the approval
columns for the same reason (helpers/user_directives/crud.py).

An approval is bound to content. approve_user_directive stores a hash of what
was approved: the trigger, the action, the implementation file path and the
implementation file's bytes. Any later change through any path - a CRUD
update, a hand edit of the generated handler, a direct write to the
database - makes the stored hash stop matching, and the directive reads as
needing approval again. Dispatch (get_due_directives), activation
(activate_user_directive) and health (check_directive_health) all ask
approval_state, so an approved "daily backup" cannot be edited into something
else that is still approved.

Rows approved before user_directives schema 1.5 carry no hash, so they read
as needing approval again: trusting a flag the model could set is the defect
being fixed, not history worth keeping.

Stdlib only, by package contract (see hooks/__init__.py).
"""

import hashlib
import json
import sqlite3
from typing import Any, Callable, Mapping, Optional, Tuple

from ..database.connection import (
    _close_connection,
    _get_table_info,
    _open_connection,
    database_exists,
    get_user_directives_db_path,
    resolve_project_relative,
)
from .results import ApprovalState, HookMutationResult


# Approval states. APPROVED is the only one that dispatches or activates.
APPROVAL_APPROVED = 'approved'
APPROVAL_UNAPPROVED = 'unapproved'
APPROVAL_NEEDS_REAPPROVAL = 'needs_reapproval'

# Columns only this module writes. The generic write tools refuse them.
APPROVAL_COLUMNS = frozenset({'approved', 'approved_at', 'approved_by', 'approval_hash'})

# The directive content an approval covers, in hash order. JSON columns are
# parsed before hashing, so re-serializing the same config does not void it.
HASHED_COLUMNS = (
    'name', 'trigger_type', 'trigger_config',
    'action_type', 'action_config', 'implementation_file_path')
_JSON_COLUMNS = frozenset({'trigger_config', 'action_config'})

# Implementation digest markers for a path that is set but cannot be read.
IMPLEMENTATION_MISSING = 'missing'

_NO_DATABASE = (
    "No user_directives.db for this project. Approval is Use Case 2 only; "
    "a Use Case 1 project has no user directives to approve."
)

_NEEDS_MIGRATION = (
    "user_directives.db predates schema 1.5 and has no approval_hash column. "
    "Open an AIMFP session and run migrate_databases."
)


# ============================================================================
# Pure Logic
# ============================================================================

def _canonical_value(column: str, value: Any) -> Any:
    """Pure: A column value as hashed. JSON text is parsed when it parses."""
    if column in _JSON_COLUMNS and isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def directive_content_hash(
    row: Mapping[str, Any],
    implementation_digest: Optional[str],
) -> str:
    """
    Pure: The sha256 an approval binds to.

    Canonical JSON (sort_keys, separators (",", ":")) over HASHED_COLUMNS plus
    the implementation file's digest. The digest is passed in rather than
    read here so the hash stays pure; approval_state reads the file.

    Args:
        row: Mapping holding at least HASHED_COLUMNS
        implementation_digest: sha256 hex of the implementation file, None when
            no path is recorded, IMPLEMENTATION_MISSING when it cannot be read

    Returns:
        sha256 hex digest
    """
    payload = {column: _canonical_value(column, row[column]) for column in HASHED_COLUMNS}
    payload['implementation_sha256'] = implementation_digest
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def approval_verdict(
    approved: Any,
    stored_hash: Optional[str],
    current_hash: str,
) -> Tuple[str, str]:
    """
    Pure: (state, reason) for one directive's approval.

    Args:
        approved: The row's approved flag
        stored_hash: approval_hash recorded at approval time (None before 1.5)
        current_hash: directive_content_hash of the row as it is now

    Returns:
        One of the APPROVAL_* states and a one-line reason
    """
    if not approved:
        return APPROVAL_UNAPPROVED, 'not approved by a person'
    if not stored_hash:
        return APPROVAL_NEEDS_REAPPROVAL, (
            'approved without a content hash (before schema 1.5, or not through '
            'approve_user_directive); approve it again')
    if stored_hash != current_hash:
        return APPROVAL_NEEDS_REAPPROVAL, (
            'changed since it was approved (trigger, action or implementation '
            'file); approve it again')
    return APPROVAL_APPROVED, 'approved and unchanged since approval'


# ============================================================================
# Effects - Reads
# ============================================================================

def _effect_implementation_digest(project_root: str, path: Optional[str]) -> Optional[str]:
    """
    Effect: sha256 of the implementation file, or a marker when unreadable.

    A relative path resolves against the project (a '.aimfp-project/...' path
    follows a project-dir override). No path recorded returns None.
    """
    if not path:
        return None
    try:
        with open(resolve_project_relative(project_root, path), 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return IMPLEMENTATION_MISSING


def current_content_hash(project_root: str, row: Mapping[str, Any]) -> str:
    """Effect: directive_content_hash of a row, reading its implementation file."""
    return directive_content_hash(
        row, _effect_implementation_digest(project_root, row['implementation_file_path']))


def approval_state(project_root: str, row: Mapping[str, Any]) -> Tuple[str, str]:
    """
    Effect: (state, reason) for a user_directives row.

    The row needs HASHED_COLUMNS and approved; approval_hash may be absent (a
    database before schema 1.5), which reads as needing approval again. The
    one check dispatch, activation and health share.
    """
    stored = row['approval_hash'] if 'approval_hash' in row.keys() else None
    return approval_verdict(row['approved'], stored, current_content_hash(project_root, row))


def directive_columns(conn: sqlite3.Connection) -> frozenset:
    """Effect: The column names user_directives has on this database."""
    return frozenset(column['name'] for column in _get_table_info(conn, 'user_directives'))


def _effect_read_directive(conn: sqlite3.Connection, directive_id: int) -> Optional[sqlite3.Row]:
    """Effect: One user_directives row by id, None when absent."""
    return conn.execute(
        "SELECT * FROM user_directives WHERE id = ?", (directive_id,)).fetchone()


def get_directive_approval(directive_id: int, project_root: str) -> ApprovalState:
    """
    HOOK (library API, not an MCP tool).

    Report whether a directive is approved and still matches what was
    approved, so a host can show it before asking a person to approve.

    Args:
        directive_id: Directive to check
        project_root: Absolute path to the project root

    Returns:
        ApprovalState. Never raises.
    """
    db_path = get_user_directives_db_path(project_root)
    if not database_exists(db_path):
        return ApprovalState(success=False, directive_id=directive_id, error=_NO_DATABASE)
    conn = None
    try:
        conn = _open_connection(db_path)
        row = _effect_read_directive(conn, directive_id)
        if row is None:
            return ApprovalState(
                success=False, directive_id=directive_id,
                error=f"Directive {directive_id} not found")
        state, reason = approval_state(project_root, row)
        keys = row.keys()
        return ApprovalState(
            success=True,
            directive_id=directive_id,
            name=row['name'],
            state=state,
            reason=reason,
            approved_at=row['approved_at'],
            approved_by=row['approved_by'] if 'approved_by' in keys else None,
        )
    except sqlite3.Error as exc:
        return ApprovalState(success=False, directive_id=directive_id, error=str(exc))
    finally:
        if conn is not None:
            _close_connection(conn)


# ============================================================================
# Effects - Writes (human-facing hosts only)
# ============================================================================

def _effect_lifecycle_note(
    conn: sqlite3.Connection,
    directive_id: int,
    name: str,
    content: str,
    metadata: Mapping[str, Any],
) -> None:
    """Effect: Append a lifecycle note in the caller's transaction."""
    conn.execute(
        """
        INSERT INTO notes (content, note_type, reference_type, reference_name,
                           reference_id, severity, metadata_json)
        VALUES (?, 'lifecycle', 'directive', ?, ?, 'info', ?)
        """,
        (content, name, directive_id, json.dumps(dict(metadata), sort_keys=True)),
    )


def _effect_write_approval(
    project_root: str,
    directive_id: int,
    build: Callable[[sqlite3.Connection, sqlite3.Row], Optional[str]],
) -> HookMutationResult:
    """
    Effect: Shared open/check/commit around approve and revoke.

    build(conn, row) performs the writes for one existing directive on a
    schema 1.5 database and returns None, or an error string to roll back.
    """
    db_path = get_user_directives_db_path(project_root)
    if not database_exists(db_path):
        return HookMutationResult(success=False, directive_id=directive_id, error=_NO_DATABASE)
    conn = None
    try:
        conn = _open_connection(db_path)
        if 'approval_hash' not in directive_columns(conn):
            return HookMutationResult(
                success=False, directive_id=directive_id, error=_NEEDS_MIGRATION)
        row = _effect_read_directive(conn, directive_id)
        if row is None:
            return HookMutationResult(
                success=False, directive_id=directive_id,
                error=f"Directive {directive_id} not found")
        error = build(conn, row)
        if error is not None:
            conn.rollback()
            return HookMutationResult(success=False, directive_id=directive_id, error=error)
        conn.commit()
        return HookMutationResult(success=True, directive_id=directive_id)
    except sqlite3.Error as exc:
        if conn is not None:
            conn.rollback()
        return HookMutationResult(success=False, directive_id=directive_id, error=str(exc))
    finally:
        if conn is not None:
            _close_connection(conn)


def approve_user_directive(
    directive_id: int,
    approved_by: str,
    project_root: str,
) -> HookMutationResult:
    """
    HOOK (library API, not an MCP tool).

    Record a person's approval of a directive as it is right now: sets
    approved, approved_at, approved_by and approval_hash, and writes a
    lifecycle note. Call it only from a path where a human made the decision.
    Activation still goes through activate_user_directive.

    Args:
        directive_id: Directive being approved
        approved_by: Who approved, as the host labels them (e.g. 'user', a username)
        project_root: Absolute path to the project root

    Returns:
        HookMutationResult. success=False when the database is absent or
        predates schema 1.5, the directive is missing, or approved_by is blank.
        Never raises.
    """
    if not isinstance(approved_by, str) or not approved_by.strip():
        return HookMutationResult(
            success=False, directive_id=directive_id,
            error="approved_by must name who approved (e.g. 'user')")

    def build(conn: sqlite3.Connection, row: sqlite3.Row) -> Optional[str]:
        content_hash = current_content_hash(project_root, row)
        conn.execute(
            """
            UPDATE user_directives
            SET approved = 1, approved_at = CURRENT_TIMESTAMP,
                approved_by = ?, approval_hash = ?
            WHERE id = ?
            """,
            (approved_by.strip(), content_hash, directive_id),
        )
        _effect_lifecycle_note(
            conn, directive_id, row['name'],
            f"Approved by {approved_by.strip()}",
            {'event': 'approved', 'approved_by': approved_by.strip(),
             'approval_hash': content_hash})
        return None

    return _effect_write_approval(project_root, directive_id, build)


def revoke_user_directive_approval(
    directive_id: int,
    project_root: str,
    reason: Optional[str] = None,
) -> HookMutationResult:
    """
    HOOK (library API, not an MCP tool).

    Withdraw a directive's approval: clears the approval columns, pauses it
    when it is active (dispatch would refuse it anyway), and writes a
    lifecycle note.

    Args:
        directive_id: Directive whose approval is withdrawn
        project_root: Absolute path to the project root
        reason: Optional note text for why

    Returns:
        HookMutationResult. Never raises.
    """
    def build(conn: sqlite3.Connection, row: sqlite3.Row) -> Optional[str]:
        conn.execute(
            """
            UPDATE user_directives
            SET approved = 0, approved_at = NULL, approved_by = NULL,
                approval_hash = NULL,
                status = CASE WHEN status = 'active' THEN 'paused' ELSE status END
            WHERE id = ?
            """,
            (directive_id,),
        )
        _effect_lifecycle_note(
            conn, directive_id, row['name'],
            f"Approval revoked: {reason or 'no reason given'}",
            {'event': 'approval_revoked', 'previous_status': row['status']})
        return None

    return _effect_write_approval(project_root, directive_id, build)
