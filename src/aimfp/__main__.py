"""
AIMFP MCP Server Entry Point

Enables running the MCP server via: python -m aimfp

The server communicates over stdio using the Model Context Protocol,
exposing AIMFP helper functions as tools for AI assistants.

Usage:
    python -m aimfp                  Start the MCP server (stdio)
    python -m aimfp --system-prompt  Print the AIMFP system prompt to stdout
    python -m aimfp --project-dir <rel>
                                     Keep this folder's project databases at
                                     <cwd>/<rel> instead of <cwd>/.aimfp-project.
                                     Root discovery then looks ONLY at <cwd>.
    python -m aimfp --no-watchdog    Never spawn the watchdog daemon; the host
                                     runs its own watcher on this project.
    python -m aimfp --compact-returns
                                     Send each tool's return statements in full
                                     once per session, then a one-line pointer.

All server flags are per-process opt-ins (no environment variable), so a
server started without them behaves exactly as before.
"""

import os
import sys
from pathlib import Path

NO_WATCHDOG_FLAG = "--no-watchdog"
COMPACT_RETURNS_FLAG = "--compact-returns"


def _apply_server_flags(argv: tuple) -> None:
    """Effect: Apply --project-dir / --no-watchdog / --compact-returns before the server starts."""
    from .database.connection import extract_project_dir_arg, set_project_dir_override
    from .helpers.orchestrators.entry_points import set_session_watchdog_enabled

    project_dir = extract_project_dir_arg(argv)
    if project_dir is not None:
        set_project_dir_override(os.getcwd(), project_dir)
    if NO_WATCHDOG_FLAG in argv:
        set_session_watchdog_enabled(False)
    if COMPACT_RETURNS_FLAG in argv:
        from .mcp_server.server import set_compact_returns
        set_compact_returns(True)


def main() -> None:
    """Start the AIMFP MCP server, or print system prompt if --system-prompt flag is given."""
    if "--system-prompt" in sys.argv:
        prompt_path = Path(__file__).parent / "reference" / "system_prompt.txt"
        if not prompt_path.exists():
            print("Error: system_prompt.txt not found at expected location:", file=sys.stderr)
            print(f"  {prompt_path}", file=sys.stderr)
            sys.exit(1)
        print(prompt_path.read_text(), end="")
        return

    try:
        _apply_server_flags(tuple(sys.argv[1:]))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    from .mcp_server import run_server
    run_server()


if __name__ == "__main__":
    main()
