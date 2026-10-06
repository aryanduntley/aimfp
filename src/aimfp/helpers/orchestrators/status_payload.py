"""
AIMFP Helper Functions - Status Payload Tiers

aimfp_status returns one of three tiers, chosen by its `type` argument:

- quick (default): where the work is. Path, milestone, the focused
  task/subtask/sidequest with its item names, the flows and themes of that
  work, other open work in the milestone, and the structure gap count.
  Sized for completion loops, which call status often.
- summary: the reload after lost or compacted context. quick, plus project
  purpose and goals, infrastructure, recent files annotated with their
  flows/themes/module, recent notes, recent history, active branches. The
  caller adds the core supportive context.
- detailed: summary plus the full work tree.

Structure (themes, flows, modules) is attached to what the payload already
shows. It is never listed whole: a full map every call is a list the AI has
to sort for relevance itself, which buries the part that matters now.

All functions here are pure; aimfp_status (entry_points.py) gathers the rows.
"""

import json
from typing import Any, Dict, Final, Optional, Tuple

from ..project.structure_health import (
    StructureRows,
    WorkFocus,
    milestone_flow_ids,
    structure_of_files,
    structure_of_flows,
)
from .status import parse_flow_ids


# ============================================================================
# Constants
# ============================================================================

TIER_QUICK: Final[str] = 'quick'
TIER_SUMMARY: Final[str] = 'summary'
TIER_DETAILED: Final[str] = 'detailed'

# Focused-work description cap in summary/detailed (quick omits it).
FOCUS_DESC_LIMIT: Final[int] = 300


# ============================================================================
# Pure Helpers
# ============================================================================

def drop_empty(data: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: Remove keys whose value is None or an empty container."""
    return {
        k: v for k, v in data.items()
        if v is not None and not (isinstance(v, (dict, list, tuple, set, str)) and not v)
    }


def _brief(row: Optional[Dict[str, Any]], keys: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    """Pure: A row reduced to keys (None passes through)."""
    if not row:
        return None
    return {k: row.get(k) for k in keys}


def _clip(text: Any, limit: int) -> Any:
    """Pure: Truncate a string with an ellipsis; non-strings pass through."""
    if not isinstance(text, str) or len(text) <= limit:
        return text
    return text[:limit].rstrip() + '…'


def focus_scope(
    focus: Optional[Dict[str, Any]],
    parent_task: Optional[Dict[str, Any]],
) -> Tuple[Optional[int], Tuple[int, ...]]:
    """
    Pure: (milestone_id, flow_ids) of the focused work.

    A task carries both. A subtask has neither, so it inherits its parent
    task's. A sidequest carries its own flows; its milestone is its paused
    task's.

    Args:
        focus: current_focus row (with item_type), or None
        parent_task: parent (subtask) or paused (sidequest) task row, or None

    Returns:
        (milestone_id or None, flow_ids)
    """
    if not focus:
        return (None, ())
    kind = focus.get('item_type')
    parent = parent_task or {}
    if kind == 'task':
        return (focus.get('milestone_id'), parse_flow_ids(focus.get('flow_ids')))
    if kind == 'sidequest':
        own = parse_flow_ids(focus.get('flow_ids'))
        return (parent.get('milestone_id'), own or parse_flow_ids(parent.get('flow_ids')))
    return (parent.get('milestone_id'), parse_flow_ids(parent.get('flow_ids')))


def compact_project_metadata(meta: Dict[str, Any], tier: str) -> Dict[str, Any]:
    """
    Pure: The project row for a tier.

    quick: name + status. summary/detailed: adds purpose, decoded goals,
    version, and user_directives_status when set.
    """
    if not meta:
        return {}
    if tier == TIER_QUICK:
        return drop_empty({'name': meta.get('name'), 'status': meta.get('status')})
    raw_goals = meta.get('goals_json')
    try:
        goals = json.loads(raw_goals) if isinstance(raw_goals, str) else raw_goals
    except (ValueError, TypeError):
        goals = raw_goals
    return drop_empty({
        'name': meta.get('name'),
        'purpose': meta.get('purpose'),
        'goals': goals,
        'status': meta.get('status'),
        'version': meta.get('version'),
        'user_directives_status': meta.get('user_directives_status'),
    })


def compact_infrastructure(rows: Tuple[Dict[str, Any], ...]) -> Dict[str, Any]:
    """
    Pure: {values: {type: value}} for filled rows; empty rows under `unset`
    with their description, which tells the AI what to put there.
    """
    values = {r.get('type'): r.get('value') for r in rows if r.get('value')}
    unset = tuple(
        {'type': r.get('type'), 'description': r.get('description')}
        for r in rows if not r.get('value')
    )
    return drop_empty({'values': values, 'unset': unset})


def active_branches(rows: Tuple[Dict[str, Any], ...]) -> Tuple[Dict[str, Any], ...]:
    """Pure: Work branches still active; merged/abandoned history is dropped."""
    return tuple(
        _brief(r, ('branch_name', 'user_name', 'purpose', 'created_from', 'created_at'))
        for r in rows if r.get('status') == 'active'
    )


def compact_history(historical: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: Positional history reduced to ids, names and item names."""
    if not historical:
        return {}
    def task_with_items(task_key: str, items_key: str) -> Optional[Dict[str, Any]]:
        task = _brief(historical.get(task_key), ('id', 'name', 'milestone_id'))
        if task is None:
            return None
        items = tuple(i.get('name') for i in historical.get(items_key) or ())
        return drop_empty({**task, 'items': items})
    return drop_empty({
        'last_completed_task': task_with_items('last_completed_task', 'last_completed_task_items'),
        'last_completed_milestone': _brief(historical.get('last_completed_milestone'), ('id', 'name')),
        'last_task_in_prev_milestone': task_with_items(
            'last_task_in_prev_milestone', 'last_task_in_prev_milestone_items'),
    })


def annotate_recent_files(
    files: Tuple[Dict[str, Any], ...],
    rows: StructureRows,
) -> Tuple[Dict[str, Any], ...]:
    """Pure: Recent files with their flows, themes and module attached."""
    structure = structure_of_files(rows, tuple(f['id'] for f in files if f.get('id') is not None))
    return tuple(
        drop_empty({
            'id': f.get('id'),
            'path': f.get('path'),
            'updated_at': f.get('updated_at'),
            **structure.get(f.get('id'), {}),
        })
        for f in files
    )


def _open_work_nodes(
    open_work: Dict[str, Any],
    focus: Optional[Dict[str, Any]],
) -> Tuple[Tuple[Dict[str, Any], ...], Tuple[Dict[str, Any], ...]]:
    """Pure: Other open tasks (subtask names nested) and sidequests, minus the focus, items dropped."""
    focus_key = (focus.get('item_type'), focus.get('id')) if focus else None

    def strip(node: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
        if (kind, node.get('id')) == focus_key:
            return None
        base = {k: node.get(k) for k in ('id', 'name', 'status', 'priority')}
        subs = tuple(
            {k: s.get(k) for k in ('id', 'name', 'status')}
            for s in node.get('subtasks') or ()
            if ('subtask', s.get('id')) != focus_key
        )
        return drop_empty({**base, 'subtasks': subs})

    tasks = tuple(n for t in open_work.get('tasks') or () for n in (strip(t, 'task'),) if n)
    sidequests = tuple(
        n for q in open_work.get('sidequests') or () for n in (strip(q, 'sidequest'),) if n)
    return (tasks, sidequests)


def build_position(
    hierarchy: Dict[str, Any],
    parent_task: Optional[Dict[str, Any]],
    open_work: Dict[str, Any],
    rows: StructureRows,
    tier: str,
    blocked: Tuple[Dict[str, Any], ...] = (),
) -> Tuple[Dict[str, Any], WorkFocus]:
    """
    Pure: Where the work is, and the WorkFocus that scopes structure gaps.

    The milestone is the focused work's own milestone when there is focus
    (it can differ from the first open milestone of the path), otherwise the
    hierarchy's active milestone. Flows and themes are attached to the
    milestone and the focus; nothing else lists them.

    Args:
        hierarchy: get_project_status('summary') hierarchy
        parent_task: Parent/paused task of a subtask/sidequest focus, or None
        open_work: get_open_work(milestone_id) data for the chosen milestone
        rows: Structure rows (names of flows, themes, milestones, paths)
        tier: quick | summary | detailed
        blocked: Blocked work rows

    Returns:
        (position dict, WorkFocus)
    """
    focus = hierarchy.get('current_focus')
    focus_milestone_id, focus_flow_ids = focus_scope(focus, parent_task)

    fallback_ms = hierarchy.get('active_milestone') or {}
    ms_id = focus_milestone_id if focus_milestone_id is not None else fallback_ms.get('id')
    milestone = next((m for m in rows.milestones if m['id'] == ms_id), None)
    path_id = (milestone or {}).get('completion_path_id') or (hierarchy.get('active_path') or {}).get('id')
    path = next((p for p in rows.paths if p['id'] == path_id), None)
    ms_flow_ids = milestone_flow_ids(rows, ms_id)

    focus_node = None
    if focus:
        items = tuple(
            {'id': i.get('id'), 'name': i.get('name'), 'status': i.get('status')}
            for i in hierarchy.get('active_items') or ()
        )
        # quick lists what is left to do; the done items are a count
        shown_items = (tuple(i for i in items if i['status'] != 'completed')
                       if tier == TIER_QUICK else items)
        done = sum(1 for i in items if i['status'] == 'completed')
        focus_node = drop_empty({
            'type': focus.get('item_type'),
            'id': focus.get('id'),
            'name': focus.get('name'),
            'status': focus.get('status'),
            'priority': focus.get('priority'),
            'description': (None if tier == TIER_QUICK
                            else _clip(focus.get('description'), FOCUS_DESC_LIMIT)),
            'flows': structure_of_flows(rows, focus_flow_ids),
            'items': shown_items,
            'items_completed': done if tier == TIER_QUICK else None,
        })

    open_tasks, sidequests = _open_work_nodes(open_work, focus)
    reserved = hierarchy.get('reserved_entities') or {}

    position = drop_empty({
        'path': _brief(path, ('id', 'name')),
        'milestone': drop_empty({
            **(_brief(milestone, ('id', 'name', 'status')) or {}),
            'flows': structure_of_flows(rows, ms_flow_ids),
        }) if milestone else None,
        'focus': focus_node,
        'open_tasks': open_tasks,
        'sidequests': sidequests,
        'blocked': tuple(_brief(b, ('item_type', 'id', 'name')) for b in blocked),
        'reserved': drop_empty({
            'files': reserved.get('files') or (),
            'functions': reserved.get('functions') or (),
        }),
    })
    work_focus = WorkFocus(
        path_id=path_id,
        milestone_id=ms_id,
        flow_ids=tuple(dict.fromkeys((*ms_flow_ids, *focus_flow_ids))),
        file_ids=tuple(f['id'] for f in hierarchy.get('recent_files') or () if f.get('id') is not None),
    )
    return (position, work_focus)


# ============================================================================
# Session bundle (aimfp_run) sections
# ============================================================================

# Migration skip reasons that need no report (an optional database that does
# not exist, or a name the core does not know). Anything else means the
# database could not be read.
_BENIGN_SKIP_PREFIXES: Final[Tuple[str, ...]] = ('Database file does not exist', 'Unknown database')


def compact_user_settings(settings: Tuple[Any, ...]) -> Dict[str, Any]:
    """Pure: {setting_key: setting_value} from setting rows or UserSetting records;
    descriptions are on demand (get_user_settings)."""
    def field(s: Any, name: str) -> Any:
        return s.get(name) if isinstance(s, dict) else getattr(s, name, None)
    return {
        field(s, 'setting_key'): field(s, 'setting_value')
        for s in settings or () if field(s, 'setting_key')
    }


def compact_migration(migration: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pure: Only what needs action: pending migrations, and databases that could
    not be read (their migration status is unknown, so they are reported, not
    migrated). Benign skips and up-to-date databases are dropped.
    """
    if not migration:
        return {}
    unreadable = tuple(
        {'db_name': s.get('db_name'), 'reason': s.get('reason')}
        for s in migration.get('skipped') or ()
        if not str(s.get('reason') or '').startswith(_BENIGN_SKIP_PREFIXES)
    )
    return drop_empty({'pending': tuple(migration.get('pending') or ()), 'unreadable': unreadable})
