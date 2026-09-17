"""
AIMFP Hooks - Immutable Return Types

Every type the hook surface hands to an external caller. All frozen, all
plain fields, so a caller outside AIMFP can round-trip them through
dataclasses.asdict() without knowing anything about AIMFP internals.

Two conventions worth knowing before reading the rest of the package:

1. ExecutionResult separates two different successes. `success` is whether
   AIMFP recorded the run; `action_succeeded` is whether the caller's
   handler worked. A handler that raises produces success=True with
   action_succeeded=False - the recording worked, the automation did not.
   Collapsing these would make a logging failure indistinguishable from an
   automation failure, which is exactly the distinction a monitor needs.

2. ExecutionToken carries both a wall-clock start (for the log record, which
   humans read) and a monotonic start (for the duration, which must not go
   backwards if the system clock is adjusted mid-run).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


# ============================================================================
# Directive Dispatch
# ============================================================================

@dataclass(frozen=True)
class DueDirective:
    """
    One active directive the caller's runner may execute now.

    Carries trigger config, action config, and the implementation file path
    recorded at implement time, so the runner can dispatch without a second
    query back into the database.

    condition_latched and last_condition_eval_time are the persisted
    edge-trigger state for condition directives (see set_condition_state).
    They are False/None for every other trigger type, and on a
    user_directives.db that has not been migrated to schema 1.3 yet.
    """
    directive_id: int
    name: str
    trigger_type: str
    trigger_config: Dict[str, Any]
    action_type: str
    action_config: Dict[str, Any]
    implementation_file_path: Optional[str] = None
    next_scheduled_time: Optional[str] = None
    last_execution_time: Optional[str] = None
    condition_latched: bool = False
    last_condition_eval_time: Optional[str] = None


@dataclass(frozen=True)
class DueDirectivesResult:
    """
    Return of get_due_directives.

    A missing user_directives.db is a clean success=False with an error set,
    never an exception - Use Case 1 projects have no such database and the
    caller must be able to ask without guarding.
    """
    success: bool
    directives: Tuple[DueDirective, ...] = ()
    total_count: int = 0
    error: Optional[str] = None


# ============================================================================
# Execution Recording
# ============================================================================

@dataclass(frozen=True)
class ExecutionToken:
    """
    Opaque handle from record_execution_start, passed to record_execution_end.

    `valid` is False when the start could not be recorded (no database, no
    such directive). record_execution_end accepts an invalid token and
    returns the carried error rather than failing separately, so a caller
    that ignores the start result still gets one coherent failure at the end.
    """
    directive_id: int
    project_root: str
    started_at: str
    started_monotonic: float
    valid: bool = True
    error: Optional[str] = None


@dataclass(frozen=True)
class ExecutionResult:
    """
    Outcome of a recorded execution.

    success          - AIMFP recorded the run
    action_succeeded - the caller's handler worked (None if it never ran)
    """
    success: bool
    directive_id: int
    action_succeeded: Optional[bool] = None
    duration_ms: Optional[float] = None
    action_error: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class HookMutationResult:
    """Success/error return for hook writes that produce no data."""
    success: bool
    directive_id: Optional[int] = None
    error: Optional[str] = None


# ============================================================================
# Logging
# ============================================================================

@dataclass(frozen=True)
class LogConfig:
    """
    Immutable snapshot of the logging_config row from user_directives.db.

    Resolved once per hook call rather than per write. Defaults mirror the
    schema so an absent row or absent database still yields a usable config -
    logging must never be the reason an automation run fails.
    """
    execution_logs_enabled: bool = True
    execution_log_rotation: str = 'daily'
    execution_log_retention_days: int = 30
    execution_log_compress_after_days: int = 7
    execution_log_max_size_mb: int = 100
    error_logs_enabled: bool = True
    error_log_rotation: str = 'size'
    error_log_retention_days: int = 90
    error_log_max_size_mb: int = 10
    error_log_format: str = 'json'
    store_execution_statistics: bool = True
    store_last_error_only: bool = True
    execution_log_dir: str = '.aimfp-project/logs/execution/'
    error_log_dir: str = '.aimfp-project/logs/errors/'
    lifecycle_log_path: str = '.aimfp-project/logs/user-directives.log'


@dataclass(frozen=True)
class RotationPlan:
    """
    Pure decision about what a log directory needs before the next append.

    Produced by plan_rotation from observed state, so the whole retention
    policy is testable without touching disk.
    """
    should_rotate: bool = False
    archive_name: Optional[str] = None
    compress_paths: Tuple[str, ...] = field(default_factory=tuple)
    delete_paths: Tuple[str, ...] = field(default_factory=tuple)


# ============================================================================
# Trigger Configuration
# ============================================================================

@dataclass(frozen=True)
class TriggerConfigValidation:
    """
    Outcome of checking one trigger_config against AIMFP's grammar.

    errors carries every field-level problem found, not just the first, and
    each message names the offending key - the caller should be able to fix
    the whole config from one call without re-reading the spec.

    kind echoes the resolved time-trigger kind when one was determined. It is
    None for event, condition and manual triggers, and also None when the
    kind itself was the problem.

    config carries the payload already normalised into a dict, whether it
    arrived as a mapping or as JSON text, so a caller that validates and then
    uses the config parses it exactly once. It is None when the payload could
    not be read at all. A plain dict rather than a mappingproxy, matching
    DueDirective.trigger_config, because callers round-trip these types
    through dataclasses.asdict().

    Deliberately has no return_statements field. This is a pure hook-side
    result handed to a runner, not an MCP tool result.
    """
    valid: bool
    trigger_type: Optional[str] = None
    kind: Optional[str] = None
    errors: Tuple[str, ...] = field(default_factory=tuple)
    config: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class NextFireResult:
    """
    What next_fire_time computed for one schedule.

    next_fire_time is a NAIVE LOCAL ISO timestamp, matching _now_iso and
    is_due. Handing back a tz-aware string would make is_due's
    `scheduled <= current` comparison raise TypeError against a naive `now`,
    and that exception would kill the runner's whole tick.

    A result type rather than an exception or a bare Optional, because the
    caller is an unattended runner. A non-conforming config, an unresolvable
    timezone, and a schedule with no occurrence inside the search window all
    arrive as success=False carrying an explanation.
    """
    success: bool
    next_fire_time: Optional[str] = None
    kind: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class ActionConfigValidation:
    """
    Outcome of checking one action_config against AIMFP's envelope for its
    action_type.

    caller_resolved marks the two action types AIMFP can only describe:
    function_call and command name a target that exists in the caller's own
    project, so a valid=True verdict on those means "the envelope is right,
    now check that it resolves" rather than "this will work".

    config carries the payload normalised to a dict, as
    TriggerConfigValidation does, so a caller parses the JSON once.
    """
    valid: bool
    action_type: Optional[str] = None
    errors: Tuple[str, ...] = field(default_factory=tuple)
    config: Optional[Dict[str, Any]] = None
    caller_resolved: bool = False
