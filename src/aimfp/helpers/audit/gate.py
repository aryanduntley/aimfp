"""
AIMFP Helper Functions - Task Completion Audit Gate

Completing a task, subtask or sidequest audits the files linked to it. What
happens next depends on the user setting audit_gate:

- block (default): any block-level finding (missing/stale functions, types or
  edges, repoints, parameter drift, incomplete or malformed records,
  unfinished reservations) refuses the completion. The refusal carries the
  counts, the report path, and the exact calls that fix the mechanical part.
- warn: the audit runs and its findings come back with the completion, which
  always succeeds — for projects mid-adoption or carrying legacy drift.
- off: no audit.

Rules:
- Only the work item's linked files are audited, so drift elsewhere in the
  project never blocks unrelated work.
- Only full-fidelity findings exist at block level, so a names-only language
  can never block.
- An audit that cannot run (no linked files, unreadable source, any error)
  never blocks: the gate is a safety net, not a new way to get stuck.
- The gate never writes tracking data itself (report-only, user decision):
  every repair stays a deliberate, visible call.
- audit_override_reason completes anyway and records a warning note naming the
  outstanding findings and the reason.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from ..utils import get_user_preferences_db_path, _open_connection
from .apply_fixes import CATEGORY_OPERATIONS
from .code_index import build_code_index, build_scope_request, scope_summary
from .functions_audit import reconcile_targets
from .reports import categories_at, finish_audit
from .tracking_audit import GATE_AUDITS, run_tracking_audit


# Work item table -> task_type for the audit scope.
GATED_TABLES: Dict[str, str] = {'tasks': 'task', 'subtasks': 'subtask', 'sidequests': 'sidequest'}

GATE_MODES: Tuple[str, ...] = ('block', 'warn', 'off')
DEFAULT_GATE_MODE = 'block'
GATE_SETTING = 'audit_gate'

# Blocking categories only the AI can clear (prose, reservations, lost files).
PROSE_FIXES: Dict[str, str] = {
    'incomplete_functions': "fill the listed fields with update_function / update_functions_for_file",
    'incomplete_types': "fill the listed fields with update_type",
    'reserved_files': "finalize_file(s) for files whose code exists, else delete the reservation",
    'reserved_functions': "finalize_function(s) if the code exists, else delete them",
    'missing_files': "the file is gone from disk: delete_file, or restore the file",
}


# ============================================================================
# Immutable Records
# ============================================================================

@dataclass(frozen=True)
class GateVerdict:
    """What the completion audit found for one work item."""
    blocked: bool
    mode: str = DEFAULT_GATE_MODE
    blocking: Dict[str, int] = field(default_factory=dict)
    warnings: Dict[str, int] = field(default_factory=dict)
    report_path: Optional[str] = None
    files_audited: int = 0
    fix_steps: Tuple[str, ...] = ()
    skipped_reason: Optional[str] = None


# ============================================================================
# Pure Helpers
# ============================================================================

def normalize_gate_mode(value: Optional[str]) -> str:
    """Pure: 'block' | 'warn' | 'off'; anything unrecognised is the default (block)."""
    mode = (value or '').strip().lower()
    return mode if mode in GATE_MODES else DEFAULT_GATE_MODE


def format_counts(counts: Dict[str, int]) -> str:
    """Pure: 'a=1, b=2' in category order."""
    return ', '.join(f"{category}={count}" for category, count in sorted(counts.items()))


def fix_steps(blocking: Dict[str, int], report_path: Optional[str],
              reconcile_paths: Tuple[str, ...]) -> Tuple[str, ...]:
    """
    Pure: The exact calls that clear the blocking findings, in order: catalog
    what is missing, apply the mechanical categories, then the prose work only
    the AI can do.
    """
    appliable = sorted(c for c in blocking if c in CATEGORY_OPERATIONS)
    return (
        ((f"reconcile_paths(paths={list(reconcile_paths)})",) if reconcile_paths else ())
        + ((f"apply_audit_fixes(reason='<why>', report='{report_path}', apply={appliable})",)
           if appliable and report_path else ())
        + tuple(f"{category}: {PROSE_FIXES[category]}" for category in sorted(blocking) if category in PROSE_FIXES)
    )


def gate_refusal(table: str, item_id: int, verdict: GateVerdict) -> str:
    """Pure: The error a blocked completion returns: what, where, the exact fix, how to override."""
    kind = GATED_TABLES.get(table, table)
    numbered = ' '.join(f"({i}) {step}" for i, step in enumerate(verdict.fix_steps, start=1))
    return (
        f"Cannot complete {kind} {item_id}: the tracking audit of its {verdict.files_audited} "
        f"linked file(s) found blocking drift: {format_counts(verdict.blocking)}. "
        f"Full report: {verdict.report_path}. Fix: {numbered or 'see the report'}; then complete again. "
        f"Keep deliberate indirect links out of stale deletes with "
        f"apply_audit_fixes(mark_indirect_ids=[...]) first. To complete anyway, pass "
        f"audit_override_reason='<why>' (recorded as a warning note); the user can also set "
        f"{GATE_SETTING}='warn' while legacy drift is cleaned up."
    )


def override_note(table: str, item_id: int, reason: str, verdict: GateVerdict) -> str:
    """Pure: The warning note recorded when a completion overrides the audit."""
    return (
        f"AUDIT GATE OVERRIDDEN: {GATED_TABLES.get(table, table)} {item_id} completed with "
        f"outstanding blocking drift ({format_counts(verdict.blocking)}) in its linked files. "
        f"Reason: {reason}. Report: {verdict.report_path}. Fix the drift in a follow-up."
    )


def gate_summary(verdict: GateVerdict) -> Optional[Dict[str, Any]]:
    """
    Pure: The compact audit block a successful completion returns (None when
    nothing to say). In warn mode, would_block lists what block mode would
    have refused, with the fix steps.
    """
    if not verdict.blocking and not verdict.warnings:
        return None
    return {
        'mode': verdict.mode,
        'overridden': verdict.blocked,
        ('blocking' if verdict.mode == 'block' else 'would_block'): verdict.blocking,
        'warnings': verdict.warnings,
        'report_path': verdict.report_path,
        **({'fix_steps': list(verdict.fix_steps)} if verdict.fix_steps else {}),
    }


# ============================================================================
# Effects
# ============================================================================

def _effect_gate_mode(project_root: str) -> str:
    """Effect: The audit_gate user setting (default block when unset or unreadable)."""
    try:
        conn = _open_connection(get_user_preferences_db_path(project_root))
        try:
            row = conn.execute(
                "SELECT setting_value FROM user_settings WHERE setting_key = ?", (GATE_SETTING,)
            ).fetchone()
        finally:
            conn.close()
        return normalize_gate_mode(row[0] if row else None)
    except Exception:
        return DEFAULT_GATE_MODE


def audit_work_item(project_root: str, table: str, item_id: int) -> GateVerdict:
    """
    Effect: Audit a work item's linked files under the configured gate mode.
    Writes a report only when something would block (the AI needs it to fix
    the drift).
    """
    task_type = GATED_TABLES.get(table)
    if task_type is None:
        return GateVerdict(blocked=False, skipped_reason=f"{table} is not gated")
    mode = _effect_gate_mode(project_root)
    if mode == 'off':
        return GateVerdict(blocked=False, mode=mode, skipped_reason=f"{GATE_SETTING}=off")
    try:
        index, error = build_code_index(project_root, build_scope_request(task_id=item_id, task_type=task_type))
        if index is None:
            return GateVerdict(blocked=False, mode=mode, skipped_reason=error)
        if not index.files:
            return GateVerdict(blocked=False, mode=mode, skipped_reason="no linked files to audit")
        findings = run_tracking_audit(index, GATE_AUDITS)
        counts = {category: len(items) for category, items in findings.items() if items}
        blocking = {c: counts[c] for c in categories_at(counts, 'block')}
        warnings = {c: counts[c] for c in categories_at(counts, 'warn')}
        if not blocking:
            return GateVerdict(blocked=False, mode=mode, warnings=warnings, files_audited=len(index.files))
        reconcile = reconcile_targets(findings)
        report = finish_audit(project_root, 'task_gate', scope_summary(index), findings,
                              extra={'reconcile_paths': list(reconcile)})
        return GateVerdict(
            blocked=(mode == 'block'),
            mode=mode,
            blocking=blocking,
            warnings=warnings,
            report_path=report.report_path,
            files_audited=len(index.files),
            fix_steps=fix_steps(blocking, report.report_path, reconcile),
        )
    except Exception as exc:
        return GateVerdict(blocked=False, mode=mode, skipped_reason=f"audit could not run: {exc}")


def _effect_record_override(conn: sqlite3.Connection, table: str, item_id: int, reason: str,
                            verdict: GateVerdict) -> None:
    """
    Effect: Write the override warning note through the caller's connection.

    Committed here only when the caller had no transaction open: inside a
    caller's transaction (batch_update_progress holds BEGIN IMMEDIATE) a
    second connection could never get the write lock, and committing would
    end the caller's atomic batch.
    """
    was_in_transaction = conn.in_transaction
    conn.execute(
        "INSERT INTO notes (content, note_type, reference_table, reference_id, source, severity) "
        "VALUES (?, 'warning', ?, ?, 'ai', 'warning')",
        (override_note(table, item_id, reason, verdict), table, item_id),
    )
    if not was_in_transaction:
        conn.commit()


def check_completion(
    project_root: str,
    table: str,
    item_id: int,
    override_reason: Optional[str],
    conn: sqlite3.Connection,
) -> Tuple[Optional[str], GateVerdict]:
    """
    Effect: Run the completion gate for a work item.

    Args:
        project_root: Project root
        table: 'tasks', 'subtasks' or 'sidequests'
        item_id: Work item ID
        override_reason: Complete despite blocking findings (recorded as a note)
        conn: The caller's open connection (the override note is written on it)

    Returns:
        (refusal error or None, verdict). With an override reason a blocking
        verdict is recorded as a warning note and completion may proceed.
    """
    verdict = audit_work_item(project_root, table, item_id)
    if not verdict.blocked:
        return (None, verdict)
    if override_reason and override_reason.strip():
        _effect_record_override(conn, table, item_id, override_reason.strip(), verdict)
        return (None, verdict)
    return (gate_refusal(table, item_id, verdict), verdict)
