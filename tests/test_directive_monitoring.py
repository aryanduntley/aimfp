"""
AI-facing UC2 monitoring tool tests (design: project note 34).

The read side of the hook execution model. These tools are how the AI sees
what the user's automation did while no session was open.

Health classification is pure - assess_directive_health takes rows, a
timestamp and a threshold - so every verdict is asserted directly, without a
database or a clock. The 'overdue' case matters most: it is how a dead runner
is detected WITHOUT AIMFP ever knowing a process id, which is why the original
monitor workflow's PID and APScheduler checks were removed rather than built.
"""
import json
import os
import sqlite3
import tempfile

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.user_directives.monitoring import (
    HEALTH_DEGRADED,
    HEALTH_ERROR,
    HEALTH_NEVER_RUN,
    HEALTH_OK,
    HEALTH_OVERDUE,
    assess_directive_health,
    check_directive_health,
    compute_error_rate,
    get_directive_execution_stats,
    get_recent_directive_errors,
    summarize_health,
)
from aimfp.hooks.directives import (
    record_directive_error,
    run_directive,
    set_next_scheduled_time,
)

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas",
    "user_directives.sql"
)


@pytest.fixture(autouse=True)
def _isolated_process_state(tmp_path, monkeypatch):
    clear_project_root_cache()
    monkeypatch.chdir(tmp_path)
    yield
    clear_project_root_cache()


def _uc2_project() -> str:
    root = tempfile.mkdtemp(prefix="aimfp_mon_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    conn = sqlite3.connect(
        os.path.join(root, ".aimfp-project", "user_directives.db"))
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()
    return root


def _bare_project() -> str:
    root = tempfile.mkdtemp(prefix="aimfp_mon_uc1_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    return root


def _add_directive(root: str, name: str, status: str = "active") -> int:
    conn = sqlite3.connect(
        os.path.join(root, ".aimfp-project", "user_directives.db"))
    cur = conn.execute(
        """
        INSERT INTO user_directives
            (name, source_file, source_format, raw_content, validated_content,
             trigger_type, trigger_config, action_type, action_config, status)
        VALUES (?, 'd.yaml', 'yaml', 'raw', '{}', 'time', '{}', 'api_call', '{}', ?)
        """,
        (name, status),
    )
    conn.commit()
    directive_id = cur.lastrowid
    conn.close()
    return directive_id


def _row(**overrides):
    base = {
        'id': 1, 'name': 'd', 'status': 'active',
        'total_executions': 5, 'success_count': 5, 'error_count': 0,
        'last_execution_time': '2026-09-15T10:00:00',
        'next_scheduled_time': None,
        'last_error_type': None, 'last_error_message': None,
    }
    base.update(overrides)
    return base


# ============================================================================
# Pure: error rate
# ============================================================================

def test_error_rate_basic():
    assert compute_error_rate(10, 3) == pytest.approx(0.3)


def test_error_rate_no_attempts_is_zero():
    assert compute_error_rate(0, 0) == 0.0


def test_error_rate_counts_out_of_band_errors():
    """
    record_directive_error records an error WITHOUT an execution. Dividing by
    executions alone would report 0% for a directive that never once ran.
    """
    assert compute_error_rate(0, 10) == 1.0
    assert compute_error_rate(4, 6) == pytest.approx(1.0)


# ============================================================================
# Pure: health classification
# ============================================================================

def test_healthy_directive_is_ok():
    verdicts = assess_directive_health((_row(),), now='2026-09-15T11:00:00')
    assert verdicts[0].health == HEALTH_OK


def test_overdue_when_next_scheduled_time_has_passed():
    """The dead-runner signal, with no process inspection involved."""
    verdicts = assess_directive_health(
        (_row(next_scheduled_time='2026-09-15T09:00:00'),),
        now='2026-09-15T11:00:00')
    assert verdicts[0].health == HEALTH_OVERDUE
    assert verdicts[0].overdue_seconds == pytest.approx(7200)
    assert '2h' in verdicts[0].detail


def test_not_overdue_when_schedule_is_in_the_future():
    verdicts = assess_directive_health(
        (_row(next_scheduled_time='2026-09-15T23:00:00'),),
        now='2026-09-15T11:00:00')
    assert verdicts[0].health == HEALTH_OK
    assert verdicts[0].overdue_seconds < 0


def test_no_schedule_can_never_be_overdue():
    """Absence of a schedule is not lateness - the question cannot be asked."""
    verdicts = assess_directive_health(
        (_row(next_scheduled_time=None),), now='2026-09-15T11:00:00')
    assert verdicts[0].overdue_seconds is None
    assert verdicts[0].health == HEALTH_OK


def test_degraded_at_threshold():
    verdicts = assess_directive_health(
        (_row(total_executions=10, error_count=5),), now='2026-09-15T11:00:00')
    assert verdicts[0].health == HEALTH_DEGRADED

    below = assess_directive_health(
        (_row(total_executions=10, error_count=4),), now='2026-09-15T11:00:00')
    assert below[0].health == HEALTH_OK


def test_threshold_is_configurable():
    rows = (_row(total_executions=10, error_count=2),)
    assert assess_directive_health(rows, '2026-09-15T11:00:00')[0].health == HEALTH_OK
    strict = assess_directive_health(rows, '2026-09-15T11:00:00', 0.2)
    assert strict[0].health == HEALTH_DEGRADED


def test_never_run_active_directive():
    verdicts = assess_directive_health(
        (_row(total_executions=0, error_count=0, last_execution_time=None),),
        now='2026-09-15T11:00:00')
    assert verdicts[0].health == HEALTH_NEVER_RUN
    assert 'never executed' in verdicts[0].detail


def test_paused_directive_that_never_ran_is_not_flagged():
    """never_run applies to ACTIVE directives; a paused one is idle by design."""
    verdicts = assess_directive_health(
        (_row(status='paused', total_executions=0),), now='2026-09-15T11:00:00')
    assert verdicts[0].health == HEALTH_OK


def test_error_status_outranks_everything():
    """Already in error state is not better news for being on schedule."""
    verdicts = assess_directive_health(
        (_row(status='error', last_error_message='thermostat unreachable',
              next_scheduled_time='2026-09-15T09:00:00',
              total_executions=10, error_count=9),),
        now='2026-09-15T11:00:00')
    assert verdicts[0].health == HEALTH_ERROR
    assert 'thermostat unreachable' in verdicts[0].detail


def test_overdue_outranks_degraded():
    verdicts = assess_directive_health(
        (_row(total_executions=10, error_count=9,
              next_scheduled_time='2026-09-15T09:00:00'),),
        now='2026-09-15T11:00:00')
    assert verdicts[0].health == HEALTH_OVERDUE


def test_unparseable_timestamp_does_not_crash_classification():
    verdicts = assess_directive_health(
        (_row(next_scheduled_time='not-a-timestamp'),), now='2026-09-15T11:00:00')
    assert verdicts[0].overdue_seconds is None
    assert verdicts[0].health == HEALTH_OK


# ============================================================================
# Pure: summary
# ============================================================================

def test_summary_counts_by_state_and_omits_zeros():
    verdicts = assess_directive_health(
        (
            _row(id=1),
            _row(id=2, status='error'),
            _row(id=3, next_scheduled_time='2026-09-15T09:00:00'),
        ),
        now='2026-09-15T11:00:00')
    summary = summarize_health(verdicts)
    assert summary == {'error': 1, 'overdue': 1, 'ok': 1}
    assert 'degraded' not in summary


def test_summary_of_nothing_is_empty():
    assert summarize_health(()) == {}


# ============================================================================
# Tools against a real fixture project
# ============================================================================

def test_stats_reflect_recorded_runs():
    root = _uc2_project()
    directive_id = _add_directive(root, 'nightly_backup')
    run_directive(directive_id, lambda: None, root)
    run_directive(directive_id, lambda: None, root)

    result = get_directive_execution_stats(project_root=root)
    assert result.success is True
    assert result.total_count == 1
    row = result.stats[0]
    assert row['name'] == 'nightly_backup'
    assert row['total_executions'] == 2
    assert row['error_rate'] == 0.0


def test_stats_filter_to_one_directive():
    root = _uc2_project()
    first = _add_directive(root, 'a')
    _add_directive(root, 'b')

    all_rows = get_directive_execution_stats(project_root=root)
    assert all_rows.total_count == 2

    one = get_directive_execution_stats(directive_id=first, project_root=root)
    assert one.total_count == 1
    assert one.stats[0]['name'] == 'a'


def test_stats_include_directives_that_never_ran():
    """LEFT JOIN: a never-run directive must still appear, with zero counts."""
    root = _uc2_project()
    _add_directive(root, 'never_deployed')
    result = get_directive_execution_stats(project_root=root)
    assert result.total_count == 1
    assert result.stats[0]['total_executions'] == 0


def test_recent_errors_returns_newest_first():
    root = _uc2_project()
    directive_id = _add_directive(root, 'flaky')
    record_directive_error(directive_id, 'first_error', 'one', root)
    record_directive_error(directive_id, 'second_error', 'two', root)

    result = get_recent_directive_errors(project_root=root)
    assert result.success is True
    assert result.total_count == 2
    assert result.errors[0]['error_type'] == 'second_error'


def test_recent_errors_respects_limit_and_filter():
    root = _uc2_project()
    a = _add_directive(root, 'a')
    b = _add_directive(root, 'b')
    record_directive_error(a, 'ea', 'x', root)
    record_directive_error(b, 'eb', 'y', root)

    only_b = get_recent_directive_errors(directive_id=b, project_root=root)
    assert only_b.total_count == 1
    assert only_b.errors[0]['error_type'] == 'eb'

    capped = get_recent_directive_errors(limit=1, project_root=root)
    assert len(capped.errors) == 1
    assert capped.total_count == 2


def test_recent_errors_captures_handler_traceback():
    root = _uc2_project()
    directive_id = _add_directive(root, 'raises')

    def boom():
        raise ValueError("boom")

    run_directive(directive_id, boom, root)

    result = get_recent_directive_errors(project_root=root)
    assert result.errors[0]['error_type'] == 'handler_exception'
    assert 'ValueError' in result.errors[0]['traceback']


def test_recent_errors_tolerates_truncated_final_line():
    """Normal when a run was interrupted mid-write: count it, do not hide it."""
    root = _uc2_project()
    directive_id = _add_directive(root, 'd')
    record_directive_error(directive_id, 'good', 'parsed fine', root)

    log = os.path.join(root, ".aimfp-project", "logs", "errors", "errors.jsonl")
    with open(log, 'a') as f:
        f.write('{"directive_id": 1, "error_type": "trunca')

    result = get_recent_directive_errors(project_root=root)
    assert result.success is True
    assert result.total_count == 1
    assert result.malformed_lines == 1


def test_recent_errors_missing_log_is_an_empty_read():
    """No error log means nothing has failed - the good case, not an error."""
    root = _uc2_project()
    _add_directive(root, 'clean')
    result = get_recent_directive_errors(project_root=root)
    assert result.success is True
    assert result.errors == ()


def test_recent_errors_rejects_bad_limit():
    root = _uc2_project()
    assert get_recent_directive_errors(limit=0, project_root=root).success is False


def test_health_end_to_end_flags_overdue_runner():
    root = _uc2_project()
    directive_id = _add_directive(root, 'stove_watch')
    set_next_scheduled_time(directive_id, '2020-01-01T00:00:00', root)

    result = check_directive_health(project_root=root)
    assert result.success is True
    assert result.needs_attention is True
    assert result.summary == {'overdue': 1}
    assert result.directives[0].name == 'stove_watch'


def test_health_is_quiet_when_everything_is_fine():
    root = _uc2_project()
    directive_id = _add_directive(root, 'fine')
    run_directive(directive_id, lambda: None, root)
    set_next_scheduled_time(directive_id, '2099-01-01T00:00:00', root)

    result = check_directive_health(project_root=root)
    assert result.needs_attention is False
    assert result.summary == {'ok': 1}


# ============================================================================
# Use Case 1 projects fail cleanly
# ============================================================================

def test_all_tools_fail_cleanly_without_user_directives_db():
    root = _bare_project()
    for result in (
        get_directive_execution_stats(project_root=root),
        get_recent_directive_errors(project_root=root),
        check_directive_health(project_root=root),
    ):
        assert result.success is False
        assert 'Use Case 2' in result.error


# ============================================================================
# The tools are tools, and the hooks they read are not
# ============================================================================

def test_monitoring_tools_are_dispatchable():
    from aimfp.mcp_server.registry import TOOL_REGISTRY
    for name in ('get_directive_execution_stats',
                 'get_recent_directive_errors',
                 'check_directive_health'):
        assert name in TOOL_REGISTRY


# ============================================================================
# Session-start surfacing (aimfp_run / build_status_bundle)
# ============================================================================

def test_case_2_context_is_none_for_use_case_1():
    """A Case 1 project must never grow a Case 2 section."""
    from aimfp.helpers.orchestrators.entry_points import _build_case_2_context
    assert _build_case_2_context({'user_directives_status': None}) is None


def test_health_key_absent_when_everything_is_healthy():
    """
    A section that appears every session saying everything is fine trains the
    reader to skip it - and then it is not read on the session that matters.
    """
    from aimfp.helpers.orchestrators.entry_points import _build_case_2_context
    context = _build_case_2_context({'user_directives_status': 'active'}, None)
    assert context is not None
    assert 'health' not in context


def test_health_key_present_when_attention_needed():
    from aimfp.helpers.orchestrators.entry_points import _build_case_2_context
    health = {
        'summary': {'overdue': 1},
        'needs_attention': True,
        'directives': [{'name': 'backup_photos', 'health': 'overdue',
                        'detail': 'overdue by 14h'}],
    }
    context = _build_case_2_context({'user_directives_status': 'active'}, health)
    assert context['health']['summary'] == {'overdue': 1}


def test_health_read_degrades_to_none_without_database():
    """Session start must never fail because of a monitoring read."""
    from aimfp.helpers.orchestrators.entry_points import _get_directive_health_safe
    assert _get_directive_health_safe(_bare_project()) is None


def test_health_read_is_none_when_all_directives_are_healthy():
    from aimfp.helpers.orchestrators.entry_points import _get_directive_health_safe
    root = _uc2_project()
    directive_id = _add_directive(root, 'fine')
    run_directive(directive_id, lambda: None, root)
    set_next_scheduled_time(directive_id, '2099-01-01T00:00:00', root)
    assert _get_directive_health_safe(root) is None


def test_health_read_carries_only_directives_needing_attention():
    from aimfp.helpers.orchestrators.entry_points import _get_directive_health_safe
    root = _uc2_project()
    sick = _add_directive(root, 'stove_watch')
    set_next_scheduled_time(sick, '2020-01-01T00:00:00', root)
    healthy = _add_directive(root, 'fine')
    run_directive(healthy, lambda: None, root)
    set_next_scheduled_time(healthy, '2099-01-01T00:00:00', root)

    payload = _get_directive_health_safe(root)
    assert payload['needs_attention'] is True
    names = [d['name'] for d in payload['directives']]
    assert names == ['stove_watch']
    assert payload['summary'] == {'overdue': 1, 'ok': 1}
