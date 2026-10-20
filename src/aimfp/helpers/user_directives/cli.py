"""
AIMFP Helper Functions - `aimfp directives` Command Line

The human approval path for a project that has no host application of its
own (AIMFP used as a plain MCP server). A person runs it in a terminal:

    aimfp directives list
    aimfp directives approve <name|id>
    aimfp directives revoke <name|id> [--reason TEXT]
    (each accepts --project-dir REL)

approve shows what is being approved and asks the person to type the
directive's name back. It refuses unless stdin is an interactive terminal, so
an AI's shell tool, whose stdin is a pipe, cannot approve through it. There is
deliberately no flag to skip that. A host with its own UI (svamanas) calls
aimfp.hooks.approval directly instead.

Exit codes: 0 done, 1 refused or declined, 2 usage or runtime error.
"""

import argparse
import json
import os
import sys
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from ...database.connection import (
    _close_connection,
    _discover_project_root,
    _open_connection,
    database_exists,
    get_user_directives_db_path,
    set_project_dir_override,
)
from ...hooks.approval import (
    approval_state,
    approve_user_directive,
    revoke_user_directive_approval,
)


EXIT_DONE = 0
EXIT_REFUSED = 1
EXIT_ERROR = 2


# ============================================================================
# Pure Helpers
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Pure: The argument parser for `aimfp directives`."""
    parser = argparse.ArgumentParser(
        prog='aimfp directives',
        description="List, approve or revoke this project's user directives. "
                    "Approval is a person's decision; run it yourself in a terminal.",
    )
    parser.add_argument('--project-dir', help='project folder relative to the working directory (default .aimfp-project)')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='show every directive with its approval state')
    approve = commands.add_parser('approve', help='approve a directive as it is now (interactive)')
    approve.add_argument('directive', help='directive name or id')
    approve.add_argument('--by', default=None, help='who is approving (default: your login name)')
    revoke = commands.add_parser('revoke', help="withdraw a directive's approval")
    revoke.add_argument('directive', help='directive name or id')
    revoke.add_argument('--reason', default=None, help='why, recorded in the lifecycle note')
    return parser


def _pretty_json(value: Optional[str]) -> str:
    """Pure: A JSON column for display, as stored when it does not parse."""
    try:
        return json.dumps(json.loads(value), indent=2, sort_keys=True) if value else '(none)'
    except ValueError:
        return value


def render_directive(row: Mapping[str, Any], state: str, reason: str) -> str:
    """Pure: What a person is shown before approving."""
    return '\n'.join((
        f"Directive {row['id']}: {row['name']}  [status {row['status']}, approval {state}: {reason}]",
        f"Trigger ({row['trigger_type']}):",
        _pretty_json(row['trigger_config']),
        f"Action ({row['action_type']}):",
        _pretty_json(row['action_config']),
        f"Implementation file: {row['implementation_file_path'] or '(none recorded)'}",
    ))


def render_list(entries: Sequence[Tuple[Mapping[str, Any], str, str]]) -> str:
    """Pure: One line per directive with its approval state."""
    if not entries:
        return 'no user directives'
    return '\n'.join(
        f"{row['id']:>4}  {row['name']:<32} {row['status']:<18} {state}: {reason}"
        for row, state, reason in entries)


# ============================================================================
# Effects
# ============================================================================

def _effect_read_rows(project_root: str) -> Tuple[Any, ...]:
    """Effect: Every user_directives row, ordered by id."""
    conn = _open_connection(get_user_directives_db_path(project_root), readonly=True)
    try:
        return tuple(conn.execute("SELECT * FROM user_directives ORDER BY id").fetchall())
    finally:
        _close_connection(conn)


def _find_row(rows: Sequence[Any], ref: str) -> Optional[Any]:
    """Pure: The row whose name, or id, is ref."""
    return next((row for row in rows if row['name'] == ref or str(row['id']) == ref), None)


def _default_approver() -> str:
    """Effect: The login name, for approved_by when --by is not given."""
    try:
        return os.getlogin()
    except OSError:
        return os.environ.get('USER') or os.environ.get('USERNAME') or 'user'


def run_command(
    args: argparse.Namespace,
    project_root: str,
    is_tty: bool,
    ask: Callable[[str], str],
    say: Callable[[str], None],
) -> int:
    """
    Effect: Run one parsed command against a project.

    is_tty, ask and say are passed in so the interactive gate is testable.
    """
    if not database_exists(get_user_directives_db_path(project_root)):
        say("no user_directives.db in this project (user directives are Use Case 2 only)")
        return EXIT_ERROR
    rows = _effect_read_rows(project_root)

    if args.command == 'list':
        say(render_list(tuple((row, *approval_state(project_root, row)) for row in rows)))
        return EXIT_DONE

    row = _find_row(rows, args.directive)
    if row is None:
        say(f"no directive named or numbered '{args.directive}'")
        return EXIT_ERROR

    if args.command == 'revoke':
        result = revoke_user_directive_approval(row['id'], project_root, args.reason)
        say(f"approval of '{row['name']}' revoked" if result.success else f"revoke failed: {result.error}")
        return EXIT_DONE if result.success else EXIT_ERROR

    if not is_tty:
        say("approve needs an interactive terminal: approval is a person's decision, "
            "so it cannot be piped or scripted. Run this command yourself.")
        return EXIT_REFUSED
    say(render_directive(row, *approval_state(project_root, row)))
    typed = ask(f"Type the directive name ('{row['name']}') to approve it as shown, anything else to cancel: ")
    if typed.strip() != row['name']:
        say('not approved; nothing changed')
        return EXIT_REFUSED
    result = approve_user_directive(row['id'], args.by or _default_approver(), project_root)
    if not result.success:
        say(f"approve failed: {result.error}")
        return EXIT_ERROR
    say(f"'{row['name']}' approved. Activate it from your AIMFP session (activate_user_directive).")
    return EXIT_DONE


def main_directives(argv: Sequence[str], cwd: Optional[str] = None) -> int:
    """
    Effect: Entry point for `aimfp directives`; returns the exit code.

    Args:
        argv: Arguments after 'directives'
        cwd: Working directory to discover the project from (default: the process cwd)
    """
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return EXIT_DONE if exc.code == 0 else EXIT_ERROR
    workdir = cwd or os.getcwd()
    try:
        if args.project_dir:
            set_project_dir_override(workdir, args.project_dir)
        project_root = _discover_project_root() if cwd is None else (
            workdir if os.path.isdir(os.path.join(workdir, args.project_dir or '.aimfp-project')) else None)
        if project_root is None:
            print("no AIMFP project found from the working directory "
                  "(run aimfp_init, or pass --project-dir)", file=sys.stderr)
            return EXIT_ERROR
        return run_command(args, project_root, sys.stdin.isatty(), input, print)
    except Exception as exc:
        print(f"directives failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
