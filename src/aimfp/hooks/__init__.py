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
    record_directive_skip,
    record_execution_start,
    run_directive,
    set_condition_state,
    set_next_scheduled_time,
)
from .actions import (
    ACTION_TYPES,
    action_config_grammar,
    validate_action_config,
)
from .results import (
    ActionConfigValidation,
    DueDirective,
    DueDirectivesResult,
    ExecutionResult,
    ExecutionToken,
    HookMutationResult,
    LogConfig,
    NextFireResult,
    RotationPlan,
    TriggerConfigValidation,
)
from .schedule import next_fire_time
from .triggers import (
    CONDITION_REPEAT_MODES,
    TIME_KINDS,
    TRIGGER_TYPES,
    WEEKDAY_TOKENS,
    trigger_config_grammar,
    validate_trigger_config,
)

__all__ = [
    # Dispatch
    'get_due_directives',
    'run_directive',
    # Execution recording
    'record_execution_start',
    'record_execution_end',
    'record_directive_error',
    'record_directive_skip',
    'set_next_scheduled_time',
    'set_condition_state',
    # Availability and configuration
    'hooks_available',
    'load_log_config',
    # Trigger configuration grammar
    'validate_trigger_config',
    'trigger_config_grammar',
    'next_fire_time',
    # Action configuration envelope
    'validate_action_config',
    'action_config_grammar',
    'ACTION_TYPES',
    'TRIGGER_TYPES',
    'TIME_KINDS',
    'WEEKDAY_TOKENS',
    'CONDITION_REPEAT_MODES',
    # Types
    'DueDirective',
    'DueDirectivesResult',
    'ExecutionToken',
    'ExecutionResult',
    'HookMutationResult',
    'LogConfig',
    'RotationPlan',
    'TriggerConfigValidation',
    'NextFireResult',
    'ActionConfigValidation',
]
