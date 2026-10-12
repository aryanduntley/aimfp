"""
AIMFP Helper Functions - `aimfp audit` Command Line

Runs the combined tracking audit outside an AI session — in CI, a pre-commit
hook, or by hand — against the AIMFP project found from the working directory.

    aimfp audit [--task N [--task-type T]] [--since REF] [--path P ...]
                [--file-id N ...] [--module DIR] [--only A,B] [--json]
                [--strict] [--no-report] [--project-dir REL]

Exit codes: 0 no blocking findings (warnings allowed), 1 blocking findings
(or any warning with --strict), 2 usage or runtime error.
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional, Sequence

from ...database.connection import _discover_project_root, set_project_dir_override
from .code_index import build_code_index, build_scope_request
from .reports import AuditResult
from .tracking_audit import AUDITS, normalize_include, tracking_audit_result


EXIT_CLEAN = 0
EXIT_BLOCKING = 1
EXIT_ERROR = 2


# ============================================================================
# Pure Helpers
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Pure: The argument parser for `aimfp audit`."""
    parser = argparse.ArgumentParser(
        prog='aimfp audit',
        description='Audit AIMFP tracking (project.db) against the source code.',
    )
    parser.add_argument('--task', type=int, dest='task_id', help='audit the files linked to this task')
    parser.add_argument('--task-type', default='task', choices=('task', 'subtask', 'sidequest'))
    parser.add_argument('--since', dest='since_commit', help="audit files changed since a git ref ('HEAD', 'main', a commit)")
    parser.add_argument('--path', dest='paths', action='append', default=[], help='project-relative file (repeatable)')
    parser.add_argument('--file-id', dest='file_ids', type=int, action='append', default=[], help='tracked file id (repeatable)')
    parser.add_argument('--module', dest='module_path', help='audit every file under this project-relative directory')
    parser.add_argument('--only', default='', help=f"comma-separated audits: {','.join(AUDITS)} (default all)")
    parser.add_argument('--json', action='store_true', help='print the result as JSON')
    parser.add_argument('--strict', action='store_true', help='exit 1 on warnings too')
    parser.add_argument('--no-report', action='store_true', help='do not write the report file')
    parser.add_argument('--project-dir', help='project folder relative to the working directory (default .aimfp-project)')
    return parser


def exit_code(result: AuditResult, strict: bool) -> int:
    """Pure: 0 clean, 1 blocking (or warnings when strict), 2 failed."""
    if not result.success:
        return EXIT_ERROR
    if result.blocking or (strict and result.warnings):
        return EXIT_BLOCKING
    return EXIT_CLEAN


def render_text(result: AuditResult) -> str:
    """Pure: A human-readable summary of an audit result."""
    if not result.success:
        return f"audit failed: {result.error}"
    scope = result.scope
    lines = [
        f"AIMFP tracking audit — {scope.get('label')}: {scope.get('files_audited', 0)} file(s) "
        f"{scope.get('fidelity', {})}",
    ]
    if not result.counts:
        lines.append("clean: no findings")
    for level, categories in (('BLOCKING', result.blocking), ('warning', result.warnings)):
        for category in categories:
            lines.append(f"  {level:8} {category}: {result.counts[category]}")
    info = sorted(set(result.counts) - set(result.blocking) - set(result.warnings))
    for category in info:
        lines.append(f"  {'info':8} {category}: {result.counts[category]}")
    if result.report_path:
        lines.append(f"report: {result.report_path}")
    return '\n'.join(lines)


def result_json(result: AuditResult) -> Dict[str, Any]:
    """Pure: The JSON document `--json` prints (return statements omitted: they address an AI)."""
    return {
        'success': result.success, 'error': result.error, 'scope': result.scope,
        'counts': result.counts, 'blocking': list(result.blocking), 'warnings': list(result.warnings),
        'clean': result.clean, 'report_path': result.report_path, 'findings': result.findings,
    }


# ============================================================================
# Effects
# ============================================================================

def run_audit(args: argparse.Namespace, project_root: str) -> AuditResult:
    """Effect: Build the index for the parsed arguments and run the requested audits."""
    audits, unknown = normalize_include([a for a in args.only.split(',') if a])
    if unknown:
        return AuditResult(success=False, error=f"unknown audit(s): {', '.join(unknown)}")
    index, error = build_code_index(project_root, build_scope_request(
        args.file_ids, args.paths, args.since_commit, args.task_id, args.task_type, args.module_path))
    if index is None:
        return AuditResult(success=False, error=error)
    return tracking_audit_result(project_root, index, audits, 'audit_tracking', (),
                                 write_report=not args.no_report)


def main_audit(argv: Sequence[str], cwd: Optional[str] = None) -> int:
    """
    Effect: Entry point for `aimfp audit`; prints the result and returns the exit code.

    Args:
        argv: Arguments after 'audit'
        cwd: Working directory to discover the project from (default: the process cwd)
    """
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return EXIT_CLEAN if exc.code == 0 else EXIT_ERROR
    workdir = cwd or os.getcwd()
    try:
        if args.project_dir:
            set_project_dir_override(workdir, args.project_dir)
        project_root = _discover_project_root() if cwd is None else (
            workdir if os.path.isdir(os.path.join(workdir, args.project_dir or '.aimfp-project')) else None)
        if project_root is None:
            print("audit failed: no AIMFP project found from the working directory "
                  "(run aimfp_init, or pass --project-dir)", file=sys.stderr)
            return EXIT_ERROR
        result = run_audit(args, project_root)
    except Exception as exc:
        result = AuditResult(success=False, error=str(exc))
    print(json.dumps(result_json(result), indent=1, default=str) if args.json else render_text(result))
    return exit_code(result, args.strict)
