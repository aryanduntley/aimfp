"""
Skip visibility and persisted condition latch (milestone 6).

Source: svamanas UC2 runner feedback, docs/svamanas/uc2-runner-feedback-2026-09-16.md.

Covers:
- record_directive_skip  - skip fields only, never execution/error counters;
                           one 'skipped' execution-log line; optional slot
- consecutive skips      - reset by a recorded execution, not by an error
- health                 - 'skipping' verdict, its precedence, and the
                           never_run detail when dispatch errors exist
- set_condition_state    - round-trips onto DueDirective
- schema 1.2 databases   - hooks degrade cleanly instead of breaking a runner
                           that upgraded aimfp before migrating
- migration              - a 1.2 database migrates to 1.3 with its rows intact
"""
import json
import os
import sqlite3
import subprocess
import tempfile

import pytest

from aimfp.database.connection import clear_project_root_cache, set_project_root
from aimfp.helpers.user_directives.monitoring import (
    HEALTH_DEGRADED,
    HEALTH_NEVER_RUN,
    HEALTH_OK,
    HEALTH_ORDER,
    HEALTH_OVERDUE,
    HEALTH_SKIPPING,
    assess_directive_health,
    check_directive_health,
    get_directive_execution_stats,
    summarize_health,
)
from aimfp.hooks.directives import (
    _build_statistics_update,
    get_due_directives,
    record_directive_error,
    record_directive_skip,
    run_directive,
    set_condition_state,
)
from aimfp.hooks.logs import build_skip_record

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCHEMA_PATH = os.path.join(
    REPO_ROOT, "src", "aimfp", "database", "schemas", "user_directives.sql")

NEW_COLUMNS = (
    "skip_count", "consecutive_skip_count", "last_skip_time", "last_skip_reason",
    "condition_latched", "last_condition_eval_time",
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def _isolated_process_state(tmp_path, monkeypatch):
    clear_project_root_cache()
    monkeypatch.chdir(tmp_path)
    yield
    clear_project_root_cache()


def _db_path(root: str) -> str:
    return os.path.join(root, ".aimfp-project", "user_directives.db")


def _uc2_project(schema_sql: str = None) -> str:
    root = tempfile.mkdtemp(prefix="aimfp_skip_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    if schema_sql is None:
        with open(SCHEMA_PATH) as f:
            schema_sql = f.read()
    conn = sqlite3.connect(_db_path(root))
    conn.executescript(schema_sql)
    conn.close()
    return root


def _legacy_project() -> str:
    """A project whose user_directives.db is still on schema 1.2."""
    root = _uc2_project()
    conn = sqlite3.connect(_db_path(root))
    for column in NEW_COLUMNS:
        conn.execute(f"ALTER TABLE directive_executions DROP COLUMN {column}")
    conn.execute("UPDATE schema_version SET version = '1.2' WHERE id = 1")
    conn.commit()
    conn.close()
    return root


def _add_directive(root: str, name: str = "d", trigger_type: str = "time") -> int:
    trigger_config = (
        '{"kind": "daily", "at": "17:00"}' if trigger_type == "time"
        else '{"expression": "cpu_percent > 90"}')
    conn = sqlite3.connect(_db_path(root))
    cur = conn.execute(
        """
        INSERT INTO user_directives
            (name, source_file, source_format, raw_content, validated_content,
             trigger_type, trigger_config, action_type, action_config, status)
        VALUES (?, 'd.yaml', 'yaml', 'raw', '{}', ?, ?, 'api_call',
                '{"endpoint": "/x"}', 'active')
        """,
        (name, trigger_type, trigger_config),
    )
    conn.commit()
    directive_id = cur.lastrowid
    conn.close()
    return directive_id


def _stats(root: str, directive_id: int) -> dict:
    conn = sqlite3.connect(_db_path(root))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM directive_executions WHERE directive_id = ?",
        (directive_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _execution_log(root: str) -> list:
    path = os.path.join(
        root, ".aimfp-project", "logs", "execution", "execution.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _row(**overrides):
    base = {
        'id': 1, 'name': 'd', 'status': 'active',
        'total_executions': 5, 'success_count': 5, 'error_count': 0,
        'last_execution_time': '2026-09-15T10:00:00',
        'next_scheduled_time': None,
        'last_error_type': None, 'last_error_message': None,
        'skip_count': 0, 'consecutive_skip_count': 0,
        'last_skip_time': None, 'last_skip_reason': None,
    }
    base.update(overrides)
    return base


# ============================================================================
# record_directive_skip
# ============================================================================

def test_skip_increments_only_skip_fields():
    root = _uc2_project()
    directive_id = _add_directive(root)

    result = record_directive_skip(directive_id, "missed_occurrence", root)

    assert result.success, result.error
    row = _stats(root, directive_id)
    assert row["skip_count"] == 1
    assert row["consecutive_skip_count"] == 1
    assert row["last_skip_reason"] == "missed_occurrence"
    assert row["last_skip_time"] is not None
    assert row["total_executions"] == 0
    assert row["success_count"] == 0
    assert row["error_count"] == 0
    assert row["last_execution_time"] is None


def test_skip_writes_one_skipped_execution_log_line():
    root = _uc2_project()
    directive_id = _add_directive(root, name="lights")

    record_directive_skip(
        directive_id, "missed_occurrence", root,
        next_scheduled_time="2026-09-17T17:00:00")

    lines = _execution_log(root)
    assert len(lines) == 1
    assert lines[0]["event"] == "skipped"
    assert lines[0]["outcome"] == "skipped"
    assert lines[0]["reason"] == "missed_occurrence"
    assert lines[0]["directive_name"] == "lights"
    assert lines[0]["next_scheduled_time"] == "2026-09-17T17:00:00"


def test_skip_can_declare_the_next_slot_in_the_same_write():
    root = _uc2_project()
    directive_id = _add_directive(root)

    record_directive_skip(
        directive_id, "missed_occurrence", root,
        next_scheduled_time="2026-09-17T17:00:00")

    assert _stats(root, directive_id)["next_scheduled_time"] == "2026-09-17T17:00:00"


def test_skip_without_slot_leaves_schedule_alone():
    """A skip never advances the schedule by itself."""
    root = _uc2_project()
    directive_id = _add_directive(root)
    conn = sqlite3.connect(_db_path(root))
    conn.execute(
        "INSERT INTO directive_executions (directive_id, next_scheduled_time) "
        "VALUES (?, '2026-09-16T17:00:00')", (directive_id,))
    conn.commit()
    conn.close()

    record_directive_skip(directive_id, "unreadable_schedule", root)

    assert _stats(root, directive_id)["next_scheduled_time"] == "2026-09-16T17:00:00"


@pytest.mark.parametrize("reason", ["", "   ", None, 3])
def test_skip_rejects_blank_reason(reason):
    root = _uc2_project()
    directive_id = _add_directive(root)
    result = record_directive_skip(directive_id, reason, root)
    assert result.success is False
    assert "reason" in result.error
    assert _stats(root, directive_id) is None


def test_skip_fails_cleanly_without_database():
    root = tempfile.mkdtemp(prefix="aimfp_skip_uc1_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    assert record_directive_skip(1, "missed_occurrence", root).success is False
    assert set_condition_state(1, True, root).success is False


def test_execution_resets_consecutive_skips_but_not_total():
    root = _uc2_project()
    directive_id = _add_directive(root)
    for _ in range(3):
        record_directive_skip(directive_id, "missed_occurrence", root)

    run_directive(directive_id, lambda: None, root)

    row = _stats(root, directive_id)
    assert row["skip_count"] == 3
    assert row["consecutive_skip_count"] == 0
    assert row["total_executions"] == 1


def test_failed_execution_also_resets_consecutive_skips():
    """A failed run is still a run: the host was up at the scheduled time."""
    root = _uc2_project()
    directive_id = _add_directive(root)
    record_directive_skip(directive_id, "missed_occurrence", root)

    def boom():
        raise RuntimeError("nope")

    run_directive(directive_id, boom, root)
    assert _stats(root, directive_id)["consecutive_skip_count"] == 0


def test_out_of_band_error_does_not_reset_consecutive_skips():
    """A denied dispatch is not a run."""
    root = _uc2_project()
    directive_id = _add_directive(root)
    record_directive_skip(directive_id, "missed_occurrence", root)

    record_directive_error(directive_id, "denied", "no grant", root)

    assert _stats(root, directive_id)["consecutive_skip_count"] == 1


def test_statistics_update_only_resets_when_asked():
    current = {'total_executions': 0, 'avg_execution_time_ms': None,
               'max_execution_time_ms': None}
    with_reset, _ = _build_statistics_update(
        current, 5.0, True, None, None, count_execution=True,
        reset_consecutive_skips=True)
    without, _ = _build_statistics_update(
        current, 5.0, True, None, None, count_execution=True)
    error_only, _ = _build_statistics_update(
        current, None, False, 'x', 'y', count_execution=False,
        reset_consecutive_skips=True)
    assert "consecutive_skip_count = 0" in with_reset
    assert "consecutive_skip_count = 0" not in without
    assert "consecutive_skip_count = 0" not in error_only


def test_skip_record_builder_shape():
    record = build_skip_record(4, None, "missed_occurrence", "2026-09-16T17:06:00")
    assert record == {
        'directive_id': 4, 'directive_name': None, 'event': 'skipped',
        'outcome': 'skipped', 'reason': 'missed_occurrence',
        'skipped_at': '2026-09-16T17:06:00',
    }


# ============================================================================
# Health - pure
# ============================================================================

def test_health_order_places_skipping_after_overdue_before_degraded():
    assert HEALTH_ORDER == (
        'error', 'overdue', 'skipping', 'degraded', 'never_run', 'ok')


def test_always_skipped_directive_that_never_ran_is_skipping():
    verdict = assess_directive_health((_row(
        total_executions=0, success_count=0, last_execution_time=None,
        skip_count=5, consecutive_skip_count=5,
        last_skip_time='2026-09-15T17:06:00',
        last_skip_reason='missed_occurrence'),), now='2026-09-16T09:00:00')[0]

    assert verdict.health == HEALTH_SKIPPING
    assert "skipped 5 occurrences and has never run" in verdict.detail
    assert "missed_occurrence" in verdict.detail
    assert verdict.skip_count == 5
    assert verdict.consecutive_skip_count == 5
    assert verdict.last_skip_reason == 'missed_occurrence'


def test_skipping_since_last_run():
    verdict = assess_directive_health((_row(
        skip_count=9, consecutive_skip_count=4,
        last_skip_time='2026-09-15T17:06:00',
        last_skip_reason='missed_occurrence'),), now='2026-09-16T09:00:00')[0]
    assert verdict.health == HEALTH_SKIPPING
    assert "skipped 4 occurrences since its last run" in verdict.detail


def test_skips_below_threshold_are_ok():
    verdict = assess_directive_health((_row(
        skip_count=2, consecutive_skip_count=2,
        last_skip_time='2026-09-15T17:06:00'),), now='2026-09-16T09:00:00')[0]
    assert verdict.health == HEALTH_OK


def test_skip_threshold_is_configurable():
    row = _row(consecutive_skip_count=2, last_skip_time='2026-09-15T17:06:00')
    assert assess_directive_health(
        (row,), now='2026-09-16T09:00:00', skip_threshold=2)[0].health == HEALTH_SKIPPING


def test_run_after_last_skip_is_not_skipping():
    """Guard for rows whose counter was not reset by AIMFP's own recording."""
    verdict = assess_directive_health((_row(
        consecutive_skip_count=5,
        last_skip_time='2026-09-15T09:00:00',
        last_execution_time='2026-09-15T10:00:00'),), now='2026-09-16T09:00:00')[0]
    assert verdict.health == HEALTH_OK


def test_overdue_outranks_skipping():
    verdict = assess_directive_health((_row(
        next_scheduled_time='2026-09-15T09:00:00',
        consecutive_skip_count=5, last_skip_time='2026-09-15T17:00:00'),),
        now='2026-09-16T09:00:00')[0]
    assert verdict.health == HEALTH_OVERDUE


def test_skipping_outranks_degraded():
    verdict = assess_directive_health((_row(
        total_executions=4, error_count=4,
        consecutive_skip_count=5, last_skip_time='2026-09-15T17:00:00'),),
        now='2026-09-16T09:00:00')[0]
    assert verdict.health == HEALTH_SKIPPING


def test_degraded_when_not_skipping():
    verdict = assess_directive_health((_row(
        total_executions=4, error_count=4),), now='2026-09-16T09:00:00')[0]
    assert verdict.health == HEALTH_DEGRADED


def test_always_denied_never_run_names_the_errors():
    verdict = assess_directive_health((_row(
        total_executions=0, success_count=0, error_count=7,
        last_execution_time=None,
        last_error_type='authorization_denied',
        last_error_message='no grant for run_bash'),), now='2026-09-16T09:00:00')[0]

    assert verdict.health == HEALTH_NEVER_RUN
    assert "7 dispatch errors" in verdict.detail
    assert "authorization_denied: no grant for run_bash" in verdict.detail
    assert "runner is running but refusing or failing" in verdict.detail
    assert "may not be deployed" not in verdict.detail


def test_never_run_without_errors_keeps_deployment_detail():
    verdict = assess_directive_health((_row(
        total_executions=0, success_count=0, last_execution_time=None),),
        now='2026-09-16T09:00:00')[0]
    assert verdict.health == HEALTH_NEVER_RUN
    assert "may not be deployed" in verdict.detail


def test_rows_without_skip_columns_classify_as_before():
    """A pre-1.3 row simply lacks the keys."""
    row = {k: v for k, v in _row().items() if 'skip' not in k}
    verdict = assess_directive_health((row,), now='2026-09-16T09:00:00')[0]
    assert verdict.health == HEALTH_OK
    assert verdict.skip_count == 0


def test_summary_counts_skipping_in_order():
    verdicts = assess_directive_health((
        _row(id=1),
        _row(id=2, consecutive_skip_count=3, last_skip_time='2026-09-15T17:00:00'),
    ), now='2026-09-16T09:00:00')
    assert list(summarize_health(verdicts)) == ['skipping', 'ok']


# ============================================================================
# Health and stats - end to end
# ============================================================================

def test_health_end_to_end_flags_skipping_runner():
    root = _uc2_project()
    directive_id = _add_directive(root, name="evening")
    for _ in range(3):
        record_directive_skip(directive_id, "missed_occurrence", root)

    result = check_directive_health(project_root=root)

    assert result.success
    assert result.needs_attention
    assert result.directives[0].health == HEALTH_SKIPPING
    assert result.summary == {'skipping': 1}


def test_stats_include_skip_and_condition_fields():
    root = _uc2_project()
    directive_id = _add_directive(root)
    record_directive_skip(directive_id, "missed_occurrence", root)

    row = get_directive_execution_stats(project_root=root).stats[0]
    for column in NEW_COLUMNS:
        assert column in row
    assert row["skip_count"] == 1


# ============================================================================
# set_condition_state
# ============================================================================

def test_condition_state_round_trips_onto_due_directive():
    root = _uc2_project()
    directive_id = _add_directive(root, trigger_type="condition")

    result = set_condition_state(
        directive_id, True, root, evaluated_at="2026-09-16T12:00:00")
    assert result.success, result.error

    due = get_due_directives(project_root=root).directives[0]
    assert due.condition_latched is True
    assert due.last_condition_eval_time == "2026-09-16T12:00:00"

    set_condition_state(directive_id, False, root, evaluated_at="2026-09-16T12:01:00")
    due = get_due_directives(project_root=root).directives[0]
    assert due.condition_latched is False
    assert due.last_condition_eval_time == "2026-09-16T12:01:00"


def test_condition_state_defaults_for_never_evaluated_directive():
    root = _uc2_project()
    _add_directive(root, trigger_type="condition")
    due = get_due_directives(project_root=root).directives[0]
    assert due.condition_latched is False
    assert due.last_condition_eval_time is None


def test_condition_state_defaults_eval_time_to_now():
    root = _uc2_project()
    directive_id = _add_directive(root, trigger_type="condition")
    set_condition_state(directive_id, True, root)
    assert _stats(root, directive_id)["last_condition_eval_time"] is not None


def test_condition_state_stores_aware_time_as_naive_local():
    root = _uc2_project()
    directive_id = _add_directive(root, trigger_type="condition")
    set_condition_state(directive_id, True, root, evaluated_at="2026-09-16T12:00:00+00:00")
    stored = _stats(root, directive_id)["last_condition_eval_time"]
    assert "+" not in stored and not stored.endswith("Z")


def test_condition_state_does_not_touch_counters():
    root = _uc2_project()
    directive_id = _add_directive(root, trigger_type="condition")
    set_condition_state(directive_id, True, root)
    row = _stats(root, directive_id)
    assert row["total_executions"] == 0
    assert row["error_count"] == 0
    assert row["skip_count"] == 0


@pytest.mark.parametrize("latched", [1, 0, "true", None])
def test_condition_state_requires_a_bool(latched):
    root = _uc2_project()
    directive_id = _add_directive(root, trigger_type="condition")
    result = set_condition_state(directive_id, latched, root)
    assert result.success is False
    assert "latched" in result.error


def test_condition_state_rejects_bad_timestamp():
    root = _uc2_project()
    directive_id = _add_directive(root, trigger_type="condition")
    result = set_condition_state(directive_id, True, root, evaluated_at="noon")
    assert result.success is False
    assert "evaluated_at" in result.error


def test_new_hooks_exported_from_package():
    import aimfp.hooks as hooks
    assert hooks.record_directive_skip is record_directive_skip
    assert hooks.set_condition_state is set_condition_state


# ============================================================================
# Schema 1.2 databases - an upgraded runner must keep working
# ============================================================================

def test_legacy_database_still_dispatches_and_records():
    root = _legacy_project()
    directive_id = _add_directive(root, trigger_type="condition")

    due = get_due_directives(project_root=root)
    assert due.success, due.error
    assert due.directives[0].condition_latched is False

    result = run_directive(directive_id, lambda: None, root)
    assert result.success, result.error
    assert _stats(root, directive_id)["total_executions"] == 1


def test_legacy_database_new_hooks_fail_cleanly_with_migration_hint():
    root = _legacy_project()
    directive_id = _add_directive(root)

    skip = record_directive_skip(directive_id, "missed_occurrence", root)
    latch = set_condition_state(directive_id, True, root)

    for result in (skip, latch):
        assert result.success is False
        assert "migrate_databases" in result.error
    assert _execution_log(root) == []


def test_legacy_database_health_and_stats_still_work():
    root = _legacy_project()
    _add_directive(root)
    assert check_directive_health(project_root=root).success
    assert get_directive_execution_stats(project_root=root).success


# ============================================================================
# Migration 1.2 -> 1.3
# ============================================================================

def _schema_at_head() -> str:
    """The user_directives schema as last committed, when it is still 1.2."""
    try:
        sql = subprocess.run(
            ["git", "show", "521064b:src/aimfp/database/schemas/user_directives.sql"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git history unavailable")
    return sql


def test_schema_1_2_database_migrates_with_rows_intact():
    from aimfp.helpers.orchestrators.migration import migrate_databases

    root = _uc2_project(_schema_at_head())
    directive_id = _add_directive(root)
    conn = sqlite3.connect(_db_path(root))
    conn.execute(
        "INSERT INTO directive_executions "
        "(directive_id, total_executions, success_count, next_scheduled_time) "
        "VALUES (?, 4, 4, '2026-09-17T17:00:00')", (directive_id,))
    conn.commit()
    conn.close()
    # migrate_databases also inspects project.db / user_preferences.db, which
    # this fixture does not have - they are skipped as absent.
    set_project_root(root)

    result = migrate_databases()

    assert result.success, result.error
    migrated = [m for m in result.data['migrated'] if m['db_name'] == 'user_directives']
    assert len(migrated) == 1
    entry = migrated[0]
    assert entry['old_version'] == '1.2'
    assert entry['new_version'] == '1.3'
    assert all(v['match'] for v in entry['verification'].values())

    conn = sqlite3.connect(entry['new_db_temp_path'])
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute(
        "SELECT * FROM directive_executions WHERE directive_id = ?",
        (directive_id,)).fetchone())
    conn.close()
    for path in (entry['new_db_temp_path'], entry['backup_temp_path']):
        os.remove(path)
    assert row['total_executions'] == 4
    assert row['next_scheduled_time'] == '2026-09-17T17:00:00'
    assert row['skip_count'] == 0
    assert row['consecutive_skip_count'] == 0
    assert row['condition_latched'] == 0
