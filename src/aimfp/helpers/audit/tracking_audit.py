"""
AIMFP Helper Functions - Combined Tracking Audit

audit_tracking runs every audit over ONE code index — files, functions,
types, parameters, interactions, descriptions — plus the project's modularity
gaps, and writes one report. It is what the task-completion gate, aimfp_end
and the `aimfp audit` CLI run; the single audits exist for focused work.

include narrows which audits run: 'functions', 'interactions',
'descriptions', 'structure' (default: all).
"""

from typing import Any, Dict, List, Optional, Tuple

from ..utils import get_cached_project_root, get_return_statements, _open_project_connection
from ..project.structure_health import collect_structure_health
from ..project.task_files import query_current_focus_row
from ...watchdog.config import STRUCTURE_GAP_LABELS
from .code_index import CodeIndex, ScopeRequest, build_code_index, build_scope_request, scope_summary
from .descriptions_audit import run_description_audit
from .functions_audit import audit_function_findings, reconcile_targets
from .interactions_audit import run_interaction_audit
from .reports import SAMPLE_SIZE, AuditResult, finish_audit, validate_sample_options


AUDITS: Tuple[str, ...] = ('functions', 'interactions', 'descriptions', 'structure')

# Audits for a slice of the project (a task's files, a session's changes):
# structure gaps are project-wide, so they are reported by structure_health instead.
GATE_AUDITS: Tuple[str, ...] = ('functions', 'interactions', 'descriptions')


# ============================================================================
# Pure Helpers
# ============================================================================

def normalize_include(include: Optional[List[str]]) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Pure: (audits to run, unknown names). None or empty means all."""
    requested = tuple(dict.fromkeys(i.strip().lower() for i in include or () if i and i.strip()))
    if not requested:
        return (AUDITS, ())
    return (tuple(a for a in AUDITS if a in requested), tuple(r for r in requested if r not in AUDITS))


def merge_findings(*parts: Dict[str, Tuple[Any, ...]]) -> Dict[str, Tuple[Any, ...]]:
    """
    Pure: One findings map from several audits. Categories every audit emits
    (unchecked_files, skipped_paths) are merged without duplicates.
    """
    merged: Dict[str, Tuple[Any, ...]] = {}
    for part in parts:
        for category, items in part.items():
            if category not in merged:
                merged[category] = tuple(items)
                continue
            seen = {repr(sorted(i.items())) if isinstance(i, dict) else repr(i) for i in merged[category]}
            merged[category] += tuple(
                i for i in items
                if (repr(sorted(i.items())) if isinstance(i, dict) else repr(i)) not in seen
            )
    if 'unchecked_files' in merged:
        by_path: Dict[str, Any] = {}
        for item in merged['unchecked_files']:
            by_path.setdefault(item.get('path'), item)
        merged['unchecked_files'] = tuple(by_path.values())
    return merged


def structure_gap_findings(health: Dict[str, Any]) -> Tuple[Dict[str, Any], ...]:
    """Pure: One {gap, label, total} finding per kind of modularity gap present."""
    return tuple(
        {'gap': gap, 'label': label, 'total': (health.get(gap) or {}).get('total', 0),
         'detail': 'get_structure_health lists every one'}
        for gap, label in STRUCTURE_GAP_LABELS.items()
        if (health.get(gap) or {}).get('total')
    )


# ============================================================================
# Effects
# ============================================================================

def _effect_structure_health(project_root: str) -> Dict[str, Any]:
    """Effect: The project's structure gaps ({} when they cannot be read)."""
    try:
        conn = _open_project_connection(project_root)
        try:
            return collect_structure_health(conn)[1]
        finally:
            conn.close()
    except Exception:
        return {}


def run_tracking_audit(index: CodeIndex, audits: Tuple[str, ...]) -> Dict[str, Tuple[Any, ...]]:
    """Effect: Every requested audit over one index, merged."""
    return merge_findings(
        audit_function_findings(index) if 'functions' in audits else {},
        run_interaction_audit(index) if 'interactions' in audits else {},
        run_description_audit(index) if 'descriptions' in audits else {},
        {'structure_gaps': structure_gap_findings(_effect_structure_health(index.project_root))}
        if 'structure' in audits else {},
    )


def tracking_audit_result(
    project_root: str,
    index: CodeIndex,
    audits: Tuple[str, ...],
    tool: str,
    statements: Tuple[str, ...],
    write_report: bool = True,
    sample_size: int = SAMPLE_SIZE,
    full_categories: Tuple[str, ...] = (),
) -> AuditResult:
    """Effect: Run the combined audit and finish it (report + compact result)."""
    findings = run_tracking_audit(index, audits)
    return finish_audit(
        project_root, tool, {**scope_summary(index), 'audits': list(audits)}, findings,
        statements=statements,
        extra={'reconcile_paths': list(reconcile_targets(findings))},
        write_report=write_report,
        sample_size=sample_size,
        full_categories=full_categories,
    )


def _effect_focus_request(project_root: str) -> Optional[ScopeRequest]:
    """Effect: A scope request for the current in-progress work item's files, or None."""
    conn = _open_project_connection(project_root)
    try:
        focus = query_current_focus_row(conn)
    finally:
        conn.close()
    if focus is None:
        return None
    return build_scope_request(task_id=focus['id'], task_type=focus['item_type'])


def session_audit(project_root: str) -> Dict[str, Any]:
    """
    Effect: The audit aimfp_end reports — this session's uncommitted work
    (files changed since HEAD), or the in-progress work item's files when the
    project is not a git repository. Never raises.

    Returns:
        {scope, files_audited, counts, blocking, warnings, report_path} — or
        {scope, files_audited: 0} when there is nothing to audit, or {error}
    """
    try:
        index, error = build_code_index(project_root, build_scope_request(since_commit='HEAD'))
        label = 'uncommitted changes (since HEAD)'
        if index is None:
            request = _effect_focus_request(project_root)
            if request is None:
                return {'scope': f'nothing to audit ({error})', 'files_audited': 0}
            index, error = build_code_index(project_root, request)
            label = f"in-progress {request.task_type} {request.task_id} (no git: {error})"
            if index is None:
                return {'error': error}
        if not index.files:
            return {'scope': label, 'files_audited': 0}
        result = tracking_audit_result(project_root, index, GATE_AUDITS, 'session_audit', ())
        return {
            'scope': label,
            'files_audited': len(index.files),
            'counts': result.counts,
            'blocking': list(result.blocking),
            'warnings': list(result.warnings),
            'report_path': result.report_path,
        }
    except Exception as exc:
        return {'error': f"session audit could not run: {exc}"}


# ============================================================================
# Public Tool
# ============================================================================

def audit_tracking(
    file_ids: Optional[List[int]] = None,
    paths: Optional[List[str]] = None,
    since_commit: Optional[str] = None,
    task_id: Optional[int] = None,
    task_type: Optional[str] = None,
    module_path: Optional[str] = None,
    include: Optional[List[str]] = None,
    project_root: Optional[str] = None,
    sample_limit: int = SAMPLE_SIZE,
    full: Optional[List[str]] = None,
) -> AuditResult:
    """
    Run every tracking audit over one scope and write one report. Read-only.

    Scope selectors combine as a union; with none, the whole project is audited.

    Args:
        file_ids: Tracked file IDs to audit
        paths: Project-relative file paths to audit
        since_commit: Audit files changed since this git ref (e.g. 'HEAD', 'main')
        task_id: Audit the files linked to this task (and its subtasks)
        task_type: 'task' (default), 'subtask' or 'sidequest' for task_id
        module_path: Audit every file under this project-relative directory
        include: Audits to run: 'functions', 'interactions', 'descriptions',
            'structure' (default all)
        project_root: Explicit root for embedding hosts (defaults to the session root)
        sample_limit: Findings shown inline per category (0 = counts only)
        full: Finding categories to return inline in full (e.g. ['stale_descriptions'])

    Returns:
        AuditResult: counts, samples, block/warn verdict, report_path
    """
    root = project_root or get_cached_project_root()
    audits, unknown = normalize_include(include)
    if unknown:
        return AuditResult(success=False, error=(
            f"Unknown audit(s): {', '.join(unknown)}. Use any of: {', '.join(AUDITS)}."))
    full_categories = tuple(full or ())
    sample_error = validate_sample_options(sample_limit, full_categories)
    if sample_error:
        return AuditResult(success=False, error=sample_error)
    try:
        index, error = build_code_index(root, build_scope_request(
            file_ids, paths, since_commit, task_id, task_type, module_path))
        if index is None:
            return AuditResult(success=False, error=error)
        return tracking_audit_result(root, index, audits, 'audit_tracking',
                                     get_return_statements("audit_tracking"),
                                     sample_size=sample_limit,
                                     full_categories=full_categories)
    except Exception as exc:
        return AuditResult(success=False, error=f"Tracking audit failed: {exc}")
