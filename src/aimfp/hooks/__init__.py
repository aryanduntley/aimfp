"""
AIMFP Hooks - Library Surface for Code Running Outside AIMFP

Nothing in this package is an MCP tool, and nothing here may become one.
These functions are imported and called by code that is not AIMFP: a Use Case
2 automation runner generated into a user's project, firing from cron,
systemd, or its own scheduler, with no AI session open.

The AI's relationship to this package is that it WRITES CODE CALLING IT
during user_directive_implement - it never invokes these functions itself.
Exposing run_directive as a tool would let the AI execute a user's automation
inside an MCP session at an arbitrary moment, which is the exact thing the
hook execution model exists to prevent.

Import weight is part of the contract. This package reaches only into
database/connection.py, which is stdlib-only, so installing aimfp in a
headless automation environment stays cheap. Do not import the watchdog, the
directive loader, or the MCP server from here.

Design recorded in project note 34.
"""

from .config import hooks_available, load_log_config
from .directives import (
    get_due_directives,
    record_directive_error,
    record_execution_end,
    record_execution_start,
    run_directive,
    set_next_scheduled_time,
)
from .results import (
    DueDirective,
    DueDirectivesResult,
    ExecutionResult,
    ExecutionToken,
    HookMutationResult,
    LogConfig,
    RotationPlan,
)

__all__ = [
    # Dispatch
    'get_due_directives',
    'run_directive',
    # Execution recording
    'record_execution_start',
    'record_execution_end',
    'record_directive_error',
    'set_next_scheduled_time',
    # Availability and configuration
    'hooks_available',
    'load_log_config',
    # Types
    'DueDirective',
    'DueDirectivesResult',
    'ExecutionToken',
    'ExecutionResult',
    'HookMutationResult',
    'LogConfig',
    'RotationPlan',
]
