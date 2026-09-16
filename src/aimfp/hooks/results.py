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
