"""
AIMFP Helper Functions - Audit Report Files

An audit of a whole project can list thousands of findings. Returning all of
them through a tool call spends the AI's context on data it will mostly hand
straight back to apply_audit_fixes. So every audit writes its full report to

    <project folder>/audit/<tool>-<UTC timestamp>.json

and returns only counts, a few samples per category, and report_path. The AI
reads slices of the file when it needs detail, and apply_audit_fixes can apply
whole categories straight from it (report=..., apply=[...]).

The folder carries its own .gitignore ('*'), so reports never reach git in any
project; backups skip it; the watchdog already ignores the project folder.
Each tool keeps only its newest KEEP_REPORTS_PER_TOOL reports. Deleting a
report by hand (or apply_audit_fixes(cleanup=true)) is always safe.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from ..utils import get_aimfp_project_dir
from ..shared.return_gates import gate_return_statements


AUDIT_DIR_NAME = 'audit'
KEEP_REPORTS_PER_TOOL = 3
SAMPLE_SIZE = 5
REPORT_FORMAT_VERSION = 1

# How much each finding category matters. 'block' stops a task from being
# completed (full-fidelity findings only); 'warn' is reported at the gate but
# never stops it; 'info' is context. Unlisted categories are 'info'.
CATEGORY_LEVELS: Dict[str, str] = {
    # interactions
    'missing_interactions': 'block',
    'stale_interactions': 'block',
    'repoint_interactions': 'block',
    'unconfirmed_interactions': 'warn',
    'indirect_interactions': 'info',
    'non_call_interactions': 'info',
    'unresolved_calls': 'info',
    # functions, types, files
    'missing_functions': 'block',
    'stale_functions': 'block',
    'param_mismatch': 'block',
    'incomplete_functions': 'block',
    'malformed_json': 'block',
    'reserved_functions': 'block',
    'missing_types': 'block',
    'stale_types': 'block',
    'incomplete_types': 'block',
    'missing_files': 'block',
    'reserved_files': 'block',
    'untracked_files': 'block',
    'missing_type_links': 'warn',
    'names_only_missing': 'warn',
    'files_without_functions': 'info',
    # descriptions
    'stale_descriptions': 'warn',
    'dangling_mentions': 'warn',
    'unbaselined_descriptions': 'info',
    'unbaselined_needs_review': 'info',
    # modularity layer (audit_tracking)
    'structure_gaps': 'warn',
    # every audit
    'exempted': 'info',
    'unchecked_files': 'info',
    'skipped_paths': 'info',
}


# ============================================================================
# Immutable Records
# ============================================================================

@dataclass(frozen=True)
class AuditResult:
    """Compact result every audit tool returns; the full findings are in report_path."""
    success: bool
    scope: Dict[str, Any] = field(default_factory=dict)
    counts: Dict[str, int] = field(default_factory=dict)
    findings: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    blocking: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    clean: bool = False
    report_path: Optional[str] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Helpers
# ============================================================================

def report_file_name(tool: str, now: datetime) -> str:
    """Pure: '<tool>-<YYYYmmddTHHMMSSffffff>Z.json' — sorts chronologically by name."""
    return f"{tool}-{now.strftime('%Y%m%dT%H%M%S%f')}Z.json"


def summarize_findings(
    findings: Dict[str, Tuple[Any, ...]],
    sample_size: int = SAMPLE_SIZE,
) -> Dict[str, Dict[str, Any]]:
    """
    Pure: {category: {count, sample}} for the categories that have findings.

    Args:
        findings: category -> every finding in it
        sample_size: How many findings per category to show inline
    """
    return {
        category: {'count': len(items), 'sample': list(items[:sample_size])}
        for category, items in findings.items()
        if items
    }


def build_report(
    tool: str,
    scope: Dict[str, Any],
    findings: Dict[str, Tuple[Any, ...]],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pure: The full report document written to disk."""
    return {
        'format_version': REPORT_FORMAT_VERSION,
        'tool': tool,
        'scope': scope,
        'counts': {category: len(items) for category, items in findings.items()},
        'findings': {category: list(items) for category, items in findings.items()},
        **(extra or {}),
    }


def category_level(category: str) -> str:
    """Pure: 'block', 'warn' or 'info' for a finding category."""
    return CATEGORY_LEVELS.get(category, 'info')


def categories_at(counts: Dict[str, int], level: str) -> Tuple[str, ...]:
    """Pure: Categories with findings at a level, in a stable order."""
    return tuple(sorted(c for c, n in counts.items() if n and category_level(c) == level))


def stale_reports(names: Tuple[str, ...], tool: str, keep: int) -> Tuple[str, ...]:
    """Pure: A tool's report file names beyond its newest `keep`, oldest first."""
    own = sorted(n for n in names if n.startswith(f"{tool}-") and n.endswith('.json'))
    return tuple(own[:-keep]) if keep > 0 else tuple(own)


# ============================================================================
# Effects
# ============================================================================

def audit_dir(project_root: str) -> str:
    """Effect-free path helper: absolute path of the audit report folder."""
    return os.path.join(get_aimfp_project_dir(project_root), AUDIT_DIR_NAME)


def _effect_write_report(
    project_root: str,
    tool: str,
    report: Dict[str, Any],
    keep: int = KEEP_REPORTS_PER_TOOL,
) -> str:
    """
    Effect: Write a report, keep the folder git-ignored, prune the tool's old
    reports.

    Returns:
        The report's path relative to the project root
    """
    folder = audit_dir(project_root)
    os.makedirs(folder, exist_ok=True)
    ignore_file = os.path.join(folder, '.gitignore')
    if not os.path.isfile(ignore_file):
        with open(ignore_file, 'w', encoding='utf-8') as f:
            f.write("# AIMFP audit reports: working files, safe to delete\n*\n")

    name = report_file_name(tool, datetime.now(timezone.utc))
    path = os.path.join(folder, name)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=1, default=str)

    for old in stale_reports(tuple(os.listdir(folder)), tool, keep):
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    return os.path.relpath(path, project_root)


def resolve_report_path(project_root: str, report_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Pure (path arithmetic): Absolute path of a report, refusing anything outside
    the audit folder.

    Accepts the project-relative report_path an audit returned, an absolute
    path, or a bare file name.

    Returns:
        (absolute path, None) or (None, reason)
    """
    folder = os.path.realpath(audit_dir(project_root))
    raw = (report_path or '').strip()
    if not raw:
        return (None, "report path is empty")
    candidate = raw if os.path.isabs(raw) else (
        os.path.join(folder, raw) if os.sep not in raw and '/' not in raw
        else os.path.join(project_root, raw)
    )
    resolved = os.path.realpath(candidate)
    if os.path.dirname(resolved) != folder:
        return (None, f"report must be a file in {os.path.relpath(folder, project_root)}/")
    return (resolved, None)


def _effect_load_report(project_root: str, report_path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Effect: Read a report written by an audit tool. Returns (report, None) or (None, error)."""
    path, reason = resolve_report_path(project_root, report_path)
    if path is None:
        return (None, reason)
    try:
        with open(path, encoding='utf-8') as f:
            report = json.load(f)
    except FileNotFoundError:
        return (None, f"report not found: {report_path}")
    except (OSError, ValueError) as exc:
        return (None, f"report unreadable: {exc}")
    if not isinstance(report, dict) or 'findings' not in report:
        return (None, "not an AIMFP audit report")
    return (report, None)


def _effect_delete_report(project_root: str, report_path: str) -> bool:
    """Effect: Delete one report file. Returns whether a file was removed."""
    path, _ = resolve_report_path(project_root, report_path)
    if path is None:
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def finish_audit(
    project_root: str,
    tool: str,
    scope: Dict[str, Any],
    findings: Dict[str, Tuple[Any, ...]],
    statements: Tuple[str, ...] = (),
    extra: Optional[Dict[str, Any]] = None,
    write_report: bool = True,
) -> AuditResult:
    """
    Effect: Write the full report (unless write_report is False) and build the
    compact result: non-zero counts, a few samples per category, the
    block/warn verdict, and the tool's return statements gated against it.

    Args:
        project_root: Project root
        tool: Tool name (report file prefix)
        scope: code_index.scope_summary output
        findings: category -> every finding
        statements: The tool's own return statements (the tool fetches them,
            so custom statements attached to it are included); gated here
        extra: More top-level report fields (e.g. reconcile_paths arguments)
        write_report: False for in-process callers (gates) that need no file
    """
    counts = {category: len(items) for category, items in findings.items() if items}
    report_path = (
        _effect_write_report(project_root, tool, build_report(tool, scope, findings, extra))
        if write_report and counts else None
    )
    blocking = categories_at(counts, 'block')
    payload = {
        'counts': counts,
        'blocking': blocking,
        'warnings': categories_at(counts, 'warn'),
        'clean': not blocking and not categories_at(counts, 'warn'),
        'report_path': report_path,
        'scope': scope,
    }
    return AuditResult(
        success=True,
        scope=scope,
        counts=counts,
        findings=summarize_findings(findings),
        blocking=blocking,
        warnings=payload['warnings'],
        clean=payload['clean'],
        report_path=report_path,
        return_statements=gate_return_statements(statements, payload),
    )
