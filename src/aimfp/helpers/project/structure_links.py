"""
AIMFP Helper Functions - Structure Links

Links between the modularity layers of a project, all many-to-many junctions:

- flow_themes            flow -> themes        (every flow belongs to >= 1 theme)
- completion_path_themes path -> themes        (themes are stable, like paths)
- milestone_flows        milestone -> flows    (flows evolve, like milestones)
- file_flows             file -> flows         (written here by reserve_file(s))

One table-driven core (LinkSpec + insert/delete/query effects) serves every
junction, so each public tool is a thin binding of a spec to an operation.
Every write tool is batch-native and atomic: link tools take [owner_id,
target_id] pairs, move tools re-point many owners from one target to another.
Writers elsewhere (add_flow, add_completion_path, add_milestone, reserve_file)
call insert_links_effect inside their own transaction.

Helpers in this file:
- add_flow_themes / remove_flow_themes / move_flows_to_theme
- add_path_themes / remove_path_themes
- add_milestone_flows / remove_milestone_flows
- move_files_to_flow (add_file_flows / remove_file_from_flow live in themes_flows_2)

Removals are audited and guarded like deletes: every remove_* call requires a
note (written per owner as an entry_deletion note on the owner row), and is
refused while something still depends on the link (a flow's last theme; a
milestone flow still listed by the milestone's open tasks or sidequests).
delete_theme / delete_flow / delete_milestone / delete_completion_path treat
these links as associations that must be resolved first.
- get_themes_for_path / get_flows_for_milestone
"""

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Dict, Final, Iterable, Optional, Tuple

from ..utils import get_return_statements
from ._common import (
    VALID_NOTE_SOURCES,
    _check_entity_exists,
    _create_deletion_note,
    _open_project_connection,
    _validate_severity,
    get_cached_project_root,
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class LinkSpec:
    """Immutable description of one junction table: owner row -> target rows."""
    table: str
    owner_col: str
    owner_table: str
    owner_label: str
    target_col: str
    target_table: str
    target_label: str


@dataclass(frozen=True)
class LinkResult:
    """Result of a batch link write (add, remove, or move)."""
    success: bool
    added_count: int = 0
    removed_count: int = 0
    skipped_count: int = 0
    linked: Tuple[Dict[str, Any], ...] = ()          # [{owner_id, target_ids}] for every owner touched
    unlinked_owner_ids: Tuple[int, ...] = ()         # owners left with no links (e.g. files with no flow)
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class LinkedRowsResult:
    """Result of reading the rows linked to an owner (themes or flows)."""
    success: bool
    owner_id: Optional[int] = None
    rows: Tuple[Dict[str, Any], ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Constants
# ============================================================================

FLOW_THEMES: Final[LinkSpec] = LinkSpec(
    'flow_themes', 'flow_id', 'flows', 'Flow', 'theme_id', 'themes', 'Theme')
PATH_THEMES: Final[LinkSpec] = LinkSpec(
    'completion_path_themes', 'completion_path_id', 'completion_path', 'Completion path',
    'theme_id', 'themes', 'Theme')
MILESTONE_FLOWS: Final[LinkSpec] = LinkSpec(
    'milestone_flows', 'milestone_id', 'milestones', 'Milestone', 'flow_id', 'flows', 'Flow')
FILE_FLOWS: Final[LinkSpec] = LinkSpec(
    'file_flows', 'file_id', 'files', 'File', 'flow_id', 'flows', 'Flow')


# ============================================================================
# Pure Functions
# ============================================================================

def unique_pairs(links: Optional[Iterable[Any]]) -> Tuple[Tuple[int, int], ...]:
    """
    Pure: Normalize [owner_id, target_id] pairs (lists or tuples), dropping repeats.

    Args:
        links: Pairs as given by the caller

    Returns:
        Tuple of distinct (owner_id, target_id) tuples, first-seen order
    """
    return tuple(dict.fromkeys((int(a), int(b)) for a, b in (links or ())))


def unique_ids(ids: Optional[Iterable[Optional[int]]]) -> Tuple[int, ...]:
    """
    Pure: Drop None values and duplicates, preserving first-seen order.

    Args:
        ids: IDs, possibly None, with None entries or repeats

    Returns:
        Tuple of distinct IDs
    """
    return tuple(dict.fromkeys(i for i in (ids or ()) if i is not None))


# Marks a flow declaration the caller never made (absent key), as opposed to an
# explicit None, which declares "this file belongs to no flow".
FLOWS_NOT_DECLARED: Final[str] = "<flow_ids not declared>"

# Recorded on files.no_flow_reason when flow_ids=None is passed without a reason.
DEFAULT_NO_FLOW_REASON: Final[str] = "declared no flow"


def flow_choice_error(flow_ids: Any, no_flow_reason: Optional[str]) -> Optional[str]:
    """
    Pure: Validate a file's flow declaration: a list of flows, or an explicit None.

    Args:
        flow_ids: List of flow IDs, None (explicitly no flow), or FLOWS_NOT_DECLARED
        no_flow_reason: Optional explanation for flow_ids=None (config, data)

    Returns:
        Error message, or None when the declaration is explicit and consistent
    """
    if flow_ids is None:
        return None
    if isinstance(flow_ids, (list, tuple)) and unique_ids(flow_ids):
        if (no_flow_reason or '').strip():
            return "no_flow_reason is only valid with flow_ids=null; drop it or the flow IDs."
        return None
    return ("flow_ids is required: list the flow(s) this file implements (get_all_flows / "
            "get_task_flows). A file that belongs to no flow (config, data, fixtures) must say so "
            "explicitly with flow_ids=null, ideally with no_flow_reason.")


def no_flow_reason_for(flow_ids: Any, no_flow_reason: Optional[str]) -> Optional[str]:
    """
    Pure: The no_flow_reason to record for a valid declaration (None when flows were given).

    Args:
        flow_ids: Validated flow declaration (list or None)
        no_flow_reason: Caller's explanation, possibly empty

    Returns:
        Stripped reason or DEFAULT_NO_FLOW_REASON for flow_ids=None; else None
    """
    if flow_ids is not None:
        return None
    return (no_flow_reason or '').strip() or DEFAULT_NO_FLOW_REASON


# ============================================================================
# Effect Functions
# ============================================================================

def query_missing_ids(conn: sqlite3.Connection, table: str, ids: Tuple[int, ...]) -> Tuple[int, ...]:
    """
    Effect: Return the IDs in ids that have no row in table.

    Args:
        conn: Project database connection
        table: Table with an integer id column
        ids: IDs to check

    Returns:
        Tuple of IDs not found, in input order
    """
    return tuple(i for i in ids if not _check_entity_exists(conn, table, i))


def validate_link_targets(
    conn: sqlite3.Connection,
    spec: LinkSpec,
    target_ids: Tuple[int, ...],
) -> Optional[str]:
    """
    Effect: Check that every target exists; return an error message or None.

    Args:
        conn: Project database connection
        spec: Junction being written
        target_ids: Target IDs (themes or flows)

    Returns:
        Error message naming the missing IDs, or None when all exist
    """
    missing = query_missing_ids(conn, spec.target_table, target_ids)
    if missing:
        return f"{spec.target_label} ID(s) not found: {list(missing)}"
    return None


def insert_links_effect(
    conn: sqlite3.Connection,
    spec: LinkSpec,
    owner_id: int,
    target_ids: Tuple[int, ...],
) -> int:
    """
    Effect: INSERT OR IGNORE junction rows (no commit); returns rows actually inserted.

    Args:
        conn: Project database connection (caller owns the transaction)
        spec: Junction to write
        owner_id: Owner row ID
        target_ids: Target row IDs

    Returns:
        Number of new links
    """
    before = conn.total_changes
    conn.executemany(
        f"INSERT OR IGNORE INTO {spec.table} ({spec.owner_col}, {spec.target_col}) VALUES (?, ?)",
        tuple((owner_id, t) for t in target_ids),
    )
    return conn.total_changes - before


def delete_links_effect(
    conn: sqlite3.Connection,
    spec: LinkSpec,
    owner_id: int,
    target_ids: Tuple[int, ...],
) -> int:
    """
    Effect: DELETE junction rows (no commit); returns rows actually removed.

    Args:
        conn: Project database connection (caller owns the transaction)
        spec: Junction to write
        owner_id: Owner row ID
        target_ids: Target row IDs to unlink

    Returns:
        Number of links removed
    """
    before = conn.total_changes
    conn.executemany(
        f"DELETE FROM {spec.table} WHERE {spec.owner_col} = ? AND {spec.target_col} = ?",
        tuple((owner_id, t) for t in target_ids),
    )
    return conn.total_changes - before


def query_linked_ids(conn: sqlite3.Connection, spec: LinkSpec, owner_id: int) -> Tuple[int, ...]:
    """
    Effect: Target IDs currently linked to an owner, ascending.

    Args:
        conn: Project database connection
        spec: Junction to read
        owner_id: Owner row ID

    Returns:
        Tuple of linked target IDs
    """
    rows = conn.execute(
        f"SELECT {spec.target_col} FROM {spec.table} WHERE {spec.owner_col} = ? "
        f"ORDER BY {spec.target_col}",
        (owner_id,),
    ).fetchall()
    return tuple(r[0] for r in rows)


def query_linked_rows(conn: sqlite3.Connection, spec: LinkSpec, owner_id: int) -> Tuple[Dict[str, Any], ...]:
    """
    Effect: Full target rows (id, name, description) linked to an owner.

    Args:
        conn: Project database connection
        spec: Junction to read
        owner_id: Owner row ID

    Returns:
        Tuple of target row dicts, ordered by id
    """
    rows = conn.execute(
        f"SELECT t.id, t.name, t.description FROM {spec.target_table} t "
        f"JOIN {spec.table} j ON j.{spec.target_col} = t.id "
        f"WHERE j.{spec.owner_col} = ? ORDER BY t.id",
        (owner_id,),
    ).fetchall()
    return tuple({'id': r[0], 'name': r[1], 'description': r[2]} for r in rows)


def query_link_owner_names(conn: sqlite3.Connection, spec: LinkSpec, target_id: int) -> Tuple[str, ...]:
    """
    Effect: Names of the owners linked to a target (e.g. paths linked to a theme).

    Args:
        conn: Project database connection
        spec: Junction to read
        target_id: Target row ID

    Returns:
        Tuple of owner names, ordered by owner id
    """
    rows = conn.execute(
        f"SELECT o.name FROM {spec.owner_table} o JOIN {spec.table} j ON j.{spec.owner_col} = o.id "
        f"WHERE j.{spec.target_col} = ? ORDER BY o.id",
        (target_id,),
    ).fetchall()
    return tuple(r[0] for r in rows)


def query_open_work_using_flow(conn: sqlite3.Connection, milestone_id: int, flow_id: int) -> Tuple[str, ...]:
    """
    Effect: Open tasks (and their open sidequests) under a milestone whose flow_ids list the flow.

    Args:
        conn: Project database connection
        milestone_id: Milestone ID
        flow_id: Flow ID

    Returns:
        Tuple of 'task:<name>' / 'sidequest:<name>' labels
    """
    rows = conn.execute(
        """
        SELECT 'task:' || t.name FROM tasks t, json_each(COALESCE(t.flow_ids, '[]')) j
        WHERE t.milestone_id = ? AND t.status != 'completed' AND j.value = ?
        UNION ALL
        SELECT 'sidequest:' || s.name FROM sidequests s
        JOIN tasks t ON t.id = s.paused_task_id, json_each(COALESCE(s.flow_ids, '[]')) j
        WHERE t.milestone_id = ? AND s.status != 'completed' AND j.value = ?
        """,
        (milestone_id, flow_id, milestone_id, flow_id),
    ).fetchall()
    return tuple(r[0] for r in rows)


def removal_blockers(
    conn: sqlite3.Connection, spec: LinkSpec, pairs: Tuple[Tuple[int, int], ...]
) -> Tuple[str, ...]:
    """
    Effect: Dependencies that forbid removing these links (empty = removable).

    Args:
        conn: Project database connection
        spec: Junction being written
        pairs: (owner_id, target_id) links to remove

    Returns:
        Tuple of human-readable blockers
    """
    if spec is not MILESTONE_FLOWS:
        return ()
    return tuple(
        f"milestone {m} flow {f}: still listed by {', '.join(users)}"
        for m, f in pairs
        for users in (query_open_work_using_flow(conn, m, f),) if users
    )


# ============================================================================
# Shared tool core
# ============================================================================

def _links_summary(
    conn: sqlite3.Connection, spec: LinkSpec, owner_ids: Tuple[int, ...]
) -> Tuple[Tuple[Dict[str, Any], ...], Tuple[int, ...]]:
    """Effect: ([{owner_id, target_ids}], owners now without links) for touched owners."""
    linked = tuple({'owner_id': o, 'target_ids': query_linked_ids(conn, spec, o)} for o in owner_ids)
    return linked, tuple(entry['owner_id'] for entry in linked if not entry['target_ids'])


def _change_links(
    spec: LinkSpec,
    links: Optional[Iterable[Any]],
    remove: bool,
    statements: Callable[[], Tuple[str, ...]],
    min_remaining: int,
    project_root: Optional[str],
    note: Optional[Tuple[str, str, str]] = None,
) -> LinkResult:
    """
    Effect: Add or remove a batch of [owner_id, target_id] links in one transaction.

    All-or-nothing: unknown owners (or, for adds, unknown targets) refuse the
    whole batch. Removals need note = (reason, severity, source), refuse links
    something still depends on (removal_blockers), and min_remaining refuses a
    removal that would leave any owner with fewer links than that (flows must
    keep at least one theme). One entry_deletion note per owner is written in
    the same transaction. statements fetches the calling tool's return
    statements, only on success.
    """
    pairs = unique_pairs(links)
    if not pairs:
        return LinkResult(success=False, error=f"No [{spec.owner_label.lower()}_id, "
                                               f"{spec.target_label.lower()}_id] pairs given")
    owners = unique_ids(o for o, _ in pairs)
    if remove:
        reason, severity, source = note or ('', '', '')
        if not reason.strip():
            return LinkResult(success=False, error="note_reason is required to remove links")
        if not _validate_severity(severity):
            return LinkResult(success=False, error=f"Invalid note_severity: {severity!r}")
        if source not in VALID_NOTE_SOURCES:
            return LinkResult(success=False, error=f"Invalid note_source: {source!r}")

    conn = _open_project_connection(project_root or get_cached_project_root())
    try:
        missing_owners = query_missing_ids(conn, spec.owner_table, owners)
        if missing_owners:
            return LinkResult(success=False,
                              error=f"{spec.owner_label} ID(s) not found: {list(missing_owners)}")
        if not remove:
            error = validate_link_targets(conn, spec, unique_ids(t for _, t in pairs))
            if error:
                return LinkResult(success=False, error=error)
        blockers = removal_blockers(conn, spec, pairs) if remove else ()
        if blockers:
            return LinkResult(
                success=False,
                error=("Refused: resolve these associations first (update their flow_ids): "
                       + "; ".join(blockers)))
        if remove and min_remaining:
            starved = tuple(
                o for o in owners
                if len(set(query_linked_ids(conn, spec, o)) - {t for oo, t in pairs if oo == o})
                < min_remaining)
            if starved:
                return LinkResult(
                    success=False,
                    error=(f"Refused: {spec.owner_label.lower()}(s) {list(starved)} must keep at least "
                           f"{min_remaining} {spec.target_label.lower()}. Link the replacement first "
                           f"(or use the move tool), then remove."))
        effect = delete_links_effect if remove else insert_links_effect
        changed = sum(effect(conn, spec, o, tuple(t for oo, t in pairs if oo == o)) for o in owners)
        if remove:
            for o in owners:
                targets = [t for oo, t in pairs if oo == o]
                _create_deletion_note(
                    conn, spec.owner_table, o,
                    f"{note[0].strip()} [unlinked {spec.table}: {spec.owner_col}={o} "
                    f"{spec.target_col}={targets}]",
                    note[1], note[2], "entry_deletion")
        conn.commit()
        linked, unlinked = _links_summary(conn, spec, owners)
        return LinkResult(
            success=True,
            added_count=0 if remove else changed,
            removed_count=changed if remove else 0,
            skipped_count=len(pairs) - changed,
            linked=linked,
            unlinked_owner_ids=unlinked,
            return_statements=statements(),
        )
    except Exception as e:
        conn.rollback()
        return LinkResult(success=False, error=f"Failed to update links: {e}")
    finally:
        conn.close()


def _move_links(
    spec: LinkSpec,
    owner_ids: Optional[Iterable[int]],
    from_id: int,
    to_id: int,
    statements: Callable[[], Tuple[str, ...]],
    project_root: Optional[str],
) -> LinkResult:
    """
    Effect: Re-point many owners from one target to another in one transaction.

    Owners not linked to from_id are skipped (counted, untouched). An owner
    already linked to to_id simply loses its from_id link.
    """
    owners = unique_ids(owner_ids)
    if not owners:
        return LinkResult(success=False, error=f"No {spec.owner_label.lower()} IDs given")
    if from_id == to_id:
        return LinkResult(success=False, error="from and to are the same")

    conn = _open_project_connection(project_root or get_cached_project_root())
    try:
        missing_owners = query_missing_ids(conn, spec.owner_table, owners)
        if missing_owners:
            return LinkResult(success=False,
                              error=f"{spec.owner_label} ID(s) not found: {list(missing_owners)}")
        error = validate_link_targets(conn, spec, (to_id,))
        if error:
            return LinkResult(success=False, error=error)
        movable = tuple(o for o in owners if from_id in query_linked_ids(conn, spec, o))
        removed = sum(delete_links_effect(conn, spec, o, (from_id,)) for o in movable)
        added = sum(insert_links_effect(conn, spec, o, (to_id,)) for o in movable)
        conn.commit()
        linked, unlinked = _links_summary(conn, spec, movable)
        return LinkResult(
            success=True,
            added_count=added,
            removed_count=removed,
            skipped_count=len(owners) - len(movable),
            linked=linked,
            unlinked_owner_ids=unlinked,
            return_statements=statements(),
        )
    except Exception as e:
        conn.rollback()
        return LinkResult(success=False, error=f"Failed to move links: {e}")
    finally:
        conn.close()


def _read_links(
    spec: LinkSpec,
    owner_id: int,
    statements: Callable[[], Tuple[str, ...]],
    project_root: Optional[str],
) -> LinkedRowsResult:
    """Effect: Read the rows linked to one owner."""
    conn = _open_project_connection(project_root or get_cached_project_root())
    try:
        if not _check_entity_exists(conn, spec.owner_table, owner_id):
            return LinkedRowsResult(success=False, error=f"{spec.owner_label} with ID {owner_id} not found")
        return LinkedRowsResult(
            success=True,
            owner_id=owner_id,
            rows=query_linked_rows(conn, spec, owner_id),
            return_statements=statements(),
        )
    except Exception as e:
        return LinkedRowsResult(success=False, owner_id=owner_id, error=f"Query failed: {e}")
    finally:
        conn.close()


# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def add_flow_themes(links: list, project_root: Optional[str] = None) -> LinkResult:
    """Link flows to themes: [[flow_id, theme_id], ...]. Existing links are skipped."""
    return _change_links(
        FLOW_THEMES, links, remove=False, min_remaining=0,
        statements=lambda: get_return_statements("add_flow_themes"), project_root=project_root)


def remove_flow_themes(
    links: list,
    note_reason: str,
    note_severity: str,
    note_source: str,
    project_root: Optional[str] = None,
) -> LinkResult:
    """Unlink [[flow_id, theme_id], ...]; refused if any flow would be left with no theme. A note is required."""
    return _change_links(
        FLOW_THEMES, links, remove=True, min_remaining=1,
        statements=lambda: get_return_statements("remove_flow_themes"), project_root=project_root,
        note=(note_reason, note_severity, note_source))


def move_flows_to_theme(
    flow_ids: list, from_theme_id: int, to_theme_id: int, project_root: Optional[str] = None
) -> LinkResult:
    """Re-point many flows from one theme to another, atomically."""
    return _move_links(
        FLOW_THEMES, flow_ids, from_theme_id, to_theme_id,
        statements=lambda: get_return_statements("move_flows_to_theme"), project_root=project_root)


def add_path_themes(links: list, project_root: Optional[str] = None) -> LinkResult:
    """Link completion paths to themes: [[completion_path_id, theme_id], ...]."""
    return _change_links(
        PATH_THEMES, links, remove=False, min_remaining=0,
        statements=lambda: get_return_statements("add_path_themes"), project_root=project_root)


def remove_path_themes(
    links: list,
    note_reason: str,
    note_severity: str,
    note_source: str,
    project_root: Optional[str] = None,
) -> LinkResult:
    """Unlink [[completion_path_id, theme_id], ...]. A note is required."""
    return _change_links(
        PATH_THEMES, links, remove=True, min_remaining=0,
        statements=lambda: get_return_statements("remove_path_themes"), project_root=project_root,
        note=(note_reason, note_severity, note_source))


def add_milestone_flows(links: list, project_root: Optional[str] = None) -> LinkResult:
    """Link milestones to the flows they build or change: [[milestone_id, flow_id], ...]."""
    return _change_links(
        MILESTONE_FLOWS, links, remove=False, min_remaining=0,
        statements=lambda: get_return_statements("add_milestone_flows"), project_root=project_root)


def remove_milestone_flows(
    links: list,
    note_reason: str,
    note_severity: str,
    note_source: str,
    project_root: Optional[str] = None,
) -> LinkResult:
    """Unlink [[milestone_id, flow_id], ...]; refused while open tasks/sidequests of the milestone list the flow. A note is required."""
    return _change_links(
        MILESTONE_FLOWS, links, remove=True, min_remaining=0,
        statements=lambda: get_return_statements("remove_milestone_flows"), project_root=project_root,
        note=(note_reason, note_severity, note_source))


def move_files_to_flow(
    file_ids: list, from_flow_id: int, to_flow_id: int, project_root: Optional[str] = None
) -> LinkResult:
    """Re-point many files from one flow to another, atomically (e.g. when splitting a flow)."""
    return _move_links(
        FILE_FLOWS, file_ids, from_flow_id, to_flow_id,
        statements=lambda: get_return_statements("move_files_to_flow"), project_root=project_root)


def get_themes_for_path(completion_path_id: int, project_root: Optional[str] = None) -> LinkedRowsResult:
    """Themes linked to a completion path (id, name, description)."""
    return _read_links(
        PATH_THEMES, completion_path_id,
        statements=lambda: get_return_statements("get_themes_for_path"), project_root=project_root)


def get_flows_for_milestone(milestone_id: int, project_root: Optional[str] = None) -> LinkedRowsResult:
    """Flows linked to a milestone (id, name, description)."""
    return _read_links(
        MILESTONE_FLOWS, milestone_id,
        statements=lambda: get_return_statements("get_flows_for_milestone"), project_root=project_root)
