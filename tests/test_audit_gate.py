"""audit_tracking, completion gate, aimfp_end audit, watchdog per-file audit, CLI (milestone 8, task 61)."""
import json
import os
import sqlite3
import time

import pytest

from aimfp.database.connection import clear_project_root_cache, set_project_root
from aimfp.helpers.audit.cli import EXIT_BLOCKING, EXIT_CLEAN, EXIT_ERROR, main_audit
from aimfp.helpers.audit.gate import gate_refusal, GateVerdict
from aimfp.helpers.audit.tracking_audit import audit_tracking, merge_findings, normalize_include, session_audit
from aimfp.helpers.catalog.reconcile import reconcile_paths
from aimfp.helpers.orchestrators.entry_points import aimfp_init
from aimfp.helpers.orchestrators.state import batch_update_progress, update_project_state
from aimfp.helpers.project.task_files import link_files_to_task
from aimfp.helpers.project.tasks import add_milestone, add_task, update_task
from aimfp.helpers.project.subtasks_sidequests import (
    add_sidequest, add_subtask, update_sidequest, update_subtask,
)
from aimfp.helpers.project.themes_flows_2 import add_completion_path
from aimfp.watchdog.config import REMINDER_AUDIT_DRIFT
from aimfp.watchdog.reminders import _effect_read_reminders
from aimfp.watchdog.watcher import _effect_process_file_event
from aimfp.wrappers.filesystem_observer import EVENT_MODIFIED, FileEvent


@pytest.fixture
def root(tmp_path):
    clear_project_root_cache()
    assert aimfp_init(str(tmp_path), init_git=False).success
    (tmp_path / "src").mkdir()
    set_project_root(str(tmp_path))
    yield str(tmp_path)
    clear_project_root_cache()


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _q(root, sql, params=()):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


LIB = ('def add(a, b) -> int:\n    """Add."""\n    return a + b\n\n\n'
       'def scale(x) -> int:\n    """Double."""\n    return x * 2\n')
APP = 'from .lib import add\n\n\ndef run(x) -> int:\n    """Run."""\n    return add(x, 1)\n'
DRIFTED = 'from .lib import scale\n\n\ndef run(x) -> int:\n    """Run."""\n    return scale(x)\n'


@pytest.fixture
def work(root):
    """A project with lib/app tracked, and a task, subtask and sidequest linked to app.py."""
    _write(root, "src/lib.py", LIB)
    _write(root, "src/app.py", APP)
    reconcile_paths(["src/lib.py", "src/app.py"], project_root=root)
    path_id = add_completion_path("p", project_root=root).id
    milestone_id = add_milestone(path_id, "m", project_root=root).id
    task_id = add_task(milestone_id, "t", project_root=root).id
    subtask_id = add_subtask(task_id, "s", project_root=root).id
    sidequest_id = add_sidequest(task_id, "q", project_root=root).id
    app_id = _q(root, "SELECT id FROM files WHERE path = 'src/app.py'")[0][0]
    for kind, item in (("task", task_id), ("subtask", subtask_id), ("sidequest", sidequest_id)):
        assert link_files_to_task(item, [app_id], task_type=kind, project_root=root).success
    return {"root": root, "task": task_id, "subtask": subtask_id, "sidequest": sidequest_id}


def _status(root, table, item_id):
    return _q(root, f"SELECT status FROM {table} WHERE id = ?", (item_id,))[0][0]


# ============================================================================
# audit_tracking
# ============================================================================

def test_audit_tracking_combines_every_audit(work):
    root = work["root"]
    _write(root, "src/app.py", DRIFTED + "\n\ndef extra():\n    return 1\n")
    r = audit_tracking(paths=["src/app.py"], project_root=root)
    assert {"missing_interactions", "stale_interactions", "missing_functions"} <= set(r.counts)
    assert r.scope["audits"] == ["functions", "interactions", "descriptions", "structure"]
    report = json.load(open(os.path.join(root, r.report_path)))
    assert report["reconcile_paths"] == ["src/app.py"]
    only = audit_tracking(paths=["src/app.py"], include=["interactions"], project_root=root)
    assert "missing_functions" not in only.counts and "stale_interactions" in only.counts
    assert "Unknown audit" in audit_tracking(include=["x"], project_root=root).error


def test_normalize_include_and_merge():
    assert normalize_include(None)[0] == ("functions", "interactions", "descriptions", "structure")
    assert normalize_include(["Structure", "functions"]) == (("functions", "structure"), ())
    merged = merge_findings({"unchecked_files": ({"path": "a", "reason": "x"},)},
                            {"unchecked_files": ({"path": "a", "reason": "y"}, {"path": "b"})})
    assert [u["path"] for u in merged["unchecked_files"]] == ["a", "b"]


# ============================================================================
# Completion gate
# ============================================================================

def test_clean_task_completes(work):
    r = update_task(work["task"], status="completed", project_root=work["root"])
    assert r.success and r.audit is None
    assert _status(work["root"], "tasks", work["task"]) == "completed"


def test_drift_refuses_completion_with_report(work):
    root = work["root"]
    _write(root, "src/app.py", DRIFTED)
    r = update_task(work["task"], status="completed", project_root=root)
    assert not r.success
    assert "missing_interactions=1" in r.error and "stale_interactions=1" in r.error
    report_path = r.error.split("Full report: ")[1].split(". ")[0]
    assert os.path.isfile(os.path.join(root, report_path))
    assert _status(root, "tasks", work["task"]) != "completed"


def test_override_completes_and_records_note(work):
    root = work["root"]
    _write(root, "src/app.py", DRIFTED)
    r = update_task(work["task"], status="completed", audit_override_reason="follow-up task 99",
                    project_root=root)
    assert r.success and r.audit["overridden"] and r.audit["blocking"]["stale_interactions"] == 1
    (content, severity), = _q(root, "SELECT content, severity FROM notes WHERE note_type = 'warning' "
                                    "AND reference_table = 'tasks' AND reference_id = ?", (work["task"],))
    assert "AUDIT GATE OVERRIDDEN" in content and "follow-up task 99" in content and severity == "warning"


def test_fixing_the_drift_unblocks(work):
    root = work["root"]
    _write(root, "src/app.py", DRIFTED)
    reconcile_paths(["src/app.py"], project_root=root)        # adds run -> scale; run -> add stays stale
    assert not update_task(work["task"], status="completed", project_root=root).success
    from aimfp.helpers.audit.apply_fixes import apply_audit_fixes
    audit = audit_tracking(task_id=work["task"], project_root=root)
    apply_audit_fixes("dead call", report=audit.report_path, apply=["stale_interactions"], project_root=root)
    assert update_task(work["task"], status="completed", project_root=root).success


def test_warnings_never_block(work):
    root = work["root"]
    _write(root, "src/app.py", 'OPS = {}\n\n\ndef run(x) -> int:\n    """Run."""\n    return OPS["add"](x, 1)\n')
    reconcile_paths(["src/app.py"], project_root=root)        # run -> lib.add: named in a string, unconfirmed
    r = update_task(work["task"], status="completed", project_root=root)
    assert r.success and not r.audit["overridden"]
    assert r.audit["warnings"] == {"stale_descriptions": 1, "unconfirmed_interactions": 1}


def test_no_linked_files_and_non_completion_changes_pass(root):
    path_id = add_completion_path("p", project_root=root).id
    task_id = add_task(add_milestone(path_id, "m", project_root=root).id, "t", project_root=root).id
    assert update_task(task_id, status="completed", project_root=root).success
    assert update_task(task_id, description="later edit", project_root=root).success


def test_already_completed_is_not_re_audited(work):
    root = work["root"]
    assert update_task(work["task"], status="completed", project_root=root).success
    _write(root, "src/app.py", DRIFTED)
    assert update_task(work["task"], status="completed", description="x", project_root=root).success


def test_subtask_and_sidequest_are_gated(work):
    root = work["root"]
    _write(root, "src/app.py", DRIFTED)
    assert not update_subtask(work["subtask"], status="completed", project_root=root).success
    assert not update_sidequest(work["sidequest"], status="completed", project_root=root).success
    assert update_subtask(work["subtask"], status="completed", audit_override_reason="ok",
                          project_root=root).success
    assert update_sidequest(work["sidequest"], status="completed", audit_override_reason="ok",
                            project_root=root).success


def test_update_project_state_is_gated(work):
    root = work["root"]
    _write(root, "src/app.py", DRIFTED)
    refused = update_project_state("complete_task", "task", work["task"])
    assert not refused.success and "blocking drift" in refused.error
    done = update_project_state("complete_task", "task", work["task"], data={"audit_override_reason": "why"})
    assert done.success and done.data["audit"]["overridden"]
    assert _status(root, "tasks", work["task"]) == "completed"


def test_batch_update_progress_is_gated_inside_its_transaction(work):
    root = work["root"]
    _write(root, "src/app.py", DRIFTED)
    refused = batch_update_progress([{"target_type": "subtask", "target_id": work["subtask"], "action": "start_subtask"},
                                     {"target_type": "task", "target_id": work["task"], "action": "complete_task"}])
    assert not refused.success and "blocking drift" in refused.error
    assert _status(root, "subtasks", work["subtask"]) == "pending"          # whole batch rolled back
    done = batch_update_progress([{"target_type": "task", "target_id": work["task"], "action": "complete_task",
                                   "data": {"audit_override_reason": "batch override"}}])
    assert done.success and _status(root, "tasks", work["task"]) == "completed"
    assert _q(root, "SELECT COUNT(*) FROM notes WHERE content LIKE '%batch override%'") == [(1,)]


def test_gate_refusal_text():
    text = gate_refusal("subtasks", 4, GateVerdict(blocked=True, blocking={"b": 2, "a": 1},
                                                   report_path="r.json", files_audited=3))
    assert text.startswith("Cannot complete subtask 4: the tracking audit of its 3 linked file(s)")
    assert "a=1, b=2" in text and "audit_override_reason" in text


# ============================================================================
# aimfp_end session audit
# ============================================================================

def test_session_audit_falls_back_to_focus_without_git(work):
    root = work["root"]
    assert session_audit(root)["files_audited"] == 0                        # not git, nothing in progress
    update_task(work["task"], status="in_progress", project_root=root)
    _write(root, "src/app.py", DRIFTED)
    audit = session_audit(root)
    assert audit["scope"].startswith("in-progress task") and audit["files_audited"] == 1
    assert "stale_interactions" in audit["blocking"] and os.path.isfile(os.path.join(root, audit["report_path"]))


def test_aimfp_end_returns_audit(work):
    from aimfp.helpers.orchestrators.entry_points import aimfp_end
    root = work["root"]
    update_task(work["task"], status="in_progress", project_root=root)
    _write(root, "src/app.py", DRIFTED)
    r = aimfp_end()
    assert r.success and "stale_interactions" in r.data["audit"]["blocking"]


# ============================================================================
# Watchdog per-file audit
# ============================================================================

def _event(root, rel):
    return FileEvent(event_type=EVENT_MODIFIED, src_path=os.path.join(root, rel), is_directory=False,
                     timestamp=time.time())


def test_watchdog_audit_drift_reminder_replaced_and_cleared(work):
    root = work["root"]
    db = os.path.join(root, ".aimfp-project", "project.db")
    reminders = os.path.join(root, ".aimfp-project", "watchdog", "reminders.json")
    os.makedirs(os.path.dirname(reminders), exist_ok=True)

    _write(root, "src/app.py", DRIFTED)
    _effect_process_file_event(_event(root, "src/app.py"), root, root, db, reminders, None, {})
    _effect_process_file_event(_event(root, "src/app.py"), root, root, db, reminders, None, {})
    drift = [r for r in _effect_read_reminders(reminders) if r["type"] == REMINDER_AUDIT_DRIFT]
    assert len(drift) == 1 and "stale_interactions=1" in drift[0]["message"]

    _write(root, "src/app.py", APP)                                         # back in sync
    _effect_process_file_event(_event(root, "src/app.py"), root, root, db, reminders, None, {})
    assert not [r for r in _effect_read_reminders(reminders) if r["type"] == REMINDER_AUDIT_DRIFT]


def test_watchdog_skips_byte_identical_rewrites(work):
    root = work["root"]
    db = os.path.join(root, ".aimfp-project", "project.db")
    reminders = os.path.join(root, ".aimfp-project", "watchdog", "reminders.json")
    os.makedirs(os.path.dirname(reminders), exist_ok=True)
    from aimfp.watchdog.analyzers import content_digest
    hashes = {"src/app.py": content_digest(APP)}
    time.sleep(0.01)
    _write(root, "src/app.py", APP)                                         # same bytes, new mtime
    _effect_process_file_event(_event(root, "src/app.py"), root, root, db, reminders, None, hashes)
    assert _effect_read_reminders(reminders) == ()


# ============================================================================
# CLI
# ============================================================================

def test_cli_exit_codes(work, capsys):
    root = work["root"]
    assert main_audit(["--path", "src/app.py", "--only", "interactions", "--json"], cwd=root) == EXIT_CLEAN
    _write(root, "src/app.py", DRIFTED)
    assert main_audit(["--path", "src/app.py", "--only", "interactions"], cwd=root) == EXIT_BLOCKING
    out = capsys.readouterr().out
    assert "BLOCKING stale_interactions: 1" in out and "report:" in out
    assert main_audit(["--only", "nope"], cwd=root) == EXIT_ERROR
    assert main_audit(["--bogus-flag"], cwd=root) == EXIT_ERROR


def test_cli_strict_fails_on_warnings(work):
    root = work["root"]
    _write(root, "src/app.py", 'OPS = {}\n\n\ndef run(x) -> int:\n    """Run."""\n    return OPS["add"](x, 1)\n')
    reconcile_paths(["src/app.py"], project_root=root)        # run -> lib.add unconfirmed (a warning)
    args = ["--path", "src/app.py", "--only", "interactions", "--no-report"]
    assert main_audit(args, cwd=root) == EXIT_CLEAN
    assert main_audit(args + ["--strict"], cwd=root) == EXIT_BLOCKING


def test_cli_without_project(tmp_path):
    assert main_audit([], cwd=str(tmp_path)) == EXIT_ERROR


# ============================================================================
# audit_gate setting and actionable refusals (task 65)
# ============================================================================

def _set_gate(root, value):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "user_preferences.db"))
    conn.execute("UPDATE user_settings SET setting_value = ? WHERE setting_key = 'audit_gate'", (value,))
    conn.commit()
    conn.close()


def test_new_projects_seed_audit_gate_block(root):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "user_preferences.db"))
    assert conn.execute("SELECT setting_value FROM user_settings WHERE setting_key = 'audit_gate'").fetchone() == ("block",)
    conn.close()


def test_warn_mode_completes_and_reports_would_block(work):
    root = work["root"]
    _set_gate(root, "warn")
    _write(root, "src/app.py", DRIFTED)
    r = update_task(work["task"], status="completed", project_root=root)
    assert r.success and r.audit["mode"] == "warn" and not r.audit["overridden"]
    assert r.audit["would_block"]["stale_interactions"] == 1 and r.audit["fix_steps"]
    assert _q(root, "SELECT COUNT(*) FROM notes WHERE note_type = 'warning'") == [(0,)]


def test_off_mode_skips_the_audit(work):
    root = work["root"]
    _set_gate(root, "off")
    _write(root, "src/app.py", DRIFTED)
    r = update_task(work["task"], status="completed", project_root=root)
    assert r.success and r.audit is None


def test_unknown_mode_blocks(work):
    root = work["root"]
    _set_gate(root, "sometimes")
    _write(root, "src/app.py", DRIFTED)
    assert not update_task(work["task"], status="completed", project_root=root).success


def test_refusal_carries_exact_fix_calls(work):
    root = work["root"]
    _write(root, "src/app.py", DRIFTED + '\n\ndef extra() -> int:\n    """Extra."""\n    return 1\n')
    r = update_task(work["task"], status="completed", project_root=root)
    assert "(1) reconcile_paths(paths=['src/app.py'])" in r.error
    assert "apply_audit_fixes(reason='<why>', report='" in r.error
    assert "apply=['missing_interactions', 'stale_interactions']" in r.error


def test_fix_steps_order_and_prose():
    from aimfp.helpers.audit.gate import fix_steps, normalize_gate_mode
    steps = fix_steps({"incomplete_functions": 1, "param_mismatch": 2, "missing_functions": 1}, "r.json", ("a.py",))
    assert steps[0] == "reconcile_paths(paths=['a.py'])"
    assert steps[1] == "apply_audit_fixes(reason='<why>', report='r.json', apply=['param_mismatch'])"
    assert steps[2].startswith("incomplete_functions: fill the listed fields")
    assert normalize_gate_mode(" WARN ") == "warn" and normalize_gate_mode(None) == "block"
