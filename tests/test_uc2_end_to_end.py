"""
UC2 end-to-end: a generated runner, the hooks it records through, and the
tools the AI reads it back with.

This is the mitigation for why task 7 sat deferred since August: AIMFP is a
Use Case 1 project with no user_directives.db, so the UC2 pipeline cannot be
dogfooded the way the catalog tooling was. Every other test exercises one
layer. This one proves the layers compose.

The runner below is written the way user_directive_implement now instructs the
AI to write one - dispatch loop through get_due_directives/run_directive,
set_next_scheduled_time on every schedule, no custom logging. If those
instructions produce code that does not work, it shows up here, and the
instructions are what ship.

Every test runs with the session cache unset and cwd pointed away from the
project: the cron/systemd condition, which is the entire reason project_root is
threaded as a parameter rather than read from a process global.
"""
import os
import sqlite3
import tempfile

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.orchestrators.entry_points import _get_directive_health_safe
from aimfp.helpers.user_directives.monitoring import (
    HEALTH_NEVER_RUN,
    HEALTH_OVERDUE,
    check_directive_health,
    get_directive_execution_stats,
    get_recent_directive_errors,
)
from aimfp.hooks.directives import (
    get_due_directives,
    run_directive,
    set_next_scheduled_time,
)

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas",
    "user_directives.sql"
)


@pytest.fixture(autouse=True)
def _cron_conditions(tmp_path, monkeypatch):
    """Session cache unset, cwd outside the project - what cron actually gives."""
    clear_project_root_cache()
    monkeypatch.chdir(tmp_path)
    yield
    clear_project_root_cache()


# ============================================================================
# The fixture project and its "generated" runner
# ============================================================================

def _project() -> str:
    root = tempfile.mkdtemp(prefix="aimfp_uc2_e2e_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    conn = sqlite3.connect(
        os.path.join(root, ".aimfp-project", "user_directives.db"))
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()
    return root


def _directive(root: str, name: str, status: str = "active") -> int:
    conn = sqlite3.connect(
        os.path.join(root, ".aimfp-project", "user_directives.db"))
    cur = conn.execute(
        """
        INSERT INTO user_directives
            (name, source_file, source_format, raw_content, validated_content,
             trigger_type, trigger_config, action_type, action_config, status)
        VALUES (?, 'directives/home.yaml', 'yaml', 'raw', '{}',
                'time', '{}', 'api_call', '{}', ?)
        """,
        (name, status),
    )
    conn.commit()
    directive_id = cur.lastrowid
    conn.close()
    return directive_id


def generated_runner(root, handlers, now=None, next_run=None):
    """
    A runner shaped exactly as user_directive_implement instructs.

    Dispatch through get_due_directives + run_directive; declare the next run
    with set_next_scheduled_time; no custom logging or statistics, because
    run_directive already writes both.

    Returns the directive names it dispatched.
    """
    dispatched = []
    result = get_due_directives(project_root=root, now=now)
    if not result.success:
        return dispatched

    for directive in result.directives:
        handler = handlers.get(directive.name)
        if handler is None:
            continue
        run_directive(directive.directive_id, handler, root)
        dispatched.append(directive.name)
        if next_run:
            set_next_scheduled_time(directive.directive_id, next_run, root)
    return dispatched


# ============================================================================
# (1) A runner that never deploys
# ============================================================================

def test_undeployed_runner_surfaces_as_never_run():
    """Implementation completed, nothing ever ran it."""
    root = _project()
    _directive(root, 'backup_photos')

    health = check_directive_health(project_root=root)
    assert health.directives[0].health == HEALTH_NEVER_RUN
    assert health.needs_attention is True

    surfaced = _get_directive_health_safe(root)
    assert surfaced['summary'] == {'never_run': 1}


# ============================================================================
# (2) A runner that dies - the claim the whole design rests on
# ============================================================================

def test_dead_runner_detected_with_no_process_inspection():
    """
    The runner ran, declared its next run, then stopped existing. Nothing
    inspects a PID or a scheduler; the passed deadline IS the evidence.
    """
    root = _project()
    _directive(root, 'stove_watch')
    handlers = {'stove_watch': lambda: None}

    # One healthy cycle, declaring a next run that will come and go.
    dispatched = generated_runner(
        root, handlers, now='2026-09-15T08:00:00',
        next_run='2026-09-15T09:00:00')
    assert dispatched == ['stove_watch']

    # ...and then the runner dies. Nothing calls the hooks again.
    health = check_directive_health(project_root=root)
    assert health.directives[0].health == HEALTH_OVERDUE
    assert health.directives[0].overdue_seconds > 0

    surfaced = _get_directive_health_safe(root)
    assert surfaced['directives'][0]['name'] == 'stove_watch'
    assert 'runner may have died' in surfaced['directives'][0]['detail']


def test_runner_that_never_declares_a_schedule_cannot_be_called_overdue():
    """
    The documented corollary: without set_next_scheduled_time, silence is
    indistinguishable from health. A runner omitting it produces a directive
    that looks fine forever.
    """
    root = _project()
    _directive(root, 'silent')
    generated_runner(root, {'silent': lambda: None}, next_run=None)

    health = check_directive_health(project_root=root)
    assert health.directives[0].health != HEALTH_OVERDUE
    assert health.directives[0].overdue_seconds is None


# ============================================================================
# (3) A failing handler must not take the scheduler down
# ============================================================================

def test_failing_handler_does_not_stop_the_rest_of_the_cycle():
    """
    An escaping exception would kill the scheduler thread and silently stop
    every other directive. The one that matters most is the directive AFTER
    the failure.
    """
    root = _project()
    _directive(root, 'first_fails')
    _directive(root, 'second_must_still_run')
    ran = []

    def boom():
        raise ConnectionError("home assistant unreachable")

    dispatched = generated_runner(root, {
        'first_fails': boom,
        'second_must_still_run': lambda: ran.append(1),
    })

    assert dispatched == ['first_fails', 'second_must_still_run']
    assert ran == [1], "the cycle stopped at the failure"

    errors = get_recent_directive_errors(project_root=root)
    assert errors.total_count == 1
    assert errors.errors[0]['error_type'] == 'handler_exception'
    assert 'ConnectionError' in errors.errors[0]['traceback']

    stats = {s['name']: s for s in
             get_directive_execution_stats(project_root=root).stats}
    assert stats['first_fails']['error_rate'] == 1.0
    assert stats['second_must_still_run']['error_rate'] == 0.0


def test_repeated_failures_read_back_as_a_systemic_cause():
    """
    What the monitor directive tells the AI to look for: a repeated error_type
    across records means a dead dependency, not a flaky run.
    """
    root = _project()
    _directive(root, 'flaky')

    def boom():
        raise TimeoutError("gateway timeout")

    for _ in range(4):
        generated_runner(root, {'flaky': boom})

    errors = get_recent_directive_errors(directive_id=1, project_root=root)
    assert errors.total_count == 4
    assert {e['error_type'] for e in errors.errors} == {'handler_exception'}

    health = check_directive_health(project_root=root)
    assert health.directives[0].health == 'degraded'


# ============================================================================
# (4) A healthy runner across several cycles stays quiet
# ============================================================================

def test_healthy_runner_accumulates_statistics_and_stays_silent():
    """
    Five real cycles, each waking after the deadline it declared last time.

    Note the discipline this forces: a runner cannot simply loop, because its
    own declared next_scheduled_time gates the next dispatch. Advancing `now`
    past each deadline is what a real scheduler does by waiting.
    """
    root = _project()
    _directive(root, 'nightly')
    handlers = {'nightly': lambda: None}

    cycles = [
        ('2026-09-15T01:00:00', '2026-09-16T01:00:00'),
        ('2026-09-16T01:00:00', '2026-09-17T01:00:00'),
        ('2026-09-17T01:00:00', '2026-09-18T01:00:00'),
        ('2026-09-18T01:00:00', '2026-09-19T01:00:00'),
        # The last cycle schedules a run that has not come due yet, which is
        # the steady state of a healthy runner between firings.
        ('2026-09-19T01:00:00', '2099-01-01T00:00:00'),
    ]
    for woke_at, next_run in cycles:
        assert generated_runner(
            root, handlers, now=woke_at, next_run=next_run) == ['nightly']

    stats = get_directive_execution_stats(project_root=root).stats[0]
    assert stats['total_executions'] == 5
    assert stats['success_count'] == 5
    assert stats['error_count'] == 0
    assert stats['error_rate'] == 0.0
    assert stats['avg_execution_time_ms'] is not None
    assert stats['max_execution_time_ms'] >= stats['avg_execution_time_ms']

    # Nothing to say at session start.
    assert _get_directive_health_safe(root) is None


def test_due_filtering_drives_the_dispatch_loop():
    """A runner waking early must dispatch nothing."""
    root = _project()
    directive_id = _directive(root, 'scheduled')
    set_next_scheduled_time(directive_id, '2026-09-15T12:00:00', root)
    calls = []
    handlers = {'scheduled': lambda: calls.append(1)}

    assert generated_runner(root, handlers, now='2026-09-15T11:00:00') == []
    assert calls == []

    assert generated_runner(root, handlers, now='2026-09-15T13:00:00') == ['scheduled']
    assert calls == [1]


def test_paused_directive_is_never_dispatched():
    root = _project()
    _directive(root, 'paused_one', status='paused')
    calls = []
    assert generated_runner(root, {'paused_one': lambda: calls.append(1)}) == []
    assert calls == []


# ============================================================================
# (5) The cron condition, explicitly
# ============================================================================

def test_whole_loop_works_with_cwd_elsewhere_and_cache_unset():
    """
    The autouse fixture already guarantees this, but assert it directly: this
    is the entire reason project_root is a parameter rather than a process
    global, and a regression here would break every deployed runner.
    """
    import aimfp.database.connection as connection

    root = _project()
    _directive(root, 'from_cron')
    assert connection.get_cached_project_root.__module__  # cache API present

    dispatched = generated_runner(
        root, {'from_cron': lambda: None}, next_run='2099-01-01T00:00:00')

    assert dispatched == ['from_cron']
    assert os.getcwd() != root
    assert get_directive_execution_stats(
        project_root=root).stats[0]['total_executions'] == 1


def test_two_projects_in_one_process_stay_isolated():
    """
    One runner process could serve several projects. Statistics must not leak
    between them - the multi-tenant hazard Hook A was raised about.
    """
    first, second = _project(), _project()
    _directive(first, 'alpha')
    _directive(second, 'beta')

    generated_runner(first, {'alpha': lambda: None})
    generated_runner(first, {'alpha': lambda: None})
    generated_runner(second, {'beta': lambda: None})

    assert get_directive_execution_stats(
        project_root=first).stats[0]['total_executions'] == 2
    assert get_directive_execution_stats(
        project_root=second).stats[0]['total_executions'] == 1
