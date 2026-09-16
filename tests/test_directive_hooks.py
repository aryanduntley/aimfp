"""
UC2 runtime hook contract tests (design: project note 34).

Covers:
- Availability     — Use Case 1 projects fail cleanly, never by exception
- Due detection    — injected `now`, so no clock manipulation is needed
- run_directive    — timing, stats folding, and exception containment
- Out-of-band      — record_directive_error counts an error, not an execution
- Rotation policy  — plan_rotation as pure logic, testable at any timescale
- Import weight    — the package never drags in the watchdog

Every test runs with the session cache unset and cwd pointed away from the
project, matching tests/test_embedding_hooks.py: these are the exact
conditions a cron-launched runner hits and MCP never does.
"""
import json
import os
import sqlite3
import tempfile

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.hooks.config import hooks_available, load_log_config, resolve_log_path
from aimfp.hooks.directives import (
    get_due_directives,
    is_due,
    record_directive_error,
    record_execution_end,
    record_execution_start,
    run_directive,
    set_next_scheduled_time,
    update_running_average,
)
from aimfp.hooks.logs import plan_rotation
from aimfp.hooks.results import LogConfig

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas",
    "user_directives.sql"
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def _isolated_process_state(tmp_path, monkeypatch):
    """Unset cache + cwd outside any project, restored afterwards."""
    clear_project_root_cache()
    monkeypatch.chdir(tmp_path)
    yield
    clear_project_root_cache()


def _bare_project() -> str:
    """A temp project with .aimfp-project/ but NO user_directives.db (UC1)."""
    root = tempfile.mkdtemp(prefix="aimfp_hooks_uc1_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    return root


def _uc2_project() -> str:
    """A temp project with user_directives.db on the current schema."""
    root = tempfile.mkdtemp(prefix="aimfp_hooks_uc2_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    conn = sqlite3.connect(
        os.path.join(root, ".aimfp-project", "user_directives.db"))
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()
    return root


def _add_directive(
    root: str,
    name: str = "turn_off_lights",
    trigger_type: str = "time",
    status: str = "active",
) -> int:
    """Insert one directive and return its id."""
    conn = sqlite3.connect(
        os.path.join(root, ".aimfp-project", "user_directives.db"))
    cursor = conn.execute(
        """
        INSERT INTO user_directives
            (name, source_file, source_format, raw_content, validated_content,
             trigger_type, trigger_config, action_type, action_config, status)
        VALUES (?, 'directives/home.yaml', 'yaml', 'turn off lights at 5pm',
                '{}', ?, '{"time": "17:00"}', 'api_call', '{"endpoint": "/off"}', ?)
        """,
        (name, trigger_type, status),
    )
    conn.commit()
    directive_id = cursor.lastrowid
    conn.close()
    return directive_id


def _stats(root: str, directive_id: int):
    """Read the directive_executions row, or None."""
    conn = sqlite3.connect(
        os.path.join(root, ".aimfp-project", "user_directives.db"))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM directive_executions WHERE directive_id = ?",
        (directive_id,),
    ).fetchone()
    conn.close()
    return row


def _error_log_lines(root: str) -> list:
    """Parse the error JSONL log into records."""
    path = os.path.join(root, ".aimfp-project", "logs", "errors", "errors.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _execution_log_lines(root: str) -> list:
    """Parse the execution JSONL log into records."""
    path = os.path.join(
        root, ".aimfp-project", "logs", "execution", "execution.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# ============================================================================
# Availability — Use Case 1 must fail cleanly
# ============================================================================

def test_hooks_unavailable_without_user_directives_db():
    root = _bare_project()
    assert hooks_available(root) is False


def test_hooks_available_in_uc2_project():
    root = _uc2_project()
    assert hooks_available(root) is True


def test_get_due_directives_returns_clean_failure_not_exception():
    """A UC1 project must be askable without guarding."""
    root = _bare_project()
    result = get_due_directives(project_root=root)
    assert result.success is False
    assert result.directives == ()
    assert "Use Case 2" in result.error


def test_record_hooks_fail_cleanly_without_database():
    root = _bare_project()

    token = record_execution_start(1, root)
    assert token.valid is False

    # An invalid token still resolves to one coherent failure at the end.
    result = record_execution_end(token, success=True)
    assert result.success is False
    assert result.error is not None

    assert record_directive_error(1, "x", "y", root).success is False
    assert set_next_scheduled_time(1, None, root).success is False


def test_load_log_config_defaults_when_database_absent():
    """Logging must never be the reason a run fails."""
    config = load_log_config(_bare_project())
    assert config == LogConfig()


# ============================================================================
# Due detection
# ============================================================================

def test_is_due_time_trigger_respects_schedule():
    assert is_due("time", "2026-09-15T10:00:00", "2026-09-15T11:00:00") is True
    assert is_due("time", "2026-09-15T12:00:00", "2026-09-15T11:00:00") is False


def test_is_due_never_scheduled_runs_immediately():
    """Withholding the first run would mean a directive that never starts."""
    assert is_due("time", None, "2026-09-15T11:00:00") is True


def test_is_due_caller_judged_triggers_always_returned():
    for trigger in ("event", "condition", "manual"):
        assert is_due(trigger, "2099-01-01T00:00:00", "2026-09-15T11:00:00") is True


def test_get_due_directives_filters_by_injected_now():
    root = _uc2_project()
    directive_id = _add_directive(root)
    set_next_scheduled_time(directive_id, "2026-09-15T12:00:00", root)

    before = get_due_directives(root, now="2026-09-15T11:00:00")
    assert before.success is True
    assert before.directives == ()
    assert before.total_count == 1

    after = get_due_directives(root, now="2026-09-15T13:00:00")
    assert len(after.directives) == 1
    assert after.directives[0].name == "turn_off_lights"


def test_get_due_directives_excludes_inactive():
    root = _uc2_project()
    _add_directive(root, name="paused_one", status="paused")
    result = get_due_directives(root, now="2026-09-15T13:00:00")
    assert result.success is True
    assert result.directives == ()


def test_due_directive_carries_dispatch_payload():
    """The runner must not need a second query to dispatch."""
    root = _uc2_project()
    _add_directive(root)
    directive = get_due_directives(root).directives[0]
    assert directive.trigger_config == {"time": "17:00"}
    assert directive.action_config == {"endpoint": "/off"}
    assert directive.action_type == "api_call"


# ============================================================================
# run_directive
# ============================================================================

def test_run_directive_records_success():
    root = _uc2_project()
    directive_id = _add_directive(root)
    calls = []

    result = run_directive(directive_id, lambda: calls.append(1), root)

    assert result.success is True
    assert result.action_succeeded is True
    assert result.duration_ms >= 0
    assert calls == [1]

    row = _stats(root, directive_id)
    assert row["total_executions"] == 1
    assert row["success_count"] == 1
    assert row["error_count"] == 0
    assert row["last_execution_time"] is not None


def test_run_directive_contains_handler_exception():
    """An escaping exception would kill the caller's scheduler thread."""
    root = _uc2_project()
    directive_id = _add_directive(root)

    def boom():
        raise ValueError("thermostat unreachable")

    result = run_directive(directive_id, boom, root)

    assert result.success is True          # AIMFP recorded it
    assert result.action_succeeded is False  # the automation did not work
    assert "thermostat unreachable" in result.action_error

    row = _stats(root, directive_id)
    assert row["total_executions"] == 1
    assert row["success_count"] == 0
    assert row["error_count"] == 1
    assert row["last_error_type"] == "handler_exception"
    assert "thermostat unreachable" in row["last_error_message"]


def test_failed_run_writes_exactly_one_error_line_with_traceback():
    root = _uc2_project()
    directive_id = _add_directive(root)

    def boom():
        raise RuntimeError("nope")

    run_directive(directive_id, boom, root)

    lines = _error_log_lines(root)
    assert len(lines) == 1
    assert lines[0]["error_type"] == "handler_exception"
    assert "RuntimeError" in lines[0]["traceback"]


def test_run_directive_writes_execution_log_record():
    root = _uc2_project()
    directive_id = _add_directive(root)
    run_directive(directive_id, lambda: None, root)

    lines = _execution_log_lines(root)
    assert len(lines) == 1
    assert lines[0]["directive_name"] == "turn_off_lights"
    assert lines[0]["outcome"] == "success"


def test_repeated_runs_keep_one_statistics_row():
    """The schema has no UNIQUE on directive_id; a blind INSERT would duplicate."""
    root = _uc2_project()
    directive_id = _add_directive(root)
    for _ in range(3):
        run_directive(directive_id, lambda: None, root)

    conn = sqlite3.connect(
        os.path.join(root, ".aimfp-project", "user_directives.db"))
    count = conn.execute(
        "SELECT COUNT(*) FROM directive_executions WHERE directive_id = ?",
        (directive_id,),
    ).fetchone()[0]
    conn.close()

    assert count == 1
    assert _stats(root, directive_id)["total_executions"] == 3


def test_running_average_tracks_across_runs():
    root = _uc2_project()
    directive_id = _add_directive(root)
    for _ in range(2):
        run_directive(directive_id, lambda: None, root)

    row = _stats(root, directive_id)
    assert row["avg_execution_time_ms"] is not None
    assert row["max_execution_time_ms"] >= row["avg_execution_time_ms"]


# ============================================================================
# Out-of-band errors
# ============================================================================

def test_record_directive_error_counts_error_not_execution():
    """A directive that failed to start has errors and zero executions."""
    root = _uc2_project()
    directive_id = _add_directive(root)

    result = record_directive_error(
        directive_id, "dependency_missing", "no such module", root)

    assert result.success is True
    row = _stats(root, directive_id)
    assert row["error_count"] == 1
    assert row["total_executions"] == 0
    assert row["last_error_type"] == "dependency_missing"


def test_set_next_scheduled_time_enables_overdue_detection():
    root = _uc2_project()
    directive_id = _add_directive(root)

    assert set_next_scheduled_time(
        directive_id, "2026-09-16T17:00:00", root).success is True
    assert _stats(root, directive_id)["next_scheduled_time"] == "2026-09-16T17:00:00"

    assert set_next_scheduled_time(directive_id, None, root).success is True
    assert _stats(root, directive_id)["next_scheduled_time"] is None


# ============================================================================
# Pure logic
# ============================================================================

def test_update_running_average_first_run_is_the_duration():
    assert update_running_average(None, 0, 120.0) == 120.0


def test_update_running_average_folds_incrementally():
    # Two runs at 100ms, then one at 400ms -> (100+100+400)/3
    after_two = update_running_average(100.0, 1, 100.0)
    assert after_two == 100.0
    assert update_running_average(after_two, 2, 400.0) == 200.0


def test_plan_rotation_size_policy():
    plan = plan_rotation(
        policy="size", base_name="errors.jsonl",
        current_size_bytes=11 * 1024 * 1024, current_mtime="2026-09-15T10:00:00",
        now="2026-09-15T11:00:00", max_size_mb=10, retention_days=90)
    assert plan.should_rotate is True
    assert plan.archive_name.startswith("errors-")
    assert plan.archive_name.endswith(".jsonl")


def test_plan_rotation_size_policy_under_threshold():
    plan = plan_rotation(
        policy="size", base_name="errors.jsonl",
        current_size_bytes=1024, current_mtime="2026-09-15T10:00:00",
        now="2026-09-15T11:00:00", max_size_mb=10, retention_days=90)
    assert plan.should_rotate is False


def test_plan_rotation_daily_policy_crosses_midnight():
    same_day = plan_rotation(
        policy="daily", base_name="execution.jsonl",
        current_size_bytes=10, current_mtime="2026-09-15T01:00:00",
        now="2026-09-15T23:00:00", max_size_mb=100, retention_days=30)
    assert same_day.should_rotate is False

    next_day = plan_rotation(
        policy="daily", base_name="execution.jsonl",
        current_size_bytes=10, current_mtime="2026-09-15T23:00:00",
        now="2026-09-16T01:00:00", max_size_mb=100, retention_days=30)
    assert next_day.should_rotate is True


def test_plan_rotation_empty_log_never_rotates():
    plan = plan_rotation(
        policy="daily", base_name="execution.jsonl",
        current_size_bytes=0, current_mtime=None,
        now="2026-09-16T01:00:00", max_size_mb=100, retention_days=30)
    assert plan.should_rotate is False


def test_plan_rotation_selects_expired_and_compressible_archives():
    archives = (
        ("/logs/execution-20260901-000000.jsonl", "2026-09-01T00:00:00"),  # old
        ("/logs/execution-20260914-000000.jsonl", "2026-09-14T00:00:00"),  # recent
        ("/logs/execution-20260801-000000.jsonl.gz", "2026-08-01T00:00:00"),
    )
    plan = plan_rotation(
        policy="daily", base_name="execution.jsonl",
        current_size_bytes=10, current_mtime="2026-09-15T10:00:00",
        now="2026-09-15T11:00:00", max_size_mb=100,
        retention_days=30, compress_after_days=7, existing_archives=archives)

    # Compression skips what is already gzipped
    assert "/logs/execution-20260901-000000.jsonl" in plan.compress_paths
    assert "/logs/execution-20260801-000000.jsonl.gz" not in plan.compress_paths
    assert "/logs/execution-20260914-000000.jsonl" not in plan.compress_paths

    # Deletion honors retention and does include gzipped archives
    assert "/logs/execution-20260801-000000.jsonl.gz" in plan.delete_paths
    assert "/logs/execution-20260914-000000.jsonl" not in plan.delete_paths


def test_plan_rotation_zero_threshold_disables_rule():
    """0 must disable the policy, not select every archive."""
    archives = (("/logs/execution-20200101-000000.jsonl", "2020-01-01T00:00:00"),)
    plan = plan_rotation(
        policy="daily", base_name="execution.jsonl",
        current_size_bytes=10, current_mtime="2026-09-15T10:00:00",
        now="2026-09-15T11:00:00", max_size_mb=100,
        retention_days=0, compress_after_days=0, existing_archives=archives)
    assert plan.compress_paths == ()
    assert plan.delete_paths == ()


def test_resolve_log_path_honors_absolute_override():
    assert resolve_log_path("/proj", ".aimfp-project/logs/") == \
        os.path.join("/proj", ".aimfp-project/logs/")
    assert resolve_log_path("/proj", "/var/log/aimfp/") == "/var/log/aimfp/"


# ============================================================================
# Import weight is part of the contract
# ============================================================================

def test_hooks_package_does_not_import_watchdog():
    """
    A headless automation environment must not pay for the observer.

    Pinned to the repo's src/ rather than the installed build: the MCP server
    always runs the last installed package (project note 5), so a bare
    subprocess would test the wrong code - or, before the first install of
    this package, fail to import it at all.
    """
    import subprocess
    import sys
    src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; import aimfp.hooks; "
         "sys.exit(1 if 'watchdog' in sys.modules else 0)"],
        capture_output=True,
        env={**os.environ, "PYTHONPATH": src},
    )
    assert result.returncode == 0, result.stderr.decode()
