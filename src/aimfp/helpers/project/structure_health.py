"""
AIMFP Helper Functions - Structure Health

One report on the modularity layer of a project, shared by every surface that
shows it: aimfp_status / aimfp_run (structure_summary + structure_health),
aimfp_end, the get_structure_health tool, and the watchdog's structural
reminders. Each surface renders the same collected data, so they cannot
disagree about what is missing.

The layer:
    completion path --themes--> theme --flows--> flow --files--> file --module--> module
    milestone ------flows-----> flow

Gaps reported (each a capped list plus a total):
- flows_without_theme        every flow belongs to >= 1 theme
- themes_without_flows       a theme nothing implements
- files_without_flow         no file_flows row and no recorded no_flow_reason
- files_outside_module       file sits under a module's path but is in no module
- open_paths_without_themes  completion path (not completed) with no themes
- open_milestones_without_flows  milestone (not completed) with no flows
- oversized_flows            description long enough that the flow should split

Effects read rows (tolerating pre-v1.13 databases that lack the newer
tables or columns); pure builders turn rows into the summary and gaps.

Helpers in this file:
- get_structure_health: MCP tool returning summary + gaps
"""

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Final, Optional, Tuple

from ..utils import get_return_statements
from ._common import _open_project_connection, get_cached_project_root


# ============================================================================
# Constants
# ============================================================================

# A flow description past this length has usually absorbed several behaviours
# that belong in separate flows (split candidates).
FLOW_SPLIT_CHARS: Final[int] = 1500

# Each gap list is capped; totals are always exact.
GAP_LIST_CAP: Final[int] = 15

GAP_KEYS: Final[Tuple[str, ...]] = (
    'flows_without_theme',
    'themes_without_flows',
    'files_without_flow',
    'files_outside_module',
    'open_paths_without_themes',
    'open_milestones_without_flows',
    'oversized_flows',
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class StructureRows:
    """Immutable raw rows read from project.db for the structure report."""
    themes: Tuple[Dict[str, Any], ...]
    flows: Tuple[Dict[str, Any], ...]
    flow_themes: Tuple[Dict[str, Any], ...]
    file_flow_counts: Tuple[Dict[str, Any], ...]
    files: Tuple[Dict[str, Any], ...]
    modules: Tuple[Dict[str, Any], ...]
    module_file_ids: Tuple[int, ...]
    paths: Tuple[Dict[str, Any], ...]
    path_themes: Tuple[Dict[str, Any], ...]
    milestones: Tuple[Dict[str, Any], ...]
    milestone_flows: Tuple[Dict[str, Any], ...]


@dataclass(frozen=True)
class StructureHealthResult:
    """Result of the structure health report."""
    success: bool
    summary: Optional[Dict[str, Any]] = None
    health: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Functions
# ============================================================================

def _ids_by(rows: Tuple[Dict[str, Any], ...], key: str, value: str) -> Dict[int, Tuple[int, ...]]:
    """Pure: Group value column by key column: {key: (values...)}."""
    grouped: Dict[int, Tuple[int, ...]] = {}
    for row in rows:
        grouped[row[key]] = grouped.get(row[key], ()) + (row[value],)
    return grouped


def owning_module_path(file_path: str, module_paths: Tuple[str, ...]) -> Optional[str]:
    """
    Pure: The most specific module path a file sits under, or None.

    Args:
        file_path: Project-relative file path
        module_paths: Module directory paths (with or without trailing '/')

    Returns:
        Matching module path, longest first, or None
    """
    matches = tuple(
        mp for mp in module_paths
        if mp and file_path.startswith(mp.rstrip('/') + '/')
    )
    return max(matches, key=len) if matches else None


def capped(entries: Tuple[Dict[str, Any], ...], cap: int = GAP_LIST_CAP) -> Dict[str, Any]:
    """Pure: {'total': n, 'items': first cap entries}."""
    return {'total': len(entries), 'items': entries[:cap]}


def build_structure_summary(rows: StructureRows) -> Dict[str, Any]:
    """
    Pure: Compact map of the layer: themes -> flows (with file counts), open paths
    -> themes, open milestones -> flows.

    Args:
        rows: Raw structure rows

    Returns:
        Dict with themes, unthemed_flows, open_paths, open_milestones
    """
    files_per_flow = {r['flow_id']: r['file_count'] for r in rows.file_flow_counts}
    flows_by_id = {f['id']: f for f in rows.flows}
    flows_of_theme = _ids_by(rows.flow_themes, 'theme_id', 'flow_id')
    themed = {r['flow_id'] for r in rows.flow_themes}
    themes_of_path = _ids_by(rows.path_themes, 'completion_path_id', 'theme_id')
    flows_of_milestone = _ids_by(rows.milestone_flows, 'milestone_id', 'flow_id')

    def flow_entry(flow_id: int) -> Dict[str, Any]:
        return {'id': flow_id, 'name': flows_by_id.get(flow_id, {}).get('name'),
                'files': files_per_flow.get(flow_id, 0)}

    return {
        'themes': tuple(
            {'id': t['id'], 'name': t['name'],
             'flows': tuple(flow_entry(fid) for fid in sorted(flows_of_theme.get(t['id'], ())))}
            for t in rows.themes
        ),
        'unthemed_flows': tuple(flow_entry(f['id']) for f in rows.flows if f['id'] not in themed),
        'open_paths': tuple(
            {'id': p['id'], 'name': p['name'], 'theme_ids': tuple(sorted(themes_of_path.get(p['id'], ())))}
            for p in rows.paths if p.get('status') != 'completed'
        ),
        'open_milestones': tuple(
            {'id': m['id'], 'name': m['name'],
             'flow_ids': tuple(sorted(flows_of_milestone.get(m['id'], ())))}
            for m in rows.milestones if m.get('status') != 'completed'
        ),
    }


def build_structure_gaps(rows: StructureRows, split_chars: int = FLOW_SPLIT_CHARS) -> Dict[str, Any]:
    """
    Pure: Every gap in the modularity layer, as capped lists with totals.

    Args:
        rows: Raw structure rows
        split_chars: Flow description length that marks a split candidate

    Returns:
        Dict with ok (no gaps), total_gaps, and one capped entry per non-empty gap
    """
    themed = {r['flow_id'] for r in rows.flow_themes}
    theme_with_flows = {r['theme_id'] for r in rows.flow_themes}
    module_paths = tuple(m['path'] for m in rows.modules if m.get('path'))
    in_module = set(rows.module_file_ids)
    path_with_themes = {r['completion_path_id'] for r in rows.path_themes}
    milestone_with_flows = {r['milestone_id'] for r in rows.milestone_flows}

    gaps = {
        'flows_without_theme': tuple(
            {'id': f['id'], 'name': f['name']} for f in rows.flows if f['id'] not in themed),
        'themes_without_flows': tuple(
            {'id': t['id'], 'name': t['name']} for t in rows.themes if t['id'] not in theme_with_flows),
        'files_without_flow': tuple(
            {'id': f['id'], 'path': f['path']} for f in rows.files
            if not f.get('flow_count') and not f.get('no_flow_reason')),
        'files_outside_module': tuple(
            {'id': f['id'], 'path': f['path'], 'module_path': mp}
            for f in rows.files if f['id'] not in in_module
            for mp in (owning_module_path(f['path'], module_paths),) if mp),
        'open_paths_without_themes': tuple(
            {'id': p['id'], 'name': p['name']} for p in rows.paths
            if p.get('status') != 'completed' and p['id'] not in path_with_themes),
        'open_milestones_without_flows': tuple(
            {'id': m['id'], 'name': m['name']} for m in rows.milestones
            if m.get('status') != 'completed' and m['id'] not in milestone_with_flows),
        'oversized_flows': tuple(
            {'id': f['id'], 'name': f['name'], 'chars': len(f.get('description') or '')}
            for f in rows.flows if len(f.get('description') or '') > split_chars),
    }
    present = {k: capped(v) for k, v in gaps.items() if v}
    total = sum(entry['total'] for entry in present.values())
    return {'ok': total == 0, 'total_gaps': total, **present}


# ============================================================================
# Effect Functions
# ============================================================================

def _rows(conn: sqlite3.Connection, sql: str) -> Tuple[Dict[str, Any], ...]:
    """Effect: Run a read query; () when a table or column is absent (pre-v1.13)."""
    try:
        return tuple(dict(r) for r in conn.execute(sql).fetchall())
    except sqlite3.OperationalError:
        return ()


def query_structure_rows(conn: sqlite3.Connection) -> StructureRows:
    """
    Effect: Read everything the structure report needs in one pass.

    Args:
        conn: Project database connection (row_factory=sqlite3.Row)

    Returns:
        StructureRows
    """
    files = _rows(conn, """
        SELECT f.id, f.path, f.no_flow_reason,
               (SELECT COUNT(*) FROM file_flows ff WHERE ff.file_id = f.id) AS flow_count
        FROM files f WHERE f.is_reserved = 0 ORDER BY f.path""") or _rows(conn, """
        SELECT f.id, f.path, NULL AS no_flow_reason,
               (SELECT COUNT(*) FROM file_flows ff WHERE ff.file_id = f.id) AS flow_count
        FROM files f WHERE f.is_reserved = 0 ORDER BY f.path""")
    return StructureRows(
        themes=_rows(conn, "SELECT id, name FROM themes ORDER BY id"),
        flows=_rows(conn, "SELECT id, name, description FROM flows ORDER BY id"),
        flow_themes=_rows(conn, "SELECT flow_id, theme_id FROM flow_themes"),
        file_flow_counts=_rows(
            conn, "SELECT flow_id, COUNT(*) AS file_count FROM file_flows GROUP BY flow_id"),
        files=files,
        modules=_rows(conn, "SELECT id, name, path FROM modules"),
        module_file_ids=tuple(r['file_id'] for r in _rows(conn, "SELECT DISTINCT file_id FROM module_files")),
        paths=_rows(conn, "SELECT id, name, status FROM completion_path ORDER BY order_index, id"),
        path_themes=_rows(conn, "SELECT completion_path_id, theme_id FROM completion_path_themes"),
        milestones=_rows(conn, "SELECT id, name, status FROM milestones ORDER BY id"),
        milestone_flows=_rows(conn, "SELECT milestone_id, flow_id FROM milestone_flows"),
    )


def collect_structure_health(conn: sqlite3.Connection) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Effect: (summary, gaps) for an open project connection — the single entry
    point every surface (status, aimfp_end, tool, watchdog) uses.

    Args:
        conn: Project database connection

    Returns:
        Tuple of (build_structure_summary, build_structure_gaps) results
    """
    rows = query_structure_rows(conn)
    return build_structure_summary(rows), build_structure_gaps(rows)


# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def get_structure_health(include_summary: bool = True, project_root: Optional[str] = None) -> StructureHealthResult:
    """
    Report the modularity layer: themes -> flows -> files, paths -> themes,
    milestones -> flows, module membership, and every gap in it.

    Args:
        include_summary: Also return the themes/flows/paths/milestones map (default True)

    Returns:
        StructureHealthResult with summary (optional) and health (gaps)
    """
    conn = _open_project_connection(project_root or get_cached_project_root())
    try:
        summary, health = collect_structure_health(conn)
        return StructureHealthResult(
            success=True,
            summary=summary if include_summary else None,
            health=health,
            return_statements=get_return_statements("get_structure_health"),
        )
    except Exception as e:
        return StructureHealthResult(success=False, error=f"Structure health failed: {e}")
    finally:
        conn.close()
