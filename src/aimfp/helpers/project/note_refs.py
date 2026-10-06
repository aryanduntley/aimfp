"""
AIMFP Helper Functions - Note References

Single-entity getters (file, function, type, theme, flow, module by name or
path, and get_task_context) return the notes attached to what they return as
references only: {id, type, severity, reference_id}. The AI reads a note in
full with get_notes_comprehensive(note_id). Resolved and audit-trail notes
(completed, obsolete, entry_deletion) are left out. List getters deliberately
carry no references: they are heavy already.

A note is attached through notes.reference_table + notes.reference_id, so
reference_table must name a real project.db table or the note can never be
found. add_note / update_note validate it with reference_table_error.
"""

import sqlite3
from dataclasses import dataclass
from typing import Final, FrozenSet, Iterable, Optional, Tuple

from ..shared.return_gates import gate_return_statements
from .schema import _query_tables


# ============================================================================
# Constants
# ============================================================================

# Note types that never surface as references: resolved work and the deletion
# audit trail (its rows point at entities that no longer exist).
EXCLUDED_REF_NOTE_TYPES: Final[Tuple[str, ...]] = ('completed', 'obsolete', 'entry_deletion')

# Tables a note may not reference: SQLite internals, FTS shadow tables and
# bookkeeping with no entity rows.
_NON_REFERENCE_TABLES: Final[FrozenSet[str]] = frozenset({'schema_version'})


# ============================================================================
# Data Structures
# ============================================================================

@dataclass(frozen=True)
class NoteRef:
    """A reference to a note attached to an entity (not the note's content)."""
    id: int
    type: str
    severity: str
    reference_id: int


# ============================================================================
# Pure Functions
# ============================================================================

def is_reference_table(name: str) -> bool:
    """Pure: Whether a project.db table name can be a note's reference_table."""
    return not (
        name.startswith('sqlite_')
        or '_fts' in name
        or name in _NON_REFERENCE_TABLES
    )


def note_reference_tables(table_names: Iterable[str]) -> FrozenSet[str]:
    """Pure: The tables a note may reference, from a database's table names."""
    return frozenset(name for name in table_names if is_reference_table(name))


def reference_table_error(
    reference_table: Optional[str],
    valid_tables: FrozenSet[str],
) -> Optional[str]:
    """
    Pure: Error text for an unknown reference_table, or None when it is valid.

    None (no reference) is valid. A singular form of a valid table name gets
    a did-you-mean hint ('file' -> 'files').
    """
    if reference_table is None or reference_table in valid_tables:
        return None
    hint = (
        f" Did you mean '{reference_table}s'?"
        if f"{reference_table}s" in valid_tables else ""
    )
    return (
        f"Unknown reference_table '{reference_table}'.{hint} It must be the "
        f"project.db table the note is attached to: {', '.join(sorted(valid_tables))}"
    )


# ============================================================================
# Effect Functions
# ============================================================================

def query_note_reference_tables(conn: sqlite3.Connection) -> FrozenSet[str]:
    """Effect: The project.db tables a note may reference, read from the live schema."""
    return note_reference_tables(_query_tables(conn))


def query_note_refs(
    conn: sqlite3.Connection,
    reference_table: str,
    reference_ids: Iterable[Optional[int]],
) -> Tuple[NoteRef, ...]:
    """
    Effect: References to the notes attached to rows of one table, in one query.

    Ordered by reference_id, newest note first. Excludes EXCLUDED_REF_NOTE_TYPES.
    Returns () for no ids, or when the notes table cannot be read, so a getter
    never fails because of its note references.
    """
    ids = tuple(sorted({i for i in reference_ids if i is not None}))
    if not ids:
        return ()
    id_marks = ','.join('?' * len(ids))
    type_marks = ','.join('?' * len(EXCLUDED_REF_NOTE_TYPES))
    try:
        rows = conn.execute(
            "SELECT id, note_type, severity, reference_id FROM notes "
            f"WHERE reference_table = ? AND reference_id IN ({id_marks}) "
            f"AND note_type NOT IN ({type_marks}) "
            "ORDER BY reference_id, created_at DESC, id DESC",
            (reference_table, *ids, *EXCLUDED_REF_NOTE_TYPES),
        ).fetchall()
    except sqlite3.OperationalError:
        return ()
    return tuple(
        NoteRef(id=row[0], type=row[1], severity=row[2], reference_id=row[3])
        for row in rows
    )


def note_ref_return_statements(
    statements: Tuple[str, ...],
    notes: Tuple[NoteRef, ...],
) -> Tuple[str, ...]:
    """
    Pure: A getter's return statements gated on its note references.

    Statements tagged [when:notes] ship only when the result carries refs.
    """
    return gate_return_statements(statements, {'notes': notes})
