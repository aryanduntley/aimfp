"""
Schedule arithmetic and automatic advancement (src/aimfp/hooks/schedule.py).

Two things are under test. First, that AIMFP computes the same next fire time
every UC2 project would otherwise hand-roll - the drift this milestone
exists to prevent. Second, that a directive which has just run is no longer
due, which before this task depended entirely on the runner remembering to
say so.
"""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from aimfp.database.connection import clear_project_root_cache
from aimfp.hooks.directives import (
    get_due_directives,
    is_due,
    record_execution_end,
    record_execution_start,
    run_directive,
    set_next_scheduled_time,
)
from aimfp.hooks.schedule import next_fire_time

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas",
    "user_directives.sql"
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def _isolated_process_state(tmp_path, monkeypatch):
    """The conditions a cron-launched runner hits: no cache, cwd elsewhere."""
    clear_project_root_cache()
    monkeypatch.chdir(tmp_path)
    yield
    clear_project_root_cache()


def _uc2_project() -> str:
    root = tempfile.mkdtemp(prefix="aimfp_schedule_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    conn = sqlite3.connect(
        os.path.join(root, ".aimfp-project", "user_directives.db"))
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()
    return root


def _add_directive(root: str, trigger_type: str, trigger_config: str) -> int:
    conn = sqlite3.connect(
        os.path.join(root, ".aimfp-project", "user_directives.db"))
    cursor = conn.execute(
        """
        INSERT INTO user_directives
            (name, source_file, source_format, raw_content, validated_content,
             trigger_type, trigger_config, action_type, action_config, status)
        VALUES ('nightly', 'directives/home.yaml', 'yaml', 'run nightly',
                '{}', ?, ?, 'api_call', '{"endpoint": "/off"}', 'active')
        """,
        (trigger_type, trigger_config),
    )
    conn.commit()
    directive_id = cursor.lastrowid
    conn.close()
    return directive_id


def _scheduled(root: str, directive_id: int):
    conn = sqlite3.connect(
        os.path.join(root, ".aimfp-project", "user_directives.db"))
    row = conn.execute(
        "SELECT next_scheduled_time FROM directive_executions WHERE directive_id = ?",
        (directive_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else None


# ============================================================================
# Interval
# ============================================================================

def test_interval_adds_seconds():
    result = next_fire_time({"kind": "interval", "seconds": 900},
                            after="2026-09-15T18:00:00")
    assert result.success
    assert result.next_fire_time == "2026-09-15T18:15:00"
    assert result.kind == "interval"


def test_interval_crosses_midnight():
    result = next_fire_time({"kind": "interval", "seconds": 7200},
                            after="2026-09-15T23:30:00")
    assert result.next_fire_time == "2026-09-16T01:30:00"


# ============================================================================
# Daily, weekly, monthly
# ============================================================================

def test_daily_picks_tomorrow_when_today_has_passed():
    result = next_fire_time({"kind": "daily", "at": "17:00"},
                            after="2026-09-15T18:00:00")
    assert result.next_fire_time == "2026-09-16T17:00:00"


def test_daily_picks_today_when_the_time_is_still_ahead():
    result = next_fire_time({"kind": "daily", "at": "17:00"},
                            after="2026-09-15T09:00:00")
    assert result.next_fire_time == "2026-09-15T17:00:00"


def test_occurrence_exactly_at_the_anchor_is_skipped():
    """
    Strictly after, never equal. An occurrence at the anchor would leave a
    directive that just ran immediately due again - the fire-forever loop in
    miniature.
    """
    result = next_fire_time({"kind": "daily", "at": "17:00"},
                            after="2026-09-15T17:00:00")
    assert result.next_fire_time == "2026-09-16T17:00:00"


def test_weekly_picks_the_next_listed_weekday():
    # 2026-09-15 is a Tuesday.
    result = next_fire_time(
        {"kind": "weekly", "at": "17:00", "weekdays": ["mon", "fri"]},
        after="2026-09-15T18:00:00")
    assert result.next_fire_time == "2026-09-18T17:00:00"
    assert datetime.fromisoformat(result.next_fire_time).weekday() == 4


def test_weekly_wraps_to_the_following_week():
    # Saturday, with only Monday scheduled.
    result = next_fire_time(
        {"kind": "weekly", "at": "08:00", "weekdays": ["mon"]},
        after="2026-09-19T12:00:00")
    assert result.next_fire_time == "2026-09-21T08:00:00"


def test_monthly_picks_the_next_listed_day():
    result = next_fire_time(
        {"kind": "monthly", "at": "09:00", "days": [1, 15]},
        after="2026-09-15T10:00:00")
    assert result.next_fire_time == "2026-10-01T09:00:00"


def test_monthly_skips_a_month_that_lacks_the_day():
    """
    'The 31st' means the 31st. Following cron, February is skipped rather
    than clamped to the 28th, which would fire on a date nobody asked for.
    """
    result = next_fire_time({"kind": "monthly", "at": "09:00", "days": [31]},
                            after="2026-01-31T10:00:00")
    assert result.next_fire_time == "2026-03-31T09:00:00"


def test_monthly_day_29_finds_a_leap_february():
    result = next_fire_time({"kind": "monthly", "at": "09:00", "days": [29]},
                            after="2028-01-30T10:00:00")
    assert result.next_fire_time == "2028-02-29T09:00:00"


# ============================================================================
# Timezones and DST
# ============================================================================

def test_wall_clock_time_is_preserved_across_a_dst_boundary():
    """
    'Every day at 17:00 in New York' means 17:00 on both sides of the
    boundary, even though the two are 23 hours apart in absolute time. This
    is the arithmetic each UC2 project would otherwise hand-roll, and get
    subtly different.
    """
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    # US DST ends 2026-11-01. Anchor the evening before.
    before = datetime(2026, 10, 31, 18, 0, tzinfo=eastern)
    result = next_fire_time(
        {"kind": "daily", "at": "17:00", "timezone": "America/New_York"},
        after=before.isoformat())
    assert result.success, result.error

    fired = datetime.fromisoformat(result.next_fire_time).astimezone(eastern)
    assert (fired.hour, fired.minute) == (17, 0)
    assert fired.date() == datetime(2026, 11, 1).date()


def test_timezone_changes_the_answer():
    """Two zones must not produce the same instant for the same wall clock."""
    tokyo = next_fire_time(
        {"kind": "daily", "at": "09:00", "timezone": "Asia/Tokyo"},
        after="2026-09-15T00:00:00")
    london = next_fire_time(
        {"kind": "daily", "at": "09:00", "timezone": "Europe/London"},
        after="2026-09-15T00:00:00")
    assert tokyo.success and london.success
    assert tokyo.next_fire_time != london.next_fire_time


def test_unresolvable_timezone_is_a_result_not_an_exception():
    """
    The validator deliberately accepts any well-formed IANA name so its
    verdict is machine-independent. Resolution failure lands here instead,
    and must not raise: the caller is an unattended runner.
    """
    result = next_fire_time(
        {"kind": "daily", "at": "17:00", "timezone": "Mars/Olympus_Mons"},
        after="2026-09-15T18:00:00")
    assert not result.success
    assert result.next_fire_time is None
    assert "could not be resolved" in result.error


def test_returned_timestamp_is_naive():
    """
    An aware timestamp would make is_due's comparison against a naive now
    raise TypeError, killing the runner's tick rather than mis-scheduling
    one directive.
    """
    result = next_fire_time(
        {"kind": "daily", "at": "17:00", "timezone": "Asia/Tokyo"},
        after="2026-09-15T18:00:00")
    assert datetime.fromisoformat(result.next_fire_time).tzinfo is None


def test_aware_anchor_is_accepted():
    """A runner may already have stored an aware timestamp; still answer it."""
    result = next_fire_time({"kind": "interval", "seconds": 60},
                            after="2026-09-15T18:00:00+09:00")
    assert result.success


# ============================================================================
# Failure modes
# ============================================================================

def test_non_conforming_config_fails_with_the_validator_errors():
    result = next_fire_time({"time": "17:00"}, after="2026-09-15T18:00:00")
    assert not result.success
    assert "trigger_config" in result.error


def test_malformed_anchor_is_reported():
    result = next_fire_time({"kind": "daily", "at": "17:00"}, after="yesterday")
    assert not result.success
    assert "after" in result.error


def test_default_anchor_is_now():
    result = next_fire_time({"kind": "interval", "seconds": 3600})
    assert result.success
    assert datetime.fromisoformat(result.next_fire_time) > datetime.now()


# ============================================================================
# Automatic advancement
# ============================================================================

def test_running_a_time_directive_schedules_its_next_run():
    """
    The fire-forever bug, closed. is_due returns a time directive with no
    next_scheduled_time on every tick; before this, only the runner could
    stop that, and the package's own example did not.
    """
    root = _uc2_project()
    directive_id = _add_directive(
        root, "time", '{"kind": "interval", "seconds": 900}')

    assert get_due_directives(root).total_count == 1
    assert len(get_due_directives(root).directives) == 1

    run_directive(directive_id, lambda: None, root)

    scheduled = _scheduled(root, directive_id)
    assert scheduled is not None
    assert datetime.fromisoformat(scheduled) > datetime.now()
    assert get_due_directives(root).directives == ()


def test_advance_is_anchored_on_the_start_not_on_completion():
    """
    Anchoring on completion would push a fifteen-minute schedule later by
    the duration of every run, drifting a few seconds per cycle forever.
    """
    root = _uc2_project()
    directive_id = _add_directive(
        root, "time", '{"kind": "interval", "seconds": 3600}')

    token = record_execution_start(directive_id, root)
    record_execution_end(token, success=True)

    expected = datetime.fromisoformat(token.started_at).replace(microsecond=0)
    scheduled = datetime.fromisoformat(_scheduled(root, directive_id))
    assert abs((scheduled - expected).total_seconds() - 3600) <= 1


def test_a_run_longer_than_its_interval_is_late_once_not_due_forever():
    """
    When the anchored time has already passed, recompute from now. Otherwise
    a slow run leaves the directive permanently due and the runner spins.
    """
    root = _uc2_project()
    directive_id = _add_directive(
        root, "time", '{"kind": "interval", "seconds": 1}')

    token = record_execution_start(directive_id, root)
    import time
    time.sleep(1.2)
    record_execution_end(token, success=True)

    scheduled = datetime.fromisoformat(_scheduled(root, directive_id))
    assert scheduled > datetime.now()


def test_a_failed_run_still_advances_the_schedule():
    """A directive that errors must not therefore run on every tick."""
    root = _uc2_project()
    directive_id = _add_directive(
        root, "time", '{"kind": "interval", "seconds": 900}')

    def explode():
        raise RuntimeError("thermostat unreachable")

    result = run_directive(directive_id, explode, root)
    assert not result.action_succeeded
    assert _scheduled(root, directive_id) is not None


def test_explicit_set_next_scheduled_time_still_wins():
    """Auto-advance is a floor, not a lock: the runner keeps control."""
    root = _uc2_project()
    directive_id = _add_directive(
        root, "time", '{"kind": "interval", "seconds": 900}')

    run_directive(directive_id, lambda: None, root)
    set_next_scheduled_time(directive_id, "2030-01-01T00:00:00", root)
    assert _scheduled(root, directive_id) == "2030-01-01T00:00:00"


def test_a_non_conforming_config_never_fails_the_recording():
    """
    Insert-time enforcement means a non-conforming config should not reach
    the column, but a row written directly by SQL still can. Recording a run
    must never fail over a schedule problem - the caller is an unattended
    runner, and losing the execution record would be the worse outcome. Such
    a directive keeps the old behaviour: the runner declares its own next
    fire, or is reported overdue for never doing so.
    """
    root = _uc2_project()
    directive_id = _add_directive(root, "time", '{"time": "17:00"}')

    result = run_directive(directive_id, lambda: None, root)
    assert result.success
    assert _scheduled(root, directive_id) is None


@pytest.mark.parametrize("trigger_type", ["event", "condition", "manual"])
def test_non_time_triggers_are_never_auto_scheduled(trigger_type):
    """
    next_scheduled_time is a time trigger's field. Writing one for an event
    would make the monitoring tools report a live runner as overdue.
    """
    root = _uc2_project()
    configs = {
        "event": '{"event": "stove_on"}',
        "condition": '{"expression": "cpu > 90"}',
        "manual": '{}',
    }
    directive_id = _add_directive(root, trigger_type, configs[trigger_type])

    run_directive(directive_id, lambda: None, root)
    assert _scheduled(root, directive_id) is None


# ============================================================================
# is_due hardening
# ============================================================================

def test_is_due_survives_a_timezone_aware_scheduled_time():
    """
    set_next_scheduled_time accepts whatever string it is given, so an
    existing runner may have stored an aware timestamp. Comparing it to a
    naive now raises TypeError - inside the loop the runner drives every
    tick, which would take down every other directive with it.
    """
    assert is_due("time", "2020-01-01T00:00:00+00:00", "2026-09-15T18:00:00") is True
    assert is_due("time", "2099-01-01T00:00:00+00:00", "2026-09-15T18:00:00") is False


def test_is_due_still_treats_an_unscheduled_time_directive_as_due():
    """First run, unchanged: withholding it would mean a directive that never starts."""
    assert is_due("time", None, "2026-09-15T18:00:00") is True
